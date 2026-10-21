"""Decision Model classification: vocabulary, adapter and the classify stage.

The vocabulary and adapter cases are pure unit tests. The stage cases need a real
PostgreSQL instance (``TEST_DATABASE_URL``) because the current classification is
written under the Document row lock in the same transaction as the stage outcome,
and that is the behaviour under test.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.classification.service import (
    CLASSIFIER_PROCESSOR_VERSION,
    ClassifyStageProcessor,
    classify_config_fingerprint,
    classify_document_stage,
    load_classifier_route,
)
from stocks.decision import (
    ClassificationInput,
    DecisionError,
    DecisionRoute,
    build_classification_input,
    classify_document,
    load_decision_route,
)
from stocks.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentProcessingState,
    Source,
)
from stocks.processing.processors import build_parse_processor, build_stage_registry
from stocks.processing.runner import StageRegistry, execute_claimed_stage
from stocks.processing.stages import (
    SKIP_NOT_APPLICABLE,
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
from stocks.taxonomy import (
    ClassificationInvalid,
    load_taxonomy,
    parse_taxonomy,
)

ARTICLE_HTML = b"""\
<html><head><meta charset="utf-8" /></head><body>
<article>
  <h1>Fed holds rates as inflation cools</h1>
  <p>The Federal Reserve kept its policy rate unchanged. Core inflation was 3.1%
  and officials signalled one further cut this year.</p>
</article>
</body></html>
"""

COMPANY_HTML = b"""\
<html><head><meta charset="utf-8" /></head><body>
<article>
  <h1>NVIDIA reports record data-center revenue</h1>
  <p>NVIDIA Corporation said data-center revenue reached $18.4 billion.</p>
</article>
</body></html>
"""

TAXONOMY_YAML = """\
version: "test-1"
instructions: "Label the document."
categories:
  - id: macro
  - id: policy
  - id: industry
  - id: company
  - id: other
event_types:
  - id: monetary_policy
  - id: earnings
topics:
  - id: interest_rates
  - id: inflation
  - id: liquidity
industries:
  - id: banking
  - id: semiconductors
regions:
  - id: US
  - id: EU
"""

MODELS_YAML = """\
providers:
  default:
    protocol: openai_compatible
    base_url: https://model.example/v1
    api_key: test-key
    capabilities: [chat, structured_output]
models:
  fast:
    provider: default
    name: decision-model
    capabilities: [chat, structured_output]
  backup:
    provider: default
    name: decision-model-backup
    capabilities: [chat, structured_output]
agents:
  classification:
    protocol: openai_compatible
    routing:
      primary: fast
      fallbacks: [backup]
      required_capabilities: [chat, structured_output]
      timeout_seconds: 10
      retries: 1
      retry_backoff_seconds: 0
"""


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "parse_chunk_size": 400,
        "parse_chunk_overlap": 40,
        "classification_max_characters": 1200,
    }
    values.update(overrides)
    return Settings(**values)


def write_taxonomy(tmp_path) -> str:
    path = tmp_path / "taxonomy.yaml"
    path.write_text(TAXONOMY_YAML, encoding="utf-8")
    return str(path)


def write_models(tmp_path, text: str = MODELS_YAML) -> str:
    path = tmp_path / "models.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def decision_payload(**overrides) -> dict:
    payload = {
        "category": "macro",
        "event_type": "monetary_policy",
        "topics": ["interest_rates", "inflation"],
        "related_industries": ["banking"],
        "regions": ["US"],
        "companies": [],
        "symbols": [],
        "should_extract": True,
        "reason": "The document reports a policy decision and an inflation figure.",
    }
    payload.update(overrides)
    return payload


def chat_response(payload: dict, *, model: str = "decision-model") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
            },
        },
    )


def make_route() -> DecisionRoute:
    target = ModelTarget(
        base_url="https://model.example/v1",
        api_key="test-key",
        name="decision-model",
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


def make_input(text: str = "The Fed held rates. Core inflation was 3.1%.") -> ClassificationInput:
    return ClassificationInput(
        title="Fed holds rates",
        source_id="sec",
        content_type="text/html",
        published_at=None,
        collected_at="2026-10-21T00:00:00+00:00",
        chunk_ids=("c1",),
        text=text,
        truncated=False,
    )


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_taxonomy_schema_is_derived_from_the_controlled_vocabulary(tmp_path):
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    schema = taxonomy.schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert properties["category"]["enum"] == list(taxonomy.category_ids)
    assert properties["topics"]["items"]["enum"] == list(taxonomy.topic_ids)
    assert properties["related_industries"]["items"]["enum"] == list(
        taxonomy.industry_ids
    )
    # Companies and symbols are free text on purpose: the document names them, the
    # vocabulary cannot enumerate every issuer, and guessing a ticker is forbidden
    # by the prompt rather than by an enumeration.
    assert properties["companies"]["items"]["type"] == "string"
    assert properties["should_extract"]["type"] == "boolean"
    assert "event_type" in schema["required"] or "event_type" in properties


def test_taxonomy_normalizes_a_valid_decision():
    taxonomy = parse_taxonomy(
        {
            "version": "v1",
            "instructions": "Label it.",
            "categories": [{"id": "macro"}],
            "topics": [{"id": "inflation"}],
            "industries": [{"id": "banking"}],
            "regions": [{"id": "US"}],
        }
    )

    normalized = taxonomy.normalize(
        {
            "category": "macro",
            "event_type": None,
            "topics": ["inflation"],
            "related_industries": ["banking"],
            "regions": ["US"],
            "companies": [" Federal Reserve "],
            "symbols": [],
            "should_extract": False,
            "reason": " Macro backdrop only. ",
        }
    )

    assert normalized["category"] == "macro"
    assert normalized["event_type"] is None
    assert normalized["companies"] == ["Federal Reserve"]
    assert normalized["should_extract"] is False
    assert normalized["reason"] == "Macro backdrop only."


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (decision_payload(category="equities"), "CLASSIFICATION_OUT_OF_VOCABULARY"),
        (decision_payload(topics=["sports"]), "CLASSIFICATION_OUT_OF_VOCABULARY"),
        (decision_payload(related_industries=["nvda"]), "CLASSIFICATION_OUT_OF_VOCABULARY"),
        (decision_payload(event_type="merger"), "CLASSIFICATION_OUT_OF_VOCABULARY"),
        (decision_payload(regions=["Mars"]), "CLASSIFICATION_OUT_OF_VOCABULARY"),
        (decision_payload(should_extract="yes"), "CLASSIFICATION_INVALID"),
        (decision_payload(topics=["inflation", "inflation"]), "CLASSIFICATION_INVALID"),
        (decision_payload(symbols=["nvda"]), "CLASSIFICATION_INVALID"),
        (decision_payload(reason="   "), "CLASSIFICATION_INVALID"),
        ({**decision_payload(), "sentiment": "bullish"}, "CLASSIFICATION_INVALID"),
    ],
)
def test_taxonomy_rejects_anything_outside_the_controlled_contract(tmp_path, payload, code):
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))

    with pytest.raises(ClassificationInvalid) as exc_info:
        taxonomy.normalize(payload)

    assert exc_info.value.code == code


def test_taxonomy_rejects_a_duplicate_or_malformed_label_id():
    for section in (
        {"categories": [{"id": "macro"}, {"id": "macro"}]},
        {"categories": [{"id": "not a label"}]},
        {"categories": []},
    ):
        config = {
            "version": "v1",
            "instructions": "Label it.",
            **section,
        }
        with pytest.raises(ValueError):
            parse_taxonomy(config)


# --------------------------------------------------------------------------- #
# Decision adapter
# --------------------------------------------------------------------------- #


def test_one_call_returns_tags_and_the_extraction_decision(tmp_path):
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return chat_response(decision_payload())

    decision = classify_document(
        route,
        ModelClient(route.targets, httpx.MockTransport(handler)),
        taxonomy,
        make_input(),
    )

    assert len(requests) == 1
    assert decision.result["category"] == "macro"
    assert decision.result["should_extract"] is True
    assert decision.model_alias == "fast"
    assert decision.usage["total_tokens"] == 160
    sent = requests[0]
    assert sent["response_format"]["json_schema"]["strict"] is True
    # The bounded excerpt, not a fabricated full text, is what the model reads.
    assert sent["messages"][0]["role"] == "system"
    assert "untrusted data" in sent["messages"][0]["content"]
    document = json.loads(sent["messages"][1]["content"])
    assert document["excerpt"] == "The Fed held rates. Core inflation was 3.1%."


def test_out_of_vocabulary_answer_is_retried_then_fails_instead_of_false(tmp_path):
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return chat_response(decision_payload(category="equities"))

    with pytest.raises(DecisionError) as exc_info:
        classify_document(
            route,
            ModelClient(route.targets, httpx.MockTransport(handler)),
            taxonomy,
            make_input(),
        )

    # One business classification, but the invalid answer consumed the configured
    # retry budget and then failed visibly.
    assert len(attempts) == 1
    # A vocabulary violation is refused by the shared structured-output check,
    # which carries the taxonomy enums; it surfaces as one stable rejected-answer
    # code, never as a false extraction decision.
    assert exc_info.value.code == "CLASSIFICATION_REJECTED"
    assert exc_info.value.detail["reason"] == "schema_or_vocabulary_violation"
    assert exc_info.value.detail["taxonomy_version"] == taxonomy.version
    assert exc_info.value.detail["attempts"][0]["status"] == "failed"


def test_provider_failure_is_a_diagnosable_error_not_a_false_decision(tmp_path):
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(DecisionError) as exc_info:
        classify_document(
            route,
            ModelClient(route.targets, httpx.MockTransport(handler)),
            taxonomy,
            make_input(),
        )

    assert exc_info.value.code == "MODEL_AUTHENTICATION_FAILED"


def test_unimplemented_decision_protocol_is_refused(tmp_path):
    models = write_models(
        tmp_path,
        MODELS_YAML.replace(
            "    protocol: openai_compatible\n    routing:\n      primary: fast",
            "    protocol: proprietary_decision_api\n    routing:\n      primary: fast",
        ),
    )

    with pytest.raises(ValueError, match="protocol is not implemented"):
        load_decision_route(models)


def test_classification_input_respects_the_character_budget():
    class _Chunk:
        def __init__(self, chunk_id: str, text: str) -> None:
            self.id = chunk_id
            self.text = text

    document = type(
        "Document",
        (),
        {
            "title": "Title",
            "source_id": "sec",
            "content_type": "text/html",
            "published_at": None,
            "collected_at": datetime(2026, 10, 21, tzinfo=UTC),
        },
    )()
    chunks = [_Chunk("c1", "a" * 60), _Chunk("c2", "b" * 60), _Chunk("c3", "c" * 60)]

    selected = build_classification_input(document, chunks, max_characters=100)

    # The separator between chunks counts against the budget, so the model never
    # receives more characters than configured.
    assert len(selected.text) == 100
    assert selected.truncated is True
    assert selected.chunk_ids == ("c1", "c2")
    # Only the excerpt the model actually saw is recorded as its input.
    assert selected.text.startswith("a" * 60)
    assert selected.text.endswith("b" * 38)


# --------------------------------------------------------------------------- #
# Stage integration
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for classification stage tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def ingest(engine, config: Settings, payload: bytes = ARTICLE_HTML) -> Document:
    with Session(engine) as session:
        if session.get(Source, "sec") is None:
            session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
            session.commit()
        item = DocumentInput(
            source_id="sec",
            dedupe_key=f"release-{hashlib.sha256(payload).hexdigest()[:8]}",
            title="SEC release",
            payload=payload,
            content_type="text/html; charset=utf-8",
            collected_at=datetime(2026, 10, 21, 1, tzinfo=UTC),
            published_at=datetime(2026, 10, 20, tzinfo=UTC),
            completeness="complete",
        )
        return ingest_document(
            session,
            config,
            item,
            store=lambda config, source_id, data, content_type: (
                f"sources/{source_id}/{item.dedupe_key}",
                hashlib.sha256(data).hexdigest(),
            ),
        ).document


def parsed(engine, config: Settings, payload: bytes = ARTICLE_HTML) -> Document:
    """Ingest one Document version and complete its parse stage.

    Classification is only claimable after a successful parse, so the helper goes
    through the real parse stage instead of calling the parser directly: otherwise
    the ``parse`` row would stay ``pending`` and the classification claim would be
    refused for the wrong reason.
    """

    document = ingest(engine, config, payload)
    now = datetime(2026, 10, 21, 2, tzinfo=UTC)
    registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: payload)
    )
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="parse", now=now)
        assert claim is not None
        execution = execute_claimed_stage(
            session, config, registry, claim, clock=lambda: now
        )
    assert execution.status == "completed"
    return document


def stage_row(engine, document_id, stage: str) -> DocumentProcessingState:
    with Session(engine) as session:
        return get_stage_state(session, document_id, stage)


def stored_classification(engine, document_id) -> dict | None:
    with Session(engine) as session:
        return session.get(Document, document_id).classification


def test_macro_document_classifies_with_empty_company_tags(tmp_path, engine):
    """AC-1: a macro document succeeds and never claims a stock was mentioned."""

    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 4, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            decision_payload(
                topics=["interest_rates", "inflation"],
                related_industries=["banking"],
                companies=[],
                symbols=[],
            )
        )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls", now=now)
        assert claim is not None
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                classify=ClassifyStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                    taxonomy=taxonomy,
                )
            ),
            claim,
            clock=lambda: now,
        )

    assert execution.status == "completed"
    stored = stored_classification(engine, document.id)
    assert stored["category"] == "macro"
    assert stored["topics"] == ["interest_rates", "inflation"]
    # Industry relevance is recorded as background, never as a direct mention.
    assert stored["related_industries"] == ["banking"]
    assert stored["companies"] == []
    assert stored["symbols"] == []
    assert stored["should_extract"] is True
    assert stored["method"] == "decision_model"
    assert stored["model_ref"] == "classification"
    assert stored["taxonomy_version"] == taxonomy.version
    assert stored["reason_or_evidence_ref"]["chunk_ids"]
    row = stage_row(engine, document.id, STAGE_CLASSIFY)
    assert row.status == "completed"
    assert row.processor_version == CLASSIFIER_PROCESSOR_VERSION
    assert row.applicability == {"requires_extraction": True}
    # The original Document version is untouched: classification is an attribute.
    with Session(engine) as session:
        assert len(session.scalars(select(Document)).all()) == 1


def test_should_extract_false_skips_facts_without_deleting_the_document(tmp_path, engine):
    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 5, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(decision_payload(should_extract=False))

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                classify=ClassifyStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                    taxonomy=taxonomy,
                )
            ),
            claim,
            clock=lambda: now,
        )

    assert stage_row(engine, document.id, STAGE_CLASSIFY).status == "completed"
    facts = stage_row(engine, document.id, STAGE_FACTS)
    assert facts.status == "skipped"
    assert facts.skip_reason == SKIP_NOT_APPLICABLE
    # A "no facts needed" decision is not a retention decision.
    with Session(engine) as session:
        stored = session.get(Document, document.id)
        assert stored is not None
        assert stored.classification["should_extract"] is False
        assert session.scalars(select(DocumentChunk)).all()


def test_failed_classification_keeps_the_previous_result_and_the_original(tmp_path, engine):
    """AC-2/AC-3 boundary: a failure is diagnosable and never erases evidence."""

    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 6, tzinfo=UTC)

    def good(request: httpx.Request) -> httpx.Response:
        return chat_response(decision_payload())

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                classify=ClassifyStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(good)),
                    taxonomy=taxonomy,
                )
            ),
            claim,
            clock=lambda: now,
        )
    first = stored_classification(engine, document.id)
    assert first["category"] == "macro"

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    later = now.replace(hour=7)
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_CLASSIFY, now=later)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls2", now=later)
        assert claim is not None
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                classify=ClassifyStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(broken)),
                    taxonomy=taxonomy,
                )
            ),
            claim,
            clock=lambda: later,
        )

    assert execution.status == "failed"
    row = stage_row(engine, document.id, STAGE_CLASSIFY)
    assert row.status == "failed"
    assert row.error["code"] == "MODEL_SERVICE_UNAVAILABLE"
    # The previously usable classification is not silently replaced by a failure.
    assert stored_classification(engine, document.id) == first
    with Session(engine) as session:
        assert session.get(Document, document.id) is not None
        assert session.scalars(select(DocumentChunk)).all()


def test_reclassification_updates_the_current_jsonb_without_a_new_document(
    tmp_path, engine
):
    """AC-3: an explicit reclassification rewrites the current result in place."""

    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 8, tzinfo=UTC)

    answers = [
        decision_payload(category="macro", topics=["interest_rates"]),
        decision_payload(category="policy", topics=["liquidity"], should_extract=False),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(answers.pop(0))

    client = ModelClient(route.targets, httpx.MockTransport(handler))
    processor = ClassifyStageProcessor(route=route, client=client, taxonomy=taxonomy)
    registry = StageRegistry(classify=processor)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="c1", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)
    first = stored_classification(engine, document.id)

    later = now.replace(hour=9)
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_CLASSIFY, now=later)
        # Mark facts completed to prove a reclassification does not reopen them.
        facts = session.get(DocumentProcessingState, (document.id, STAGE_FACTS))
        facts.status = "completed"
        facts.attempts = 1
        session.commit()
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="c2", now=later)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: later)

    second = stored_classification(engine, document.id)
    assert second["category"] == "policy"
    assert second["topics"] == ["liquidity"]
    assert second["classified_at"] != first["classified_at"]
    with Session(engine) as session:
        # One Document version, no classification history row anywhere.
        assert len(session.scalars(select(Document)).all()) == 1
        assert (
            session.scalar(
                select(DocumentProcessingState).where(
                    DocumentProcessingState.document_id == document.id,
                    DocumentProcessingState.stage == STAGE_FACTS,
                )
            ).status
            == "completed"
        )
        assert (
            session.scalar(
                select(DocumentProcessingState).where(
                    DocumentProcessingState.document_id == document.id,
                    DocumentProcessingState.stage == STAGE_CLASSIFY,
                )
            ).status
            == "completed"
        )


def test_classification_without_a_parse_batch_fails_without_a_decision(tmp_path, engine):
    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no Decision Model call without parsed text")

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls", now=now)
        assert claim is None  # its parse dependency has not completed

    with Session(engine) as session:
        document_row = session.get(Document, document.id)
        summary = classify_document_stage(
            session,
            config,
            document_row,
            route=route,
            client=ModelClient(route.targets, httpx.MockTransport(handler)),
            taxonomy=taxonomy,
            claim=None,
            clock=lambda: now,
        )

    assert summary.status == "failed"
    assert summary.error["code"] == "PARSED_CHUNKS_UNAVAILABLE"
    assert stored_classification(engine, document.id) is None


def test_company_mention_is_recorded_with_its_symbol(tmp_path, engine):
    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config, COMPANY_HTML)
    now = datetime(2026, 10, 21, 11, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            decision_payload(
                category="company",
                event_type="earnings",
                topics=[],
                related_industries=["semiconductors"],
                companies=["NVIDIA Corporation"],
                symbols=["NVDA"],
            )
        )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                classify=ClassifyStageProcessor(
                    route=route,
                    client=ModelClient(route.targets, httpx.MockTransport(handler)),
                    taxonomy=taxonomy,
                )
            ),
            claim,
            clock=lambda: now,
        )

    stored = stored_classification(engine, document.id)
    assert stored["companies"] == ["NVIDIA Corporation"]
    assert stored["symbols"] == ["NVDA"]
    assert stored["related_industries"] == ["semiconductors"]


def test_classify_route_is_resolved_lazily_by_the_registry(tmp_path, monkeypatch):
    models = write_models(tmp_path)
    taxonomy_path = write_taxonomy(tmp_path)
    resolved: list[str] = []

    import stocks.classification.service as classification_service

    original = classification_service.load_classify_processor

    def tracking(*args, **kwargs):
        resolved.append("classify")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        classification_service, "load_classify_processor", tracking
    )

    registry = build_stage_registry(
        models, taxonomy_path=taxonomy_path
    )

    # Asking about capability must not read the decision route.
    assert STAGE_CLASSIFY in registry.available_stages()
    assert resolved == []
    processor = registry.for_stage(STAGE_CLASSIFY)
    assert resolved == ["classify"]
    assert isinstance(processor, ClassifyStageProcessor)
    assert STAGE_FACTS not in registry.available_stages()


def test_config_fingerprint_identifies_route_and_vocabulary(tmp_path):
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = load_classifier_route(write_models(tmp_path))[0]

    fingerprint = classify_config_fingerprint(route, taxonomy)

    assert len(fingerprint) == 64
    assert "test-key" not in fingerprint
    assert fingerprint == classify_config_fingerprint(route, taxonomy)
    other = load_taxonomy(write_taxonomy(tmp_path))
    assert fingerprint == classify_config_fingerprint(route, other)


def test_stage_processor_persists_the_outcome_in_its_own_transaction(tmp_path, engine):
    """The processor reports ``persisted`` so the runner cannot write twice."""

    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(decision_payload())

    processor = ClassifyStageProcessor(
        route=route,
        client=ModelClient(route.targets, httpx.MockTransport(handler)),
        taxonomy=taxonomy,
    )
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="cls", now=now)
        with Session(engine) as executor:
            result = processor(
                executor, config, executor.get(Document, document.id), claim
            )
        assert result.persisted is True
        assert result.outcome.status == "completed"

    row = stage_row(engine, document.id, STAGE_CLASSIFY)
    assert row.status == "completed"
    assert row.attempts == 1
    assert row.owner_token is None
    assert row.input_batch_id == stage_row(engine, document.id, STAGE_PARSE).input_batch_id


def test_stale_classification_owner_cannot_overwrite_a_newer_result(tmp_path, engine):
    """A superseded attempt must be refused, not applied to the new input."""

    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(decision_payload(category="policy"))

    processor = ClassifyStageProcessor(
        route=route,
        client=ModelClient(route.targets, httpx.MockTransport(handler)),
        taxonomy=taxonomy,
    )
    stale = StageClaim(
        document_id=document.id,
        stage=STAGE_CLASSIFY,
        owner_token="stale",
        attempts=0,
        max_attempts=3,
        reprocess_requested=False,
    )
    with Session(engine) as session:
        claim_stage(session, STAGE_CLASSIFY, owner_token="current", now=now)

    with Session(engine) as session:
        with pytest.raises(Exception) as exc_info:
            processor(session, config, session.get(Document, document.id), stale)
        session.rollback()

    assert getattr(exc_info.value, "code", None) == "STAGE_NOT_OWNED"
    assert stored_classification(engine, document.id) is None


def test_registry_registers_classify_and_leaves_facts_pending(tmp_path):
    registry = build_stage_registry(
        write_models(tmp_path), taxonomy_path=write_taxonomy(tmp_path)
    )

    assert STAGE_CLASSIFY in registry.available_stages()
    assert STAGE_FACTS not in registry.available_stages()


def test_reclassification_from_no_extraction_to_extraction_reopens_facts(
    tmp_path, engine
):
    """An explicit reclassification can make Facts runnable again.

    Facts skipped because the classifier decided no extraction was needed are not
    a permanent verdict: a later successful classification that says extraction is
    required reopens them. A completed Fact row is never reopened this way.
    """

    config = make_config()
    taxonomy = load_taxonomy(write_taxonomy(tmp_path))
    route = make_route()
    document = parsed(engine, config)
    now = datetime(2026, 10, 21, 14, tzinfo=UTC)

    answers = [
        decision_payload(should_extract=False),
        decision_payload(should_extract=True),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(answers.pop(0))

    registry = StageRegistry(
        classify=ClassifyStageProcessor(
            route=route,
            client=ModelClient(route.targets, httpx.MockTransport(handler)),
            taxonomy=taxonomy,
        )
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="c1", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)
    assert stage_row(engine, document.id, STAGE_FACTS).status == "skipped"

    later = now.replace(hour=15)
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_CLASSIFY, now=later)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_CLASSIFY, owner_token="c2", now=later)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: later)

    facts = stage_row(engine, document.id, STAGE_FACTS)
    assert facts.status == "pending"
    assert facts.skip_reason is None
    assert facts.attempts == 0
