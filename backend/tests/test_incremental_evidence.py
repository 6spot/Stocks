"""PostgreSQL-backed tests for V1-06 incremental Evidence Selection.

The cases here exercise the persisted query results rather than the internal
collector calls: one Document must form one evidence chain across incremental
and semantic retrieval, coverage must distinguish screened from unscanned
material, and a completed filtered scan must still be resumable and advanceable.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.changes import (
    ChangeWindow,
    checkpoint_scan,
    current_watermark,
    initial_scan_progress,
    record_change,
    scope_fingerprint,
)
from stocks.execution.config import AgentConfig, TaskConfig
from stocks.execution.service import _complete_change_scan, create_or_get_run
from stocks.facts import FactInput, persist_facts
from stocks.models import (
    Analysis,
    Base,
    Document,
    DocumentChunk,
    DocumentParseBatch,
    DocumentParseState,
    DocumentProcessingState,
    EvidencePackage,
    Fact,
    Report,
    ResearchCursor,
    Source,
    TaskRun,
    TaskStepResult,
)
from stocks.providers import (
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    ModelTarget,
)
from stocks.repository import SearchHit
from stocks.research.evidence import mark_conflicting_facts
from stocks.research.service import select_evidence
from stocks.settings import Settings
from stocks.embedding_space import VECTOR_DIMENSIONS


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
        pytest.skip(
            "TEST_DATABASE_URL is required for incremental evidence tests"
        )
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
                dimensions=VECTOR_DIMENSIONS,
                capabilities=("embedding",),
            ),
        ),
    )


def fake_embed(client, targets, texts):
    return EmbeddingResponse(
        model_alias=targets.primary,
        embeddings=((0.0,) * VECTOR_DIMENSIONS,),
    )


def add_document_with_chunks(
    session: Session,
    *,
    source_id: str = "sec",
    dedupe_key: str = "doc-1",
    title: str = "Quarterly economic update",
    chunk_texts: tuple[str, ...] = ("Inflation declined to 2.4 percent.",),
    classification: dict | None = None,
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
    now = datetime(2026, 10, 20, 8, tzinfo=UTC)
    document = Document(
        source_id=source.id,
        dedupe_key=dedupe_key,
        version=1,
        title=title,
        original_url=f"https://example.test/{dedupe_key}",
        published_at=now - timedelta(days=1),
        collected_at=now,
        summary=None,
        completeness="complete",
        content_type="text/plain",
        content_size=len(" ".join(chunk_texts)),
        content_hash=(dedupe_key.encode().hex() + "0" * 64)[:64],
        snapshot_key=f"sources/{source.id}/{dedupe_key}",
        trust_tier_snapshot="T1",
        classification=classification,
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
        created_at=now,
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
            attempted_at=now,
        )
    )
    session.commit()
    return document, chunks


def add_facts(
    session: Session,
    document: Document,
    chunk: DocumentChunk,
    *,
    values: tuple[float, ...] = (2.4,),
) -> list[Fact]:
    inputs = [
        FactInput(
            fact_type="macro_indicator",
            origin_method="llm_extraction",
            evidence_ref={
                "kind": "chunk",
                "chunk_id": str(chunk.id),
                "parse_batch_id": str(chunk.parse_batch_id),
                "position": chunk.position,
            },
            subject_name="US CPI",
            metric_id="cpi_yoy",
            statement=f"Inflation was {value} percent.",
            value=value,
            unit="percent",
            period_end=datetime(2026, 9, 30, tzinfo=UTC),
            available_at=document.collected_at,
            attributes={"source_text_position": index},
        )
        for index, value in enumerate(values)
    ]
    persisted = persist_facts(
        session,
        document.id,
        inputs,
        processor_version="test",
        config_fingerprint="f" * 64,
        created_at=document.collected_at,
    )
    session.commit()
    return list(
        session.scalars(
            select(Fact)
            .where(Fact.id.in_(persisted.created_ids))
            .order_by(Fact.created_at, Fact.id)
        ).all()
    )


def search_hit(
    document: Document,
    chunk: DocumentChunk,
    *,
    similarity: float = 0.9,
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
            "name": "Incremental evidence",
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
                        "limit_per_query": 2,
                        "max_tokens": 30000,
                    },
                },
                {
                    "id": "research",
                    "research_agent": {
                        "kind": "research_agent",
                        "agent": "researcher",
                        "input": "evidence",
                    },
                },
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
                    "strategy",
                    "scope",
                    "as_of",
                    "evidence_package_id",
                    "evidence",
                    "coverage",
                    "truncated",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "objective": {"type": "string"},
                    "strategy": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "scope": {"type": "object", "additionalProperties": True},
                    "as_of": {"type": "string"},
                    "evidence_package_id": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                    },
                    "coverage": {"type": "object", "additionalProperties": True},
                    "truncated": {"type": "boolean"},
                },
            },
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
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
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                    },
                },
            },
        }
    )
    return task, agent


def create_run(session: Session, task_id: str) -> TaskRun:
    task, agent = make_task(task_id)
    return create_or_get_run(session, task, [agent]).run


def add_change(
    session: Session,
    document: Document,
    *,
    kind: str = "document_indexed",
    fact_id=None,
    recorded_at: datetime | None = None,
) -> int:
    watermark = record_change(
        session,
        kind=kind,
        document_id=document.id,
        fact_id=fact_id,
        recorded_at=recorded_at or datetime(2026, 10, 20, 9, tzinfo=UTC),
    )
    session.commit()
    return watermark


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
        "limit_per_query": 2,
        "max_tokens": 30000,
        "page_limit": 100,
    }


def semantic_result(*hits: SearchHit, truncated: bool = False):
    return type(
        "SemanticResult",
        (),
        {"hits": tuple(hits), "coverage": None, "truncated": truncated},
    )()


def test_document_and_fact_merge_across_incremental_and_semantic_paths(
    engine, monkeypatch
):
    """AC-1: one Document is one chain, while unrelated Facts still remain."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "incremental-merge")
        document, chunks = add_document_with_chunks(
            session,
            chunk_texts=(
                "Inflation was 2.4 percent.",
                "Unemployment held at 4.1 percent.",
            ),
        )
        facts = add_facts(session, document, chunks[0], values=(2.4,))
        other = Fact(
            document_id=document.id,
            fact_key="unrelated-fact",
            fact_type="labor_indicator",
            origin_method="llm_extraction",
            evidence_ref={
                "kind": "chunk",
                "chunk_id": str(chunks[1].id),
                "parse_batch_id": str(chunks[1].parse_batch_id),
                "position": chunks[1].position,
            },
            subject_name="US labor",
            metric_id="unemployment_rate",
            statement="Unemployment held at 4.1 percent.",
            value=4.1,
            unit="percent",
            available_at=document.collected_at,
            attributes={},
            processor_version="test",
            config_fingerprint="f" * 64,
            created_at=document.collected_at,
        )
        session.add(other)
        session.commit()
        add_change(session, document)
        window = change_window(session, run)

        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(document, chunks[0])
            ),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        )

        package = result.package
        groups = [
            group
            for group in package.evidence_groups
            if group["document_id"] == str(document.id)
        ]
        assert len(groups) == 1
        group = groups[0]
        assert group["role"] == "new_information"
        assert group["match_role"] == "background"
        assert str(chunks[0].id) in group["chunk_ids"]
        assert str(chunks[1].id) in group["chunk_ids"]
        assert {item["fact_id"] for item in group["fact_refs"]} == {
            str(facts[0].id),
            str(other.id),
        }
        assert package.selected_count == 2
        assert package.coverage["evidence_group_count"] == 1
        assert package.coverage["fact_count"] == 2
        assert package.candidates_discovered == 2
        assert package.candidates_screened == 2
        assert package.queries[0]["role"] == "background"
        # The semantic result overlaps the changed Document and adds no second
        # selection or evidence group.
        assert package.queries[0]["selected_count"] == 0

        assert session.get(EvidencePackage, package.id) is not None


def test_failed_classification_does_not_filter_semantic_recall(engine, monkeypatch):
    """AC-1: classification failure is a processing gap, not a recall filter."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "failed-classification")
        document, chunks = add_document_with_chunks(
            session,
            dedupe_key="classification-failed",
            classification=None,
        )
        session.add(
            DocumentProcessingState(
                document_id=document.id,
                stage="classify",
                status="failed",
                attempts=1,
                max_attempts=3,
                auto_retry=True,
                reprocess_requested=False,
                error={
                    "code": "CLASSIFICATION_INVALID",
                    "message": "Classification result was invalid",
                },
            )
        )
        session.commit()

        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(document, chunks[0])
            ),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=1,
            max_tokens=30000,
            embed=fake_embed,
        )

        assert result.package.selected_chunks[0]["document_id"] == str(document.id)
        assert result.package.evidence_groups[0]["role"] == "historical_context"


def test_incremental_and_history_do_not_double_count_chunks(
    engine, monkeypatch
):
    """AC-2: raw candidates are de-duplicated while group count stays distinct."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "dedupe")
        document, chunks = add_document_with_chunks(
            session,
            chunk_texts=("Inflation fell.", "Core inflation also fell."),
        )
        add_change(session, document, kind="document_ingested")
        add_change(session, document, kind="document_parsed")
        window = change_window(session, run)
        hits = tuple(search_hit(document, chunk) for chunk in chunks)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(*hits),
        )

        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        )

        package = result.package
        assert package.selected_count == 2
        assert package.coverage["evidence_group_count"] == 1
        assert package.coverage["evidence_group_count"] < package.selected_count
        # Two distinct Document+Chunk candidates were seen through both paths.
        assert package.candidates_discovered == 2
        assert package.candidates_screened == 2
        assert len({item["chunk_id"] for item in package.selected_chunks}) == 2
        assert len(package.evidence_groups) == 1


def test_new_and_historical_material_keep_distinct_roles(
    engine, monkeypatch
):
    """AC-2: one package carries both roles without collapsing their meanings."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "mixed-roles")
        new_document, new_chunks = add_document_with_chunks(
            session,
            dedupe_key="new-change",
            title="New inflation release",
            chunk_texts=("The new inflation release showed 2.2 percent.",),
        )
        historical, historical_chunks = add_document_with_chunks(
            session,
            dedupe_key="historical",
            title="Historical inflation analysis",
            chunk_texts=("A year ago inflation was 4.8 percent.",),
        )
        add_change(session, new_document, kind="document_classified")
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(historical, historical_chunks[0])
            ),
        )

        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        )

        roles = {
            group["document_id"]: group["role"]
            for group in result.package.evidence_groups
        }
        assert roles == {
            str(new_document.id): "new_information",
            str(historical.id): "historical_context",
        }
        assert result.package.coverage["new_information_group_count"] == 1
        assert result.package.coverage["historical_context_group_count"] == 1


def test_truncated_semantic_query_preserves_unknown_counts_and_raw_cursor(
    engine, monkeypatch
):
    """AC-3: a bounded query leaves both counts unknown and keeps continuation."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "truncated")
        changed_document, changed_chunks = add_document_with_chunks(
            session,
            dedupe_key="changed",
        )
        semantic_document, semantic_chunks = add_document_with_chunks(
            session,
            dedupe_key="semantic",
        )
        change_id = add_change(session, changed_document)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(
                search_hit(semantic_document, semantic_chunks[0]),
                truncated=True,
            ),
        )

        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=1,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        )

        package = result.package
        assert package.candidates_discovered is None
        assert package.unexamined_count is None
        assert package.retrieval_truncated is True
        assert result.scan_page["completed"] is True
        assert result.scan_page["after_watermark"] == change_id
        assert result.scan_page["after_change_id"] is not None
        assert package.change_scan == {
            "window": {
                "change_watermark_start": 0,
                "change_watermark_end": change_id,
            },
            **result.scan_page,
        }


def test_completed_filtered_scan_can_advance_cursor(engine, monkeypatch):
    """AC-3: screened but unselected material does not look unscanned."""

    import stocks.research.service as research_service

    with Session(engine) as session:
        run = create_run(session, "filtered")
        document, chunks = add_document_with_chunks(session)
        change_id = add_change(session, document)
        window = change_window(session, run)
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(),
        )

        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            # The changed Chunk is examined, then rejected by the input budget.
            max_tokens=1,
            change_window=window,
            scan_query_snapshot={**query_snapshot(), "max_tokens": 1},
            embed=fake_embed,
        )
        package = result.package
        assert package.selected_count == 0
        assert package.coverage["filtered_out_count"] > 0
        assert package.unexamined_count == 0
        assert package.candidates_discovered == 1
        assert package.candidates_screened == 1

        # A checkpoint records the exhausted raw page independently of whether
        # the material was selected for the package.
        run.change_watermark_start = window.start
        run.change_watermark_end = window.end
        run.window_start = run.created_at
        run.window_end = run.created_at + timedelta(seconds=1)
        run.scope_fingerprint = window.fingerprint
        run.change_scan_progress = initial_scan_progress(
            window,
            query_snapshot={**query_snapshot(), "max_tokens": 1},
            window_start=run.window_start,
            window_end=run.window_end,
        )
        run.change_scan_completed = False
        checkpoint_scan(
            session,
            run,
            page=result.scan_page,
            selection={
                "evidence_package_id": str(package.id),
                "selected_count": package.selected_count,
            },
        )

        task, _ = make_task(run.task_id)
        session.add(
            Analysis(
                run_id=run.id,
                evidence_package_id=package.id,
                title="研究资料与覆盖边界",
                summary="Coverage was insufficient.",
                sections=[{"heading": "资料覆盖", "body": "Coverage was insufficient.", "finding_ids": []}],
                findings=[],
                open_questions=["Did the filtered material matter?"],
                evidence_refs=[],
                coverage=package.coverage,
                created_at=run.created_at,
            )
        )
        session.flush()
        analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == run.id)
        )
        assert analysis is not None
        session.add(
            Report(
                run_id=run.id,
                analysis_id=analysis.id,
                task_id=task.id,
                title=task.name,
                content="No selected evidence.",
                created_at=run.created_at,
            )
        )
        session.add(
            TaskStepResult(
                run_id=run.id,
                step_id="research",
                position=1,
                step_type="research_agent",
                status="succeeded",
                output={"summary": "Coverage was insufficient."},
                evidence_refs=[],
                usage={},
                error=None,
                created_at=run.created_at,
            )
        )
        session.commit()
        _complete_change_scan(
            session,
            run,
            task,
            window,
        )
        session.commit()

        cursor = session.scalar(
            select(ResearchCursor).where(
                ResearchCursor.task_id == run.task_id,
                ResearchCursor.scope_fingerprint == window.fingerprint,
            )
        )
        assert run.change_scan_progress["status"] == "scanned"
        assert run.change_scan_progress["after_watermark"] == change_id
        assert run.change_scan_completed is True
        assert cursor is not None
        assert cursor.watermark == change_id


def test_conflicting_facts_are_retained_and_marked():
    """Different values for one structured identity stay side by side."""

    now = datetime(2026, 10, 21, tzinfo=UTC)
    facts_document_id = uuid4()
    facts = [
        Fact(
            id=uuid4(),
            document_id=facts_document_id,
            fact_key="first",
            fact_type="macro_indicator",
            origin_method="llm_extraction",
            evidence_ref={"kind": "json_pointer", "pointer": "/value"},
            subject_name="US CPI",
            symbol=None,
            metric_id="cpi_yoy",
            statement="Inflation was 2.4 percent.",
            value=2.4,
            unit="percent",
            period_end=now,
            available_at=now,
            attributes={},
            processor_version="test",
            config_fingerprint="f" * 64,
            created_at=now,
        ),
        Fact(
            id=uuid4(),
            document_id=facts_document_id,
            fact_key="second",
            fact_type="macro_indicator",
            origin_method="llm_extraction",
            evidence_ref={"kind": "json_pointer", "pointer": "/other"},
            subject_name="US CPI",
            symbol=None,
            metric_id="cpi_yoy",
            statement="Inflation was 2.7 percent.",
            value=2.7,
            unit="percent",
            period_end=now,
            available_at=now,
            attributes={},
            processor_version="test",
            config_fingerprint="f" * 64,
            created_at=now,
        ),
    ]

    projected = mark_conflicting_facts(facts)

    assert [item["value"] for item in projected] == [2.4, 2.7]
    assert all(item["conflict"] is True for item in projected)
    assert len({item["fact_id"] for item in projected}) == 2
