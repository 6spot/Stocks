"""Source independence and conflicting evidence for V2-03.

Two Documents merge into one origin group only when their sources declared the
same original; comparable Facts are compared side by side and never averaged;
material without a declaration stays explicitly unknown.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.changes import (
    ChangeWindow,
    current_watermark,
    record_change,
    scope_fingerprint,
)
from stocks.execution.config import AgentConfig, TaskConfig
from stocks.execution.service import create_or_get_run
from stocks.facts import FactInput, persist_facts
from stocks.models import (
    Analysis,
    Base,
    Document,
    DocumentChunk,
    DocumentParseBatch,
    DocumentParseState,
    Fact,
    Source,
    TaskRun,
)
from stocks.origins import (
    canonical_href,
    document_origin,
    normalize_origin_url,
    origin_key,
)
from stocks.providers import (
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    ModelTarget,
)
from stocks.repository import DocumentInput, SearchHit, ingest_document
from stocks.research.service import render_report, select_evidence
from stocks.settings import Settings

CANONICAL = "https://origin.example/story"
COLLECTED_AT = datetime(2026, 10, 20, 8, tzinfo=UTC)


def make_config() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        embedding_max_query_characters=4000,
    )


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for evidence conflict tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def embedding_targets() -> EmbeddingTargets:
    return EmbeddingTargets(
        aliases=("embedding",),
        targets=(
            ModelTarget(
                base_url="https://embedding.example/v1",
                api_key="test",
                name="embedding",
                dimensions=1536,
                capabilities=("embedding",),
            ),
        ),
    )


def fake_embed(client, targets, texts):
    return EmbeddingResponse(
        model_alias=targets.primary,
        embeddings=((0.0,) * 1536,),
    )


def declared_origin(key: str, *, kind: str = "canonical_url") -> dict:
    return {
        "kind": kind,
        "key": key,
        "url": key.split(":", 1)[1],
        "declared_sources": [{"name": "Wire", "url": "https://wire.example"}],
    }


def add_document(
    session: Session,
    *,
    source_id: str = "sec",
    dedupe_key: str = "doc-1",
    title: str = "Quarterly economic update",
    article_url: str | None = None,
    chunk_texts: tuple[str, ...] = ("Inflation declined to 2.4 percent.",),
    origin: dict | None = None,
) -> tuple[Document, list[DocumentChunk]]:
    source = session.get(Source, source_id)
    if source is None:
        source = Source(
            id=source_id,
            name=source_id,
            connector="rss",
            publisher=source_id,
            trust_tier="T1",
        )
        session.add(source)
        session.flush()
    document = Document(
        source_id=source.id,
        dedupe_key=dedupe_key,
        version=1,
        title=title,
        original_url=article_url or f"https://example.test/{dedupe_key}",
        published_at=COLLECTED_AT - timedelta(days=1),
        collected_at=COLLECTED_AT,
        summary=None,
        completeness="complete",
        content_type="text/plain",
        content_size=len(" ".join(chunk_texts)),
        content_hash=(dedupe_key.encode().hex() + "0" * 64)[:64],
        snapshot_key=f"sources/{source.id}/{dedupe_key}",
        trust_tier_snapshot="T1",
        classification=None,
        extra={"origin": origin} if origin else {},
    )
    session.add(document)
    session.flush()
    batch = DocumentParseBatch(
        document_id=document.id,
        processor_version="test",
        config_fingerprint="f" * 64,
        content_hash=document.content_hash,
        attempt_number=1,
        status="completed",
        chunk_count=len(chunk_texts),
        created_at=COLLECTED_AT,
    )
    session.add(batch)
    session.flush()
    chunks = [
        DocumentChunk(
            document_id=document.id,
            parse_batch_id=batch.id,
            position=index,
            text=text,
            locator={"segments": []},
        )
        for index, text in enumerate(chunk_texts)
    ]
    session.add_all(chunks)
    session.flush()
    session.add(
        DocumentParseState(
            document_id=document.id,
            status="completed",
            processor_version="test",
            config_fingerprint="f" * 64,
            latest_batch_id=batch.id,
            current_batch_id=batch.id,
            chunk_count=len(chunks),
            attempted_at=COLLECTED_AT,
        )
    )
    session.commit()
    return document, chunks


def add_fact(
    session: Session,
    document: Document,
    chunk: DocumentChunk,
    *,
    value: float,
    unit: str = "percent",
    metric_id: str = "cpi_yoy",
    subject_name: str = "US CPI",
    currency: str | None = None,
    period_end: datetime = datetime(2026, 9, 30, tzinfo=UTC),
) -> Fact:
    attributes = {"currency": currency} if currency else {}
    persisted = persist_facts(
        session,
        document.id,
        [
            FactInput(
                fact_type="macro_indicator",
                origin_method="llm_extraction",
                evidence_ref={
                    "kind": "chunk",
                    "chunk_id": str(chunk.id),
                    "parse_batch_id": str(chunk.parse_batch_id),
                    "position": chunk.position,
                },
                subject_name=subject_name,
                metric_id=metric_id,
                statement=f"{subject_name} was {value}.",
                value=value,
                unit=unit,
                period_end=period_end,
                available_at=document.collected_at,
                attributes=attributes,
            )
        ],
        processor_version="test",
        config_fingerprint="f" * 64,
        created_at=document.collected_at,
    )
    session.commit()
    return session.get(Fact, persisted.created_ids[0])


def search_hit(
    document: Document, chunk: DocumentChunk, *, similarity: float = 0.9
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk.id,
        document_id=document.id,
        parse_batch_id=chunk.parse_batch_id,
        position=chunk.position,
        text=chunk.text,
        locator=chunk.locator,
        title=document.title,
        source_id=document.source_id,
        version=document.version,
        published_at=document.published_at,
        source_updated_at=document.source_updated_at,
        collected_at=document.collected_at,
        completeness=document.completeness,
        similarity=similarity,
    )


def make_task(task_id: str) -> tuple[TaskConfig, AgentConfig]:
    task = TaskConfig.model_validate(
        {
            "id": task_id,
            "name": "Evidence conflicts",
            "type": "research",
            "trigger": "manual",
            "objective": "Research inflation.",
            "scope": {"companies": ["NVIDIA"], "topics": ["macro"]},
            "steps": [
                {
                    "id": "evidence",
                    "select_evidence": {
                        "kind": "select_evidence",
                        "queries": ["inflation"],
                        "limit_per_query": 3,
                        "max_tokens": 30000,
                    },
                }
            ],
        }
    )
    agent = AgentConfig.model_validate(
        {
            "id": "researcher",
            "role": "Researcher",
            "prompt": "Research.",
            "model_ref": "research",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "run_id",
                    "objective",
                    "scope",
                    "as_of",
                    "evidence",
                    "coverage",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "objective": {"type": "string"},
                    "scope": {"type": "object", "additionalProperties": True},
                    "as_of": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                    },
                    "coverage": {"type": "object", "additionalProperties": True},
                },
            },
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "findings", "open_questions"],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                    },
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    )
    return task, agent


def create_run(session: Session, task_id: str) -> TaskRun:
    task, agent = make_task(task_id)
    return create_or_get_run(session, task, [agent]).run


def add_change(session: Session, document: Document) -> None:
    record_change(
        session,
        kind="document_indexed",
        document_id=document.id,
        recorded_at=datetime(2026, 10, 20, 9, tzinfo=UTC),
    )
    session.commit()


def change_window(session: Session, run: TaskRun) -> ChangeWindow:
    return ChangeWindow(
        start=0,
        end=current_watermark(session),
        task_id=run.task_id,
        fingerprint=scope_fingerprint(run.task_id, run.scope),
        resume=False,
    )


def query_snapshot() -> dict:
    return {
        "queries": ["inflation"],
        "source_ids": [],
        "limit_per_query": 3,
        "max_tokens": 30000,
        "page_limit": 100,
    }


def semantic_result(*hits: SearchHit):
    return type(
        "SemanticResult",
        (),
        {"hits": tuple(hits), "coverage": None, "truncated": False},
    )()


# --- declared origin keys -------------------------------------------------------


def test_origin_urls_normalize_to_one_comparison_form():
    assert (
        normalize_origin_url(
            "http://WWW.Example.test:80/a/b/?utm_source=feed&b=2&a=1#section"
        )
        == "https://example.test/a/b?a=1&b=2"
    )
    # Nothing is guessed: a relative or non-http value is not an origin.
    assert normalize_origin_url("/news/story-1") is None
    assert normalize_origin_url("mailto:press@example.test") is None
    assert normalize_origin_url(None) is None


def test_declared_canonical_link_is_read_without_merging_by_similarity():
    payload = (
        '<html><head><link rel="canonical" '
        'href="https://origin.example/story?utm_medium=rss">'
        "</head><body>Copy.</body></html>"
    ).encode()
    assert canonical_href(payload, "text/html; charset=utf-8") == (
        "https://origin.example/story?utm_medium=rss"
    )
    # A PDF has no canonical link, and no title/similarity ever stands in for one.
    assert canonical_href(b"%PDF-1.4", "application/pdf") is None


def test_document_origin_prefers_the_most_explicit_declaration():
    html = (
        '<html><head><link rel="canonical" href="https://origin.example/story">'
        "</head></html>"
    ).encode()
    canonical = document_origin(
        article_url="https://mirror.example/reprint",
        payload=html,
        content_type="text/html",
        declared=[{"name": "Wire", "url": "https://wire.example/feed"}],
        external_id="tag:mirror.example,2026:1",
    )
    assert canonical["kind"] == "canonical_url"
    assert canonical["key"] == f"canonical_url:{CANONICAL}"
    # The syndication label is provenance, never the merge key.
    assert canonical["declared_sources"] == [
        {"name": "Wire", "url": "https://wire.example/feed"}
    ]
    assert origin_key(canonical) == canonical["key"]

    fetched = document_origin(
        article_url="https://mirror.example/reprint",
        payload=b"plain text",
        content_type="text/plain",
    )
    assert fetched["kind"] == "article_url"

    feed_id = document_origin(
        article_url=None,
        payload=b"",
        content_type="application/json",
        external_id="https://mirror.example/story-1",
    )
    assert feed_id["kind"] == "external_id"

    # An opaque feed id (a ``tag:`` URI, a bare counter) confirms nothing, so it
    # is recorded as no declaration instead of merging unrelated articles.
    assert (
        document_origin(
            article_url=None,
            payload=b"",
            content_type="application/json",
            external_id="tag:mirror.example,2026:1",
        )
        is None
    )

    assert (
        document_origin(
            article_url="not a url",
            payload=b"",
            content_type="application/json",
            external_id="relative/id",
        )
        is None
    )


def test_origin_key_rejects_declarations_that_confirm_nothing():
    assert origin_key(None) is None
    assert origin_key({"kind": "title", "key": "title:Merger talk"}) is None
    assert origin_key({"kind": "article_url", "key": "article_url:"}) is None
    assert (
        origin_key({"kind": "article_url", "key": "external_id:https://a.example"})
        is None
    )


# --- selection: confirmed reprints, unknown origins, conflicts ------------------


def test_confirmed_reprint_merges_but_every_original_stays_open(engine, monkeypatch):
    """AC-1: one confirmed original, both Documents still citable."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "confirmed-reprint")
        wire, wire_chunks = add_document(
            session,
            source_id="wire",
            dedupe_key="wire-story",
            title="Chipmaker raises guidance",
            origin=declared_origin(f"canonical_url:{CANONICAL}"),
        )
        mirror, mirror_chunks = add_document(
            session,
            source_id="mirror",
            dedupe_key="mirror-story",
            title="Chipmaker lifts its outlook",
            origin=declared_origin(f"canonical_url:{CANONICAL}"),
        )
        add_change(session, wire)
        add_change(session, mirror)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(wire, wire_chunks[0]),
                search_hit(mirror, mirror_chunks[0], similarity=0.7),
            ),
        )

        package = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=3,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        ).package

        coverage = package.coverage
        assert package.selected_count == 2
        assert coverage["evidence_group_count"] == 2
        assert coverage["origin_group_count"] == 1
        assert coverage["merged_origin_group_count"] == 1
        origin_group = coverage["origin_groups"][0]
        assert origin_group["kind"] == "canonical_url"
        assert origin_group["key"] == f"canonical_url:{CANONICAL}"
        assert origin_group["merged"] is True
        assert origin_group["independent_source_count"] == 1
        assert set(origin_group["document_ids"]) == {
            str(wire.id),
            str(mirror.id),
        }
        assert sorted(origin_group["source_ids"]) == ["mirror", "wire"]
        # Every original is still a citable entry with its own Chunk.
        assert {item["document_id"] for item in package.selected_chunks} == {
            str(wire.id),
            str(mirror.id),
        }
        assert {
            item["origin_id"] for item in package.selected_chunks
        } == {origin_group["origin_id"]}
        assert [
            group["origin_group_size"] for group in package.evidence_groups
        ] == [2, 2]


def test_similar_articles_without_a_declared_original_stay_independent(
    engine, monkeypatch
):
    """AC-1/AC-3: comparable titles alone never merge an independent opinion."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "similar-not-reprint")
        first, first_chunks = add_document(
            session,
            source_id="wire",
            dedupe_key="story-a",
            title="Analysts disagree about the chip cycle",
            article_url="https://wire.example/story-a",
            origin=declared_origin(
                "article_url:https://wire.example/story-a", kind="article_url"
            ),
        )
        second, second_chunks = add_document(
            session,
            source_id="opinion",
            dedupe_key="story-b",
            title="Analysts disagree about the chip cycle",
            article_url="https://opinion.example/story-b",
            origin=declared_origin(
                "article_url:https://opinion.example/story-b", kind="article_url"
            ),
        )
        add_change(session, first)
        add_change(session, second)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(first, first_chunks[0]),
                search_hit(second, second_chunks[0], similarity=0.95),
            ),
        )

        package = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=3,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        ).package

        coverage = package.coverage
        assert coverage["origin_group_count"] == 2
        assert coverage["merged_origin_group_count"] == 0
        assert len(coverage["origin_groups"]) == 2
        assert all(
            item["merged"] is False for item in coverage["origin_groups"]
        )
        assert {
            group["origin_group_size"] for group in package.evidence_groups
        } == {1}


def test_document_without_a_declared_origin_is_reported_as_unknown(
    engine, monkeypatch
):
    """AC-3: no declaration means unknown, never 'confirmed unique'."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "unknown-origin")
        document, chunks = add_document(
            session,
            dedupe_key="unlabelled",
            origin=None,
        )
        add_change(session, document)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(document, chunks[0])
            ),
        )

        package = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=3,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        ).package

        origin_group = package.coverage["origin_groups"][0]
        assert origin_group["kind"] == "single_document"
        assert origin_group["key"] == f"document:{document.id}"
        assert origin_group["url"] is None
        assert origin_group["merged"] is False
        assert origin_group["document_ids"] == [str(document.id)]
        assert package.evidence_groups[0]["origin_group_size"] == 1


def test_same_metric_conflict_is_listed_side_by_side(engine, monkeypatch):
    """AC-2: differing values are compared, never averaged or overwritten."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "metric-conflict")
        first, first_chunks = add_document(
            session,
            source_id="bureau",
            dedupe_key="cpi-official",
            title="Official CPI release",
            origin=declared_origin(
                "article_url:https://bureau.example/cpi", kind="article_url"
            ),
        )
        second, second_chunks = add_document(
            session,
            source_id="commentary",
            dedupe_key="cpi-commentary",
            title="Commentary on CPI",
            origin=declared_origin(
                "article_url:https://commentary.example/cpi", kind="article_url"
            ),
        )
        add_fact(session, first, first_chunks[0], value=2.4)
        add_fact(session, second, second_chunks[0], value=3.1)
        add_change(session, first)
        add_change(session, second)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(first, first_chunks[0]),
                search_hit(second, second_chunks[0]),
            ),
        )

        package = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=3,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        ).package

        comparisons = package.metric_comparisons
        assert package.coverage["metric_comparisons"] == comparisons
        assert len(comparisons) == 1
        comparison = comparisons[0]
        assert comparison["metric_id"] == "cpi_yoy"
        assert comparison["value_conflict"] is True
        assert comparison["basis_mismatch"] is False
        assert comparison["period_mismatch"] is False
        assert sorted(item["value"] for item in comparison["facts"]) == [2.4, 3.1]
        assert {item["document_id"] for item in comparison["facts"]} == {
            str(first.id),
            str(second.id),
        }
        # Both published values survive in the model input, and no averaged
        # value is invented anywhere in the package.
        assert "2.75" not in str(comparisons)
        assert "2.75" not in str(package.evidence_groups)
        flagged = [
            fact
            for group in package.evidence_groups
            for fact in group["fact_refs"]
            if fact["comparison_id"] == comparison["comparison_id"]
        ]
        assert len(flagged) == 2
        assert all(fact["value_conflict"] is True for fact in flagged)


def test_different_basis_or_period_is_flagged_not_merged(engine, monkeypatch):
    """AC-2: unit/period differences are named instead of silently combined."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "basis-mismatch")
        first, first_chunks = add_document(
            session,
            source_id="bureau",
            dedupe_key="revenue-quarter",
            origin=declared_origin(
                "article_url:https://bureau.example/q3", kind="article_url"
            ),
        )
        second, second_chunks = add_document(
            session,
            source_id="commentary",
            dedupe_key="revenue-annual",
            origin=declared_origin(
                "article_url:https://commentary.example/fy", kind="article_url"
            ),
        )
        add_fact(
            session,
            first,
            first_chunks[0],
            value=120.0,
            unit="usd_billion",
            metric_id="revenue",
            subject_name="Example Corp",
        )
        add_fact(
            session,
            second,
            second_chunks[0],
            value=430.0,
            unit="usd_billion",
            metric_id="revenue",
            subject_name="Example Corp",
            period_end=datetime(2026, 12, 31, tzinfo=UTC),
        )
        add_change(session, first)
        add_change(session, second)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(first, first_chunks[0]),
                search_hit(second, second_chunks[0]),
            ),
        )

        package = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("revenue",),
            limit_per_query=3,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        ).package

        comparison = package.metric_comparisons[0]
        assert comparison["metric_id"] == "revenue"
        # Two different periods, so the values are not a same-basis conflict:
        # the entry names the period difference instead of picking a winner.
        assert comparison["period_mismatch"] is True
        assert comparison["value_conflict"] is False
        assert {
            item["period_end"] for item in comparison["facts"]
        } == {"2026-09-30T00:00:00+00:00", "2026-12-31T00:00:00+00:00"}


def test_report_states_independent_originals_and_conflicting_values():
    """AC-2/AC-3: the deterministic report never calls a conflict verified."""

    analysis = Analysis(
        id=uuid4(),
        run_id=uuid4(),
        evidence_package_id=uuid4(),
        summary="Two origins reported different CPI values.",
        findings=[],
        open_questions=["Which CPI basis is comparable?"],
        evidence_refs=[],
        research_inputs=[],
        coverage={
            "gaps": [],
            "origin_groups": [
                {
                    "origin_id": "origin-1",
                    "kind": "canonical_url",
                    "key": f"canonical_url:{CANONICAL}",
                    "url": CANONICAL,
                    "declared_sources": [],
                    "document_ids": ["doc-1", "doc-2"],
                    "source_ids": ["wire", "mirror"],
                    "document_count": 2,
                    "independent_source_count": 1,
                    "merged": True,
                }
            ],
        },
        created_at=COLLECTED_AT,
    )
    report = render_report(
        title="CPI brief",
        objective="Research inflation.",
        scope={"topics": ["macro"]},
        as_of=COLLECTED_AT,
        analysis=analysis,
        metric_comparisons=[
            {
                "comparison_id": "comparison-1",
                "subject_name": "US CPI",
                "symbol": None,
                "metric_id": "cpi_yoy",
                "value_conflict": True,
                "basis_mismatch": False,
                "period_mismatch": False,
                "fact_count": 2,
                "units": ["percent"],
                "facts": [
                    {
                        "fact_id": "fact-1",
                        "document_id": "doc-1",
                        "value": 2.4,
                        "unit": "percent",
                        "currency": None,
                        "period_start": None,
                        "period_end": "2026-09-30T00:00:00+00:00",
                        "available_at": "2026-10-20T08:00:00+00:00",
                        "statement": "US CPI was 2.4.",
                    },
                    {
                        "fact_id": "fact-2",
                        "document_id": "doc-2",
                        "value": 3.1,
                        "unit": "percent",
                        "currency": None,
                        "period_start": None,
                        "period_end": "2026-09-30T00:00:00+00:00",
                        "available_at": "2026-10-20T08:00:00+00:00",
                        "statement": "US CPI was 3.1.",
                    },
                ],
                "note": "Values are listed as published.",
            }
        ],
    )

    assert "## Sources and Conflicts" in report
    assert "Independent originals: 1 covering 2 document version(s)." in report
    assert "Confirmed reprints of one original are counted once (1 group(s))" in report
    assert "cpi_yoy (US CPI): values differ" in report
    assert "2.4 percent" in report
    assert "3.1 percent" in report
    assert "none of them is averaged, overwritten or marked verified." in report


def test_report_says_unknown_instead_of_implying_independence():
    """AC-3: without declarations the count is missing detail, not zero risk."""

    analysis = Analysis(
        id=uuid4(),
        run_id=uuid4(),
        evidence_package_id=uuid4(),
        summary="Nothing declared an original.",
        findings=[],
        open_questions=[],
        evidence_refs=[],
        research_inputs=[],
        coverage={"gaps": [], "origin_groups": []},
        created_at=COLLECTED_AT,
    )

    report = render_report(
        title="Brief",
        objective="Research inflation.",
        scope={"topics": ["macro"]},
        as_of=COLLECTED_AT,
        analysis=analysis,
        metric_comparisons=[],
    )

    assert "number of independent originals is unknown" in report
    assert "No two selected Facts describe the same metric" in report


def local_store(payload: bytes):
    digest = hashlib.sha256(payload).hexdigest()

    def store(config, source_id, body, *, content_type):
        assert body == payload
        return f"sources/{source_id}/{digest}", digest

    return store


def make_item(payload: bytes, **overrides) -> DocumentInput:
    values = {
        "source_id": "sec",
        "dedupe_key": "release-1",
        "title": "CPI release",
        "payload": payload,
        "content_type": "text/html",
        "collected_at": COLLECTED_AT,
    }
    values.update(overrides)
    return DocumentInput(**values)


def test_ingest_rejects_an_origin_that_confirms_nothing(engine):
    """A declaration without a supported kind/key is never stored silently."""

    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                publisher="SEC",
                trust_tier="T1",
            )
        )
        session.commit()
        item = make_item(b"<html><body>Copy.</body></html>", origin={"kind": "title"})

        with pytest.raises(ValueError, match="origin must declare"):
            ingest_document(
                session, make_config(), item, store=local_store(item.payload)
            )


def test_document_input_origin_round_trips_through_ingest(engine):
    """The stored declaration is exactly what the source wrote down."""

    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                publisher="SEC",
                trust_tier="T1",
            )
        )
        session.commit()
        origin = declared_origin(f"canonical_url:{CANONICAL}")
        item = make_item(
            b"<html><body>Copy.</body></html>", origin=origin
        )
        document = ingest_document(
            session, make_config(), item, store=local_store(item.payload)
        ).document
        session.commit()

        stored = session.scalars(
            select(Document).where(Document.id == document.id)
        ).one()
        assert stored.extra["origin"] == origin
