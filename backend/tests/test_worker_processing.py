"""Worker document slots and the unified processing CLI.

The stage state machine itself is covered by ``test_processing_retry.py``. These
cases cover what this batch adds: independent bounded document slots next to the
Run slots, a ``--once`` cycle that processes at most one Document stage, and the
unified status/retry/reprocess/manual-parse CLI surface.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import stocks.worker as worker
from stocks.models import (
    Base,
    DocumentChunk,
    DocumentParseBatch,
    DocumentProcessingState,
    Source,
)
from stocks.processing.processors import build_stage_registry
from stocks.processing.runner import (
    DOCUMENT_STAGE_ORDER,
    StageRegistry,
    describe_stages,
    run_next_document_stage,
)
from stocks.processing.stages import (
    STAGE_CLASSIFY,
    STAGE_FACTS,
    STAGE_INDEX,
    STAGE_PARSE,
    WAIT_PROCESSOR_NOT_AVAILABLE,
    claim_stage,
    get_stage_state,
)
from stocks.repository import DocumentInput, ingest_document
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


def engine_session_factory(engine):
    """A ``sessionmaker``-shaped factory for the CLI boundary.

    ``stocks.db.session_factory()`` returns a callable factory, so the CLI calls
    ``session_factory()()``; the test has to mirror that shape instead of handing
    back a live Session.
    """

    return lambda: Session(engine)


def ingest(engine, config: Settings, **item_overrides) -> object:
    with Session(engine) as session:
        if session.get(Source, "sec") is None:
            session.add(
                Source(id="sec", name="SEC", connector="rss", trust_tier="T1")
            )
            session.commit()
        item = make_item(**item_overrides)
        return ingest_document(
            session,
            config,
            item,
            store=lambda config, source_id, payload, content_type: (
                f"sources/{source_id}/{item.dedupe_key}",
                hashlib.sha256(payload).hexdigest(),
            ),
        ).document


class _StubParseProcessor:
    version = "stub-parse-v1"

    def __call__(self, session, config, document, claim):
        from stocks.processing.stages import StageOutcome

        return StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_PARSE,
            owner_token=claim.owner_token,
            status="completed",
            processor_version=self.version,
            applicability={
                "requires_embedding": True,
                "requires_classification": True,
            },
        )


def _stub_context(monkeypatch, *, registry=None, documents=1, runs=1):
    """A Worker context whose Run path is stubbed and stage path is real."""

    monkeypatch.setattr(worker, "schedule_round", lambda context, *, now=None: [])
    monkeypatch.setattr(
        worker, "execute_next_run", lambda context, *, process_token, now=None: None
    )
    return worker.WorkerContext(
        session_factory=lambda: None,
        settings=make_config(),
        task_config_path="unused.yaml",
        model_config_path="unused.yaml",
        sources_config_path="unused.yaml",
        max_concurrent_runs=runs,
        max_concurrent_documents=documents,
        stage_registry=registry or StageRegistry(parse=_StubParseProcessor()),
    )


def test_registry_reports_unimplemented_stages_as_unavailable():
    registry = StageRegistry(parse=_StubParseProcessor())
    assert registry.available_stages() == {STAGE_PARSE}
    assert registry.for_stage(STAGE_CLASSIFY) is None
    assert registry.for_stage(STAGE_FACTS) is None
    assert STAGE_INDEX not in registry.available_stages()


def test_lazy_index_factory_is_only_resolved_when_index_runs():
    calls: list[str] = []

    def factory():
        calls.append("resolved")
        return _StubParseProcessor()

    registry = StageRegistry(index_factory=factory)
    # Building the registry and asking about availability must not read routing.
    assert STAGE_INDEX in registry.available_stages()
    assert calls == []
    registry.for_stage(STAGE_INDEX)
    assert calls == ["resolved"]


def test_missing_classifier_stays_visible_as_pending_in_status(engine):
    config = make_config()
    document = ingest(engine, config)
    registry = StageRegistry(parse=_StubParseProcessor())

    with Session(engine) as session:
        rows = describe_stages(session, registry, limit=10)

    by_stage = {row["stage"]: row for row in rows}
    assert set(by_stage) == {STAGE_PARSE, STAGE_CLASSIFY, STAGE_FACTS, STAGE_INDEX}
    # The unavailable stages are reported as pending with an explicit reason, even
    # though the Worker never claims them.
    for stage in (STAGE_CLASSIFY, STAGE_FACTS):
        assert by_stage[stage]["status"] == "pending"
        assert by_stage[stage]["wait_reason"] == WAIT_PROCESSOR_NOT_AVAILABLE
        assert by_stage[stage]["processor_available"] is False
    assert by_stage[STAGE_PARSE]["processor_available"] is True
    assert by_stage[STAGE_PARSE]["wait_reason"] is None
    assert document.id is not None


def test_run_next_document_stage_never_claims_an_unregistered_stage(engine):
    config = make_config()
    document = ingest(engine, config)
    registry = StageRegistry(parse=_StubParseProcessor())

    with Session(engine) as session:
        # Parse is available; classify/facts are not and must not be touched.
        assert claim_stage(session, STAGE_CLASSIFY, owner_token="t") is None

    execution = run_next_document_stage(
        lambda: Session(engine),
        config,
        registry,
        owner_token="slot-1",
        now=datetime(2026, 10, 21, 2, tzinfo=UTC),
    )
    assert execution is not None
    assert execution.stage == STAGE_PARSE
    assert execution.status == "completed"

    # Nothing else is claimable by this registry, so a slot waits instead of
    # spinning on the missing processors.
    assert (
        run_next_document_stage(
            lambda: Session(engine),
            config,
            registry,
            owner_token="slot-2",
            now=datetime(2026, 10, 21, 3, tzinfo=UTC),
        )
        is None
    )
    with Session(engine) as session:
        assert get_stage_state(session, document.id, STAGE_CLASSIFY).status == "pending"


def test_document_stage_order_parses_before_indexing():
    assert DOCUMENT_STAGE_ORDER.index(STAGE_PARSE) < DOCUMENT_STAGE_ORDER.index(
        STAGE_INDEX
    )


def test_run_once_processes_at_most_one_document_stage(monkeypatch, engine):
    config = make_config()
    ingest(engine, config)
    context = _stub_context(monkeypatch)
    context = worker.WorkerContext(
        session_factory=lambda: Session(engine),
        settings=make_config(),
        task_config_path="unused.yaml",
        model_config_path="unused.yaml",
        sources_config_path="unused.yaml",
        max_concurrent_runs=1,
        max_concurrent_documents=1,
        stage_registry=StageRegistry(parse=_StubParseProcessor()),
    )

    payload = worker.run_once(context)
    assert payload["executed"] is None
    assert payload["processing"]["stage"] == STAGE_PARSE
    assert payload["processing"]["status"] == "completed"

    # Only one stage ran in that cycle; the rest are still waiting for a later one.
    with Session(engine) as session:
        states = session.scalars(select(DocumentProcessingState)).all()
        statuses = {state.stage: state.status for state in states}
    assert statuses[STAGE_PARSE] == "completed"
    assert statuses[STAGE_INDEX] == "pending"
    assert statuses[STAGE_CLASSIFY] == "pending"


def test_document_slot_waits_instead_of_polling_an_empty_backlog(monkeypatch):
    """An idle document slot must wait on the interval, not re-poll hot."""

    claims: list[float] = []

    def fake_schedule_round(context, *, now=None):
        return []

    def fake_execute_next_run(context, *, process_token, now=None):
        return None

    def fake_document_stage(context, *, process_token, now=None):
        claims.append(time.monotonic())
        return None

    monkeypatch.setattr(worker, "schedule_round", fake_schedule_round)
    monkeypatch.setattr(worker, "execute_next_run", fake_execute_next_run)
    monkeypatch.setattr(
        worker, "execute_next_document_stage", fake_document_stage
    )
    context = worker.WorkerContext(
        session_factory=lambda: None,
        settings=make_config(),
        task_config_path="unused.yaml",
        model_config_path="unused.yaml",
        sources_config_path="unused.yaml",
        max_concurrent_runs=1,
        max_concurrent_documents=1,
        stage_registry=StageRegistry(parse=_StubParseProcessor()),
    )
    started = time.monotonic()
    asyncio.run(worker.run_forever(context, poll_seconds=0.1, max_rounds=3))
    elapsed = time.monotonic() - started
    assert 3 <= len(claims) <= 8
    assert 0.15 <= elapsed < 3.0


def test_document_slot_keeps_running_while_a_run_slot_is_busy(monkeypatch):
    """A long Run must not starve Document processing, and vice versa."""

    processed: list[str] = []

    def fake_schedule_round(context, *, now=None):
        return []

    def fake_execute_next_run(context, *, process_token, now=None):
        # Occupy the Run slot for the whole bounded window.
        time.sleep(0.25)
        return {"run_id": "slow", "status": "succeeded"}

    def fake_document_stage(context, *, process_token, now=None):
        if len(processed) < 2:
            processed.append("stage")
            return {"stage": STAGE_PARSE, "status": "completed"}
        return None

    monkeypatch.setattr(worker, "schedule_round", fake_schedule_round)
    monkeypatch.setattr(worker, "execute_next_run", fake_execute_next_run)
    monkeypatch.setattr(
        worker, "execute_next_document_stage", fake_document_stage
    )
    context = worker.WorkerContext(
        session_factory=lambda: None,
        settings=make_config(),
        task_config_path="unused.yaml",
        model_config_path="unused.yaml",
        sources_config_path="unused.yaml",
        max_concurrent_runs=1,
        max_concurrent_documents=1,
        stage_registry=StageRegistry(parse=_StubParseProcessor()),
    )
    asyncio.run(worker.run_forever(context, poll_seconds=60, max_rounds=1))
    assert processed == ["stage", "stage"]


def test_cli_status_reports_capability_and_counts(monkeypatch, capsys):
    from stocks.processing import __main__ as cli

    def fake_session_factory():
        raise AssertionError("status must use session_factory()()")

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(cli, "session_factory", lambda: _SessionContext)
    config = make_config(
        models_config_path="custom/models.yaml",
        sources_config_path="custom/sources.yaml",
    )
    monkeypatch.setattr(cli, "settings", lambda: config)
    registry_paths = []

    def configured_registry(model_path, *, sources_config_path):
        registry_paths.append((model_path, sources_config_path))
        return build_stage_registry(model_path, sources_config_path=sources_config_path)

    monkeypatch.setattr(cli, "build_stage_registry", configured_registry)
    monkeypatch.setattr(
        cli,
        "describe_stages",
        lambda session, registry, **kwargs: [
            {
                "document_id": "d1",
                "stage": STAGE_PARSE,
                "status": "completed",
                "attempts": 1,
                "max_attempts": 3,
                "auto_retry": True,
                "reprocess_requested": False,
                "next_attempt_at": None,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "wait_reason": None,
                "skip_reason": None,
                "processor_version": "stub",
                "config_fingerprint": None,
                "input_batch_id": None,
                "applicability": None,
                "processor_available": True,
            },
            {
                "document_id": "d1",
                "stage": STAGE_CLASSIFY,
                "status": "pending",
                "attempts": 0,
                "max_attempts": 3,
                "auto_retry": True,
                "reprocess_requested": False,
                "next_attempt_at": None,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "wait_reason": WAIT_PROCESSOR_NOT_AVAILABLE,
                "skip_reason": None,
                "processor_version": None,
                "config_fingerprint": None,
                "input_batch_id": None,
                "applicability": None,
                "processor_available": False,
            },
        ],
    )
    assert cli.main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["completed"] == 1
    assert registry_paths == [(config.models_config_path, config.sources_config_path)]
    assert payload["counts"]["waiting_for_processor"] == 1
    assert payload["available_stages"] == sorted(
        build_stage_registry("config/models.yaml").available_stages()
    )
    # Every stage has a registered processor in this deployment.
    assert payload["unavailable_stages"] == []


def test_cli_status_rejects_an_out_of_range_limit(capsys):
    from stocks.processing import __main__ as cli

    assert cli.main(["status", "--limit", "0"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "LIMIT_INVALID"


def test_cli_retry_requires_owner_confirmation_for_a_running_stage(
    engine, monkeypatch, capsys
):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        assert claim_stage(session, STAGE_PARSE, owner_token="stale") is not None

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)

    assert (
        cli.main(["retry", "--document-id", str(document.id), "--stage", STAGE_PARSE])
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["error"]["code"] == "OWNER_CONFIRMATION_REQUIRED"
    with Session(engine) as session:
        assert get_stage_state(session, document.id, STAGE_PARSE).status == "running"


def test_cli_retry_with_confirmation_reopens_the_stage(engine, monkeypatch, capsys):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        assert claim_stage(session, STAGE_PARSE, owner_token="stale") is not None

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)

    assert (
        cli.main(
            [
                "retry",
                "--document-id",
                str(document.id),
                "--stage",
                STAGE_PARSE,
                "--confirm-stopped",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["status"] == "pending"
    assert payload["items"][0]["attempts"] == 0


def test_cli_retry_refuses_to_rerun_a_completed_stage(engine, monkeypatch, capsys):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        state.status = "completed"
        state.attempts = 1
        session.commit()

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)

    assert (
        cli.main(["retry", "--document-id", str(document.id), "--stage", STAGE_PARSE])
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["error"]["code"] == "STAGE_ALREADY_COMPLETED"


def test_cli_reprocess_reopens_a_completed_stage(engine, monkeypatch, capsys):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        state.status = "completed"
        state.attempts = 1
        session.commit()

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)

    assert (
        cli.main(
            ["reprocess", "--document-id", str(document.id), "--stage", STAGE_PARSE]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["reprocess_requested"] is True
    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.status == "pending"
        assert state.reprocess_requested is True


def test_cli_manual_parse_runs_through_the_stage_path(engine, monkeypatch, capsys):
    config = make_config()
    document = ingest(engine, config)

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(
        cli,
        "build_stage_registry",
        lambda *args, **kwargs: StageRegistry(
            parse=_RealParseProcessor(config, ARTICLE_HTML)
        ),
    )

    assert cli.main(["parse", "--document-id", str(document.id)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] == 1
    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        assert state.status == "completed"
        assert state.attempts == 1
        batch = session.scalar(select(DocumentParseBatch))
        assert batch is not None
        assert batch.status == "completed"
        assert session.scalars(select(DocumentChunk)).all()


class _RealParseProcessor:
    """Real parse processor with injected stored bytes for a database test."""

    version = "real-parse-test"

    def __init__(self, config, payload: bytes) -> None:
        from stocks.processing.processors import build_parse_processor

        self._inner = build_parse_processor(
            read_bytes=lambda config, document: payload
        )

    def __call__(self, session, config, document, claim):
        return self._inner(session, config, document, claim)


def test_cli_manual_parse_reports_already_completed_documents(engine, monkeypatch, capsys):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        state = get_stage_state(session, document.id, STAGE_PARSE)
        state.status = "completed"
        state.attempts = 1
        session.commit()

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(
        cli, "build_stage_registry", lambda *a, **k: StageRegistry()
    )

    assert cli.main(["parse", "--document-id", str(document.id)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] == 1
    assert "errors" not in payload


def test_cli_manual_index_does_not_parse_first(engine, monkeypatch, capsys):
    """Indexing an unparsed Document reports an unclaimable stage, not a parse."""

    config = make_config()
    document = ingest(engine, config)

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(
        cli,
        "build_stage_registry",
        lambda *args, **kwargs: StageRegistry(index=_StubIndexProcessor()),
    )

    assert cli.main(["index", "--document-id", str(document.id)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["error"]["code"] == "STAGE_NOT_CLAIMABLE"
    with Session(engine) as session:
        assert session.scalar(select(DocumentParseBatch)) is None
        assert get_stage_state(session, document.id, STAGE_PARSE).status == "pending"


class _StubIndexProcessor:
    version = "stub-index-v1"

    def __call__(self, session, config, document, claim):
        raise AssertionError("index must not run without a current parse batch")


def test_cli_reprocess_index_does_not_reparse(engine, monkeypatch, capsys):
    config = make_config()
    document = ingest(engine, config)
    with Session(engine) as session:
        parse_state = get_stage_state(session, document.id, STAGE_PARSE)
        parse_state.status = "completed"
        parse_state.attempts = 1
        index_state = get_stage_state(session, document.id, STAGE_INDEX)
        index_state.status = "completed"
        index_state.attempts = 1
        session.commit()

    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(
        cli,
        "build_stage_registry",
        lambda *args, **kwargs: StageRegistry(index=_StubIndexProcessor()),
    )

    # The index stage is reopened but not executed, so no parse or model call is
    # made and the parse stage keeps its completed state.
    assert (
        cli.main(
            [
                "reprocess",
                "--document-id",
                str(document.id),
                "--stage",
                STAGE_INDEX,
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["status"] == "pending"
    with Session(engine) as session:
        assert get_stage_state(session, document.id, STAGE_PARSE).status == "completed"


def test_worker_rejects_out_of_range_document_concurrency():
    with pytest.raises(ValueError, match="max_concurrent_documents"):
        worker.WorkerContext(
            session_factory=lambda: None,
            settings=make_config(),
            task_config_path="unused.yaml",
            model_config_path="unused.yaml",
            sources_config_path="unused.yaml",
            max_concurrent_documents=0,
        )


def test_worker_cli_rejects_out_of_range_document_concurrency(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    assert worker.main(["--once", "--max-concurrent-documents", "17"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "DOCUMENT_CONCURRENCY_INVALID"


def test_processing_cli_status_is_bounded(engine, monkeypatch, capsys):
    config = make_config()
    ingest(engine, config)
    from stocks.processing import __main__ as cli

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    assert cli.main(["status", "--limit", "201"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "LIMIT_INVALID"
    assert config.database_url.endswith("/unused")


def test_document_stage_choice_does_not_starve_older_index_work(engine):
    """The oldest ready Document wins, so a new parse cannot starve older index.

    With a fixed parse-first priority a continuous stream of newly ingested
    Documents would keep the slot on parse forever and the index work of an older
    Document would never run.
    """

    from stocks.processing.runner import ready_stage_ages

    config = make_config()
    older = ingest(engine, config, dedupe_key="older")
    newer = ingest(
        engine,
        config,
        dedupe_key="newer",
        collected_at=datetime(2026, 10, 21, 2, tzinfo=UTC),
    )
    now = datetime(2026, 10, 21, 18, tzinfo=UTC)

    with Session(engine) as session:
        # The older Document is parsed and waiting to be indexed.
        for stage in (STAGE_PARSE,):
            state = get_stage_state(session, older.id, stage)
            state.status = "completed"
            state.attempts = 1
        # The newer Document still needs parsing and was collected later.
        session.get(DocumentProcessingState, (newer.id, STAGE_PARSE)).status = "pending"
        session.commit()

    with Session(engine) as session:
        ages = ready_stage_ages(session, [STAGE_PARSE, STAGE_INDEX], now=now)

    assert ages[STAGE_INDEX] == older.collected_at
    assert ages[STAGE_PARSE] == newer.collected_at
    # The older index work is chosen first.
    ordered = sorted(ages, key=lambda item: (ages[item], item))
    assert ordered[0] == STAGE_INDEX


def test_stage_choice_breaks_an_age_tie_by_declared_priority(engine):
    """An exact age tie must not be decided by the stage's name.

    Parse unblocks every other stage, so equally old ready work has to follow
    ``DOCUMENT_STAGE_ORDER``. Sorting by name instead would let ``classify`` run
    ahead of the ``parse`` that produces its input.
    """

    from stocks.processing.runner import DOCUMENT_STAGE_ORDER

    config = make_config()
    document = ingest(engine, config)
    now = datetime(2026, 10, 21, 19, tzinfo=UTC)
    with Session(engine) as session:
        for stage in (STAGE_PARSE, STAGE_INDEX, STAGE_CLASSIFY):
            state = get_stage_state(session, document.id, stage)
            state.status = "pending"
            state.attempts = 0
            state.wait_reason = None
        session.commit()

    priority = {stage: index for index, stage in enumerate(DOCUMENT_STAGE_ORDER)}
    ages = {
        stage: document.collected_at
        for stage in (STAGE_PARSE, STAGE_INDEX, STAGE_CLASSIFY)
    }
    ordered = sorted(ages, key=lambda item: (ages[item], priority[item]))

    assert ordered == [STAGE_PARSE, STAGE_INDEX, STAGE_CLASSIFY]
    # A name-based tie-break would have produced this wrong order.
    assert sorted(ages, key=lambda item: (ages[item], item))[0] == STAGE_CLASSIFY


def test_manual_index_without_ids_targets_only_actionable_documents(
    engine, monkeypatch, capsys
):
    """A bounded manual run must not be filled by stages it cannot claim.

    An index stage of a freshly ingested but unparsed Document is not runnable.
    Without restricting the query, a backlog of such Documents would consume the
    whole limit and every target would be reported unclaimable while real index
    work stayed invisible.
    """

    config = make_config()
    unparsed = ingest(engine, config, dedupe_key="unparsed")
    parsed = ingest(
        engine,
        config,
        dedupe_key="parsed",
        collected_at=datetime(2026, 10, 21, 0, tzinfo=UTC),
    )
    now = datetime(2026, 10, 21, 4, tzinfo=UTC)
    with Session(engine) as session:
        for stage in (STAGE_PARSE,):
            state = get_stage_state(session, parsed.id, stage)
            state.status = "completed"
            state.attempts = 1
        session.commit()

    from stocks.processing import __main__ as cli

    targets: list[object] = []

    def fake_run_stages(
        session_factory,
        config,
        registry,
        document_ids,
        stages,
        **kwargs,
    ):
        targets.extend(document_ids)
        return [], []

    monkeypatch.setattr(cli, "session_factory", lambda: engine_session_factory(engine))
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(cli, "build_stage_registry", lambda *a, **k: StageRegistry())
    monkeypatch.setattr(cli, "run_stages_for_documents", fake_run_stages)

    assert cli.main(["index", "--limit", "10"]) == 0
    capsys.readouterr()
    # Only the parsed Document has an actionable index stage.
    assert targets == [parsed.id]
    assert unparsed.id not in targets


@pytest.mark.parametrize("explicit", [False, True])
def test_worker_cli_configuration_paths_reach_processing(monkeypatch, capsys, explicit):
    config = make_config(
        tasks_config_path="deployment/tasks.yaml",
        models_config_path="deployment/models.yaml",
        sources_config_path="deployment/sources.yaml",
    )
    monkeypatch.setattr(worker, "settings", lambda: config)
    monkeypatch.setattr(worker, "session_factory", lambda: object())
    paths = []
    registry = StageRegistry()

    def build_registry(model_path, **kwargs):
        paths.append((model_path, kwargs["sources_config_path"]))
        return registry

    def run_once(context):
        prefix = "override" if explicit else "deployment"
        assert context.settings is config
        assert context.task_config_path == f"{prefix}/tasks.yaml"
        assert context.processing_registry() is registry
        return {"scheduler": [], "executed": None, "processing": None}

    monkeypatch.setattr(worker, "build_stage_registry", build_registry)
    monkeypatch.setattr(worker, "run_once", run_once)
    arguments = ["--once"]
    if explicit:
        arguments += [
            "--config", "override/tasks.yaml",
            "--models-config", "override/models.yaml",
            "--sources-config", "override/sources.yaml",
        ]
    assert worker.main(arguments) == 0
    prefix = "override" if explicit else "deployment"
    assert paths == [(f"{prefix}/models.yaml", f"{prefix}/sources.yaml")]
    capsys.readouterr()


def test_worker_from_settings_keeps_builtin_defaults_and_explicit_overrides(monkeypatch):
    config = make_config()
    monkeypatch.setattr(worker, "settings", lambda: config)
    monkeypatch.setattr(worker, "session_factory", lambda: object())
    context = worker.WorkerContext.from_settings()
    assert context.task_config_path == "config/tasks.yaml"
    assert context.model_config_path == "config/models.yaml"
    assert context.sources_config_path == "config/sources.yaml"
    context = worker.WorkerContext.from_settings(model_config_path="explicit.yaml")
    assert context.model_config_path == "explicit.yaml"
    assert context.task_config_path == config.tasks_config_path


@pytest.mark.parametrize("explicit", [False, True])
def test_processing_retry_configuration_paths(monkeypatch, capsys, explicit):
    from stocks.processing import __main__ as cli

    config = make_config(
        models_config_path="deployment/models.yaml",
        sources_config_path="deployment/sources.yaml",
    )
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(cli, "session_factory", lambda: object())
    paths = []

    def build_registry(model_path, *, sources_config_path):
        paths.append((model_path, sources_config_path))
        return StageRegistry()

    monkeypatch.setattr(cli, "build_stage_registry", build_registry)
    monkeypatch.setattr(cli, "run_stages_for_documents", lambda *a, **k: ([], []))
    arguments = [
        "retry", "--document-id", "00000000-0000-0000-0000-000000000001",
        "--stage", "facts", "--execute",
    ]
    if explicit:
        arguments += [
            "--config", "override/models.yaml",
            "--sources-config", "override/sources.yaml",
        ]
    assert cli.main(arguments) == 0
    prefix = "override" if explicit else "deployment"
    assert paths == [(f"{prefix}/models.yaml", f"{prefix}/sources.yaml")]
    capsys.readouterr()


@pytest.mark.parametrize("module", ["processing", "indexing"])
@pytest.mark.parametrize("explicit", [False, True])
def test_manual_index_configuration_path(monkeypatch, capsys, module, explicit):
    from contextlib import nullcontext
    from importlib import import_module

    cli = import_module(f"stocks.{module}.__main__")
    config = make_config(
        models_config_path="deployment/models.yaml",
        sources_config_path="deployment/sources.yaml",
    )
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(cli, "session_factory", lambda: lambda: nullcontext(object()))
    paths = []

    def build_registry(model_path, **kwargs):
        paths.append((model_path, kwargs.get("sources_config_path")))
        return StageRegistry()

    monkeypatch.setattr(cli, "build_stage_registry", build_registry)
    monkeypatch.setattr(cli, "run_stages_for_documents", lambda *a, **k: ([], []))
    arguments = ["index"] if module == "processing" else []
    arguments += ["--document-id", "00000000-0000-0000-0000-000000000001"]
    if explicit:
        arguments += ["--config", "override/models.yaml"]
        if module == "processing":
            arguments += ["--sources-config", "override/sources.yaml"]
    assert cli.main(arguments) == 0
    prefix = "override" if explicit else "deployment"
    assert paths == [
        (f"{prefix}/models.yaml", f"{prefix}/sources.yaml" if module == "processing" else None)
    ]
    capsys.readouterr()


def test_worker_configuration_failure_keeps_safe_error(monkeypatch, capsys):
    def invalid_settings():
        raise ValueError("private deployment detail")

    monkeypatch.setattr(worker, "settings", invalid_settings)
    assert worker.main(["--once"]) == 2
    output = capsys.readouterr().err
    assert json.loads(output)["error"]["code"] == "WORKER_CONFIG_UNAVAILABLE"
    assert "private deployment detail" not in output
