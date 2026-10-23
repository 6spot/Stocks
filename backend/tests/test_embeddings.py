import asyncio
import os
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from stocks.embedding_space import VECTOR_DIMENSIONS
from stocks.indexing.service import index_document
from stocks.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentEmbeddingState,
    DocumentParseState,
    Source,
)
from stocks.processing.service import process_document
from stocks.providers import (
    EmbeddingError,
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    ModelTarget,
    load_embedding_targets,
    load_models,
)
from stocks.repository import (
    SearchFilters,
    get_chunk_context,
    get_embedding_coverage,
    persist_embedding_result,
)
from stocks.search.service import semantic_search
from stocks.settings import Settings
from test_processing import (
    ARTICLE_HTML,
    make_document,
)
from test_processing import (
    make_config as make_parse_config,
)


def make_targets(
    *,
    model_name: str = "text-embedding-3-small",
    dimensions: int = VECTOR_DIMENSIONS,
) -> EmbeddingTargets:
    target = ModelTarget(
        base_url="https://embeddings.example/v1",
        api_key="test-key",
        name=model_name,
        dimensions=dimensions,
    )
    return EmbeddingTargets(aliases=("embedding",), targets=(target,))


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "embedding_batch_size": 2,
    }
    values.update(overrides)
    return Settings(**values)


def keyword_vector(text: str, dimensions: int = VECTOR_DIMENSIONS) -> tuple[float, ...]:
    normalized = text.lower()
    vector = [0.0] * dimensions
    for index, keyword in enumerate(
        ("inflation", "cpi", "policy", "earnings", "employment")
    ):
        if keyword in normalized:
            vector[index] = 1.0
    if not any(vector):
        vector[-1] = 1.0
    return tuple(vector)


def stub_embed(client, targets, texts):
    return EmbeddingResponse(
        model_alias=targets.primary,
        embeddings=tuple(keyword_vector(text) for text in texts),
    )


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL embedding tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def prepare_parsed_document(session, payload: bytes = ARTICLE_HTML) -> Document:
    document = make_document(payload)
    session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
    session.commit()
    session.add(document)
    session.commit()

    process_document(
        session,
        make_parse_config(parse_chunk_size=200, parse_chunk_overlap=30),
        document,
        read_bytes=lambda config, item: payload,
        clock=lambda: datetime(2026, 10, 20, 2, tzinfo=UTC),
    )
    return document


def test_embedding_response_requires_expected_dimensions_and_indexes():
    target = ModelTarget("https://example/v1", "key", "model", 3)
    requests = []

    def mock(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 1, 0]},
                    {"index": 0, "embedding": [1, 0, 0]},
                ]
            },
        )

    response = asyncio.run(
        ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
            ["first", "second"],
            aliases=["embedding"],
        )
    )

    assert response.embeddings == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert requests[0].url.path.endswith("/embeddings")

    def wrong_dimensions(request):
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1, 2]}]},
        )

    with pytest.raises(EmbeddingError, match="dimensions"):
        asyncio.run(
            ModelClient(
                {"embedding": target},
                httpx.MockTransport(wrong_dimensions),
            ).embed(["first"], aliases=["embedding"])
        )


def test_embedding_configuration_rejects_incompatible_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_BASE_URL", "https://example/v1")
    monkeypatch.setenv("MODEL_API_KEY", "key")
    monkeypatch.setenv("MODEL_NAME", "chat")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "embedding")
    path = tmp_path / "models.yaml"
    path.write_text(
        f"""
providers:
  default:
    protocol: openai_compatible
    base_url: ${{MODEL_API_BASE_URL}}
    api_key: ${{MODEL_API_KEY}}
models:
  first:
    provider: default
    name: ${{EMBEDDING_MODEL_NAME}}
    dimensions: {VECTOR_DIMENSIONS}
  second:
    provider: default
    name: ${{MODEL_NAME}}
    dimensions: {VECTOR_DIMENSIONS}
embedding:
  models: [first, second]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="same model"):
        load_embedding_targets(str(path))


def test_embedding_configuration_rejects_schema_dimension_mismatch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MODEL_API_BASE_URL", "https://example/v1")
    monkeypatch.setenv("MODEL_API_KEY", "key")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "embedding")
    path = tmp_path / "models.yaml"
    path.write_text(
        """
providers:
  default:
    protocol: openai_compatible
    base_url: ${MODEL_API_BASE_URL}
    api_key: ${MODEL_API_KEY}
models:
  embedding:
    provider: default
    name: ${EMBEDDING_MODEL_NAME}
    dimensions: 3072
embedding:
  models: [embedding]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=f"must match {VECTOR_DIMENSIONS}"):
        load_embedding_targets(str(path))


def test_indexing_reuses_complete_vectors_and_records_failure_gap(
    engine,
):
    calls = []

    def tracked_embed(client, targets, texts):
        calls.append(list(texts))
        return stub_embed(client, targets, texts)

    with Session(engine) as session:
        document = prepare_parsed_document(session)
        first = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=tracked_embed,
            clock=lambda: datetime(2026, 10, 20, 3, tzinfo=UTC),
        )
        assert first.status == "completed"
        assert first.indexed_count == first.chunk_count
        assert first.reused is False
        assert len(calls) >= 1
        expected_call_count = len(calls)

        reused = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=tracked_embed,
        )
        assert reused.status == "completed"
        assert reused.reused is True
        assert len(calls) == expected_call_count

        def failed_embed(client, targets, texts):
            raise EmbeddingError(
                "EMBEDDING_REQUEST_FAILED",
                "All configured embedding targets failed",
            )

        failed = index_document(
            session,
            make_config(),
            document,
            make_targets(model_name="replacement-embedding"),
            object(),
            embed=failed_embed,
        )
        assert failed.status == "failed"
        assert failed.error is not None
        state = session.get(DocumentEmbeddingState, document.id)
        assert state is not None
        assert state.status == "failed"
        assert session.scalars(select(DocumentChunkEmbedding)).all()
        assert session.get(DocumentParseState, document.id) is not None


def test_indexing_failure_without_parse_chunks_leaves_document_readable(engine):
    with Session(engine) as session:
        document = make_document(ARTICLE_HTML)
        session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
        session.commit()
        session.add(document)
        session.commit()

        summary = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=stub_embed,
        )

        assert summary.status == "failed"
        assert summary.error is not None
        assert summary.error["code"] == "PARSED_CHUNKS_UNAVAILABLE"
        assert session.get(Document, document.id) is not None
        assert session.get(DocumentEmbeddingState, document.id) is None


def test_semantic_search_filters_current_indexed_chunks(engine):
    with Session(engine) as session:
        document = prepare_parsed_document(session)
        summary = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=stub_embed,
            clock=lambda: datetime(2026, 10, 20, 3, tzinfo=UTC),
        )
        assert summary.status == "completed"

        result = semantic_search(
            session,
            make_config(),
            make_targets(),
            object(),
            "inflation and CPI",
            filters=SearchFilters(
                source_ids=("sec",),
            ),
            limit=10,
            embed=stub_embed,
        )
        assert result.hits
        assert result.hits[0].similarity is not None
        assert result.hits[0].document_id == document.id
        assert result.coverage is not None
        assert result.coverage.indexed_chunks == result.coverage.current_chunks

        empty = semantic_search(
            session,
            make_config(),
            make_targets(),
            object(),
            "inflation and CPI",
            filters=SearchFilters(
                collected_from=datetime(2026, 10, 21, tzinfo=UTC),
            ),
            limit=10,
            embed=stub_embed,
        )
        assert empty.hits == ()
        coverage = get_embedding_coverage(
            session,
            model_name="text-embedding-3-small",
            dimensions=VECTOR_DIMENSIONS,
            filters=SearchFilters(
                collected_from=datetime(2026, 10, 21, tzinfo=UTC),
            ),
        )
        assert coverage.current_documents == 0


def test_semantic_search_uses_iterative_hnsw_scan(engine):
    statements = []

    class RecordingSession(Session):
        def execute(self, statement, *args, **kwargs):
            statements.append(str(statement))
            return super().execute(statement, *args, **kwargs)

    with RecordingSession(engine) as session:
        semantic_search(
            session,
            make_config(),
            make_targets(),
            object(),
            "no indexed documents",
            embed=stub_embed,
        )

    assert any(
        statement.startswith("SET LOCAL hnsw.iterative_scan = strict_order")
        for statement in statements
    )
    assert "LOAD 'vector'" in statements


def test_semantic_search_enforces_query_budget_before_embedding(engine):
    calls = []

    def tracked_embed(client, targets, texts):
        calls.append(texts)
        return stub_embed(client, targets, texts)

    with Session(engine) as session, pytest.raises(ValueError, match="character limit"):
        semantic_search(
            session,
            make_config(embedding_max_query_characters=10),
            make_targets(),
            object(),
            "query that is too long",
            embed=tracked_embed,
        )

    assert calls == []


def test_duplicate_vectors_cannot_switch_embedding_space(engine):
    with Session(engine) as session:
        document = prepare_parsed_document(session)
        result = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=stub_embed,
        )
        assert result.status == "completed"
        vectors = session.scalars(select(DocumentChunkEmbedding)).all()
        assert vectors
        assert {vector.model_name for vector in vectors} == {
            "text-embedding-3-small"
        }
        assert {vector.dimensions for vector in vectors} == {VECTOR_DIMENSIONS}

        with pytest.raises(ValueError, match="dimensions"):
            state = session.get(DocumentParseState, document.id)
            assert state is not None and state.current_batch_id is not None
            persist_embedding_result(
                session,
                document.id,
                parse_batch_id=state.current_batch_id,
                model_alias="embedding",
                model_name="other-dimensions",
                dimensions=3,
                status="completed",
                embeddings=(),
                attempted_at=datetime.now(UTC),
                error=None,
            )


def test_reparse_makes_old_batch_vectors_unsearchable_until_reindexed(engine):
    payload = ARTICLE_HTML
    with Session(engine) as session:
        document = prepare_parsed_document(session, payload)
        first = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=stub_embed,
        )
        original_hits = semantic_search(
            session,
            make_config(),
            make_targets(),
            object(),
            "inflation and CPI",
            embed=stub_embed,
        )
        assert original_hits.hits

        process_document(
            session,
            make_parse_config(parse_chunk_size=200, parse_chunk_overlap=30),
            document,
            force_reprocess=True,
            read_bytes=lambda config, item: payload,
            clock=lambda: datetime(2026, 10, 20, 4, tzinfo=UTC),
        )
        stale = semantic_search(
            session,
            make_config(),
            make_targets(),
            object(),
            "inflation and CPI",
            embed=stub_embed,
        )
        assert stale.hits == ()
        assert stale.coverage is not None
        assert stale.coverage.indexed_chunks == 0
        assert stale.coverage.unindexed_chunks > 0

        rebuilt = index_document(
            session,
            make_config(),
            document,
            make_targets(),
            object(),
            embed=stub_embed,
        )
        assert rebuilt.status == "completed"
        assert rebuilt.parse_batch_id != first.parse_batch_id
        current_hits = semantic_search(
            session,
            make_config(),
            make_targets(),
            object(),
            "inflation and CPI",
            embed=stub_embed,
        )
        assert current_hits.hits
        assert current_hits.hits[0].parse_batch_id == rebuilt.parse_batch_id

        context = get_chunk_context(
            session,
            current_hits.hits[0].chunk_id,
            radius=0,
        )
        assert [hit.chunk_id for hit in context] == [
            current_hits.hits[0].chunk_id
        ]

        with pytest.raises(ValueError, match="current successful parse"):
            get_chunk_context(
                session,
                next(
                    chunk.id
                    for chunk in session.scalars(select(DocumentChunk)).all()
                    if chunk.parse_batch_id == first.parse_batch_id
                ),
                radius=1,
            )


def test_chat_and_embedding_may_use_different_providers(tmp_path, monkeypatch):
    """A chat-only vendor can be paired with a separate embedding vendor.

    Each model names its own provider, so the two endpoints and keys stay
    independent instead of forcing one base_url and key for both.
    """

    monkeypatch.setenv("MODEL_API_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("MODEL_API_KEY", "chat-key")
    monkeypatch.setenv("MODEL_NAME", "chat-model")
    monkeypatch.setenv("EMBEDDING_API_BASE_URL", "https://embed.example/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embed-key")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "embed-model")
    path = tmp_path / "models.yaml"
    path.write_text(
        f"""
providers:
  chat:
    protocol: openai_compatible
    base_url: ${{MODEL_API_BASE_URL}}
    api_key: ${{MODEL_API_KEY}}
  embedder:
    protocol: openai_compatible
    base_url: ${{EMBEDDING_API_BASE_URL}}
    api_key: ${{EMBEDDING_API_KEY}}
models:
  research:
    provider: chat
    name: ${{MODEL_NAME}}
  embedding:
    provider: embedder
    name: ${{EMBEDDING_MODEL_NAME}}
    dimensions: {VECTOR_DIMENSIONS}
embedding:
  models: [embedding]
""",
        encoding="utf-8",
    )

    models = load_models(str(path))
    embedding = load_embedding_targets(str(path))

    assert models["research"].base_url == "https://chat.example/v1"
    assert models["research"].api_key == "chat-key"
    assert embedding.targets[0].base_url == "https://embed.example/v1"
    assert embedding.targets[0].api_key == "embed-key"
