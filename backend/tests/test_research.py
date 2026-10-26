import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentParseBatch,
    DocumentParseState,
    EvidencePackage,
    Source,
)
from stocks.providers import (
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    ModelTarget,
)
from stocks.repository import SearchHit
from stocks.research.service import select_evidence, validate_selected_versions
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
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL research tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def add_document_with_chunks(
    session: Session,
    *,
    source_id: str = "sec",
    dedupe_key: str = "doc-1",
    title: str = "Quarterly economic update",
    chunk_texts: tuple[str, ...] = ("Inflation declined to 2.4 percent.",),
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
        content_hash="a" * 64,
        snapshot_key="snapshot-key",
        trust_tier_snapshot="T1",
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


def _task_yaml(task_id: str, *, scope: str = "{topics: [macro]}") -> str:
    return f"""
tasks:
  - id: {task_id}
    name: Test
    type: research
    trigger: manual
    objective: Research inflation.
    scope: {scope}
    steps:
      - id: evidence
        select_evidence: {{kind: select_evidence, queries: [inflation], limit_per_query: 2}}
      - id: research
        research_agent: {{kind: research_agent, agent: researcher, input: evidence}}
agents:
  - id: researcher
    role: Researcher
    prompt: Research.
    model_ref: research
    input_schema:
      type: object
      additionalProperties: false
      required: [run_id, objective, strategy, scope, as_of, evidence_package_id, evidence, coverage, truncated]
      properties:
        run_id: {{type: string}}
        objective: {{type: string}}
        strategy:
          anyOf: [{{type: string}}, {{type: "null"}}]
        scope: {{type: object, additionalProperties: true}}
        as_of: {{type: string}}
        evidence_package_id: {{type: string}}
        evidence: {{type: array, items: {{type: object, additionalProperties: true}}}}
        coverage: {{type: object, additionalProperties: true}}
        truncated: {{type: boolean}}
    output_schema:
      type: object
      additionalProperties: false
      required: [title, summary, sections, findings, open_questions, evidence_refs]
      properties:
        title: {{type: string}}
        sections:
          type: array
          minItems: 1
          items:
            type: object
            additionalProperties: false
            required: [heading, body, finding_ids]
            properties:
              heading: {{type: string}}
              body: {{type: string}}
              finding_ids: {{type: array, items: {{type: string}}}}
        summary: {{type: string}}
        findings: {{type: array, items: {{type: object, additionalProperties: true}}}}
        open_questions: {{type: array, items: {{type: string}}}}
        evidence_refs: {{type: array, items: {{type: object, additionalProperties: true}}}}
"""


def fake_embed(client, targets, texts):
    return EmbeddingResponse(
        model_alias=targets.primary,
        embeddings=((0.0,) * VECTOR_DIMENSIONS,),
    )


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


def test_select_evidence_persists_current_version_and_unknown_counts(
    engine, monkeypatch
):
    with Session(engine) as session:
        document, chunks = add_document_with_chunks(session)
        from stocks.execution.service import create_or_get_run
        from stocks.execution.config import load_task_configuration

        config_path = "/tmp/test-research-config.yaml"
        with open(config_path, "w", encoding="utf-8") as stream:
            stream.write(
                """
tasks:
  - id: test
    name: Test
    type: research
    trigger: manual
    objective: Research inflation.
    scope: {topics: [macro], symbols: [NVDA]}
    steps:
      - id: evidence
        select_evidence: {kind: select_evidence, queries: [NVDA inflation], limit_per_query: 1}
      - id: research
        research_agent: {kind: research_agent, agent: researcher, input: evidence}
agents:
  - id: researcher
    role: Researcher
    prompt: Research.
    model_ref: research
    input_schema:
      type: object
      additionalProperties: false
      required: [run_id, objective, strategy, scope, as_of, evidence_package_id, evidence, coverage, truncated]
      properties:
        run_id: {type: string}
        objective: {type: string}
        strategy: {type: "null"}
        scope: {type: object, additionalProperties: true}
        as_of: {type: string}
        evidence_package_id: {type: string}
        evidence: {type: array, items: {type: object, additionalProperties: true}}
        coverage: {type: object, additionalProperties: true}
        truncated: {type: boolean}
    output_schema:
      type: object
      additionalProperties: false
      required: [title, summary, sections, findings, open_questions, evidence_refs]
      properties:
        title: {type: string}
        sections:
          type: array
          minItems: 1
          items:
            type: object
            additionalProperties: false
            required: [heading, body, finding_ids]
            properties:
              heading: {type: string}
              body: {type: string}
              finding_ids: {type: array, items: {type: string}}
        summary: {type: string}
        findings: {type: array, items: {type: object, additionalProperties: true}}
        open_questions: {type: array, items: {type: string}}
        evidence_refs: {type: array, items: {type: object, additionalProperties: true}}
"""
            )
        loaded = load_task_configuration(config_path)
        claimed = create_or_get_run(
            session,
            loaded.tasks[0],
            list(loaded.agents),
        )
        hit = SearchHit(
            chunk_id=chunks[0].id,
            document_id=document.id,
            parse_batch_id=chunks[0].parse_batch_id,
            position=0,
            text=chunks[0].text,
            locator=chunks[0].locator,
            title=document.title,
            source_id=document.source_id,
            version=document.version,
            published_at=document.published_at,
            source_updated_at=None,
            collected_at=document.collected_at,
            completeness=document.completeness,
            similarity=0.9,
        )

        import stocks.research.service as research_service

        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: type(
                "Result",
                (),
                {
                    "hits": (hit,),
                    "coverage": None,
                    "truncated": False,
                },
            )(),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=claimed.run.id,
            objective=claimed.run.objective,
            scope=claimed.run.scope,
            as_of=claimed.run.created_at,
            queries=("NVDA inflation",),
            limit_per_query=1,
            max_tokens=1000,
            embed=fake_embed,
        )
        assert result.package.candidates_discovered == 1
        assert result.package.candidates_screened == 1
        assert result.package.unexamined_count == 0
        assert result.package.retrieval_truncated is False
        assert result.package.selected_count == 1
        assert result.package.coverage["candidates_discovered"] == 1
        assert result.package.coverage["unexamined_count"] == 0
        assert result.package.selected_chunks[0]["document_version"] == 1
        assert result.package.selected_chunks[0]["role"] == "direct"
        assert "coverage_unavailable" in result.package.coverage["gaps"]
        assert result.refs[0]["chunk_id"] == str(chunks[0].id)
        allowed = validate_selected_versions(
            session,
            result.package.selected_chunks,
        )
        assert (str(chunks[0].id), str(document.id)) in allowed


def test_select_evidence_truncates_budget_without_zero_count(engine, monkeypatch):
    with Session(engine) as session:
        from stocks.execution.config import load_task_configuration
        from stocks.execution.service import create_or_get_run

        config_path = "/tmp/test-research-budget.yaml"
        with open(config_path, "w", encoding="utf-8") as stream:
            stream.write(
                """
tasks:
  - id: test-budget
    name: Test
    type: research
    trigger: manual
    objective: Research inflation.
    scope: {topics: [macro]}
    steps:
      - id: evidence
        select_evidence: {kind: select_evidence, queries: [inflation], limit_per_query: 2}
      - id: research
        research_agent: {kind: research_agent, agent: researcher, input: evidence}
agents:
  - id: researcher
    role: Researcher
    prompt: Research.
    model_ref: research
    input_schema:
      type: object
      additionalProperties: false
      required: [run_id, objective, strategy, scope, as_of, evidence_package_id, evidence, coverage, truncated]
      properties:
        run_id: {type: string}
        objective: {type: string}
        strategy:
          anyOf: [{type: string}, {type: "null"}]
        scope: {type: object, additionalProperties: true}
        as_of: {type: string}
        evidence_package_id: {type: string}
        evidence: {type: array, items: {type: object, additionalProperties: true}}
        coverage: {type: object, additionalProperties: true}
        truncated: {type: boolean}
    output_schema:
      type: object
      additionalProperties: false
      required: [title, summary, sections, findings, open_questions, evidence_refs]
      properties:
        title: {type: string}
        sections:
          type: array
          minItems: 1
          items:
            type: object
            additionalProperties: false
            required: [heading, body, finding_ids]
            properties:
              heading: {type: string}
              body: {type: string}
              finding_ids: {type: array, items: {type: string}}
        summary: {type: string}
        findings: {type: array, items: {type: object, additionalProperties: true}}
        open_questions: {type: array, items: {type: string}}
        evidence_refs: {type: array, items: {type: object, additionalProperties: true}}
"""
            )
        loaded = load_task_configuration(config_path)
        claimed = create_or_get_run(session, loaded.tasks[0], list(loaded.agents))
        document, chunks = add_document_with_chunks(
            session,
            chunk_texts=("a" * 400, "b" * 400),
        )

        hits = tuple(
            SearchHit(
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
                source_updated_at=None,
                collected_at=document.collected_at,
                completeness=document.completeness,
                similarity=0.5,
            )
            for chunk in chunks
        )
        import stocks.research.service as research_service

        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: type(
                "Result",
                (),
                {"hits": hits, "coverage": None, "truncated": False},
            )(),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=claimed.run.id,
            objective=claimed.run.objective,
            scope=claimed.run.scope,
            as_of=claimed.run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            # Each 400-character Chunk estimates to 100 tokens: the first fits,
            # the second is screened but exceeds the 150-token budget.
            max_tokens=150,
            embed=fake_embed,
        )
        assert result.package.selected_count == 1
        assert result.package.retrieval_truncated is True
        assert "token_budget" in result.package.queries[0]["gap_reasons"]
        # Both returned chunks were examined, so nothing seen remains unscanned.
        assert result.package.candidates_discovered == 2
        assert result.package.candidates_screened == 2
        assert result.package.unexamined_count == 0
        assert result.package.coverage["candidates_discovered"] == 2
        assert result.package.coverage["unexamined_count"] == 0


def test_select_evidence_keeps_retrieval_truncation_and_gaps_visible(engine, monkeypatch):
    with Session(engine) as session:
        from stocks.execution.config import load_task_configuration
        from stocks.execution.service import create_or_get_run

        config_path = "/tmp/test-research-truncated.yaml"
        Path(config_path).write_text(
            _task_yaml("test-truncated"),
            encoding="utf-8",
        )
        loaded = load_task_configuration(config_path)
        claimed = create_or_get_run(session, loaded.tasks[0], list(loaded.agents))
        document, chunks = add_document_with_chunks(
            session,
            chunk_texts=("first chunk", "second chunk"),
        )
        hits = tuple(
            SearchHit(
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
                source_updated_at=None,
                collected_at=document.collected_at,
                completeness=document.completeness,
                similarity=0.5,
            )
            for chunk in chunks
        )
        import stocks.research.service as research_service

        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: type(
                "Result",
                (),
                {"hits": hits, "coverage": None, "truncated": True},
            )(),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=claimed.run.id,
            objective=claimed.run.objective,
            scope=claimed.run.scope,
            as_of=claimed.run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            max_tokens=1000,
            embed=fake_embed,
        )
        assert result.package.retrieval_truncated is True
        assert result.package.candidates_discovered is None
        assert result.package.unexamined_count is None
        assert result.package.candidates_screened == 2
        assert "retrieval_truncated" in result.package.coverage["gaps"]


def test_select_evidence_derives_direct_and_background_queries(engine, monkeypatch):
    with Session(engine) as session:
        from stocks.execution.config import load_task_configuration
        from stocks.execution.service import create_or_get_run

        config_path = "/tmp/test-research-derived.yaml"
        Path(config_path).write_text(
            _task_yaml("test-derived", scope="{companies: [NVIDIA], topics: [AI]}"),
            encoding="utf-8",
        )
        loaded = load_task_configuration(config_path)
        claimed = create_or_get_run(session, loaded.tasks[0], list(loaded.agents))
        document, chunks = add_document_with_chunks(session)
        hit = SearchHit(
            chunk_id=chunks[0].id,
            document_id=document.id,
            parse_batch_id=chunks[0].parse_batch_id,
            position=0,
            text=chunks[0].text,
            locator=chunks[0].locator,
            title=document.title,
            source_id=document.source_id,
            version=document.version,
            published_at=document.published_at,
            source_updated_at=None,
            collected_at=document.collected_at,
            completeness=document.completeness,
            similarity=0.9,
        )
        seen_queries: list[str] = []
        import stocks.research.service as research_service

        def fake_search(*args, **kwargs):
            seen_queries.append(args[4])
            return type("Result", (), {"hits": (hit,), "coverage": None, "truncated": False})()

        monkeypatch.setattr(research_service, "semantic_search", fake_search)
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=claimed.run.id,
            objective=claimed.run.objective,
            scope=claimed.run.scope,
            as_of=claimed.run.created_at,
            queries=(),
            limit_per_query=1,
            max_tokens=1000,
            embed=fake_embed,
        )
        assert len(seen_queries) == 2
        roles = [item["role"] for item in result.package.queries]
        assert roles == ["direct", "background"]
        assert any("NVIDIA" in item["query"] for item in result.package.queries)
        assert any("AI" in item["query"] for item in result.package.queries)
        # Duplicate hits across the two queries enter the package once.
        assert result.package.selected_count == 1
        assert result.package.candidates_discovered == 1


def test_validate_selected_versions_rejects_stale_document_version(engine):
    with Session(engine) as session:
        document, chunks = add_document_with_chunks(session)
        item = {
            "chunk_id": str(chunks[0].id),
            "document_id": str(document.id),
            "parse_batch_id": str(chunks[0].parse_batch_id),
            "document_version": 2,
        }
        with pytest.raises(ValueError, match="saved Document"):
            validate_selected_versions(session, [item])


def test_validate_selected_versions_accepts_a_superseded_completed_batch(engine):
    """A frozen reference stays readable after the Document is reparsed."""

    with Session(engine) as session:
        document, chunks = add_document_with_chunks(session)
        state = session.get(DocumentParseState, document.id)
        assert state is not None
        newer_batch = DocumentParseBatch(
            document_id=document.id,
            processor_version="test",
            config_fingerprint="f" * 64,
            content_hash=document.content_hash,
            attempt_number=2,
            status="completed",
            chunk_count=1,
            created_at=datetime.now(UTC),
        )
        session.add(newer_batch)
        session.flush()
        state.current_batch_id = newer_batch.id
        state.latest_batch_id = newer_batch.id
        session.commit()
        item = {
            "chunk_id": str(chunks[0].id),
            "document_id": str(document.id),
            "parse_batch_id": str(chunks[0].parse_batch_id),
            "document_version": document.version,
        }

        allowed = validate_selected_versions(session, [item])

        assert allowed[(str(chunks[0].id), str(document.id))]["text"] == (
            chunks[0].text
        )


def test_validate_selected_versions_rejects_an_unfinished_parse_batch(engine):
    """Only a batch that actually produced Chunks can back a citation."""

    with Session(engine) as session:
        document, chunks = add_document_with_chunks(session)
        batch = session.get(DocumentParseBatch, chunks[0].parse_batch_id)
        assert batch is not None
        batch.status = "failed"
        session.commit()
        item = {
            "chunk_id": str(chunks[0].id),
            "document_id": str(document.id),
            "parse_batch_id": str(chunks[0].parse_batch_id),
            "document_version": document.version,
        }
        with pytest.raises(ValueError, match="saved Document"):
            validate_selected_versions(session, [item])


def test_validate_selected_versions_rejects_a_batch_from_another_document(engine):
    """A Chunk and its batch must belong to the cited Document."""

    with Session(engine) as session:
        document, chunks = add_document_with_chunks(session)
        other, other_chunks = add_document_with_chunks(
            session, dedupe_key="doc-2", chunk_texts=("Other evidence.",)
        )
        item = {
            "chunk_id": str(other_chunks[0].id),
            "document_id": str(document.id),
            "parse_batch_id": str(chunks[0].parse_batch_id),
            "document_version": document.version,
        }
        with pytest.raises(ValueError, match="saved Document"):
            validate_selected_versions(session, [item])
        assert other.id != document.id


def test_select_evidence_persists_empty_package_without_inventing_counts(
    engine, monkeypatch
):
    with Session(engine) as session:
        from stocks.execution.config import load_task_configuration
        from stocks.execution.service import create_or_get_run

        config_path = "/tmp/test-research-empty.yaml"
        Path(config_path).write_text(_task_yaml("test-empty"), encoding="utf-8")
        loaded = load_task_configuration(config_path)
        claimed = create_or_get_run(session, loaded.tasks[0], list(loaded.agents))
        import stocks.research.service as research_service

        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: type(
                "Result",
                (),
                {"hits": (), "coverage": None, "truncated": False},
            )(),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=claimed.run.id,
            objective=claimed.run.objective,
            scope=claimed.run.scope,
            as_of=claimed.run.created_at,
            queries=("nothing stored yet",),
            limit_per_query=5,
            max_tokens=1000,
            embed=fake_embed,
        )
        assert result.package.selected_count == 0
        assert result.package.selected_chunks == []
        assert result.package.candidates_discovered == 0
        assert result.package.candidates_screened == 0
        assert result.package.unexamined_count == 0
        assert "no_selected_evidence" in result.package.coverage["gaps"]
        assert "no_direct_matches" in result.package.coverage["gaps"]
        persisted = session.get(EvidencePackage, result.package.id)
        assert persisted is not None

