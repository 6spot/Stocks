import os
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stocks.api import app
from stocks.models import (
    Analysis,
    Base,
    DocumentEmbeddingState,
    EvidencePackage,
    ModelCall,
    Report,
    Source,
    TaskDefinition,
    TaskRun,
)
from stocks.repository import (
    DocumentInput,
    ParseChunkInput,
    ingest_document,
    list_parse_chunks,
    persist_parse_result,
)
from stocks.embedding_space import VECTOR_DIMENSIONS
from stocks.settings import Settings

ARTICLE_V1 = (
    b"<html><body><article><p>Inflation eased to 2.4% in September.</p></article></body></html>"
)
ARTICLE_V2 = (
    b"<html><body><article><p>Inflation eased to 2.1% in September (revised).</p></article></body></html>"
)
CHUNK_V1 = "Inflation eased to 2.4% in September."
CHUNK_V2 = "Inflation eased to 2.1% in September (revised)."
TASK_ID = "saved_evidence_brief"
CREATED_AT = datetime(2026, 10, 20, 2, tzinfo=UTC)


def make_config(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://unused/unused",
        "s3_endpoint_url": "https://storage.example",
        "s3_bucket": "evidence",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for report API tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


@pytest.fixture
def snapshot_store():
    """Stand in for S3: keep exact bytes, return their SHA-256 like s3.py does."""
    objects: dict[str, bytes] = {}

    def store(config, source_id, payload, *, content_type):
        digest = sha256(payload).hexdigest()
        key = f"sources/{source_id}/{digest}"
        objects[key] = payload
        return key, digest

    def read(config, key):
        return objects[key]

    return objects, store, read


def build_client(engine, snapshot_store, monkeypatch, config=None):
    _objects, _store, read = snapshot_store
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    monkeypatch.setattr("stocks.api.session_factory", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr("stocks.api.settings", lambda: config or make_config())
    return TestClient(app)


@pytest.fixture
def client(engine, snapshot_store, monkeypatch):
    with build_client(engine, snapshot_store, monkeypatch) as client:
        yield client


def make_item(payload: bytes, **overrides) -> DocumentInput:
    values = {
        "source_id": "sec",
        "dedupe_key": "release-1",
        "title": "CPI release",
        "payload": payload,
        "content_type": "text/html; charset=utf-8",
        "collected_at": datetime(2026, 10, 20, 1, tzinfo=UTC),
        "published_at": datetime(2026, 10, 19, tzinfo=UTC),
        "source_updated_at": datetime(2026, 10, 19, 1, tzinfo=UTC),
        "original_url": "https://www.sec.gov/news/release-1",
        "completeness": "complete",
    }
    values.update(overrides)
    return DocumentInput(**values)


def persist_source(session) -> None:
    session.add(
        Source(
            id="sec",
            name="SEC Press Releases",
            connector="rss",
            publisher="U.S. Securities and Exchange Commission",
            trust_tier="T1",
        )
    )
    session.commit()


def persist_parsed_version(session, config, store, item, text: str) -> dict:
    """Ingest one Document version, parse it, and return plain saved identifiers.

    Values are read before the session closes, so a test can never touch a
    detached ORM instance.
    """
    document = ingest_document(session, config, item, store=store).document
    persist_parse_result(
        session,
        document.id,
        processor_version="test-parser",
        config_fingerprint="f" * 64,
        content_hash=document.content_hash,
        status="completed",
        error=None,
        chunks=(ParseChunkInput(text=text, locator={"kind": "text", "char_start": 0}),),
        attempted_at=item.collected_at,
    )
    chunk = list_parse_chunks(session, document.id)[0]
    saved = {
        "document_id": document.id,
        "chunk_id": chunk.id,
        "parse_batch_id": chunk.parse_batch_id,
        "position": chunk.position,
        "version": document.version,
        "source_id": document.source_id,
        "content_hash": document.content_hash,
        "title": document.title,
        "published_at": document.published_at,
        "source_updated_at": document.source_updated_at,
        "collected_at": document.collected_at,
        "completeness": document.completeness,
    }
    session.expunge_all()
    return saved


def mark_indexed(session, saved: dict) -> None:
    session.add(
        DocumentEmbeddingState(
            document_id=saved["document_id"],
            status="completed",
            parse_batch_id=saved["parse_batch_id"],
            model_alias="embedding",
            model_name="text-embedding-3-small",
            dimensions=VECTOR_DIMENSIONS,
            chunk_count=1,
            indexed_count=1,
            attempted_at=CREATED_AT,
        )
    )
    session.commit()


def selected_ref(saved: dict) -> dict:
    return {
        "chunk_id": str(saved["chunk_id"]),
        "document_id": str(saved["document_id"]),
        "parse_batch_id": str(saved["parse_batch_id"]),
        "document_version": saved["version"],
        "source_id": saved["source_id"],
        "position": saved["position"],
        "role": "direct",
        "reason": "direct_scope_match",
        "similarity": 0.81,
        "query_rank": 1,
        "estimated_tokens": 12,
        "title": saved["title"],
        "published_at": saved["published_at"].isoformat(),
        "source_updated_at": saved["source_updated_at"].isoformat(),
        "collected_at": saved["collected_at"].isoformat(),
        "completeness": saved["completeness"],
    }


def persist_run(
    session,
    *,
    status: str = "succeeded",
    selected_chunks: list[dict] | None = None,
    gaps: list[str] | None = None,
):
    """Save a Run with its Evidence Package, plus Analysis and Report on success.

    A failed Run keeps its Evidence Package but produces no Report, which is the
    rule the V0-06 execution service enforces.
    """
    selected = list(selected_chunks or [])
    if session.get(TaskDefinition, TASK_ID) is None:
        session.add(
            TaskDefinition(
                id=TASK_ID,
                name="Saved Evidence Brief",
                type="research",
                trigger="manual",
                enabled=True,
                scope={"regions": ["US"]},
                config_snapshot={},
                config_hash="a" * 64,
                created_at=CREATED_AT,
                updated_at=CREATED_AT,
            )
        )
    run = TaskRun(
        task_id=TASK_ID,
        status=status,
        objective="Summarize saved US macro evidence.",
        strategy="evidence_first",
        scope={"regions": ["US"], "topics": ["macro"]},
        config_snapshot={},
        model_snapshot={
            "providers": {"default": {"protocol": "openai_compatible"}},
            "agents": {"research": {"routing": {"primary": "research"}}},
        },
        prompt_snapshot={"research_writer": "Analyze only supplied evidence."},
        config_hash="a" * 64,
        created_at=CREATED_AT,
        started_at=CREATED_AT,
        finished_at=CREATED_AT,
        error=None if status == "succeeded" else {"code": "MODEL_UNAVAILABLE"},
    )
    session.add(run)
    session.flush()
    package = EvidencePackage(
        run_id=run.id,
        objective=run.objective,
        scope=run.scope,
        as_of=CREATED_AT,
        selection_policy_version="v0.1",
        selection_budget={"max_tokens": 30000, "limit_per_query": 8},
        queries=[
            {
                "query": "recent US macro policy evidence",
                "role": "direct",
                "result_count": len(selected),
                "selected_count": len(selected),
                "truncated": False,
                "gap_reasons": list(gaps or []),
            }
        ],
        selected_chunks=selected,
        coverage={"gaps": list(gaps or []), "current_query_only": True},
        candidates_discovered=None,
        candidates_screened=len(selected),
        selected_count=len(selected),
        unexamined_count=None,
        retrieval_truncated=False,
        created_at=CREATED_AT,
    )
    session.add(package)
    report = None
    if status == "succeeded":
        session.flush()
        refs = [
            {"chunk_id": item["chunk_id"], "document_id": item["document_id"]}
            for item in selected
        ]
        analysis = Analysis(
            run_id=run.id,
            evidence_package_id=package.id,
            summary="One saved release supports the statement.",
            findings=[
                {
                    "kind": "fact",
                    "statement": "Inflation eased to 2.4%.",
                    "rationale": "The only cited release reports this figure.",
                    "uncertainty": "Single source within this package.",
                    "evidence_refs": refs,
                }
            ]
            if refs
            else [],
            open_questions=["Were later revisions published?"],
            evidence_refs=refs,
            coverage=package.coverage,
            created_at=CREATED_AT,
        )
        session.add(analysis)
        session.flush()
        report = Report(
            run_id=run.id,
            analysis_id=analysis.id,
            task_id=TASK_ID,
            title="Saved Evidence Brief",
            content="# Saved Evidence Brief\n\nEvidence: "
            + ", ".join(f"{ref['document_id']}#{ref['chunk_id']}" for ref in refs)
            + "\n",
            created_at=CREATED_AT,
        )
        session.add(report)
        session.add(
            ModelCall(
                run_id=run.id,
                step_id="research",
                attempt_number=1,
                requested_alias="research",
                attempt_alias="research",
                requested_model_name="research-model",
                attempt_model_name="research-model",
                reported_model_name="research-model",
                status="succeeded",
                duration_ms=1200,
                prompt_tokens=900,
                completion_tokens=200,
                total_tokens=1100,
                created_at=CREATED_AT,
            )
        )
    session.commit()
    # Capture plain identifiers: tests read these after the session closes.
    return (
        {"run_id": run.id, "selected_count": package.selected_count},
        {"package_id": package.id, "selected_count": package.selected_count},
        {"report_id": report.id, "analysis_id": report.analysis_id} if report else None,
    )


def cite_original(client, document_id, expected_bytes: bytes, content_type: str):
    response = client.get(f"/api/documents/{document_id}/original")
    assert response.status_code == 200
    # Stored evidence comes back byte for byte; HTML never becomes a live page.
    assert response.content == expected_bytes
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
    assert response.headers["X-Content-Sha256"] == sha256(expected_bytes).hexdigest()
    assert response.headers["X-Content-Hash-Match"] == "true"
    return response


def test_report_detail_reads_the_versions_it_cited_after_a_new_version(engine, client, snapshot_store):
    config = make_config()
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        persist_source(session)
        first = persist_parsed_version(
            session, config, store, make_item(ARTICLE_V1), CHUNK_V1
        )
        mark_indexed(session, first)
        _run, _package, report = persist_run(
            session, selected_chunks=[selected_ref(first)]
        )

    # The same URL is collected again with changed bytes, so version 2 exists and
    # the collection pointer moves forward. The saved report must not follow it.
    with Session(engine) as session:
        second = persist_parsed_version(
            session,
            config,
            store,
            make_item(
                ARTICLE_V2,
                collected_at=datetime(2026, 10, 20, 5, tzinfo=UTC),
                source_updated_at=datetime(2026, 10, 20, tzinfo=UTC),
            ),
            CHUNK_V2,
        )
    first_id, second_id = first["document_id"], second["document_id"]

    detail = client.get(f"/api/reports/{report['report_id']}").json()
    entry = detail["evidence"][0]
    assert entry["document_id"] == str(first_id)
    assert entry["document_version"] == 1
    assert entry["cited_document_version"] == 1
    assert entry["version_matches"] is True
    assert entry["text"] == CHUNK_V1
    assert entry["chunk_available"] is True
    assert entry["parse_is_current"] is True
    assert entry["is_current_collection_pointer"] is False
    assert entry["original"]["storage_key"] == f"sources/sec/{sha256(ARTICLE_V1).hexdigest()}"
    assert entry["original"]["endpoint"] == f"/api/documents/{first_id}/original"
    assert entry["embedding_state"]["status"] == "completed"
    assert str(first_id) in detail["report"]["content"]
    assert detail["run"]["status"] == "succeeded"
    assert detail["cited_chunk_ids"] == [entry["chunk_id"]]
    assert detail["coverage_gaps"] == []
    assert [item["latest_document_id"] for item in detail["latest_collection_states"]] == [
        str(second_id)
    ]

    single = client.get(
        f"/api/reports/{report['report_id']}/evidence/{entry['chunk_id']}"
    ).json()
    assert single["evidence"]["text"] == CHUNK_V1
    assert single["evidence"]["document"]["version"] == 1
    assert [item["version"] for item in single["document_versions"]] == [1, 2]
    assert single["latest_collection_state"]["latest_document_id"] == str(second_id)

    cite_original(client, first_id, ARTICLE_V1, "text/plain")
    cite_original(client, second_id, ARTICLE_V2, "text/plain")

    listed = client.get("/api/reports").json()
    assert [item["report_id"] for item in listed] == [str(report["report_id"])]
    assert listed[0]["run_status"] == "succeeded"
    assert listed[0]["analysis_id"] == detail["report"]["analysis_id"]

    document_detail = client.get(f"/api/documents/{first_id}").json()
    assert document_detail["is_current_collection_pointer"] is False
    assert [item["version"] for item in document_detail["versions"]] == [1, 2]
    assert [
        item["is_current_collection_pointer"] for item in document_detail["versions"]
    ] == [False, True]
    assert document_detail["current_chunks"][0]["text"] == CHUNK_V1
    assert document_detail["original"]["stored"] is True
    assert document_detail["original"]["readable"] is True
    assert document_detail["embedding_state"]["indexed_count"] == 1


def test_failed_run_never_replaces_the_last_successful_report(engine, client, snapshot_store):
    config = make_config()
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        persist_source(session)
        document = persist_parsed_version(
            session, config, store, make_item(ARTICLE_V1), CHUNK_V1
        )
        mark_indexed(session, document)
        _run, _package, report = persist_run(
            session, selected_chunks=[selected_ref(document)]
        )
        failed_run, failed_package, failed_report = persist_run(
            session,
            status="failed",
            selected_chunks=[selected_ref(document)],
            gaps=["embedding_failures"],
        )
    assert failed_report is None
    document_id = document["document_id"]

    listed = client.get("/api/reports").json()
    assert [item["report_id"] for item in listed] == [str(report["report_id"])]
    detail = client.get(f"/api/reports/{report['report_id']}").json()
    assert detail["evidence"][0]["text"] == CHUNK_V1
    failed_detail = client.get(f"/api/documents/{document_id}").json()
    assert failed_detail["parse_state"]["status"] == "completed"
    assert client.get(f"/api/reports/{failed_run['run_id']}").status_code == 404
    assert failed_package["selected_count"] == 1


def test_original_download_and_unknown_ids(engine, client, snapshot_store):
    config = make_config()
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        persist_source(session)
        document = persist_parsed_version(
            session, config, store, make_item(ARTICLE_V1), CHUNK_V1
        )
        pdf = ingest_document(
            session,
            config,
            make_item(
                b"%PDF-1.4 not a text article",
                dedupe_key="attachment-1",
                title="Quarterly filing PDF",
                content_type="application/pdf",
            ),
            store=store,
        ).document
        pdf_id = pdf.id
        session.expunge_all()

    document_id = document["document_id"]
    download = client.get(f"/api/documents/{document_id}/original?download=true")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "text/plain" in download.headers["content-type"]

    binary = client.get(f"/api/documents/{pdf_id}/original")
    assert binary.content == b"%PDF-1.4 not a text article"
    assert "application/octet-stream" in binary.headers["content-type"]

    missing = uuid4()
    assert client.get(f"/api/documents/{missing}").status_code == 404
    assert client.get(f"/api/documents/{missing}/original").status_code == 404
    assert client.get(f"/api/reports/{missing}").status_code == 404
    assert client.get(f"/api/reports/{missing}/evidence/{missing}").status_code == 404
    assert client.get("/api/documents/not-a-uuid").status_code == 422


def test_evidence_reference_outside_the_package_is_not_resolved(engine, client, snapshot_store):
    config = make_config()
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        persist_source(session)
        document = persist_parsed_version(
            session, config, store, make_item(ARTICLE_V1), CHUNK_V1
        )
        _run, _package, report = persist_run(
            session, selected_chunks=[selected_ref(document)]
        )

    response = client.get(f"/api/reports/{report['report_id']}/evidence/{uuid4()}")
    assert response.status_code == 404
    assert "Evidence Package" in response.json()["detail"]

    # The same chunk stays readable through the collection-path route.
    resolved = client.get(
        f"/api/reports/{report['report_id']}/evidence/{document['chunk_id']}"
    )
    assert resolved.status_code == 200
    assert resolved.json()["evidence"]["text"] == CHUNK_V1


def test_missing_evidence_rows_are_reported_as_gaps_not_silence(engine, client):
    with Session(engine) as session:
        persist_source(session)
        _run, _package, report = persist_run(
            session,
            selected_chunks=[
                {
                    "chunk_id": str(uuid4()),
                    "document_id": str(uuid4()),
                    "parse_batch_id": str(uuid4()),
                    "document_version": 3,
                    "source_id": "sec",
                    "position": 0,
                    "role": "direct",
                    "title": "Partial feed item",
                    "completeness": "partial",
                    "collected_at": datetime(2026, 10, 20, 1, tzinfo=UTC).isoformat(),
                }
            ],
            gaps=["no_background_matches"],
        )

    detail = client.get(f"/api/reports/{report['report_id']}").json()
    entry = detail["evidence"][0]
    assert entry["chunk_available"] is False
    assert entry["text"] is None
    assert entry["version_matches"] is False
    assert entry["document"]["completeness"] == "partial"
    assert entry["document"]["title"] == "Partial feed item"
    assert entry["original"]["stored"] is False
    for gap in (
        "no_background_matches",
        "document_not_stored",
        "chunk_unavailable",
        "partial_source_content",
        "chunk_not_in_current_parse_batch",
        "chunk_not_in_current_index",
    ):
        assert gap in detail["coverage_gaps"]


def test_pending_index_and_unconfigured_storage_stay_visible(engine, snapshot_store, monkeypatch):
    config = make_config(s3_endpoint_url=None, s3_access_key=None, s3_secret_key=None)
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        persist_source(session)
        item = make_item(ARTICLE_V1)
        document = ingest_document(session, config, item, store=store).document
        persist_parse_result(
            session,
            document.id,
            processor_version="test-parser",
            config_fingerprint="f" * 64,
            content_hash=document.content_hash,
            status="completed",
            error=None,
            chunks=(ParseChunkInput(text=CHUNK_V1, locator={"kind": "text"}),),
            attempted_at=item.collected_at,
        )
        chunk = list_parse_chunks(session, document.id)[0]
        saved = {
            "document_id": document.id,
            "chunk_id": chunk.id,
            "parse_batch_id": chunk.parse_batch_id,
            "position": chunk.position,
            "version": document.version,
            "source_id": document.source_id,
            "title": document.title,
            "published_at": document.published_at,
            "source_updated_at": document.source_updated_at,
            "collected_at": document.collected_at,
            "completeness": document.completeness,
        }
        _run, _package, report = persist_run(
            session, selected_chunks=[selected_ref(saved)]
        )
    document_id = saved["document_id"]

    client = build_client(engine, snapshot_store, monkeypatch, config=config)
    with client:
        detail = client.get(f"/api/reports/{report['report_id']}").json()
        entry = detail["evidence"][0]
        assert entry["embedding_state"]["status"] == "pending"
        assert entry["embedding_state"]["indexed_count"] == 0
        assert entry["original"]["readable"] is False
        assert "snapshot_storage_unconfigured" in detail["coverage_gaps"]
        assert "chunk_not_in_current_index" in detail["coverage_gaps"]

        unavailable = client.get(f"/api/documents/{document_id}/original")
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "SNAPSHOT_STORAGE_UNAVAILABLE"
        assert "storage.example" not in unavailable.text

        listed = client.get("/api/documents").json()
        assert listed[0]["embedding_state"]["status"] == "pending"
        assert listed[0]["collection_state"]["latest_document_id"] == str(document_id)


def test_documents_listing_keeps_its_existing_shape(engine, client, snapshot_store):
    config = make_config()
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        persist_source(session)
        first = persist_parsed_version(
            session, config, store, make_item(ARTICLE_V1), CHUNK_V1
        )
        mark_indexed(session, first)
        second = persist_parsed_version(
            session,
            config,
            store,
            make_item(
                ARTICLE_V2,
                collected_at=datetime(2026, 10, 20, 5, tzinfo=UTC),
            ),
            CHUNK_V2,
        )

    listed = client.get("/api/documents").json()
    # Newest collection first, and both immutable versions keep their own state.
    assert [row["version"] for row in listed] == [2, 1]
    row = listed[1]
    assert row["id"] == str(first["document_id"])
    assert row["dedupe_key"] == "release-1"
    assert row["source_id"] == "sec"
    assert row["version"] == 1
    assert row["trust_tier"] == "T1"
    assert row["completeness"] == "complete"
    assert row["content_hash"] == sha256(ARTICLE_V1).hexdigest()
    assert row["parse_state"]["status"] == "completed"
    assert row["embedding_state"]["status"] == "completed"
    # Every row of the same item reports the item's current collection pointer, so an
    # older version is visible without pretending it is no longer stored.
    for row in listed:
        assert row["collection_state"]["completeness"] == "complete"
        assert row["collection_state"]["latest_document_id"] == str(second["document_id"])
        assert UUID(row["collection_state"]["latest_document_id"])
    assert listed[0]["content_hash"] == sha256(ARTICLE_V2).hexdigest()
