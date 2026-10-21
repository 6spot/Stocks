"""Facts: generic storage, Source Mapper, on-demand extraction and reads.

The pure-mapping cases run without a database. The stage cases need a real
PostgreSQL instance (``TEST_DATABASE_URL``) because idempotency, the stage fence
and the revision semantics are expressed by persisted constraints and
transactions.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.collection.config import load_sources, SourceConfig
from stocks.extraction.service import (
    EXTRACTOR_PROCESSOR_VERSION,
    FactsStageProcessor,
    classification_verdict,
    extract_document_facts,
    is_structured_response,
    map_document_facts,
    run_facts_stage,
)
from stocks.facts import (
    FactFilters,
    FactInput,
    clear_facts,
    fact_key,
    fact_to_dict,
    list_document_facts,
    persist_facts,
    search_facts,
    validate_fact,
)
from stocks.models import (
    Base,
    Document,
    DocumentProcessingState,
    Fact,
    Source,
)
from stocks.processing.processors import build_parse_processor, build_stage_registry
from stocks.processing.runner import StageRegistry, execute_claimed_stage
from stocks.processing.stages import (
    STAGE_CLASSIFY,
    STAGE_FACTS,
    STAGE_PARSE,
    StageClaim,
    claim_stage,
    get_stage_state,
    request_reprocess,
)
from stocks.providers import ChatRouting, ModelClient, ModelTarget
from stocks.repository import DocumentInput, ingest_document
from stocks.settings import Settings
from stocks.source_mapper import (
    MappingError,
    NumericField,
    SourceMapper,
    decode_json_response,
    map_response,
    parse_mapper,
    resolve_pointer,
)

ARTICLE_HTML = b"""\
<html><head><meta charset="utf-8" /></head><body>
<article>
  <h1>Nvidia data-center revenue</h1>
  <p>NVIDIA Corporation said data-center revenue reached $18.4 billion, up from
  $14.1 billion a year earlier.</p>
</article>
</body></html>
"""

CPI_JSON = json.dumps(
    {
        "series": {
            "id": "CPIAUCSL",
            "observations": [
                {"period": "2026-08", "value": 3.1, "units": "percent"},
            ],
        },
        "released_at": "2026-09-11T12:30:00+00:00",
    },
    sort_keys=True,
).encode("utf-8")

CPI_MAPPER_CONFIG = {
    "id": "cpi-series",
    "fields": [
        {
            "kind": "numeric",
            "pointer": "/series/observations/0/value",
            "fact_type": "macro_indicator",
            "metric_id": "cpi_yoy",
            "unit": "percent",
            "period_start_pointer": "/series/observations/0/period",
            "available_at_pointer": "/released_at",
        },
        {
            "kind": "text",
            "pointer": "/series/id",
            "fact_type": "series_identity",
        },
    ],
}


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "parse_chunk_size": 400,
        "parse_chunk_overlap": 40,
        "fact_extraction_max_characters": 4000,
    }
    values.update(overrides)
    return Settings(**values)


def make_route() -> object:
    from stocks.decision import DecisionRoute

    target = ModelTarget(
        base_url="https://model.example/v1",
        api_key="test-key",
        name="generation-model",
        capabilities=("chat", "structured_output"),
    )
    return DecisionRoute(
        routing=ChatRouting(
            primary="fast",
            fallbacks=(),
            required_capabilities=("chat", "structured_output"),
            timeout_seconds=10,
            max_attempts=1,
            retry_backoff_seconds=0,
        ),
        targets={"fast": target},
        protocol="openai_compatible",
    )


def chat_response(payload: dict, *, model: str = "generation-model") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        },
    )


# --------------------------------------------------------------------------- #
# Generic Fact storage and identity
# --------------------------------------------------------------------------- #


def test_fact_key_identifies_the_source_position_not_the_value():
    base = FactInput(
        fact_type="macro_indicator",
        origin_method="source_mapping",
        evidence_ref={"kind": "json_pointer", "pointer": "/value"},
        statement="CPI rose",
    )
    same = FactInput(
        fact_type="macro_indicator",
        origin_method="source_mapping",
        evidence_ref={"kind": "json_pointer", "pointer": "/value"},
        statement="CPI rose",
    )
    other_pointer = FactInput(
        fact_type="macro_indicator",
        origin_method="source_mapping",
        evidence_ref={"kind": "json_pointer", "pointer": "/other"},
        statement="CPI rose",
    )
    other_origin = FactInput(
        fact_type="macro_indicator",
        origin_method="llm_extraction",
        evidence_ref={"kind": "json_pointer", "pointer": "/value"},
        statement="CPI rose",
    )

    assert fact_key(base) == fact_key(same)
    assert fact_key(base) != fact_key(other_pointer)
    assert fact_key(base) != fact_key(other_origin)


def test_fact_without_a_unit_is_refused_instead_of_guessed():
    with pytest.raises(ValueError, match="explicit unit"):
        validate_fact(
            FactInput(
                fact_type="macro_indicator",
                origin_method="source_mapping",
                evidence_ref={"kind": "json_pointer", "pointer": "/value"},
                value=3.1,
            )
        )


def test_fact_requires_some_content_and_a_usable_evidence_ref():
    with pytest.raises(ValueError, match="statement or a numeric value"):
        validate_fact(
            FactInput(
                fact_type="note",
                origin_method="llm_extraction",
                evidence_ref={"kind": "chunk", "chunk_id": "c1"},
            )
        )
    with pytest.raises(ValueError, match="evidence_ref"):
        validate_fact(
            FactInput(
                fact_type="note",
                origin_method="llm_extraction",
                evidence_ref={},
                statement="something",
            )
        )


def test_fact_period_must_not_run_backwards():
    with pytest.raises(ValueError, match="period_start"):
        validate_fact(
            FactInput(
                fact_type="macro_indicator",
                origin_method="source_mapping",
                evidence_ref={"kind": "json_pointer", "pointer": "/value"},
                value=1.0,
                unit="percent",
                period_start=datetime(2026, 9, 30, tzinfo=UTC),
                period_end=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )


# --------------------------------------------------------------------------- #
# JSON pointer resolution and Source Mapper
# --------------------------------------------------------------------------- #


def test_pointer_resolution_distinguishes_missing_from_null():
    body = {"a": {"b": [10, 20]}, "c": None, "d/e": 1}

    assert resolve_pointer(body, "/a/b/1") == (True, 20)
    assert resolve_pointer(body, "/c") == (True, None)
    assert resolve_pointer(body, "/missing") == (False, None)
    assert resolve_pointer(body, "/a/b/9") == (False, None)
    # RFC 6901 escapes: ~1 is '/', ~0 is '~'.
    assert resolve_pointer(body, "/d~1e") == (True, 1)


def test_mapper_requires_an_explicit_unit_for_numeric_fields():
    with pytest.raises(MappingError) as exc_info:
        parse_mapper(
            {
                "id": "bad",
                "fields": [
                    {
                        "kind": "numeric",
                        "pointer": "/v",
                        "fact_type": "macro_indicator",
                    }
                ],
            }
        )
    assert exc_info.value.code == "MAPPER_INVALID"


def test_mapper_rejects_duplicate_field_paths():
    with pytest.raises(MappingError) as exc_info:
        parse_mapper(
            {
                "id": "dup",
                "fields": [
                    {"kind": "text", "pointer": "/v", "fact_type": "a"},
                    {"kind": "text", "pointer": "/v", "fact_type": "b"},
                ],
            }
        )
    assert exc_info.value.code == "MAPPER_INVALID"


class _Document:
    """The attributes a mapper reads from a stored Document version."""

    content_type = "application/json"
    content_hash = "a" * 64
    version = 3
    published_at = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)


def test_mapping_produces_facts_that_cite_their_fields():
    mapper = parse_mapper(CPI_MAPPER_CONFIG)

    facts = map_response(_Document(), CPI_JSON, mapper, created_at=datetime.now(UTC))

    numeric = [item for item in facts if item.value is not None][0]
    assert numeric.fact_type == "macro_indicator"
    assert numeric.metric_id == "cpi_yoy"
    assert numeric.value == 3.1
    assert numeric.unit == "percent"
    assert numeric.origin_method == "source_mapping"
    # The evidence is the raw field, so a reader can resolve it in the stored bytes.
    assert numeric.evidence_ref == {
        "kind": "json_pointer",
        "pointer": "/series/observations/0/value",
        "content_hash": "a" * 64,
        "document_version": 3,
    }
    # A bare period becomes a timezone-aware boundary rather than a naive instant.
    assert numeric.period_start == datetime(2026, 8, 1, tzinfo=UTC)
    assert numeric.available_at == datetime(2026, 9, 11, 12, 30, tzinfo=UTC)

    text = [item for item in facts if item.statement is not None][0]
    assert text.statement == "CPIAUCSL"
    assert text.unit is None


def test_mapping_uses_the_configured_unit_instead_of_reading_one_from_the_body():
    """The unit is a reviewed configuration claim, not something the body chooses."""

    mapper = SourceMapper(
        id="raw",
        numeric_fields=(
            NumericField(
                pointer="/value", fact_type="observed", unit="index_points"
            ),
        ),
    )
    payload = json.dumps({"value": 314.1, "units": "percent"}).encode()

    facts = map_response(_Document(), payload, mapper, created_at=datetime.now(UTC))

    assert facts[0].value == 314.1
    # The response's own "units" text did not silently become the stored unit.
    assert facts[0].unit == "index_points"


def test_mapping_rejects_a_response_with_no_configured_fields():
    mapper = parse_mapper(CPI_MAPPER_CONFIG)
    payload = json.dumps({"unrelated": True}).encode()

    with pytest.raises(MappingError) as exc_info:
        map_response(_Document(), payload, mapper, created_at=datetime.now(UTC))

    assert exc_info.value.code == "MAPPING_NO_FIELDS_MATCHED"


def test_mapping_reports_a_non_numeric_value_at_a_numeric_path():
    mapper = parse_mapper(CPI_MAPPER_CONFIG)
    payload = json.dumps(
        {"series": {"id": "x", "observations": [{"value": "3.1 percent"}]}}
    ).encode()

    with pytest.raises(MappingError) as exc_info:
        map_response(_Document(), payload, mapper, created_at=datetime.now(UTC))

    assert exc_info.value.code == "MAPPING_VALUE_NOT_NUMERIC"


def test_mapping_refuses_a_non_json_response():
    mapper = parse_mapper(CPI_MAPPER_CONFIG)
    document = _Document()
    document.content_type = "text/html"

    with pytest.raises(MappingError) as exc_info:
        map_response(document, b"<html></html>", mapper, created_at=datetime.now(UTC))

    assert exc_info.value.code == "MAPPING_UNSUPPORTED_CONTENT_TYPE"


def test_json_decoding_rejects_invalid_bytes_and_declared_charset():
    with pytest.raises(MappingError) as exc_info:
        decode_json_response(b"{not json", "application/json")
    assert exc_info.value.code == "MAPPING_INVALID_JSON"

    # A declared charset that cannot decode the bytes is refused, not guessed.
    with pytest.raises(MappingError) as exc_info:
        decode_json_response(b'{"a": "\xff"}', "application/json; charset=utf-8")
    assert exc_info.value.code == "INVALID_ENCODING"


def test_source_config_separates_api_mapper_from_feed_sources(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        """
sources:
  - source_id: sec_press_releases
    name: SEC Press Releases
    connector: rss
    publisher: SEC
    trust_tier: T1
    feed_url: https://www.sec.gov/news/pressreleases.rss
    timeout_seconds: 30
    max_response_bytes: 1000000
    max_redirects: 3
    max_items: 20
    user_agent: StocksIntelligence/0.1 (owner@example.com)
  - source_id: bls_cpi
    name: BLS CPI
    connector: http_api
    publisher: U.S. Bureau of Labor Statistics
    trust_tier: T1
    api_url: https://api.example.gov/cpi
    query: {series: CPIAUCSL}
    timeout_seconds: 30
    max_response_bytes: 1000000
    max_redirects: 3
    max_items: 20
    user_agent: StocksIntelligence/0.1 (owner@example.com)
    mapper:
      id: cpi-series
      fields:
        - kind: numeric
          pointer: /series/observations/0/value
          fact_type: macro_indicator
          metric_id: cpi_yoy
          unit: percent
""",
        encoding="utf-8",
    )

    sources = {item.source_id: item for item in load_sources(str(path))}

    assert sources["sec_press_releases"].mapper is None
    api = sources["bls_cpi"]
    assert api.connector == "http_api"
    assert api.mapper is not None
    assert api.mapper.id == "cpi-series"
    assert api.mapper.numeric_fields[0].unit == "percent"


@pytest.mark.parametrize(
    "overrides",
    [
        # An API source without a mapper cannot produce Facts.
        {"connector": "http_api", "api_url": "https://api.example.gov/x"},
        # An RSS source carrying a mapper would describe two things at once.
        {
            "connector": "rss",
            "feed_url": "https://www.sec.gov/feed.rss",
            "mapper": {"id": "x", "fields": []},
        },
        # An RSS source without a feed URL cannot discover anything.
        {"connector": "rss"},
    ],
)
def test_source_config_refuses_half_described_sources(overrides):
    base = {
        "source_id": "s1",
        "name": "S1",
        "publisher": "P",
        "trust_tier": "T1",
        "timeout_seconds": 30,
        "max_response_bytes": 1000000,
        "max_redirects": 1,
        "max_items": 5,
        "user_agent": "StocksIntelligence/0.1 (owner@example.com)",
    }
    with pytest.raises(Exception):
        SourceConfig(**{**base, **overrides})


# --------------------------------------------------------------------------- #
# Stage behaviour against a real database
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for Fact stage tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def _store(config, source_id, payload, content_type):
    """A fixed object-store stand-in; the real helper needs a live bucket."""

    return (
        f"sources/{source_id}/{hashlib.sha256(payload).hexdigest()}",
        hashlib.sha256(payload).hexdigest(),
    )


def ingest(
    engine,
    config: Settings,
    payload: bytes = ARTICLE_HTML,
    content_type: str = "text/html; charset=utf-8",
    dedupe_key: str = "release-1",
) -> Document:
    with Session(engine) as session:
        if session.get(Source, "sec") is None:
            session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
            session.commit()
        item = DocumentInput(
            source_id="sec",
            dedupe_key=dedupe_key,
            title="Release",
            payload=payload,
            content_type=content_type,
            collected_at=datetime(2026, 10, 21, 1, tzinfo=UTC),
            published_at=datetime(2026, 10, 20, tzinfo=UTC),
            completeness="complete",
        )
        return ingest_document(session, config, item, store=_store).document


def parsed(engine, config: Settings, payload: bytes = ARTICLE_HTML) -> Document:
    document = ingest(engine, config, payload)
    now = datetime(2026, 10, 21, 2, tzinfo=UTC)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="parse", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=build_parse_processor(read_bytes=lambda c, d: payload)),
            claim,
            clock=lambda: now,
        )
    return document


def classified(engine, document: Document, should_extract: bool) -> None:
    with Session(engine) as session:
        row = session.get(Document, document.id)
        row.classification = {
            "category": "company",
            "should_extract": should_extract,
            "method": "decision_model",
            "taxonomy_version": "test",
        }
        stage_rows = session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id == document.id
            )
        ).all()
        for state in stage_rows:
            if state.stage == STAGE_CLASSIFY:
                state.status = "completed"
                state.attempts = 1
        session.commit()


def test_extraction_stores_facts_citing_stored_chunks(tmp_path, engine):
    """AC-1: a text Fact can be located back in the original Chunk."""

    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=True)
    now = datetime(2026, 10, 21, 4, tzinfo=UTC)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        chunks = json.loads(payload["messages"][1]["content"])["chunks"]
        seen.extend(chunks)
        return chat_response(
            {
                "facts": [
                    {
                        "fact_type": "financial_metric",
                        "chunk_id": chunks[0]["chunk_id"],
                        "statement": "Data-center revenue reached $18.4 billion.",
                        "subject_name": "NVIDIA Corporation",
                        "symbol": "NVDA",
                        "metric_id": "data_center_revenue",
                        "value": 18.4,
                        "unit": "USD billion",
                    }
                ]
            }
        )

    route = make_route()
    processor = FactsStageProcessor(
        route=route,
        client=ModelClient(route.targets, httpx.MockTransport(handler)),
    )
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="facts", now=now)
        assert claim is not None
        execution = execute_claimed_stage(
            session, config, StageRegistry(facts=processor), claim, clock=lambda: now
        )

    assert execution.status == "completed"
    assert seen and seen[0]["chunk_id"]
    with Session(engine) as session:
        rows = list_document_facts(session, document.id)
    assert len(rows) == 1
    fact = rows[0]
    assert fact.origin_method == "llm_extraction"
    assert fact.value == 18.4 and fact.unit == "USD billion"
    assert fact.symbol == "NVDA"
    assert fact.evidence_ref["kind"] == "chunk"
    assert fact.evidence_ref["chunk_id"] == seen[0]["chunk_id"]
    assert fact.processor_version == EXTRACTOR_PROCESSOR_VERSION
    row = get_stage_state_for(engine, document.id, STAGE_FACTS)
    assert row.status == "completed"
    assert row.input_batch_id == get_stage_state_for(
        engine, document.id, STAGE_PARSE
    ).input_batch_id


def get_stage_state_for(engine, document_id, stage: str):
    with Session(engine) as session:
        return get_stage_state(session, document_id, stage)


def test_extraction_rejects_a_citation_outside_the_current_batch(tmp_path, engine):
    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=True)
    now = datetime(2026, 10, 21, 5, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            {
                "facts": [
                    {
                        "fact_type": "financial_metric",
                        "chunk_id": "00000000-0000-0000-0000-000000000000",
                        "statement": "Invented reference.",
                    }
                ]
            }
        )

    route = make_route()
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="facts", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                facts=FactsStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                )
            ),
            claim,
            clock=lambda: now,
        )

    assert execution.status == "failed"
    assert execution.error["code"] == "EXTRACTION_CITATION_INVALID"
    with Session(engine) as session:
        assert list_document_facts(session, document.id) == []


def test_number_without_a_unit_keeps_the_statement_and_drops_the_value(engine):
    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=True)
    now = datetime(2026, 10, 21, 6, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = json.loads(
            json.loads(request.content)["messages"][1]["content"]
        )["chunks"]
        return chat_response(
            {
                "facts": [
                    {
                        "fact_type": "financial_metric",
                        "chunk_id": chunks[0]["chunk_id"],
                        "statement": "Revenue grew.",
                        "value": 18.4,
                    }
                ]
            }
        )

    route = make_route()
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="facts", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                facts=FactsStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                )
            ),
            claim,
            clock=lambda: now,
        )

    with Session(engine) as session:
        rows = list_document_facts(session, document.id)
    assert len(rows) == 1
    # An unknown unit stays unknown: the incomparable number is not stored.
    assert rows[0].value is None
    assert rows[0].unit is None
    assert rows[0].statement == "Revenue grew."


def test_should_extract_false_skips_extraction_but_keeps_the_original(engine):
    """AC-3: a skip is a decision, never a deletion."""

    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=False)
    now = datetime(2026, 10, 21, 7, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no model call when the classifier said no")

    route = make_route()
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="facts", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                facts=FactsStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                )
            ),
            claim,
            clock=lambda: now,
        )

    assert execution.status == "skipped"
    assert execution.skip_reason == "NOT_APPLICABLE"
    with Session(engine) as session:
        assert list_document_facts(session, document.id) == []
        # The text is still stored, still parsed and still citable.
        assert session.get(Document, document.id) is not None
        from stocks.repository import list_parse_chunks

        assert list_parse_chunks(session, document.id)


def test_undecided_classification_waits_instead_of_dropping_facts(engine):
    """A wait consumes no attempt and clears once the verdict exists."""

    config = make_config()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 8, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not extract before the verdict exists")

    route = make_route()
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="facts", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                facts=FactsStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                )
            ),
            claim,
            clock=lambda: now,
        )

    assert execution.status == "pending"
    assert execution.wait_reason == "CLASSIFICATION_PENDING"
    assert execution.attempts == 0


def test_repeated_extraction_does_not_duplicate_facts(engine):
    """AC-2: processing the same version twice keeps one copy of each Fact."""

    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=True)
    answers = [
        {
            "facts": [
                {
                    "fact_type": "financial_metric",
                    "statement": "Revenue reached $18.4 billion.",
                    "value": 18.4,
                    "unit": "USD billion",
                }
            ]
        }
    ]
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        chars = json.loads(
            json.loads(request.content)["messages"][1]["content"]
        )["chunks"]
        facts = answers[0]["facts"]
        for fact in facts:
            fact["chunk_id"] = chars[0]["chunk_id"]
        return chat_response(answers[0])

    route = make_route()
    client = ModelClient(route.targets, httpx.MockTransport(handler))
    registry = StageRegistry(facts=FactsStageProcessor(route=route, client=client))
    now = datetime(2026, 10, 21, 9, tzinfo=UTC)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="f1", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)

    with Session(engine) as session:
        assert len(list_document_facts(session, document.id)) == 1
        # A completed stage is not claimable again: an ordinary retry reuses the
        # stored result instead of calling the model twice for the same version.
        assert claim_stage(session, STAGE_FACTS, owner_token="f2", now=now) is None

    later = now.replace(hour=10)
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_FACTS, now=later)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="f3", now=later)
        assert claim is not None
        execute_claimed_stage(session, config, registry, claim, clock=lambda: later)

    with Session(engine) as session:
        rows = list_document_facts(session, document.id)
    # An explicit reprocess really calls the model again, yet the same citation
    # still maps to one stored Fact: idempotency is a database guarantee.
    assert len(rows) == 1
    assert len(calls) == 2


def test_revision_on_a_new_version_keeps_the_old_fact(engine):
    """AC-2: a revised value does not overwrite what a report already cited."""

    config = make_config()
    original = parsed(engine, config)
    classified(engine, original, should_extract=True)
    revised_payload = ARTICLE_HTML.replace(b"18.4 billion", b"19.1 billion")
    # A changed payload becomes the next immutable Document version.
    with Session(engine) as session:
        revised_document = ingest_document(
            session,
            config,
            DocumentInput(
                source_id="sec",
                dedupe_key="release-1",
                title="Release (revised)",
                payload=revised_payload,
                content_type="text/html; charset=utf-8",
                collected_at=datetime(2026, 10, 21, 11, tzinfo=UTC),
                published_at=datetime(2026, 10, 21, 11, tzinfo=UTC),
                completeness="complete",
            ),
            store=_store,
        ).document
        revised_document_id = revised_document.id

    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    with Session(engine) as session:
        stamp = now
        persist_facts(
            session,
            original.id,
            (
                FactInput(
                    fact_type="financial_metric",
                    origin_method="source_mapping",
                    evidence_ref={"kind": "json_pointer", "pointer": "/v"},
                    value=18.4,
                    unit="USD billion",
                    statement="Revenue reached $18.4 billion.",
                ),
            ),
            processor_version="mapper-v1",
            config_fingerprint="f" * 64,
            created_at=stamp,
        )
        persist_facts(
            session,
            revised_document_id,
            (
                FactInput(
                    fact_type="financial_metric",
                    origin_method="source_mapping",
                    evidence_ref={"kind": "json_pointer", "pointer": "/v"},
                    value=19.1,
                    unit="USD billion",
                    statement="Revenue reached $19.1 billion.",
                ),
            ),
            processor_version="mapper-v1",
            config_fingerprint="f" * 64,
            created_at=stamp,
        )
        session.commit()

    with Session(engine) as session:
        old = list_document_facts(session, original.id)
        new = list_document_facts(session, revised_document_id)
    assert [fact.value for fact in old] == [18.4]
    assert [fact.value for fact in new] == [19.1]


def test_structured_response_is_mapped_without_a_model_or_vectors(engine):
    """AC-1/AC-3: an API response becomes queryable Facts without a model call."""

    config = make_config()
    document = ingest(
        engine,
        config,
        payload=CPI_JSON,
        content_type="application/json; charset=utf-8",
        dedupe_key="cpi-1",
    )
    mapper = parse_mapper(CPI_MAPPER_CONFIG)
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a mapped response must not call a model")

    with Session(engine) as session:
        summary = map_document_facts(
            session,
            config,
            document,
            mapper=mapper,
            clock=lambda: now,
            read_bytes=lambda config, item: CPI_JSON,
        )

    assert summary.status == "completed"
    # A structured response needs no parse batch and no vectors to be queryable.
    with Session(engine) as session:
        parse_state = session.scalar(
            select(Document).where(Document.id == document.id)
        )
        assert parse_state is not None
        rows = list_document_facts(session, document.id)
    assert len(rows) == 2
    numeric = [row for row in rows if row.value is not None][0]
    assert numeric.value == 3.1
    assert numeric.unit == "percent"
    assert numeric.origin_method == "source_mapping"
    assert numeric.evidence_ref["pointer"] == "/series/observations/0/value"


def test_structured_response_without_a_mapper_stays_visible(engine):
    config = make_config()
    document = ingest(
        engine,
        config,
        payload=CPI_JSON,
        content_type="application/json; charset=utf-8",
        dedupe_key="cpi-2",
    )
    now = datetime(2026, 10, 21, 14, tzinfo=UTC)
    with Session(engine) as session:
        session.get(DocumentProcessingState, (document.id, STAGE_PARSE)).status = (
            "completed"
        )
        session.commit()

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="facts", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(facts=FactsStageProcessor()),
            claim,
            clock=lambda: now,
        )

    assert execution.status == "pending"
    assert execution.wait_reason == "PROCESSOR_NOT_AVAILABLE"
    assert execution.attempts == 0


def test_fact_query_filters_by_source_symbol_and_metric(engine):
    config = make_config()
    document = ingest(
        engine,
        config,
        payload=CPI_JSON,
        content_type="application/json; charset=utf-8",
        dedupe_key="cpi-3",
    )
    now = datetime(2026, 10, 21, 15, tzinfo=UTC)
    with Session(engine) as session:
        persist_facts(
            session,
            document.id,
            (
                FactInput(
                    fact_type="macro_indicator",
                    origin_method="source_mapping",
                    evidence_ref={"kind": "json_pointer", "pointer": "/a"},
                    metric_id="cpi_yoy",
                    value=3.1,
                    unit="percent",
                ),
                FactInput(
                    fact_type="financial_metric",
                    origin_method="source_mapping",
                    evidence_ref={"kind": "json_pointer", "pointer": "/b"},
                    metric_id="dc_revenue",
                    symbol="NVDA",
                    value=18.4,
                    unit="USD billion",
                ),
            ),
            processor_version="mapper-v1",
            config_fingerprint="f" * 64,
            created_at=now,
        )
        session.commit()

    with Session(engine) as session:
        assert len(search_facts(session, FactFilters(source_ids=("sec",)))) == 2
        assert len(search_facts(session, FactFilters(symbols=("NVDA",)))) == 1
        assert len(search_facts(session, FactFilters(metric_ids=("cpi_yoy",)))) == 1
        assert (
            len(search_facts(session, FactFilters(fact_types=("financial_metric",))))
            == 1
        )
        assert search_facts(session, FactFilters(symbols=("MSFT",))) == []

    with Session(engine) as session:
        payload = [fact_to_dict(row) for row in search_facts(session)]
    assert {item["metric_id"] for item in payload} == {"cpi_yoy", "dc_revenue"}
    assert all(item["evidence_ref"]["kind"] == "json_pointer" for item in payload)


def test_explicit_reprocess_replaces_this_versions_facts(engine):
    """An Owner reprocess must be able to remove a Fact the model no longer states."""

    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=True)
    now = datetime(2026, 10, 21, 16, tzinfo=UTC)
    answers = [
        {
            "facts": [
                {
                    "fact_type": "financial_metric",
                    "statement": "First statement.",
                    "value": 1.0,
                    "unit": "unit",
                }
            ]
        },
        {"facts": []},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = json.loads(
            json.loads(request.content)["messages"][1]["content"]
        )["chunks"]
        payload = answers.pop(0)
        for fact in payload["facts"]:
            fact["chunk_id"] = chunks[0]["chunk_id"]
        return chat_response(payload)

    route = make_route()
    client = ModelClient(route.targets, httpx.MockTransport(handler))
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="f1", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(facts=FactsStageProcessor(route=route, client=client)),
            claim,
            clock=lambda: now,
        )
    with Session(engine) as session:
        assert len(list_document_facts(session, document.id)) == 1

    later = now.replace(hour=17)
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_FACTS, now=later)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_FACTS, owner_token="f2", now=later)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(facts=FactsStageProcessor(route=route, client=client)),
            claim,
            clock=lambda: later,
        )

    with Session(engine) as session:
        assert list_document_facts(session, document.id) == []


def test_stale_extraction_owner_cannot_write_facts(engine):
    config = make_config()
    document = parsed(engine, config)
    classified(engine, document, should_extract=True)
    now = datetime(2026, 10, 21, 18, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = json.loads(
            json.loads(request.content)["messages"][1]["content"]
        )["chunks"]
        return chat_response(
            {
                "facts": [
                    {
                        "fact_type": "financial_metric",
                        "chunk_id": chunks[0]["chunk_id"],
                        "statement": "Should not be stored.",
                    }
                ]
            }
        )

    route = make_route()
    stale = StageClaim(
        document_id=document.id,
        stage=STAGE_FACTS,
        owner_token="stale",
        attempts=0,
        max_attempts=3,
        reprocess_requested=False,
    )
    with Session(engine) as session:
        claim_stage(session, STAGE_FACTS, owner_token="current", now=now)

    with Session(engine) as session:
        with pytest.raises(Exception) as exc_info:
            extract_document_facts(
                session,
                config,
                session.get(Document, document.id),
                route=route,
                client=ModelClient(route.targets, httpx.MockTransport(handler)),
                claim=stale,
                clock=lambda: now,
            )
        session.rollback()

    assert getattr(exc_info.value, "code", None) == "STAGE_NOT_OWNED"
    with Session(engine) as session:
        assert list_document_facts(session, document.id) == []


def test_classification_verdict_and_structured_detection():
    class _Doc:
        content_type = "application/json; charset=utf-8"
        classification = None

    assert is_structured_response(_Doc()) is True
    assert classification_verdict(_Doc()) is None
    _Doc.classification = {"should_extract": False}
    assert classification_verdict(_Doc()) is False
    _Doc.classification = {"should_extract": "yes"}
    assert classification_verdict(_Doc()) is None
    _Doc.content_type = "text/html"
    assert is_structured_response(_Doc()) is False


def test_facts_stage_is_registered_and_generation_route_is_lazy(tmp_path):
    models = tmp_path / "models.yaml"
    models.write_text(
        """\
providers:
  default:
    protocol: openai_compatible
    base_url: https://model.example/v1
    api_key: test-key
    capabilities: [chat, structured_output]
models:
  research:
    provider: default
    name: generation-model
    capabilities: [chat, structured_output]
agents:
  fact_extraction:
    protocol: openai_compatible
    routing:
      primary: research
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 30
      retries: 1
      retry_backoff_seconds: 0
""",
        encoding="utf-8",
    )

    registry = build_stage_registry(str(models))

    assert STAGE_FACTS in registry.available_stages()
    processor = registry.for_stage(STAGE_FACTS)
    assert isinstance(processor, FactsStageProcessor)
    # Constructing the registry must not read credentials or the source config.
    assert processor.route is None


def test_clear_facts_only_touches_its_own_document(engine):
    config = make_config()
    first = ingest(engine, config, payload=CPI_JSON, content_type="application/json")
    second = ingest(
        engine,
        config,
        payload=CPI_JSON,
        content_type="application/json",
        dedupe_key="other",
    )
    now = datetime(2026, 10, 21, 19, tzinfo=UTC)
    fact = FactInput(
        fact_type="macro_indicator",
        origin_method="source_mapping",
        evidence_ref={"kind": "json_pointer", "pointer": "/v"},
        value=1.0,
        unit="percent",
    )
    with Session(engine) as session:
        for document in (first, second):
            persist_facts(
                session,
                document.id,
                (fact,),
                processor_version="v1",
                config_fingerprint="f" * 64,
                created_at=now,
            )
        session.commit()

    with Session(engine) as session:
        removed = clear_facts(session, first.id)
        session.commit()
        assert removed == 1

    with Session(engine) as session:
        assert list_document_facts(session, first.id) == []
        assert len(list_document_facts(session, second.id)) == 1


def test_fact_api_reads_by_document_and_filter(engine, monkeypatch):
    """The read-only API exposes stored Facts without ever running extraction."""

    from fastapi.testclient import TestClient

    import stocks.api as api_module

    config = make_config()
    document = ingest(engine, config, payload=CPI_JSON, content_type="application/json")
    now = datetime(2026, 10, 21, 20, tzinfo=UTC)
    with Session(engine) as session:
        persist_facts(
            session,
            document.id,
            (
                FactInput(
                    fact_type="macro_indicator",
                    origin_method="source_mapping",
                    evidence_ref={"kind": "json_pointer", "pointer": "/v"},
                    metric_id="cpi_yoy",
                    value=3.1,
                    unit="percent",
                ),
            ),
            processor_version="mapper-v1",
            config_fingerprint="f" * 64,
            created_at=now,
        )
        session.commit()

    monkeypatch.setattr(api_module, "session_factory", lambda: lambda: Session(engine))
    client = TestClient(api_module.app)

    listing = client.get("/api/facts", params={"source_id": "sec"})
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["count"] == 1
    assert payload["truncated"] is False
    assert payload["items"][0]["metric_id"] == "cpi_yoy"
    assert payload["items"][0]["evidence_ref"]["pointer"] == "/v"

    by_document = client.get(f"/api/documents/{document.id}/facts")
    assert by_document.status_code == 200
    assert by_document.json()["count"] == 1

    assert client.get(f"/api/documents/{uuid4()}/facts").status_code == 404
    assert client.get("/api/facts", params={"symbol": "MSFT"}).json()["count"] == 0
