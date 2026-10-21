"""Unified Document processing state, claiming, retry and recovery.

Database-backed cases need a real PostgreSQL instance (``TEST_DATABASE_URL``), as
the existing processing and repository tests do: the concurrency, stage
dependency and transaction boundaries are the behaviour under test, so they
cannot be replaced by a stub Session.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentEmbeddingState,
    DocumentParseBatch,
    DocumentParseState,
    DocumentProcessingState,
    Source,
)
from stocks.processing.runner import (
    StageRegistry,
    execute_claimed_stage,
    run_next_stage,
)
from stocks.processing.service import process_document
from stocks.processing.stages import (
    STAGE_CLASSIFY,
    STAGE_FACTS,
    STAGE_INDEX,
    STAGE_PARSE,
    SKIP_DEPENDENCY_SKIPPED,
    SKIP_NOT_APPLICABLE,
    WAIT_PARSE_UNAVAILABLE,
    WAIT_PARSE_UNSUPPORTED,
    WAIT_PROCESSOR_NOT_AVAILABLE,
    ProcessingError,
    StageClaim,
    StageOutcome,
    claim_stage,
    create_stage_states,
    get_stage_state,
    list_stage_states,
    recover_stage,
    request_reprocess,
    retry_stage,
)
from stocks.repository import (
    DocumentInput,
    ingest_document,
    list_parse_chunks,
)
from stocks.settings import Settings

ARTICLE_HTML = b"""\
<html><head><meta charset="utf-8" /></head><body>
<article>
  <h1>Quarterly inflation update</h1>
  <p>The annual rate was <b>3.1%</b> and the prior value was 2.8%.</p>
</article>
</body></html>
"""


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "s3_endpoint_url": "https://storage.example",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "parse_chunk_size": 400,
        "parse_chunk_overlap": 40,
        "embedding_batch_size": 2,
    }
    values.update(overrides)
    return Settings(**values)


def make_item(**overrides) -> DocumentInput:
    values = {
        "source_id": "sec",
        "dedupe_key": "release-1",
        "title": "SEC release",
        "payload": ARTICLE_HTML,
        "content_type": "text/html; charset=utf-8",
        "collected_at": datetime(2026, 10, 21, 1, tzinfo=UTC),
        "published_at": datetime(2026, 10, 20, tzinfo=UTC),
        "completeness": "complete",
    }
    values.update(overrides)
    return DocumentInput(**values)


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for processing stage tests")
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
def session_factory(engine):
    return lambda: Session(engine)


def seed_source(engine) -> None:
    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
        session.commit()


def ingest(engine, config: Settings, item: DocumentInput | None = None) -> Document:
    seed_source(engine)
    with Session(engine) as session:
        result = ingest_document(
            session,
            config,
            item or make_item(),
            store=lambda config, source_id, payload, content_type: (
                f"sources/{source_id}/original",
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        return result.document


def stage_rows(engine, document_id) -> dict[str, DocumentProcessingState]:
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(DocumentProcessingState).where(
                    DocumentProcessingState.document_id == document_id
                )
            ).all()
        )
        return {row.stage: row for row in rows}


def test_new_document_registers_four_pending_stages_in_the_ingest_transaction(
    engine,
):
    config = make_config()
    document = ingest(engine, config)

    rows = stage_rows(engine, document.id)
    assert set(rows) == {STAGE_PARSE, STAGE_CLASSIFY, STAGE_FACTS, STAGE_INDEX}
    for row in rows.values():
        assert row.status == "pending"
        assert row.attempts == 0
        assert row.owner_token is None
        assert row.wait_reason is None
        assert row.skip_reason is None


def test_reusing_identical_bytes_does_not_reopen_finished_stages(engine):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        state = session.get(DocumentProcessingState, (document.id, STAGE_PARSE))
        state.status = "completed"
        state.attempts = 1
        session.commit()

    with Session(engine) as session:
        result = ingest_document(
            session,
            config,
            make_item(),
            store=lambda config, source_id, payload, content_type: (
                "sources/sec/original",
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        assert result.created is False
        assert result.document.id == document.id

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "completed"
    assert rows[STAGE_PARSE].attempts == 1
    assert rows[STAGE_INDEX].status == "pending"


def test_new_content_version_registers_its_own_pending_stages(engine):
    config = make_config()
    first = ingest(engine, config)
    with Session(engine) as session:
        state = session.get(DocumentProcessingState, (first.id, STAGE_PARSE))
        state.status = "completed"
        state.attempts = 1
        session.commit()

    changed = b"<html><body><article><p>Revised release text.</p></article></body></html>"
    with Session(engine) as session:
        result = ingest_document(
            session,
            config,
            make_item(payload=changed),
            store=lambda config, source_id, payload, content_type: (
                "sources/sec/original-v2",
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        assert result.created is True
        assert result.document.version == 2
        second_id = result.document.id

    rows = stage_rows(engine, second_id)
    assert {row.status for row in rows.values()} == {"pending"}
    # The earlier version keeps its own completed state untouched.
    assert stage_rows(engine, first.id)[STAGE_PARSE].status == "completed"


def test_claim_is_atomic_across_two_sessions(engine):
    config = make_config()
    document = ingest(engine, config)

    with Session(engine) as first, Session(engine) as second:
        claimed = claim_stage(first, STAGE_PARSE, owner_token="token-a")
        assert claimed is not None
        assert claimed.document_id == document.id
        assert claimed.stage == STAGE_PARSE

        assert claim_stage(second, STAGE_PARSE, owner_token="token-b") is None

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "running"
    assert rows[STAGE_PARSE].owner_token == "token-a"


def test_downstream_stage_waits_for_its_dependency(engine):
    config = make_config()
    document = ingest(engine, config)

    with Session(engine) as session:
        assert claim_stage(session, STAGE_INDEX, owner_token="idx") is None
        assert claim_stage(session, STAGE_CLASSIFY, owner_token="cls") is None

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="parse")
        assert claim is not None
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor()),
            claim,
            clock=lambda: datetime(2026, 10, 21, 2, tzinfo=UTC),
        )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_INDEX, owner_token="idx")
        assert claim is not None
        assert claim.document_id == document.id


class _StubParseProcessor:
    version = "stub-parse-v1"

    def __init__(self, *, fail: bool = False, applicability: dict | None = None):
        self.fail = fail
        self.applicability = applicability

    def __call__(self, session, config, document, claim) -> StageOutcome:
        if self.fail:
            raise ProcessingError("STUB_PARSE_FAILED", "Stub parser failed")
        return StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_PARSE,
            owner_token=claim.owner_token,
            status="completed",
            processor_version=self.version,
            config_fingerprint="f" * 64,
            applicability=self.applicability
            or {"requires_embedding": True, "requires_classification": True},
        )


def test_missing_processor_waits_without_consuming_an_attempt(engine):
    config = make_config()
    document = ingest(engine, config)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner-1")
        assert claim is not None
        execution = execute_claimed_stage(
            session, config, StageRegistry(), claim
        )

    assert execution.status == "pending"
    assert execution.wait_reason == WAIT_PROCESSOR_NOT_AVAILABLE
    assert execution.attempts == 0
    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].owner_token is None
    # A capability gap is neither a failure nor a skip.
    assert rows[STAGE_PARSE].error is None
    assert rows[STAGE_PARSE].skip_reason is None


def test_failed_stage_is_retried_with_backoff_and_stops_when_exhausted(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 3, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner-1", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor(fail=True)),
            claim,
            clock=lambda: now,
        )
        assert execution.status == "failed"
        assert execution.attempts == 1
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.error["code"] == "STUB_PARSE_FAILED"
        assert state.next_attempt_at is not None
        assert state.next_attempt_at > now
        first_next_attempt = state.next_attempt_at

    # Not claimable before the backoff deadline, claimable after it.
    with Session(engine) as session:
        assert (
            claim_stage(
                session,
                STAGE_PARSE,
                owner_token="owner-2",
                now=now + timedelta(seconds=1),
            )
            is None
        )
        claim = claim_stage(
            session,
            STAGE_PARSE,
            owner_token="owner-2",
            now=first_next_attempt,
        )
        assert claim is not None
        assert claim.attempts == 1
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor(fail=True)),
            claim,
            clock=lambda: first_next_attempt,
        )
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.attempts == 2
        assert state.next_attempt_at is not None

    # Third attempt exhausts the default budget: the stage stops being claimed.
    with Session(engine) as session:
        claim = claim_stage(
            session,
            STAGE_PARSE,
            owner_token="owner-3",
            now=state.next_attempt_at,
        )
        assert claim is not None
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor(fail=True)),
            claim,
            clock=lambda: state.next_attempt_at,
        )
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.status == "failed"
        assert state.attempts == 3
        assert state.max_attempts == 3
        assert state.next_attempt_at is None

    with Session(engine) as session:
        assert (
            claim_stage(
                session,
                STAGE_PARSE,
                owner_token="owner-4",
                now=now + timedelta(days=1),
            )
            is None
        )


def test_owner_retry_after_exhaustion_really_reprocesses(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 4, tzinfo=UTC)

    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        state.status = "failed"
        state.attempts = state.max_attempts
        state.auto_retry = True
        state.error = {"code": "STUB_PARSE_FAILED", "message": "Stub parser failed"}
        session.commit()

        retried = retry_stage(session, document.id, STAGE_PARSE, now=now)
        assert retried.status == "pending"
        assert retried.attempts == 0
        assert retried.error is None

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner", now=now)
        assert claim is not None
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor()),
            claim,
            clock=lambda: now,
        )
        assert execution.status == "completed"
        assert execution.attempts == 1


def test_running_stage_requires_owner_confirmation_to_recover(engine):
    config = make_config()
    document = ingest(engine, config)

    with Session(engine) as session:
        assert claim_stage(session, STAGE_PARSE, owner_token="stale") is not None

    with Session(engine) as session:
        with pytest.raises(ProcessingError) as excinfo:
            retry_stage(session, document.id, STAGE_PARSE)
        assert excinfo.value.code == "OWNER_CONFIRMATION_REQUIRED"

        with pytest.raises(ProcessingError) as excinfo:
            recover_stage(session, document.id, STAGE_PARSE)
        assert excinfo.value.code == "OWNER_CONFIRMATION_REQUIRED"

    with Session(engine) as session:
        recovered = recover_stage(
            session, document.id, STAGE_PARSE, confirm_stopped=True
        )
        assert recovered.status == "pending"
        assert recovered.owner_token is None

    # The stale owner can no longer write an outcome.
    with Session(engine) as session:
        with pytest.raises(ProcessingError) as excinfo:
            execute_claimed_stage(
                session,
                config,
                StageRegistry(parse=_StubParseProcessor()),
                StageClaim(
                    document_id=document.id,
                    stage=STAGE_PARSE,
                    owner_token="stale",
                    attempts=0,
                    max_attempts=3,
                    reprocess_requested=False,
                ),
            )
        assert excinfo.value.code == "STAGE_NOT_OWNED"


def test_recovery_keeps_durable_result_of_an_interrupted_stage(engine):
    config = make_config()
    document = ingest(engine, config)
    payload = ARTICLE_HTML
    with Session(engine) as session:
        stored = session.get(Document, document.id)
        summary = process_document(
            session,
            config,
            stored,
            read_bytes=lambda config, item: payload,
        )
        assert summary.status == "completed"
        claim_stage(session, STAGE_PARSE, owner_token="crashed")

    with Session(engine) as session:
        recovered = recover_stage(
            session, document.id, STAGE_PARSE, confirm_stopped=True
        )
        assert recovered.status == "completed"
        assert recovered.owner_token is None

    with Session(engine) as session:
        assert session.scalars(select(DocumentParseBatch)).all()
        assert list_parse_chunks(session, document.id)


def test_classification_failure_does_not_block_indexing(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 5, tzinfo=UTC)

    with Session(engine) as session:
        parse_claim = claim_stage(session, STAGE_PARSE, owner_token="parse", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor()),
            parse_claim,
            clock=lambda: now,
        )

    with Session(engine) as session:
        classify_claim = claim_stage(
            session, STAGE_CLASSIFY, owner_token="cls", now=now
        )
        assert classify_claim is not None
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                classify=_StubFailingProcessor(STAGE_CLASSIFY),
            ),
            classify_claim,
            clock=lambda: now,
        )
        assert execution.status == "failed"

    with Session(engine) as session:
        index_claim = claim_stage(session, STAGE_INDEX, owner_token="idx", now=now)
        assert index_claim is not None
        assert index_claim.document_id == document.id


class _StubFailingProcessor:
    version = "stub-v1"

    def __init__(self, stage: str) -> None:
        self.stage = stage

    def __call__(self, session, config, document, claim) -> StageOutcome:
        raise ProcessingError(
            "CLASSIFICATION_FAILED", "Classification model was unavailable"
        )


def test_explicit_skip_requires_applicability_and_propagates(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 6, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="parse", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=_StubParseProcessor(
                    applicability={
                        "requires_embedding": False,
                        "requires_classification": False,
                    }
                )
            ),
            claim,
            clock=lambda: now,
        )
        # A skipped stage is produced by the processor's own applicability, not
        # by an inference the runner makes on its behalf.
        outcome = StageOutcome(
            document_id=document.id,
            stage=STAGE_PARSE,
            owner_token="none",
            status="skipped",
            skip_reason="NO_SEARCHABLE_TEXT",
        )
        assert outcome.skip_reason == "NO_SEARCHABLE_TEXT"

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "completed"
    assert rows[STAGE_PARSE].applicability == {
        "requires_embedding": False,
        "requires_classification": False,
    }


def test_skipped_status_requires_a_reason_at_the_boundary(engine):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner")
        with pytest.raises(ValueError, match="skip reason"):
            execute_claimed_stage(
                session,
                config,
                StageRegistry(
                    parse=_StubSkippingProcessor(skip_reason=None),
                ),
                claim,
            )


class _StubSkippingProcessor:
    version = "stub-skip-v1"

    def __init__(self, *, skip_reason: str | None) -> None:
        self.skip_reason = skip_reason

    def __call__(self, session, config, document, claim) -> StageOutcome:
        return StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_PARSE,
            owner_token=claim.owner_token,
            status="skipped",
            skip_reason=self.skip_reason,
        )


def test_explicit_reprocess_reruns_a_completed_stage(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 7, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="first", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor()),
            claim,
            clock=lambda: now,
        )
        assert (
            claim_stage(session, STAGE_PARSE, owner_token="second", now=now) is None
        )

    with Session(engine) as session:
        with pytest.raises(ProcessingError) as excinfo:
            retry_stage(session, document.id, STAGE_PARSE)
        assert excinfo.value.code == "STAGE_ALREADY_COMPLETED"

        reopened = request_reprocess(session, document.id, STAGE_PARSE, now=now)
        assert reopened.status == "pending"
        assert reopened.reprocess_requested is True
        assert reopened.attempts == 0

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="third", now=now)
        assert claim is not None
        assert claim.reprocess_requested is True


def test_run_next_stage_claims_and_executes_in_separate_sessions(session_factory):
    config = make_config()
    with session_factory() as session:
        session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
        session.commit()
        document = ingest_document(
            session,
            config,
            make_item(),
            store=lambda config, source_id, payload, content_type: (
                "sources/sec/original",
                hashlib.sha256(payload).hexdigest(),
            ),
        ).document

    execution = run_next_stage(
        session_factory,
        config,
        StageRegistry(parse=_StubParseProcessor()),
        STAGE_PARSE,
        owner_token="worker-1",
        now=datetime(2026, 10, 21, 8, tzinfo=UTC),
    )
    assert execution is not None
    assert execution.document_id == document.id
    assert execution.status == "completed"

    assert (
        run_next_stage(
            session_factory,
            config,
            StageRegistry(parse=_StubParseProcessor()),
            STAGE_PARSE,
            owner_token="worker-2",
        )
        is None
    )


def test_backlog_can_be_queried_by_stage_and_status(engine):
    config = make_config()
    first = ingest(engine, config)
    with Session(engine) as session:
        state = get_stage_state(session, first.id, STAGE_PARSE)
        state.status = "failed"
        state.attempts = 3
        state.error = {"code": "STUB_PARSE_FAILED", "message": "Stub parser failed"}
        session.commit()

    with Session(engine) as session:
        failed = list_stage_states(session, stages=[STAGE_PARSE], statuses=["failed"])
        assert [row.document_id for row in failed] == [first.id]
        pending_index = list_stage_states(
            session, stages=[STAGE_INDEX], statuses=["pending"]
        )
        assert [row.document_id for row in pending_index] == [first.id]

        with pytest.raises(ValueError, match="Unknown processing stage"):
            list_stage_states(session, stages=["unknown"])
        with pytest.raises(ValueError, match="limit must be between"):
            list_stage_states(session, limit=0)


def test_reprocessing_a_classification_does_not_reopen_facts(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 9, tzinfo=UTC)
    with Session(engine) as session:
        for stage in (STAGE_PARSE, STAGE_CLASSIFY, STAGE_FACTS):
            state = get_stage_state(session, document.id, stage)
            state.status = "completed"
            state.attempts = 1
        session.commit()

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_CLASSIFY, now=now)

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_CLASSIFY].status == "pending"
    assert rows[STAGE_CLASSIFY].reprocess_requested is True
    assert rows[STAGE_FACTS].status == "completed"
    assert rows[STAGE_FACTS].attempts == 1


def test_waiting_stage_is_claimable_once_a_processor_is_registered(engine):
    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner-1", now=now)
        execute_claimed_stage(session, config, StageRegistry(), claim, clock=lambda: now)

    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.wait_reason == WAIT_PROCESSOR_NOT_AVAILABLE

    # Registering the processor makes the same pending row claimable again; the
    # wait did not burn an attempt or mark the stage failed.
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner-2", now=now)
        assert claim is not None
        assert claim.attempts == 0
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=_StubParseProcessor()),
            claim,
            clock=lambda: now,
        )
        assert execution.status == "completed"
        assert execution.attempts == 1


def test_real_parse_failure_fails_the_stage_instead_of_completing_it(engine):
    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 11, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="owner", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=build_parse_processor(read_bytes=lambda config, item: b"corrupt")
            ),
            claim,
            clock=lambda: now,
        )

    assert execution.status == "failed"
    assert execution.error["code"] == "CONTENT_HASH_MISMATCH"
    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "failed"
    assert rows[STAGE_PARSE].error["code"] == "CONTENT_HASH_MISMATCH"
    assert rows[STAGE_PARSE].next_attempt_at is not None
    # The failed attempt is recorded as an immutable failed batch, but no current
    # successful batch exists, so nothing downstream becomes claimable.
    with Session(engine) as session:
        batches = session.scalars(select(DocumentParseBatch)).all()
        assert [batch.status for batch in batches] == ["failed"]
        parse_state = session.get(DocumentParseState, document.id)
        assert parse_state.current_batch_id is None
        assert claim_stage(session, STAGE_INDEX, owner_token="idx", now=now) is None


def test_real_parse_then_index_runs_end_to_end_and_reuses_results(engine):
    from stocks.indexing.service import embed_texts
    from stocks.processing.processors import (
        build_index_processor,
        build_parse_processor,
    )
    from stocks.providers import EmbeddingResponse, EmbeddingTargets, ModelTarget
    from stocks.embedding_space import VECTOR_DIMENSIONS

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    targets = EmbeddingTargets(
        aliases=("embedding",),
        targets=(
            ModelTarget(
                base_url="https://embeddings.example/v1",
                api_key="test-key",
                name="text-embedding-3-small",
                dimensions=VECTOR_DIMENSIONS,
            ),
        ),
    )
    calls: list[int] = []

    def fake_embed(client, embedding_targets, texts):
        calls.append(len(texts))
        return EmbeddingResponse(
            embeddings=[
                tuple(1.0 if index == 0 else 0.0 for index in range(VECTOR_DIMENSIONS))
                for _ in texts
            ],
            model_alias="embedding",
        )

    registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML),
        index=build_index_processor(targets, client=None, embed=fake_embed),
    )

    with Session(engine) as session:
        parse_claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        assert (
            execute_claimed_stage(session, config, registry, parse_claim, clock=lambda: now).status
            == "completed"
        )

    with Session(engine) as session:
        index_claim = claim_stage(session, STAGE_INDEX, owner_token="i", now=now)
        assert index_claim is not None
        execution = execute_claimed_stage(
            session, config, registry, index_claim, clock=lambda: now
        )
        assert execution.status == "completed"

    with Session(engine) as session:
        embedding_state = session.get(DocumentEmbeddingState, document.id)
        assert embedding_state is not None
        assert embedding_state.status == "completed"
        assert embedding_state.indexed_count == embedding_state.chunk_count
        assert embedding_state.chunk_count > 0
        chunks = list_parse_chunks(session, document.id)
        assert embedding_state.chunk_count == len(chunks)
        assert embedding_state.parse_batch_id == chunks[0].parse_batch_id

    batches_after_first_run = len(calls)

    # A second index claim is impossible: the stage is completed and its vectors
    # are reused rather than regenerated.
    with Session(engine) as session:
        assert claim_stage(session, STAGE_INDEX, owner_token="i2", now=now) is None
    assert len(calls) == batches_after_first_run


def test_reparsing_reopens_index_but_keeps_completed_facts(engine):
    """A new current parse batch reopens indexing without reopening Facts.

    Facts are only reopened by an explicit Fact request or a new Document content
    version; an explicit reparse of the same version cannot invalidate a completed
    extraction.
    """

    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)
    registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML)
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p0", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)
        for stage in (STAGE_CLASSIFY, STAGE_FACTS, STAGE_INDEX):
            state = get_stage_state(session, document.id, stage)
            state.status = "completed"
            state.attempts = 1
        session.commit()

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        assert claim is not None
        assert claim.reprocess_requested is True
        execution = execute_claimed_stage(
            session, config, registry, claim, clock=lambda: now
        )
        assert execution.status == "completed"

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "completed"
    # The index stage consumes the current parse batch, so it must run again.
    assert rows[STAGE_INDEX].status == "pending"
    assert rows[STAGE_INDEX].attempts == 0
    assert rows[STAGE_CLASSIFY].status == "pending"
    # Completed Facts are not silently re-extracted by a reparse.
    assert rows[STAGE_FACTS].status == "completed"
    assert rows[STAGE_FACTS].attempts == 1


def test_switching_embedding_model_does_not_reopen_a_completed_index(engine):
    from stocks.embedding_space import VECTOR_DIMENSIONS
    from stocks.processing.processors import build_index_processor
    from stocks.providers import EmbeddingResponse, EmbeddingTargets, ModelTarget

    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 14, tzinfo=UTC)
    calls: list[str] = []
    parser = build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML)

    def make_targets(model_name: str) -> EmbeddingTargets:
        return EmbeddingTargets(
            aliases=("embedding",),
            targets=(
                ModelTarget(
                    base_url="https://embeddings.example/v1",
                    api_key="test-key",
                    name=model_name,
                    dimensions=VECTOR_DIMENSIONS,
                ),
            ),
        )

    def fake_embed(client, targets, texts):
        calls.append(targets.model_name)
        return EmbeddingResponse(
            embeddings=[
                tuple(1.0 if i == 0 else 0.0 for i in range(VECTOR_DIMENSIONS))
                for _ in texts
            ],
            model_alias="embedding",
        )

    first_registry = StageRegistry(
        parse=parser,
        index=build_index_processor(
            make_targets("text-embedding-3-small"), None, embed=fake_embed
        ),
    )
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(session, config, first_registry, claim, clock=lambda: now)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_INDEX, owner_token="i", now=now)
        assert claim is not None
        assert (
            execute_claimed_stage(
                session, config, first_registry, claim, clock=lambda: now
            ).status
            == "completed"
        )
    assert calls == ["text-embedding-3-small"]

    # A different configured model does not automatically reopen the finished
    # stage; only an explicit reprocess does.
    with Session(engine) as session:
        assert claim_stage(session, STAGE_INDEX, owner_token="i2", now=now) is None
    assert calls == ["text-embedding-3-small"]
    assert stage_rows(engine, document.id)[STAGE_INDEX].status == "completed"


def test_owner_reprocess_index_reuses_existing_chunks(engine):
    from stocks.embedding_space import VECTOR_DIMENSIONS
    from stocks.processing.processors import build_index_processor, build_parse_processor
    from stocks.providers import EmbeddingResponse, EmbeddingTargets, ModelTarget

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 15, tzinfo=UTC)
    targets = EmbeddingTargets(
        aliases=("embedding",),
        targets=(
            ModelTarget(
                base_url="https://embeddings.example/v1",
                api_key="test-key",
                name="text-embedding-3-small",
                dimensions=VECTOR_DIMENSIONS,
            ),
        ),
    )
    registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML),
        index=build_index_processor(
            targets,
            None,
            embed=lambda client, targets, texts: EmbeddingResponse(
                embeddings=[
                    tuple(1.0 if i == 0 else 0.0 for i in range(VECTOR_DIMENSIONS))
                    for _ in texts
                ],
                model_alias="embedding",
            ),
        ),
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)
        claim = claim_stage(session, STAGE_INDEX, owner_token="i", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)
        before = [
            (chunk.id, chunk.text, chunk.parse_batch_id)
            for chunk in list_parse_chunks(session, document.id)
        ]
        batch_count = len(session.scalars(select(DocumentParseBatch)).all())

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_INDEX, now=now)
        claim = claim_stage(session, STAGE_INDEX, owner_token="i2", now=now)
        assert claim is not None
        execution = execute_claimed_stage(
            session, config, registry, claim, clock=lambda: now
        )
        assert execution.status == "completed"

    with Session(engine) as session:
        after = [
            (chunk.id, chunk.text, chunk.parse_batch_id)
            for chunk in list_parse_chunks(session, document.id)
        ]
        # Reindexing reads the existing current chunks; it never reparses, so the
        # Chunk IDs, text and batch a report may already reference are unchanged.
        assert after == before
        assert len(session.scalars(select(DocumentParseBatch)).all()) == batch_count


def test_reparse_failure_keeps_current_chunks_and_report_references(engine):
    """A failed reprocess must not revoke the current batch or its Chunks."""

    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 16, tzinfo=UTC)
    good = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML)
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        assert (
            execute_claimed_stage(session, config, good, claim, clock=lambda: now).status
            == "completed"
        )
        chunks_before = [
            (chunk.id, chunk.text, chunk.parse_batch_id)
            for chunk in list_parse_chunks(session, document.id)
        ]
        current_batch_before = session.get(
            DocumentParseState, document.id
        ).current_batch_id

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)
        claim = claim_stage(session, STAGE_PARSE, owner_token="p2", now=now)
        assert claim is not None
        failed = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=build_parse_processor(read_bytes=lambda config, item: b"corrupt")
            ),
            claim,
            clock=lambda: now,
        )
        assert failed.status == "failed"

    with Session(engine) as session:
        assert (
            session.get(DocumentParseState, document.id).current_batch_id
            == current_batch_before
        )
        assert [
            (chunk.id, chunk.text, chunk.parse_batch_id)
            for chunk in list_parse_chunks(session, document.id)
        ] == chunks_before
        # Index was not reopened by the failed attempt.
        assert stage_rows(engine, document.id)[STAGE_INDEX].status == "pending"


def test_failed_explicit_reprocess_keeps_the_request_for_a_real_retry(engine):
    """A failed explicit rebuild must not degrade into a plain retry.

    The Owner asked for a real rebuild. If that attempt fails, the request has to
    survive: a plain retry would otherwise accept already-covered vectors or an
    already stored batch as success without ever finishing the requested rebuild.
    """

    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 17, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="first", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=build_parse_processor(read_bytes=lambda c, d: ARTICLE_HTML)),
            claim,
            clock=lambda: now,
        )

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="rebuild", now=now)
        assert claim is not None and claim.reprocess_requested is True
        failed = execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=build_parse_processor(read_bytes=lambda config, item: b"corrupt")
            ),
            claim,
            clock=lambda: now,
        )
        assert failed.status == "failed"

    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.status == "failed"
        # The requested rebuild is still outstanding.
        assert state.reprocess_requested is True

        retried = retry_stage(session, document.id, STAGE_PARSE, now=now)
        # A retry reopens the stage without cancelling the rebuild request.
        assert retried.status == "pending"
        assert retried.reprocess_requested is True

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="rebuild-2", now=now)
        assert claim is not None
        assert claim.reprocess_requested is True


def test_recovery_does_not_report_completion_for_an_interrupted_reprocess(engine):
    """An interrupted explicit rebuild must not be recovered as completed.

    The stage already had a usable result before the Owner asked for a rebuild, so
    a durable-result lookup alone would report success for work the rebuild never
    finished.
    """

    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 18, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="first", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=build_parse_processor(read_bytes=lambda c, d: ARTICLE_HTML)),
            claim,
            clock=lambda: now,
        )

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)
        # The rebuilt attempt is claimed and then the process is killed.
        assert claim_stage(session, STAGE_PARSE, owner_token="killed", now=now)

    with Session(engine) as session:
        recovered = recover_stage(
            session, document.id, STAGE_PARSE, confirm_stopped=True
        )
        assert recovered.status == "pending"
        assert recovered.owner_token is None
        assert recovered.reprocess_requested is True

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="rebuild", now=now)
        assert claim is not None
        assert claim.reprocess_requested is True


def test_intermediate_vector_batch_rejects_a_released_owner(engine):
    """Every vector batch is fenced, not only the last covering one.

    A multi-batch index commits intermediate vectors. If the stage was recovered
    and re-claimed meanwhile, the old executor must not keep writing vectors as if
    it still owned the current attempt.
    """

    from stocks.embedding_space import VECTOR_DIMENSIONS
    from stocks.processing.processors import build_index_processor, build_parse_processor
    from stocks.providers import EmbeddingResponse, EmbeddingTargets, ModelTarget
    from stocks.repository import EmbeddingInput, persist_embedding_result

    # A small chunk budget produces several Chunks, so the run really has more
    # than one vector batch to commit.
    payload = (
        b"<html><body><article>"
        + b"".join(
            f"<p>Paragraph {index} reports the annual rate at 3.{index}%.</p>".encode()
            for index in range(6)
        )
        + b"</article></body></html>"
    )
    config = make_config(
        embedding_batch_size=1, parse_chunk_size=200, parse_chunk_overlap=30
    )
    document = ingest(engine, config, make_item(payload=payload))
    now = datetime(2026, 10, 21, 19, tzinfo=UTC)
    targets = EmbeddingTargets(
        aliases=("embedding",),
        targets=(
            ModelTarget(
                base_url="https://embeddings.example/v1",
                api_key="test-key",
                name="text-embedding-3-small",
                dimensions=VECTOR_DIMENSIONS,
            ),
        ),
    )
    registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: payload),
        index=build_index_processor(
            targets,
            None,
            embed=lambda client, tgt, texts: EmbeddingResponse(
                embeddings=[
                    tuple(1.0 if i == 0 else 0.0 for i in range(VECTOR_DIMENSIONS))
                    for _ in texts
                ],
                model_alias="embedding",
            ),
        ),
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)

    with Session(engine) as session:
        parse_state = session.get(DocumentParseState, document.id)
        chunks = list_parse_chunks(session, document.id)
        assert len(chunks) > 1
        stale = StageClaim(
            document_id=document.id,
            stage=STAGE_INDEX,
            owner_token="stale",
            attempts=0,
            max_attempts=3,
            reprocess_requested=True,
        )
        # The stale token is refused before an intermediate batch is committed.
        with pytest.raises(ProcessingError) as excinfo:
            persist_embedding_result(
                session,
                document.id,
                parse_batch_id=parse_state.current_batch_id,
                model_alias="embedding",
                model_name=targets.model_name,
                dimensions=targets.dimensions,
                status="failed",
                embeddings=(
                    EmbeddingInput(
                        chunk_id=chunks[0].id,
                        vector=tuple(0.0 for _ in range(VECTOR_DIMENSIONS)),
                    ),
                ),
                attempted_at=now,
                error={"code": "EMBEDDING_INCOMPLETE", "message": "not finished"},
                stage_claim=stale,
                stage_final_batch=False,
            )
        assert excinfo.value.code == "STAGE_NOT_OWNED"


def _index_targets_and_embed(**overrides):
    """Embedding targets plus a deterministic embedding stub for index stages."""

    from stocks.embedding_space import VECTOR_DIMENSIONS
    from stocks.providers import EmbeddingResponse, EmbeddingTargets, ModelTarget

    targets = EmbeddingTargets(
        aliases=("embedding",),
        targets=(
            ModelTarget(
                base_url="https://embeddings.example/v1",
                api_key="test-key",
                name="text-embedding-3-small",
                dimensions=VECTOR_DIMENSIONS,
            ),
        ),
    )
    embed = overrides.pop("embed", None) or (
        lambda client, embedding_targets, texts: EmbeddingResponse(
            embeddings=[
                tuple(1.0 if index == 0 else 0.0 for index in range(VECTOR_DIMENSIONS))
                for _ in texts
            ],
            model_alias="embedding",
        )
    )
    return targets, embed


def test_stale_index_owner_cannot_write_after_owner_recovery(engine):
    """A cached stage entity must not authorise a superseded index writer.

    The old Session keeps its claimed stage entity alive (``expire_on_commit=False``)
    while the Owner confirms the old process stopped and another executor claims the
    stage. When the stale writer resumes it must be refused, and neither vectors nor
    stage state may be overwritten.
    """

    from stocks.embedding_space import VECTOR_DIMENSIONS
    from stocks.processing.processors import (
        build_index_processor,
        build_parse_processor,
    )
    from stocks.repository import EmbeddingInput, persist_embedding_result

    config = make_config(embedding_batch_size=1)
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 22, tzinfo=UTC)
    targets, embed = _index_targets_and_embed()
    registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML),
        index=build_index_processor(targets, None, embed=embed),
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(session, config, registry, claim, clock=lambda: now)

    stale = Session(engine)
    try:
        claim = claim_stage(stale, STAGE_INDEX, owner_token="old", now=now)
        assert claim is not None
        # Hold the claimed stage entity and the parse pointer strongly: this is
        # the state the old execution would validate against.
        held = stale.get(DocumentProcessingState, (document.id, STAGE_INDEX))
        parse_state = stale.get(DocumentParseState, document.id)
        assert held is not None and held.owner_token == "old"
        old_batch_id = parse_state.current_batch_id
        assert old_batch_id is not None
        old_chunks = list_parse_chunks(stale, document.id)

        # The Owner confirms the process stopped and a new executor claims the
        # same stage with a new token.
        with Session(engine) as owner:
            recovered = recover_stage(
                owner, document.id, STAGE_INDEX, confirm_stopped=True
            )
            assert recovered.status == "pending"
            assert recovered.owner_token is None
        with Session(engine) as replacement:
            new_claim = claim_stage(
                replacement, STAGE_INDEX, owner_token="new", now=now
            )
            assert new_claim is not None
            assert new_claim.owner_token == "new"

        # The stale writer resumes with its cached entity still claiming ownership.
        with pytest.raises(ProcessingError) as excinfo:
            persist_embedding_result(
                stale,
                document.id,
                parse_batch_id=old_batch_id,
                model_alias="embedding",
                model_name=targets.model_name,
                dimensions=targets.dimensions,
                status="failed",
                embeddings=(
                    EmbeddingInput(
                        chunk_id=old_chunks[0].id,
                        vector=tuple(0.0 for _ in range(VECTOR_DIMENSIONS)),
                    ),
                ),
                attempted_at=now,
                error={"code": "EMBEDDING_INCOMPLETE", "message": "not finished"},
                stage_claim=claim,
                stage_final_batch=False,
            )
        assert excinfo.value.code == "STAGE_NOT_OWNED"
    finally:
        stale.close()

    with Session(engine) as session:
        state = session.get(DocumentProcessingState, (document.id, STAGE_INDEX))
        assert state.status == "running"
        assert state.owner_token == "new"
        assert state.attempts == 0
        assert session.get(DocumentEmbeddingState, document.id) is None


def test_new_current_batch_supersedes_a_running_index_attempt(engine):
    """An index attempt must not complete for input that is no longer current.

    While the index stage embeds the current batch, a second Session commits an
    explicit parse reprocess that becomes the new current batch. The old attempt
    must not finish the stage or persist vectors for the old batch; the released
    stage is then claimable for the new batch, and the old Chunks stay readable.
    """

    from stocks.processing.processors import (
        build_index_processor,
        build_parse_processor,
    )

    config = make_config(embedding_batch_size=1)
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 23, tzinfo=UTC)
    targets, embed = _index_targets_and_embed()
    parse_registry = StageRegistry(
        parse=build_parse_processor(read_bytes=lambda config, item: ARTICLE_HTML)
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(session, config, parse_registry, claim, clock=lambda: now)

    with Session(engine) as session:
        old_batch_id = session.get(DocumentParseState, document.id).current_batch_id
        assert old_batch_id is not None
        old_chunks = [
            (chunk.id, chunk.text) for chunk in list_parse_chunks(session, document.id)
        ]

    def embed_after_competing_reprocess(client, embedding_targets, texts):
        # Pause inside the model call once: another Session commits a new current
        # parse batch while this attempt still works on the old one.
        if not competing_done:
            competing_done.append(True)
            with Session(engine) as competing:
                request_reprocess(competing, document.id, STAGE_PARSE, now=now)
                reprocess = claim_stage(
                    competing, STAGE_PARSE, owner_token="p2", now=now
                )
                assert reprocess is not None
                execute_claimed_stage(
                    competing, config, parse_registry, reprocess, clock=lambda: now
                )
                assert (
                    competing.get(DocumentParseState, document.id).current_batch_id
                    != old_batch_id
                )
        return embed(client, embedding_targets, texts)

    competing_done: list[bool] = []

    index_registry = StageRegistry(
        index=build_index_processor(targets, None, embed=embed_after_competing_reprocess)
    )

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_INDEX, owner_token="old", now=now)
        assert claim is not None
        stale = execute_claimed_stage(
            session, config, index_registry, claim, clock=lambda: now
        )

    with Session(engine) as session:
        current_batch_id = session.get(
            DocumentParseState, document.id
        ).current_batch_id
        assert current_batch_id != old_batch_id
        # The superseded attempt neither completed the stage nor claimed vectors
        # for the batch it was working on.
        assert stale.status != "completed"
        stage = session.get(DocumentProcessingState, (document.id, STAGE_INDEX))
        assert stage.status == "pending"
        assert stage.owner_token is None
        assert stage.input_batch_id == current_batch_id
        # Superseding the attempt is not a failed attempt of the new input.
        assert stage.attempts == 0
        assert session.get(DocumentEmbeddingState, document.id) is None
        # The previous Chunks stay readable for reports that reference them.
        assert [
            (chunk.id, chunk.text) for chunk in list_parse_chunks(
                session, document.id, batch_id=old_batch_id, current=False
            )
        ] == old_chunks

    # The next Worker attempt indexes the new current batch and nothing else.
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_INDEX, owner_token="new", now=now)
        assert claim is not None
        execution = execute_claimed_stage(
            session, config, index_registry, claim, clock=lambda: now
        )
        assert execution.status == "completed"
        assert execution.input_batch_id == current_batch_id
        assert execution.processor_version == "embedding-index-v1"

    with Session(engine) as session:
        embedding_state = session.get(DocumentEmbeddingState, document.id)
        assert embedding_state.status == "completed"
        assert embedding_state.parse_batch_id == current_batch_id
        assert embedding_state.indexed_count == embedding_state.chunk_count
        stage = session.get(DocumentProcessingState, (document.id, STAGE_INDEX))
        assert stage.status == "completed"
        assert stage.input_batch_id == current_batch_id
        assert stage.processor_version == "embedding-index-v1"
        assert stage.config_fingerprint is not None


def _real_parse_registry(**kwargs):
    """A real parse processor with injected stored bytes.

    The real path derives the stage outcome from the persisted batch (including
    ``reopen_downstream``), which is what a reprocess of a Document actually does.
    """

    from stocks.processing.processors import build_parse_processor

    return StageRegistry(
        parse=build_parse_processor(
            read_bytes=kwargs.pop("read_bytes", lambda config, item: ARTICLE_HTML),
            **kwargs,
        )
    )


def test_applicability_skip_propagates_and_is_not_revoked_by_completion(engine):
    """An explicit applicability verdict skips dependents it makes pointless.

    When the processor says the content needs neither embedding nor
    classification, the dependent stages are skipped with that reason, and Facts
    inherit the skip because their classification decision will never exist. A
    completed result is never revoked by a later skip verdict, and a skip does not
    consume the retry budget.
    """

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 20, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=_StubParseProcessor(
                    applicability={
                        "requires_embedding": False,
                        "requires_classification": False,
                    }
                )
            ),
            claim,
            clock=lambda: now,
        )

    rows = stage_rows(engine, document.id)
    # Parsing itself completed, so the Document stays stored and readable.
    assert rows[STAGE_PARSE].status == "completed"
    assert rows[STAGE_INDEX].status == "skipped"
    assert rows[STAGE_INDEX].skip_reason == SKIP_NOT_APPLICABLE
    assert rows[STAGE_CLASSIFY].status == "skipped"
    assert rows[STAGE_CLASSIFY].skip_reason == SKIP_NOT_APPLICABLE
    # Facts cannot become actionable from a classification that never ran.
    assert rows[STAGE_FACTS].status == "skipped"
    assert rows[STAGE_FACTS].skip_reason == SKIP_DEPENDENCY_SKIPPED
    # Skipping is not a failed attempt: the budget is untouched.
    for stage in (STAGE_INDEX, STAGE_CLASSIFY, STAGE_FACTS):
        assert rows[stage].attempts == 0

    # A completed index result is never revoked by a later applicability verdict.
    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_INDEX)
        state.status = "completed"
        state.attempts = 1
        state.skip_reason = None
        session.commit()

    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)
        claim = claim_stage(session, STAGE_PARSE, owner_token="p2", now=now)
        assert claim is not None
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=_StubParseProcessor(
                    applicability={
                        "requires_embedding": False,
                        "requires_classification": False,
                    }
                )
            ),
            claim,
            clock=lambda: now,
        )

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_INDEX].status == "completed"
    assert rows[STAGE_INDEX].attempts == 1


def test_skipped_dependency_reopens_once_its_dependency_completes(engine):
    """An inherited skip must not outlive the gap it recorded.

    Facts were skipped only because the classification never ran. Once a parse
    succeeds and produces a real batch, the classification is runnable again and
    Facts must stop presenting that gap as permanent.
    """

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 21, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execute_claimed_stage(
            session,
            config,
            StageRegistry(
                parse=_StubParseProcessor(
                    applicability={
                        "requires_embedding": False,
                        "requires_classification": False,
                    }
                )
            ),
            claim,
            clock=lambda: now,
        )

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_CLASSIFY].status == "skipped"
    assert rows[STAGE_FACTS].status == "skipped"
    assert rows[STAGE_FACTS].skip_reason == SKIP_DEPENDENCY_SKIPPED

    # An explicit reprocess of parse now produces a real batch, so the stages that
    # read it become runnable again.
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p2", now=now)
        assert claim is not None
        execute_claimed_stage(
            session, config, _real_parse_registry(), claim, clock=lambda: now
        )

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "completed"
    # The classification was reopened by the new current batch.
    assert rows[STAGE_CLASSIFY].status == "pending"
    assert rows[STAGE_FACTS].status == "skipped"
    assert rows[STAGE_FACTS].skip_reason == SKIP_DEPENDENCY_SKIPPED


def test_downstream_wait_reason_clears_when_parse_recovers(engine):
    """A visible waiting reason must not outlive the failure it recorded.

    A failed parse leaves the index stage pending with an explicit reason. Once a
    later attempt really produces a current batch, that reason has to be cleared
    instead of leaving the stage looking permanently blocked by a gap that no
    longer exists.
    """

    from stocks.processing.processors import build_parse_processor

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 22, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            # The stored bytes do not match the registered hash, so this attempt
            # really fails without producing a current batch.
            StageRegistry(parse=build_parse_processor(read_bytes=lambda c, d: b"corrupt")),
            claim,
            clock=lambda: now,
        )
        assert execution.status == "failed"

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_INDEX].status == "pending"
    assert rows[STAGE_INDEX].wait_reason == WAIT_PARSE_UNAVAILABLE

    # An explicit reprocess that succeeds clears the recorded wait reason.
    with Session(engine) as session:
        request_reprocess(session, document.id, STAGE_PARSE, now=now)
    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p2", now=now)
        assert claim is not None
        execute_claimed_stage(
            session, config, _real_parse_registry(), claim, clock=lambda: now
        )

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_PARSE].status == "completed"
    assert rows[STAGE_INDEX].status == "pending"
    assert rows[STAGE_INDEX].wait_reason is None
    assert rows[STAGE_INDEX].input_batch_id == rows[STAGE_PARSE].input_batch_id


def test_unsupported_parse_reason_distinguishes_from_a_repairable_failure(engine):
    """The waiting reason records the persisted parse status, not an error string.

    An unsupported MIME type is a different gap from a transient failure, and the
    stage row has to say which one the current batch actually recorded.
    """

    from stocks.processing.processors import build_parse_processor

    pdf_bytes = b"%PDF-1.7 not supported by this parser"
    config = make_config()
    document = ingest(
        engine, config, make_item(payload=pdf_bytes, content_type="application/pdf")
    )
    now = datetime(2026, 10, 21, 23, tzinfo=UTC)

    with Session(engine) as session:
        claim = claim_stage(session, STAGE_PARSE, owner_token="p", now=now)
        execution = execute_claimed_stage(
            session,
            config,
            StageRegistry(parse=build_parse_processor(read_bytes=lambda c, d: pdf_bytes)),
            claim,
            clock=lambda: now,
        )
        assert execution.status == "failed"

    rows = stage_rows(engine, document.id)
    assert rows[STAGE_INDEX].status == "pending"
    assert rows[STAGE_INDEX].wait_reason == WAIT_PARSE_UNSUPPORTED
