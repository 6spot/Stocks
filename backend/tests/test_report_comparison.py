"""PostgreSQL-backed tests for V2-04 saved-report comparison.

The comparison must read two stored snapshots, keep the earlier report's own
versions readable, explain a difference as new material or a new source
version, and never let a failed Run stand in as successful history.
"""
from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stocks.api import app
from stocks.auth import SESSION_COOKIE
from stocks.extraction.service import extract_document_facts
from stocks.facts import FactInput, persist_facts
from stocks.research.evidence import fact_selection_ref
from stocks.models import (
    Analysis,
    Base,
    Document,
    EvidencePackage,
    Fact,
    Report,
    Source,
    TaskDefinition,
    TaskRun,
)
from stocks.processing.stages import STAGE_FACTS, claim_stage, request_reprocess
from stocks.repository import (
    DocumentInput,
    ParseChunkInput,
    ingest_document,
    list_parse_chunks,
    persist_parse_result,
)
from stocks.settings import Settings

ARTICLES = {
    1: b"<html><body><article><p>Inflation eased to 2.4% in September.</p></article></body></html>",
    2: b"<html><body><article><p>Inflation eased to 2.1% in September (revised).</p></article></body></html>",
}
CHUNKS = {
    1: "Inflation eased to 2.4% in September.",
    2: "Inflation eased to 2.1% in September (revised).",
}
TASK_ID = "comparison_brief"
EARLIER_CREATED = datetime(2026, 10, 20, 2, tzinfo=UTC)
LATER_CREATED = datetime(2026, 10, 21, 2, tzinfo=UTC)


def make_config() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_bucket="evidence",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
        owner_password="test-owner-password",
        owner_session_secret="test-owner-session-secret-32-characters",
    )


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for report comparison tests")
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
    objects: dict[str, bytes] = {}

    def store(config, source_id, payload, *, content_type):
        digest = sha256(payload).hexdigest()
        key = f"sources/{source_id}/{digest}"
        objects[key] = payload
        return key, digest

    def read(config, key):
        return objects[key]

    return objects, store, read


@pytest.fixture
def client(engine, snapshot_store, monkeypatch):
    _objects, _store, read = snapshot_store
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    monkeypatch.setattr("stocks.api.session_factory", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr("stocks.api.settings", lambda: make_config())
    with TestClient(app) as client:
        login = client.post(
            "/api/session/login",
            json={"password": "test-owner-password"},
        )
        assert login.status_code == 200
        assert SESSION_COOKIE in client.cookies
        yield client


def persist_source(session: Session) -> None:
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


def persist_version(
    session: Session,
    config: Settings,
    store,
    *,
    collected_at: datetime,
    payload: bytes,
    text: str,
    dedupe_key: str = "release-1",
) -> dict:
    """Ingest and parse one content version of the same source document."""

    document = ingest_document(
        session,
        config,
        DocumentInput(
            source_id="sec",
            dedupe_key=dedupe_key,
            title="CPI release",
            payload=payload,
            content_type="text/html; charset=utf-8",
            collected_at=collected_at,
            published_at=collected_at - timedelta(hours=2),
            source_updated_at=collected_at,
            original_url=f"https://www.sec.gov/news/{dedupe_key}",
            completeness="complete",
        ),
        store=store,
    ).document
    persist_parse_result(
        session,
        document.id,
        processor_version="test-parser",
        config_fingerprint="f" * 64,
        content_hash=document.content_hash,
        status="completed",
        error=None,
        chunks=(ParseChunkInput(text=text, locator={"kind": "text", "char_start": 0}),),
        attempted_at=collected_at,
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
        "collected_at": document.collected_at,
    }
    session.expunge_all()
    return saved


def persist_fact(
    session: Session,
    saved: dict,
    *,
    value: float,
    available_at: datetime,
) -> dict:
    persisted = persist_facts(
        session,
        saved["document_id"],
        [
            FactInput(
                fact_type="macro_indicator",
                origin_method="llm_extraction",
                evidence_ref={
                    "kind": "chunk",
                    "chunk_id": str(saved["chunk_id"]),
                    "parse_batch_id": str(saved["parse_batch_id"]),
                    "position": saved["position"],
                },
                subject_name="US CPI",
                metric_id="cpi_yoy",
                statement=f"Inflation was {value} percent.",
                value=value,
                unit="percent",
                period_end=datetime(2026, 9, 30, tzinfo=UTC),
                available_at=available_at,
                attributes={"currency": "USD"},
            )
        ],
        processor_version="test",
        config_fingerprint="f" * 64,
        created_at=available_at,
    )
    session.commit()
    return {"fact_id": str(persisted.created_ids[0])}


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
        "title": saved["title"],
        "published_at": saved["published_at"].isoformat(),
        "collected_at": saved["collected_at"].isoformat(),
        "fact_ids": [],
    }


def persist_task(session: Session) -> None:
    session.add(
        TaskDefinition(
            id=TASK_ID,
            name="Comparison Brief",
            type="research",
            trigger="manual",
            enabled=True,
            scope={"regions": ["US"]},
            config_snapshot={},
            config_hash="a" * 64,
            created_at=EARLIER_CREATED - timedelta(days=1),
            updated_at=EARLIER_CREATED - timedelta(days=1),
        )
    )
    session.commit()


def persist_report(
    session: Session,
    *,
    created_at: datetime,
    as_of: datetime,
    selected: dict,
    fact_ids: list[str],
    findings: list[dict],
    open_questions: list[str],
    gaps: list[str],
    unexamined_count: int | None,
    status: str = "succeeded",
    scope: dict | None = None,
    ledger: list[dict] | None = None,
    cited_inputs: list[dict] | None = None,
) -> dict:
    """Save one Run with its Package, Analysis and Report."""

    run_scope = scope or {"regions": ["US"]}
    run = TaskRun(
        task_id=TASK_ID,
        status=status,
        objective="Summarize saved US macro evidence.",
        strategy="evidence_first",
        scope=run_scope,
        config_snapshot={},
        model_snapshot={},
        prompt_snapshot={},
        config_hash="a" * 64,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        error=None if status == "succeeded" else {"code": "MODEL_UNAVAILABLE"},
    )
    session.add(run)
    session.flush()
    # The stored snapshot keeps plain identifiers, so the same helper feeds the
    # Package, the Analysis and the comparison without leaking ORM objects.
    chunk_ref = {**selected_ref(selected), "fact_ids": list(fact_ids)}
    # Freeze each cited Fact as the report knew it, like the real Evidence
    # Package does. A comparison must never read today's Fact row instead.
    frozen_facts = []
    for fact_id in fact_ids:
        row = session.get(Fact, UUID(fact_id))
        assert row is not None
        frozen_facts.append(fact_selection_ref(row))
    coverage = {
        "gaps": list(gaps),
        "selected_count": 1,
        "evidence_group_count": 1,
        "origin_group_count": 1,
        "merged_origin_group_count": 0,
        "fact_count": len(fact_ids),
    }
    package = EvidencePackage(
        run_id=run.id,
        objective=run.objective,
        scope=run_scope,
        as_of=as_of,
        selection_policy_version="v2",
        selection_budget={"max_tokens": 30000},
        queries=[],
        selected_chunks=[chunk_ref],
        evidence_groups=[
            {
                "document_id": chunk_ref["document_id"],
                "chunk_ids": [chunk_ref["chunk_id"]],
                "fact_refs": deepcopy(frozen_facts),
                "origin_id": "origin-1",
            }
        ],
        metric_comparisons=[],
        coverage=coverage,
        candidates_discovered=None if unexamined_count is None else 3,
        candidates_screened=None if unexamined_count is None else 3,
        selected_count=1,
        unexamined_count=unexamined_count,
        retrieval_truncated=False,
        created_at=created_at,
    )
    session.add(package)
    session.flush()
    article_findings = [
        {**deepcopy(item), "id": f"finding-{index}", "title": item["statement"]}
        for index, item in enumerate(findings, start=1)
    ]
    analysis = Analysis(
        run_id=run.id,
        evidence_package_id=package.id,
        title="Saved Evidence Brief",
        summary="Saved macro summary.",
        sections=[{
            "heading": "宏观观察",
            "body": "Saved macro summary.",
            "finding_ids": [item["id"] for item in article_findings],
        }],
        findings=article_findings,
        open_questions=list(open_questions),
        evidence_refs=[
            {
                "chunk_id": chunk_ref["chunk_id"],
                "document_id": chunk_ref["document_id"],
            },
            *[
                {
                    "chunk_id": chunk["chunk_id"],
                    "document_id": record["document_id"],
                }
                for record in (cited_inputs or [])
                for chunk in record.get("chunks", [])
            ],
        ],
        research_inputs=deepcopy(ledger or []),
        coverage=deepcopy(coverage),
        created_at=created_at,
    )
    session.add(analysis)
    session.flush()
    report = Report(
        run_id=run.id,
        analysis_id=analysis.id,
        task_id=TASK_ID,
        title="Saved Evidence Brief",
        content="# Saved Evidence Brief\n",
        created_at=created_at,
    )
    session.add(report)
    session.commit()
    return {
        "report_id": report.id,
        "run_id": run.id,
        "analysis_id": analysis.id,
        "package_id": package.id,
    }


def finding(kind: str, statement: str, uncertainty: str) -> dict:
    return {
        "kind": kind,
        "statement": statement,
        "rationale": "Saved rationale.",
        "uncertainty": uncertainty,
        "evidence_refs": [],
    }


def build_two_reports(engine, snapshot_store, monkeypatch):
    """One earlier and one later successful report over the same source."""

    config = make_config()
    _objects, store, read = snapshot_store
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    with Session(engine) as session:
        persist_source(session)
        persist_task(session)
        first = persist_version(
            session,
            config,
            store,
            collected_at=EARLIER_CREATED,
            payload=ARTICLES[1],
            text=CHUNKS[1],
        )
        first_fact = persist_fact(
            session, first, value=2.4, available_at=EARLIER_CREATED
        )
        earlier = persist_report(
            session,
            created_at=EARLIER_CREATED,
            as_of=EARLIER_CREATED,
            selected=first,
            fact_ids=[first_fact["fact_id"]],
            findings=[
                finding("fact", "Inflation eased to 2.4%.", "单一来源，尚未复核。"),
                finding("source_opinion", "Analysts expect a pause.", "媒体观点。"),
            ],
            open_questions=["Were later revisions published?"],
            gaps=["partial_source_content"],
            unexamined_count=None,
        )
        second = persist_version(
            session,
            config,
            store,
            collected_at=LATER_CREATED,
            payload=ARTICLES[2],
            text=CHUNKS[2],
        )
        second_fact = persist_fact(
            session, second, value=2.1, available_at=LATER_CREATED
        )
        later = persist_report(
            session,
            created_at=LATER_CREATED,
            as_of=LATER_CREATED,
            selected=second,
            fact_ids=[second_fact["fact_id"]],
            findings=[
                finding(
                    "fact",
                    "Inflation eased to 2.4%.",
                    "第二份资料给出不同数值，冲突尚未解决。",
                ),
                finding(
                    "model_inference",
                    "A pause is more likely.",
                    "结合两份资料推断。",
                ),
            ],
            open_questions=[
                "Were later revisions published?",
                "Which CPI basis is comparable?",
            ],
            gaps=[],
            unexamined_count=0,
        )
    return earlier, later, first, second


def test_comparison_orders_sides_and_reports_each_difference(
    engine, client, snapshot_store, monkeypatch
):
    """AC-1/AC-2: both snapshots stay readable and every difference is named."""

    earlier, later, first, second = build_two_reports(
        engine, snapshot_store, monkeypatch
    )

    response = client.get(
        f"/api/reports/{later['report_id']}/comparison",
        params={"other_report_id": str(earlier["report_id"])},
    )
    assert response.status_code == 200
    data = response.json()

    # The earlier/later roles come from stored created_at, not from the caller.
    assert data["earlier"]["report_id"] == str(earlier["report_id"])
    assert data["later"]["report_id"] == str(later["report_id"])
    # The path named the newer report, so the response order is swapped.
    assert data["ordering"] == {
        "by": "created_at",
        "requested_report_id": str(later["report_id"]),
        "swapped": True,
    }
    assert data["warnings"] == []
    assert data["earlier"]["as_of"].startswith("2026-10-20")
    assert data["later"]["as_of"].startswith("2026-10-21")
    assert data["earlier"]["coverage"]["unexamined_count"] is None
    assert data["later"]["coverage"]["unexamined_count"] == 0

    reversed_response = client.get(
        f"/api/reports/{earlier['report_id']}/comparison",
        params={"other_report_id": str(later["report_id"])},
    )
    assert reversed_response.status_code == 200
    reversed_data = reversed_response.json()
    assert reversed_data["ordering"]["swapped"] is False
    assert reversed_data["ordering"]["requested_report_id"] == str(
        earlier["report_id"]
    )
    assert reversed_data["earlier"]["report_id"] == str(earlier["report_id"])

    # Findings are matched only by exact (kind, statement).
    assert data["findings"]["appeared"]["count"] == 1
    assert data["findings"]["appeared"]["items"][0]["statement"] == (
        "A pause is more likely."
    )
    assert data["findings"]["disappeared"]["items"][0]["statement"] == (
        "Analysts expect a pause."
    )
    persisting = data["findings"]["persisting"]["items"][0]
    assert persisting["statement"] == "Inflation eased to 2.4%."
    assert persisting["uncertainty_changed"] is True
    assert data["open_questions"]["appeared"]["items"] == [
        "Which CPI basis is comparable?"
    ]
    assert data["open_questions"]["persisting"]["count"] == 1

    # The same stored material appears as one source version change, and both
    # versions stay cite-able.
    persisted = data["documents"]["persisting"]["items"][0]
    assert persisted["version_changed"] is True
    assert persisted["content_changed"] is True
    assert persisted["earlier"]["document_id"] == str(first["document_id"])
    assert persisted["earlier"]["version"] == 1
    assert persisted["later"]["document_id"] == str(second["document_id"])
    assert persisted["later"]["version"] == 2
    assert data["documents"]["appeared"]["count"] == 0
    assert data["documents"]["disappeared"]["count"] == 0

    for saved, payload in ((first, ARTICLES[1]), (second, ARTICLES[2])):
        original = client.get(f"/api/documents/{saved['document_id']}/original")
        assert original.status_code == 200
        assert original.content == payload


def test_reprocessing_keeps_a_report_fact_readable_and_its_comparison_unchanged(
    engine, client, snapshot_store, monkeypatch
):
    """An old report retains its cited Fact even if extraction proposes a new value."""

    earlier, later, first, _second = build_two_reports(
        engine, snapshot_store, monkeypatch
    )
    comparison_path = f"/api/reports/{later['report_id']}/comparison"
    params = {"other_report_id": str(earlier["report_id"])}
    response = client.get(comparison_path, params=params)
    assert response.status_code == 200
    original_comparison = response.json()
    old_fact = original_comparison["facts"]["metric_changes"]["items"][0]["earlier"][0]
    fact_path = f"/api/facts/{old_fact['fact_id']}"
    response = client.get(fact_path)
    assert response.status_code == 200
    original_fact = response.json()["fact"]
    with Session(engine) as session:
        analysis = session.get(Analysis, earlier["analysis_id"])
        original_refs = deepcopy(analysis.evidence_refs)
        assert original_refs
        package = session.get(EvidencePackage, earlier["package_id"])
        original_groups = deepcopy(package.evidence_groups)

    candidate = FactInput(
        fact_type=original_fact["fact_type"],
        origin_method=original_fact["origin_method"],
        evidence_ref=deepcopy(original_fact["evidence_ref"]),
        metric_id=original_fact["metric_id"],
        subject_name=original_fact["subject_name"],
        symbol=original_fact["symbol"],
        statement="A different extraction candidate.",
        value=99.0,
        unit="percent",
    )
    route = SimpleNamespace(
        protocol="openai_compatible",
        routing=SimpleNamespace(primary="test", fallbacks=()),
    )
    now = LATER_CREATED + timedelta(hours=1)
    with Session(engine) as session:
        request_reprocess(session, first["document_id"], STAGE_FACTS, now=now)
    with Session(engine) as session:
        claim = claim_stage(
            session,
            STAGE_FACTS,
            owner_token="historical-fact",
            now=now,
            document_ids=[first["document_id"]],
        )
        assert claim is not None and claim.reprocess_requested
        summary = extract_document_facts(
            session,
            make_config(),
            session.get(Document, first["document_id"]),
            route=route,
            client=None,
            claim=claim,
            clock=lambda: now,
            extract=lambda *_: ((candidate,), {"model_alias": "test"}),
        )
        assert summary.status == "completed"
        assert summary.created == 0
        assert summary.reused == 1

    response = client.get(fact_path)
    assert response.status_code == 200
    assert response.json()["fact"] == original_fact
    response = client.get(comparison_path, params=params)
    assert response.status_code == 200
    assert response.json() == original_comparison
    assert client.get(f"/api/reports/{earlier['report_id']}").status_code == 200
    original = client.get(f"/api/documents/{first['document_id']}/original")
    assert original.status_code == 200
    assert original.content == ARTICLES[1]
    with Session(engine) as session:
        analysis = session.get(Analysis, earlier["analysis_id"])
        package = session.get(EvidencePackage, earlier["package_id"])
        assert analysis.evidence_refs == original_refs
        assert package.evidence_groups == original_groups


def test_comparison_counts_an_adopted_supplement_only_when_cited(
    engine, client, snapshot_store, monkeypatch
):
    """AC-2/AC-3: adopted evidence enters by citation, once, from its frozen value."""

    config = make_config()
    _objects, store, read = snapshot_store
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    with Session(engine) as session:
        persist_source(session)
        persist_task(session)
        first = persist_version(
            session,
            config,
            store,
            collected_at=EARLIER_CREATED,
            payload=ARTICLES[1],
            text=CHUNKS[1],
        )
        first_fact = persist_fact(
            session, first, value=2.4, available_at=EARLIER_CREATED
        )
        supplemental = persist_version(
            session,
            config,
            store,
            collected_at=LATER_CREATED,
            payload=ARTICLES[2],
            text=CHUNKS[2],
            dedupe_key="release-2",
        )
        supplemental_fact = persist_fact(
            session, supplemental, value=2.1, available_at=LATER_CREATED
        )
        with Session(engine) as session:
            fact_row = session.get(Fact, UUID(supplemental_fact["fact_id"]))
            frozen = fact_selection_ref(fact_row)
        ledger = [
            {
                "kind": "input",
                "input_id": "input-1",
                "tool_id": "research_search",
                "purpose": "Fetch the revision.",
                "role": "external_supplement",
                "document_id": str(supplemental["document_id"]),
                "source_id": supplemental["source_id"],
                "document_version": supplemental["version"],
                "parse_batch_id": str(supplemental["parse_batch_id"]),
                "title": supplemental["title"],
                "url": "https://www.sec.gov/news/release-1",
                "chunks": [
                    {
                        "chunk_id": str(supplemental["chunk_id"]),
                        "position": supplemental["position"],
                        "chunk_ids": [str(supplemental["chunk_id"])],
                        "fact_ids": [str(fact_row.id)],
                    }
                ],
                "fact_refs": [frozen],
                "timing": ["collected_after_as_of"],
                "recorded_at": LATER_CREATED.isoformat(),
            }
        ]
        earlier = persist_report(
            session,
            created_at=EARLIER_CREATED,
            as_of=EARLIER_CREATED,
            selected=first,
            fact_ids=[first_fact["fact_id"]],
            findings=[finding("fact", "Inflation eased to 2.4%.", "Single source.")],
            open_questions=[],
            gaps=[],
            unexamined_count=None,
        )
        cited = persist_report(
            session,
            created_at=LATER_CREATED,
            as_of=LATER_CREATED,
            selected=first,
            fact_ids=[first_fact["fact_id"]],
            findings=[
                finding("fact", "Inflation eased to 2.4%.", "Single source."),
                finding("fact", "A revision reported 2.1%.", "Adopted input."),
            ],
            open_questions=[],
            gaps=[],
            unexamined_count=None,
            ledger=ledger,
            cited_inputs=ledger,
        )
        # The adopted supplement is registered but never cited.
        uncited = persist_report(
            session,
            created_at=LATER_CREATED + timedelta(hours=1),
            as_of=LATER_CREATED,
            selected=first,
            fact_ids=[first_fact["fact_id"]],
            findings=[finding("fact", "Inflation eased to 2.4%.", "Single source.")],
            open_questions=[],
            gaps=[],
            unexamined_count=None,
            ledger=ledger,
        )

    def comparison(newer):
        return client.get(
            f"/api/reports/{newer['report_id']}/comparison",
            params={"other_report_id": str(earlier["report_id"])},
        ).json()

    data = comparison(cited)
    appeared = data["facts"]["appeared"]
    assert appeared["count"] == 1
    assert appeared["items"][0]["value"] == 2.1
    assert appeared["items"][0]["document_id"] == str(
        supplemental["document_id"]
    )
    # The adopted Document is cited only through the supplemental Chunk; the
    # initial Chunk of the other version stays the same material for both sides.
    assert data["documents"]["appeared"]["count"] == 1
    assert data["documents"]["appeared"]["items"][0]["document_id"] == str(
        supplemental["document_id"]
    )
    # The same initial Fact cited by both reports is compared once.
    assert data["facts"]["disappeared"]["count"] == 0
    # The later report also cites a second value for the same metric, which is a
    # real revision in what the report relied on, not an averaged result.
    assert data["facts"]["metric_changes"]["count"] == 1

    uncited_data = comparison(uncited)
    assert uncited_data["facts"]["appeared"]["count"] == 0
    assert uncited_data["documents"]["appeared"]["count"] == 0


def test_comparison_ignores_material_a_report_did_not_cite(
    engine, client, snapshot_store, monkeypatch
):
    """A wider Evidence Package is not a change in what the report relied on."""

    config = make_config()
    _objects, store, read = snapshot_store
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    with Session(engine) as session:
        persist_source(session)
        persist_task(session)
        first = persist_version(
            session,
            config,
            store,
            collected_at=EARLIER_CREATED,
            payload=ARTICLES[1],
            text=CHUNKS[1],
        )
        first_fact = persist_fact(
            session, first, value=2.4, available_at=EARLIER_CREATED
        )
        uncited = persist_version(
            session,
            config,
            store,
            collected_at=LATER_CREATED,
            payload=ARTICLES[2],
            text=CHUNKS[2],
            dedupe_key="release-2",
        )
        uncited_fact = persist_fact(
            session, uncited, value=2.1, available_at=LATER_CREATED
        )
        earlier = persist_report(
            session,
            created_at=EARLIER_CREATED,
            as_of=EARLIER_CREATED,
            selected=first,
            fact_ids=[first_fact["fact_id"]],
            findings=[finding("fact", "Inflation eased to 2.4%.", "Single source.")],
            open_questions=[],
            gaps=[],
            unexamined_count=None,
        )
        later = persist_report(
            session,
            created_at=LATER_CREATED,
            as_of=LATER_CREATED,
            selected=first,
            fact_ids=[first_fact["fact_id"]],
            findings=[finding("fact", "Inflation eased to 2.4%.", "Single source.")],
            open_questions=[],
            gaps=[],
            unexamined_count=None,
        )
        # The later package selected a second Document that its Analysis never
        # cited. Its presence alone must not look like a report change.
        package = session.get(EvidencePackage, later["package_id"])
        assert package is not None
        selected = deepcopy(package.selected_chunks)
        selected.append(
            {
                **selected_ref(uncited),
                "fact_ids": [uncited_fact["fact_id"]],
            }
        )
        package.selected_chunks = selected
        groups = deepcopy(package.evidence_groups)
        groups.append(
            {
                "document_id": str(uncited["document_id"]),
                "chunk_ids": [str(uncited["chunk_id"])],
                "fact_refs": [],
                "role": "new_information",
            }
        )
        package.evidence_groups = groups
        session.commit()

    data = client.get(
        f"/api/reports/{later['report_id']}/comparison",
        params={"other_report_id": str(earlier["report_id"])},
    ).json()

    assert data["documents"]["appeared"]["count"] == 0
    assert data["facts"]["appeared"]["count"] == 0
    assert data["facts"]["disappeared"]["count"] == 0
    assert data["facts"]["metric_changes"]["count"] == 0
    assert str(uncited["document_id"]) not in str(data["documents"])
    assert str(uncited_fact["fact_id"]) not in str(data["facts"])
    assert {
        item["earlier"]["document_id"]
        for item in data["documents"]["persisting"]["items"]
    } == {str(first["document_id"])}


def test_comparison_reads_frozen_fact_values_instead_of_current_rows(
    engine, client, snapshot_store, monkeypatch
):
    """A later source change must not rewrite the value an old report cited."""

    earlier, later, first, _second = build_two_reports(
        engine, snapshot_store, monkeypatch
    )
    comparison_path = f"/api/reports/{later['report_id']}/comparison"
    params = {"other_report_id": str(earlier["report_id"])}
    before = client.get(comparison_path, params=params).json()

    # A new Fact on the same cited Document, plus a changed stored value, is a
    # current-data change. Neither may be read into the saved comparison.
    with Session(engine) as session:
        fact = session.scalar(
            select(Fact).where(Fact.document_id == first["document_id"])
        )
        assert fact is not None
        fact.value = 9.9
        session.commit()

    after = client.get(comparison_path, params=params).json()
    assert after == before
    assert after["facts"]["metric_changes"]["items"][0]["earlier"][0]["value"] == 2.4


def test_comparison_explains_a_metric_revision_as_a_source_change(
    engine, client, snapshot_store, monkeypatch
):
    """AC-2: a revised value and late material are real source changes."""

    earlier, later, _first, second = build_two_reports(
        engine, snapshot_store, monkeypatch
    )

    data = client.get(
        f"/api/reports/{later['report_id']}/comparison",
        params={"other_report_id": str(earlier["report_id"])},
    ).json()

    change = data["facts"]["metric_changes"]["items"][0]
    assert change["metric_id"] == "cpi_yoy"
    assert change["value_changed"] is True
    assert change["basis_changed"] is False
    assert change["period_changed"] is False
    assert change["earlier"][0]["value"] == 2.4
    assert change["later"][0]["value"] == 2.1
    assert change["earlier"][0]["currency"] == "USD"
    # No average is invented anywhere in the comparison.
    assert "2.25" not in str(data)

    late = data["late_material"]
    assert late["earlier_as_of"].startswith("2026-10-20")
    assert late["documents"]["count"] == 1
    assert late["documents"]["items"][0]["document_id"] == str(
        second["document_id"]
    )
    assert late["documents"]["items"][0]["collected_after_as_of"] is True

    coverage = data["coverage"]
    assert coverage["gaps_added"]["count"] == 0
    assert coverage["gaps_removed"]["items"] == ["partial_source_content"]
    assert coverage["unexamined_unknown"] == {"earlier": True, "later": False}


def test_comparison_rejects_self_unknown_and_failed_reports(
    engine, client, snapshot_store, monkeypatch
):
    """AC-3: a failed Run can never stand in as successful history."""

    earlier, later, _first, _second = build_two_reports(
        engine, snapshot_store, monkeypatch
    )

    same = client.get(
        f"/api/reports/{later['report_id']}/comparison",
        params={"other_report_id": str(later["report_id"])},
    )
    assert same.status_code == 400

    unknown = client.get(
        f"/api/reports/{later['report_id']}/comparison",
        params={"other_report_id": str(uuid4())},
    )
    assert unknown.status_code == 404

    config = make_config()
    _objects, store, _read = snapshot_store
    with Session(engine) as session:
        source_document = persist_version(
            session,
            config,
            store,
            collected_at=LATER_CREATED + timedelta(hours=1),
            payload=b"<html><body><article><p>Late copy.</p></article></body></html>",
            text="Late copy.",
        )
        # A Report row that a buggy writer might have created for a failed Run.
        failed = persist_report(
            session,
            created_at=LATER_CREATED + timedelta(hours=2),
            as_of=LATER_CREATED + timedelta(hours=2),
            selected=source_document,
            fact_ids=[],
            findings=[],
            open_questions=[],
            gaps=[],
            unexamined_count=0,
            status="failed",
        )

    blocked = client.get(
        f"/api/reports/{earlier['report_id']}/comparison",
        params={"other_report_id": str(failed["report_id"])},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Report comparison requires successful Runs"

    listed = client.get("/api/reports").json()
    statuses = {item["report_id"]: item["run_status"] for item in listed["items"]}
    assert statuses[str(failed["report_id"])] == "failed"
    assert statuses[str(later["report_id"])] == "succeeded"


def test_older_report_keeps_its_cited_version_after_a_new_one_is_saved(
    engine, client, snapshot_store, monkeypatch
):
    """AC-3: saving a newer report never rewrites the older report's citations."""

    earlier, later, first, second = build_two_reports(
        engine, snapshot_store, monkeypatch
    )

    detail = client.get(f"/api/reports/{earlier['report_id']}").json()
    entry = detail["evidence"][0]
    assert entry["document_id"] == str(first["document_id"])
    assert entry["cited_document_version"] == 1
    assert entry["version_matches"] is True
    assert entry["text"] == CHUNKS[1]
    assert entry["is_current_collection_pointer"] is False

    newer_detail = client.get(f"/api/reports/{later['report_id']}").json()
    newer_entry = newer_detail["evidence"][0]
    assert newer_entry["document_id"] == str(second["document_id"])
    assert newer_entry["cited_document_version"] == 2
    assert newer_entry["is_current_collection_pointer"] is True

