"""Scheduler, Worker and Run recovery behaviour.

Database-backed cases need a real PostgreSQL instance (``TEST_DATABASE_URL``),
matching the existing execution tests; deterministic time is passed in explicitly
so the cron/timezone and interval rules are checked without sleeping. The
``run_forever`` cases use stubbed scheduling and execution so the polling,
slot-refill and drain behaviour is observed on its own without a database.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import stocks.worker as worker
from stocks.execution.config import (
    ExecutionConfigError,
    load_task_configuration,
)
from stocks.execution.scheduler import (
    advance_due,
    next_due_at,
    run_scheduler_round,
)
from stocks.execution.service import (
    create_or_get_run,
    end_run,
    retry_run,
)
from stocks.models import (
    Base,
    EvidencePackage,
    TaskDefinition,
    TaskRun,
    TaskSchedule,
    TaskStepResult,
)
from stocks.settings import Settings


@pytest.fixture(autouse=True)
def model_env(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-key-never-persist")


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL scheduling tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


COLLECTION_TASK = """
tasks:
  - id: collect
    name: Collect
    type: collection
    trigger: {kind: schedule, every: 15m}
    sources: [sec]
    steps:
      - id: collect
        collect_sources: {kind: collect_sources}
"""

CRON_TASK = """
tasks:
  - id: collect
    name: Collect
    type: collection
    trigger: {kind: schedule, cron: "0 8,20 * * *", timezone: America/New_York}
    sources: [sec]
    steps:
      - id: collect
        collect_sources: {kind: collect_sources}
"""


def write_tasks(tmp_path, body: str) -> str:
    path = tmp_path / "tasks.yaml"
    path.write_text(body + "\nagents: []\n", encoding="utf-8")
    return str(path)


def test_config_accepts_every_and_cron_schedules(tmp_path):
    every = load_task_configuration(write_tasks(tmp_path, COLLECTION_TASK))
    trigger = every.tasks[0].trigger
    assert trigger.kind == "schedule"
    assert trigger.every_seconds == 900
    assert trigger.timezone is None
    assert every.tasks[0].type == "collection"
    assert every.tasks[0].run_scope == {"source_ids": ["sec"]}

    cron_path = tmp_path / "cron.yaml"
    cron_path.write_text(CRON_TASK + "\nagents: []\n", encoding="utf-8")
    cron = load_task_configuration(cron_path)
    assert cron.tasks[0].trigger.cron == "0 8,20 * * *"
    assert cron.tasks[0].trigger.timezone == "America/New_York"
    assert cron.tasks[0].trigger.every_seconds is None


@pytest.mark.parametrize(
    "trigger",
    [
        "{kind: schedule}",
        "{kind: schedule, every: 15m, cron: '0 8 * * *', timezone: UTC}",
        "{kind: schedule, cron: '0 8 * * *'}",
        "{kind: schedule, cron: '0 8 * *', timezone: UTC}",
        "{kind: schedule, cron: '* * * * *', timezone: Not/AZone}",
        "{kind: schedule, every: 0m}",
        "{kind: schedule, every: 15x}",
        "{kind: schedule, every: 10s}",
        "{kind: schedule, every: 15m, timezone: UTC}",
        "{kind: manual, every: 15m}",
        "sometimes",
    ],
)
def test_config_rejects_invalid_triggers(tmp_path, trigger):
    body = COLLECTION_TASK.replace(
        "{kind: schedule, every: 15m}", trigger
    )
    with pytest.raises(ExecutionConfigError):
        load_task_configuration(write_tasks(tmp_path, body))


def test_config_rejects_collection_task_shape_errors(tmp_path):
    for body in (
        COLLECTION_TASK.replace("    sources: [sec]\n", ""),
        COLLECTION_TASK.replace(
            "    sources: [sec]\n", "    sources: [sec, sec]\n"
        ),
        COLLECTION_TASK.replace(
            "    sources: [sec]\n", "    sources: [sec]\n    scope: {topics: [x]}\n"
        ),
        COLLECTION_TASK.replace(
            "    sources: [sec]\n", "    sources: [sec]\n    objective: text\n"
        ),
        COLLECTION_TASK.replace(
            "      - id: collect\n"
            "        collect_sources: {kind: collect_sources}\n",
            "      - id: collect\n"
            "        select_evidence: {kind: select_evidence}\n",
        ),
    ):
        with pytest.raises(ExecutionConfigError):
            load_task_configuration(write_tasks(tmp_path, body))


def test_next_due_uses_fixed_interval_anchor(tmp_path):
    trigger = load_task_configuration(
        write_tasks(tmp_path, COLLECTION_TASK)
    ).tasks[0].trigger
    anchor = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    assert next_due_at(trigger, anchor) == anchor + timedelta(minutes=15)


def test_advance_due_collapses_missed_intervals(tmp_path):
    trigger = load_task_configuration(
        write_tasks(tmp_path, COLLECTION_TASK)
    ).tasks[0].trigger
    due = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    # A Worker that was down for 3.5 intervals ends up one interval past now.
    now = due + timedelta(minutes=52, seconds=30)
    advanced = advance_due(trigger, due, now)
    assert advanced == due + timedelta(minutes=60)
    assert advanced > now


def test_cron_uses_task_timezone(tmp_path):
    path = tmp_path / "cron.yaml"
    path.write_text(CRON_TASK + "\nagents: []\n", encoding="utf-8")
    trigger = load_task_configuration(path).tasks[0].trigger
    after = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)
    # 08:00 New York in October is 12:00 UTC, so the next 08:00/20:00 local
    # trigger after 11:00 UTC is 12:00 UTC.
    assert next_due_at(trigger, after) == datetime(
        2026, 10, 21, 12, 0, tzinfo=UTC
    )
    after_local_evening = datetime(2026, 10, 22, 0, 30, tzinfo=UTC)
    assert next_due_at(trigger, after_local_evening) == datetime(
        2026, 10, 22, 12, 0, tzinfo=UTC
    )


def test_cron_skips_spring_forward_gap(tmp_path):
    """2026-03-08 02:30 does not exist in New York, so that day never fires.

    On the spring-forward Sunday the clock jumps 02:00 EST -> 03:00 EDT, so the
    schedule must skip the missing wall-clock time and take the next real one
    (2026-03-09 02:30 EDT = 06:30 UTC) instead of inventing an instant.
    """

    path = tmp_path / "dst-spring.yaml"
    path.write_text(_dst_task(cron="30 2 * * *"), encoding="utf-8")
    trigger = load_task_configuration(path).tasks[0].trigger
    # 2026-03-08 05:00 UTC is 00:00 EST, before the missing 02:30.
    before_gap = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    assert next_due_at(trigger, before_gap) == datetime(
        2026, 3, 9, 6, 30, tzinfo=UTC
    )


def _dst_task(*, cron: str) -> str:
    return f"""
tasks:
  - id: collect
    name: Collect
    type: collection
    trigger: {{kind: schedule, cron: "{cron}", timezone: America/New_York}}
    sources: [sec]
    steps:
      - id: collect
        collect_sources: {{kind: collect_sources}}
agents: []
"""


def test_cron_fires_repeated_wall_clock_time_only_once(tmp_path):
    """Cover the fall-back overlap itself: 2026-11-01 01:30 New York occurs twice.

    The clock goes 01:59:59 EDT -> 01:00:00 EST, so 01:30 is a real wall-clock
    time with two UTC instants: 05:30 (EDT, UTC-4) and 06:30 (EST, UTC-5). The
    schedule fires at the first occurrence only and then advances a full day, so
    the repeated hour cannot start the same Task twice.
    """

    path = tmp_path / "dst-fall.yaml"
    path.write_text(_dst_task(cron="30 1 * * *"), encoding="utf-8")
    trigger = load_task_configuration(path).tasks[0].trigger
    before_overlap = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    first = next_due_at(trigger, before_overlap)
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    # Once the first occurrence has been used, the second occurrence of the same
    # wall-clock time is not a trigger; the next one is the following day.
    assert next_due_at(trigger, first) == datetime(
        2026, 11, 2, 6, 30, tzinfo=UTC
    )
    # The same repeated local time read with fold=1 is the later instant
    # (06:30 UTC). Starting from that exact instant must not fire again at
    # 06:30 on 2026-11-01, otherwise the Task would run twice in the overlap.
    second_occurrence = datetime(
        2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1
    )
    assert second_occurrence.astimezone(UTC) == datetime(
        2026, 11, 1, 6, 30, tzinfo=UTC
    )
    assert next_due_at(trigger, second_occurrence) == datetime(
        2026, 11, 2, 6, 30, tzinfo=UTC
    )


def test_cron_overlap_day_fires_each_distinct_instant_once(tmp_path):
    """A schedule naming two repeated times fires both on their first pass only.

    On 2026-11-01 both 01:00 and 01:30 New York occur twice in wall-clock time, so
    a naive reading would produce four triggers: 05:00/05:30 UTC (EDT) and
    06:00/06:30 UTC (EST). The overlap must collapse to the two first-pass instants
    and continue with 2026-11-02 in standard time.
    """

    path = tmp_path / "dst-dual.yaml"
    path.write_text(_dst_task(cron="0,30 1 * * *"), encoding="utf-8")
    trigger = load_task_configuration(path).tasks[0].trigger
    fired = []
    cursor: datetime = datetime(2026, 11, 1, 3, 30, tzinfo=UTC)
    for _ in range(4):
        cursor = next_due_at(trigger, cursor)
        fired.append(cursor)
    assert fired == [
        datetime(2026, 11, 1, 5, 0, tzinfo=UTC),
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 6, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 6, 30, tzinfo=UTC),
    ]


def _scheduler(tmp_path, engine, body: str, *, now: datetime):
    path = write_tasks(tmp_path, body)
    configuration = load_task_configuration(path)
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=now,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    return configuration, outcomes


def test_first_round_registers_and_does_not_trigger(tmp_path, engine):
    configuration, outcomes = _scheduler(
        tmp_path,
        engine,
        COLLECTION_TASK,
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
    )
    assert [item.action for item in outcomes] == ["registered"]
    assert outcomes[0].next_due_at == datetime(
        2026, 10, 21, 12, 15, tzinfo=UTC
    )
    with Session(engine) as session:
        schedule = session.get(TaskSchedule, configuration.tasks[0].id)
        assert schedule is not None
        assert schedule.last_fired_at is None
        assert session.scalar(select(TaskRun)) is None


def test_due_round_queues_one_run_and_advances_due(tmp_path, engine):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    first = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=first,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    due = first + timedelta(minutes=15)
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=due,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    assert [item.action for item in outcomes] == ["triggered"]
    assert outcomes[0].run_id is not None
    with Session(engine) as session:
        run = session.get(TaskRun, outcomes[0].run_id)
        assert run is not None
        assert run.status == "queued"
        assert run.triggered_by == "schedule"
        assert run.retry_of_run_id is None
        schedule = session.get(TaskSchedule, "collect")
        assert schedule is not None
        assert schedule.last_fired_at == due
        assert schedule.next_due_at == due + timedelta(minutes=15)

    # A second round at the same instant must not create a second Run.
    with Session(engine) as session:
        again = run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=due,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    assert [item.action for item in again] == ["not_due"]


def test_busy_task_skips_due_and_still_advances(tmp_path, engine):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    with Session(engine) as session:
        create_or_get_run(
            session, configuration.tasks[0], [], config_path=None
        )
    first = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=first,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    due = first + timedelta(minutes=15)
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=due,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    assert [item.action for item in outcomes] == ["skipped_active_run"]
    with Session(engine) as session:
        assert len(session.scalars(select(TaskRun)).all()) == 1
        schedule = session.get(TaskSchedule, "collect")
        assert schedule.next_due_at == due + timedelta(minutes=15)


def test_disabled_and_removed_tasks_stop_scheduling(tmp_path, engine):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=now,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    assert _schedule_exists(engine)

    disabled = load_task_configuration(
        write_tasks(
            tmp_path,
            COLLECTION_TASK.replace(
                "    sources: [sec]", "    enabled: false\n    sources: [sec]"
            ),
        )
    )
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(disabled.tasks),
            disabled.agent_map,
            now=now + timedelta(hours=1),
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    assert outcomes == []
    assert not _schedule_exists(engine)


def _schedule_exists(engine) -> bool:
    with Session(engine) as session:
        return session.scalar(select(TaskSchedule)) is not None


MIXED_TASKS = """
tasks:
  - id: brief
    name: Brief
    type: research
    trigger: {kind: schedule, every: 15m}
    objective: Summarize.
    scope: {topics: [macro]}
    steps:
      - id: evidence
        select_evidence: {kind: select_evidence, queries: [macro]}
      - id: research
        research_agent:
          kind: research_agent
          agent: researcher
          input: evidence
  - id: collect
    name: Collect
    type: collection
    trigger: {kind: schedule, every: 15m}
    sources: [sec]
    steps:
      - id: collect
        collect_sources: {kind: collect_sources}
agents:
  - id: researcher
    role: Researcher
    prompt: Summarize.
    model_ref: research
    input_schema:
      type: object
      additionalProperties: false
      required: [run_id, objective, strategy, scope, as_of, evidence_package_id, evidence, coverage, truncated]
      properties:
        run_id: {type: string}
        objective: {type: string}
        strategy:
          anyOf:
            - {type: string}
            - {type: "null"}
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
        findings:
          type: array
          items: {type: object, additionalProperties: true}
        open_questions: {type: array, items: {type: string}}
        evidence_refs: {type: array, items: {type: object, additionalProperties: true}}
"""


def test_schedule_change_reanchors_due_instant(tmp_path, engine):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=now,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    changed = load_task_configuration(
        write_tasks(tmp_path, COLLECTION_TASK.replace("every: 15m", "every: 1h"))
    )
    later = now + timedelta(minutes=20)
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(changed.tasks),
            changed.agent_map,
            now=later,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    assert [item.action for item in outcomes] == ["rescheduled"]
    assert outcomes[0].next_due_at == later + timedelta(hours=1)
    with Session(engine) as session:
        assert session.scalar(select(TaskRun)) is None


def test_task_with_unavailable_model_config_does_not_block_other_tasks(
    tmp_path, engine
):
    path = tmp_path / "mixed.yaml"
    path.write_text(MIXED_TASKS, encoding="utf-8")
    configuration = load_task_configuration(path)
    assert {task.type for task in configuration.tasks} == {
        "research",
        "collection",
    }
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=now,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    due = now + timedelta(minutes=15)
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=due,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    by_task = {item.task_id: item for item in outcomes}
    # The research Task cannot resolve its model route, so it records a safe
    # error while the collection Task still triggers normally.
    assert by_task["brief"].action == "failed"
    assert by_task["brief"].error is not None
    assert by_task["collect"].action == "triggered"
    with Session(engine) as session:
        schedule = session.get(TaskSchedule, "brief")
        assert schedule is not None
        assert schedule.last_error is not None
        runs = session.scalars(select(TaskRun)).all()
        assert [run.task_id for run in runs] == ["collect"]


def test_due_task_with_active_run_skips_without_loading_model_routes(
    tmp_path, engine, monkeypatch
):
    """A busy Task must skip its due instant, not fail on model configuration.

    The research Task here points at a model file that does not exist. Because it
    already owns a queued Run, the scheduler has to detect the active Run before
    resolving any route and only advance the due instant, so a scheduled trigger
    neither reads model credentials nor records a configuration failure.
    """

    path = tmp_path / "mixed.yaml"
    path.write_text(MIXED_TASKS, encoding="utf-8")
    configuration = load_task_configuration(path)
    brief = configuration.task_map["brief"]

    # ``pytest.fail`` raises ``BaseException``, so a regression that resolves a
    # route here is reported as this reason instead of being swallowed by the
    # scheduler's per-Task error isolation.
    monkeypatch.setattr(
        "stocks.execution.service.load_chat_routing",
        lambda *args, **kwargs: pytest.fail(
            "scheduler must not resolve model routes for a busy Task"
        ),
    )
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        create_or_get_run(session, brief, list(configuration.agents), config_path=None)
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=now,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    due = now + timedelta(minutes=15)
    with Session(engine) as session:
        outcomes = run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=due,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    by_task = {item.task_id: item for item in outcomes}
    assert by_task["brief"].action == "skipped_active_run"
    assert by_task["brief"].error is None
    assert by_task["brief"].run_id is None
    assert by_task["brief"].next_due_at == due + timedelta(minutes=15)
    assert by_task["collect"].action == "triggered"
    with Session(engine) as session:
        schedule = session.get(TaskSchedule, "brief")
        assert schedule is not None
        assert schedule.next_due_at == due + timedelta(minutes=15)
        assert schedule.last_attempt_at == due
        assert schedule.last_error is None
        assert schedule.last_fired_at is None
        assert len(session.scalars(select(TaskRun)).all()) == 2


def test_concurrent_scheduler_rounds_queue_one_run(tmp_path, engine):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    first = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=first,
            model_config_path=str(tmp_path / "missing-models.yaml"),
        )
    due = first + timedelta(minutes=15)
    barrier = Barrier(2)

    def round_once(_):
        with Session(engine) as session:
            barrier.wait(timeout=10)
            outcomes = run_scheduler_round(
                session,
                list(configuration.tasks),
                configuration.agent_map,
                now=due,
                model_config_path=str(tmp_path / "missing-models.yaml"),
            )
            return [item.action for item in outcomes]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(round_once, range(2)))
    assert sum(
        "triggered" in actions for actions in results
    ) <= 1
    with Session(engine) as session:
        runs = session.scalars(select(TaskRun)).all()
        assert len(runs) == 1


def test_claim_next_run_is_atomic_across_workers(engine, tmp_path):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    task = configuration.tasks[0]
    with Session(engine) as session:
        run = create_or_get_run(session, task, [], config_path=None).run
        run_id = run.id
    barrier = Barrier(2)

    def claim(token):
        with Session(engine) as session:
            barrier.wait(timeout=10)
            return worker.claim_next_run(session, process_token=token)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["worker-a", "worker-b"]))
    assert sorted(item for item in claims if item is not None) == [run_id]
    assert sum(item is not None for item in claims) == 1


def test_worker_run_once_executes_collection_without_model_credentials(
    tmp_path, engine, monkeypatch
):
    tasks_path = write_tasks(tmp_path, COLLECTION_TASK)
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        """
sources:
  - source_id: sec
    name: SEC
    connector: rss
    enabled: true
    publisher: SEC
    trust_tier: T1
    feed_url: https://example.com/feed.xml
    timeout_seconds: 5
    max_response_bytes: 1024
    max_redirects: 1
    max_items: 5
    user_agent: StocksIntelligence/0.1 (owner@example.com)
""",
        encoding="utf-8",
    )
    calls: list[str] = []

    class Summary:
        def __init__(self, source_id: str) -> None:
            self.source_id = source_id
            self.failed = 0

        def to_dict(self) -> dict:
            return {
                "source_id": self.source_id,
                "discovered": 0,
                "ingested": 0,
                "reused": 0,
                "skipped": 0,
                "failed": 0,
                "items": [],
            }

    def fake_collect_source(session, config, source, **kwargs):
        calls.append(source.source_id)
        return Summary(source.source_id)

    monkeypatch.setattr(
        "stocks.execution.service.collect_source", fake_collect_source
    )
    context = worker.WorkerContext(
        session_factory=lambda: Session(engine),
        settings=make_config(),
        task_config_path=tasks_path,
        model_config_path=str(tmp_path / "missing-models.yaml"),
        sources_config_path=str(sources_path),
    )
    task_id = "collect"
    with Session(engine) as session:
        session.add(
            TaskDefinition(
                id=task_id,
                name="Collect",
                type="collection",
                trigger="schedule",
                enabled=True,
                scope={"source_ids": ["sec"]},
                config_snapshot={},
                config_hash="a" * 64,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

    first = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    worker.run_once(context, now=first)
    second = first + timedelta(minutes=15)
    payload = worker.run_once(context, now=second)
    assert [item["action"] for item in payload["scheduler"]] == ["triggered"]
    assert payload["executed"]["status"] == "succeeded"
    assert calls == ["sec"]
    with Session(engine) as session:
        run = session.get(TaskRun, UUID(payload["executed"]["run_id"]))
        assert run is not None
        assert run.status == "succeeded"
        assert run.model_snapshot == {"agents": {}, "embedding": None}
        step = session.scalar(
            select(TaskStepResult).where(TaskStepResult.run_id == run.id)
        )
        assert step is not None
        assert step.status == "succeeded"
        assert step.output["ingested"] == 0


def _stub_worker_context(
    monkeypatch, *, schedule_round, execute_next_run, slots=1
):
    """Build a Worker context whose scheduling and execution are stubbed.

    Only the polling, slot-refill and drain behaviour of ``run_forever`` is under
    test here, so no Session, database or Task configuration is touched.
    """

    monkeypatch.setattr(worker, "schedule_round", schedule_round)
    monkeypatch.setattr(worker, "execute_next_run", execute_next_run)
    return worker.WorkerContext(
        session_factory=lambda: None,
        settings=make_config(),
        task_config_path="unused.yaml",
        model_config_path="unused.yaml",
        sources_config_path="unused.yaml",
        max_concurrent_runs=slots,
    )


def test_run_forever_claims_an_idle_queue_once_per_interval(monkeypatch):
    """An empty queue must be polled on the bounded interval, not in a hot loop.

    One slot claims once per polling interval plus one final draining claim. With
    the wait removed, or with ``asyncio.wait`` returning immediately because every
    slot was idle, the same few rounds would produce an unbounded number of claims
    and database round-trips.
    """

    claims: list[float] = []

    def fake_schedule_round(context, *, now=None):
        return []

    def fake_execute_next_run(context, *, process_token, now=None):
        claims.append(time.monotonic())
        return None

    context = _stub_worker_context(
        monkeypatch,
        schedule_round=fake_schedule_round,
        execute_next_run=fake_execute_next_run,
        slots=1,
    )
    started = time.monotonic()
    rounds = asyncio.run(
        worker.run_forever(context, poll_seconds=0.1, max_rounds=3)
    )
    elapsed = time.monotonic() - started
    assert rounds == 3
    # A hot loop would produce hundreds of claims in this window.
    assert 3 <= len(claims) <= 8
    # Three rounds with two bounded waits really take about 0.2s; the previous
    # ``ALL_COMPLETED`` polling returned immediately and finished in milliseconds.
    assert 0.15 <= elapsed < 3.0


def test_run_forever_refills_a_freed_slot_immediately(monkeypatch):
    """A short Run releases its slot for the next queued Run without any wait.

    ``poll_seconds`` is deliberately huge: if a freed slot had to wait for the next
    polling interval before claiming again, the second Run would not execute within
    this test's bound.
    """

    queued = ["run-1", "run-2"]
    executed: list[str] = []

    def fake_schedule_round(context, *, now=None):
        return []

    def fake_execute_next_run(context, *, process_token, now=None):
        if not queued:
            return None
        run_id = queued.pop(0)
        executed.append(run_id)
        return {"run_id": run_id, "status": "succeeded"}

    context = _stub_worker_context(
        monkeypatch,
        schedule_round=fake_schedule_round,
        execute_next_run=fake_execute_next_run,
        slots=1,
    )
    started = time.monotonic()
    asyncio.run(worker.run_forever(context, poll_seconds=60, max_rounds=1))
    assert executed == ["run-1", "run-2"]
    assert time.monotonic() - started < 10


def test_run_forever_keeps_scheduling_while_a_run_is_in_flight(monkeypatch):
    """A slow Run occupies one execution slot, never the scheduler.

    The stubbed Run blocks until a second scheduler round has actually run, which
    only happens if schedule rounds and execution are independent.
    """

    schedule_calls: list[int] = []
    second_round = threading.Event()
    resumed: list[bool] = []

    def fake_schedule_round(context, *, now=None):
        schedule_calls.append(len(schedule_calls) + 1)
        if len(schedule_calls) >= 2:
            second_round.set()
        return []

    def fake_execute_next_run(context, *, process_token, now=None):
        if not resumed:
            resumed.append(bool(second_round.wait(timeout=10)))
            return {"run_id": "slow", "status": "succeeded"}
        return None

    context = _stub_worker_context(
        monkeypatch,
        schedule_round=fake_schedule_round,
        execute_next_run=fake_execute_next_run,
        slots=1,
    )
    asyncio.run(
        worker.run_forever(context, poll_seconds=0.1, max_rounds=6)
    )
    assert resumed == [True]
    assert len(schedule_calls) >= 2


def test_run_forever_backs_off_when_a_slot_keeps_failing(monkeypatch):
    """Repeated slot failures wait with a bounded backoff instead of retrying hot."""

    failures: list[float] = []

    def fake_schedule_round(context, *, now=None):
        return []

    def fake_execute_next_run(context, *, process_token, now=None):
        if len(failures) < 2:
            failures.append(time.monotonic())
            raise RuntimeError("database session unavailable")
        return None

    context = _stub_worker_context(
        monkeypatch,
        schedule_round=fake_schedule_round,
        execute_next_run=fake_execute_next_run,
        slots=1,
    )
    asyncio.run(worker.run_forever(context, poll_seconds=0.1, max_rounds=6))
    assert len(failures) == 2
    assert 0.09 <= failures[1] - failures[0] < 3.0


def test_end_run_keeps_progress_and_blocks_stale_writes(engine, tmp_path):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    with Session(engine) as session:
        created = create_or_get_run(
            session, configuration.tasks[0], [], config_path=None
        )
        run_id = created.run.id
        assert worker.claim_run(
            session, run_id, process_token="worker-a"
        )
        session.add(
            TaskStepResult(
                run_id=run_id,
                step_id="collect",
                position=0,
                step_type="collect_sources",
                status="failed",
                output=None,
                evidence_refs=[],
                usage={},
                error={"code": "HTTP_STATUS", "message": "Upstream failed"},
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    with Session(engine) as session:
        ended = end_run(session, run_id)
        assert ended.status == "cancelled"
        assert ended.process_token is None
        assert ended.error["code"] == "RUN_ENDED_BY_OWNER"

    # The old Worker output must not be able to close the ended Run.
    with Session(engine) as session:
        from stocks.execution.service import ExecutionError, _finish_run

        with pytest.raises(ExecutionError):
            _finish_run(
                session,
                run_id,
                process_token="worker-a",
                status="succeeded",
                output={},
                error=None,
            )
        session.rollback()

    with Session(engine) as session:
        run = session.get(TaskRun, run_id)
        assert run is not None
        assert run.status == "cancelled"
        steps = session.scalars(
            select(TaskStepResult).where(TaskStepResult.run_id == run_id)
        ).all()
        assert [step.status for step in steps] == ["failed"]

    with Session(engine) as session:
        retried = retry_run(session, run_id)
        assert retried.created is True
        assert retried.run.triggered_by == "retry"
        assert retried.run.retry_of_run_id == run_id
        # A failed step is not copied, so the retry really re-runs it while the
        # old Run keeps its failure history.
        assert (
            session.scalars(
                select(TaskStepResult).where(
                    TaskStepResult.run_id == retried.run.id
                )
            ).all()
            == []
        )


def test_retry_requires_ended_run_and_reuses_snapshot(engine, tmp_path):
    path = write_tasks(tmp_path, COLLECTION_TASK)
    configuration = load_task_configuration(path)
    with Session(engine) as session:
        created = create_or_get_run(
            session, configuration.tasks[0], [], config_path=None
        )
        run_id = created.run.id
        assert worker.claim_run(session, run_id, process_token="worker-a")
        from stocks.execution.service import ExecutionError

        with pytest.raises(ExecutionError) as exc_info:
            retry_run(session, run_id)
        assert exc_info.value.code == "RUN_NOT_ENDED"
        session.rollback()

        run = session.get(TaskRun, run_id)
        run.status = "failed"
        run.process_token = None
        run.error = {"code": "STEP_FAILED", "message": "failed"}
        run.finished_at = datetime.now(UTC)
        session.commit()
        snapshot = json.loads(json.dumps(run.config_snapshot))

    with Session(engine) as session:
        retried = retry_run(session, run_id)
        assert retried.run.config_snapshot == snapshot
        assert retried.run.model_snapshot == {"agents": {}, "embedding": None}
        # The original failed Run is still readable as the failure record.
        original = session.get(TaskRun, run_id)
        assert original.status == "failed"
        assert original.error["code"] == "STEP_FAILED"


def test_retry_reuses_previous_steps_but_reruns_the_agent(engine, tmp_path):
    path = tmp_path / "mixed.yaml"
    path.write_text(MIXED_TASKS, encoding="utf-8")
    configuration = load_task_configuration(path)
    brief = configuration.task_map["brief"]
    with Session(engine) as session:
        created = create_or_get_run(
            session, brief, list(configuration.agents), config_path=None
        )
        run_id = created.run.id
        assert worker.claim_run(session, run_id, process_token="worker-a")
        package = EvidencePackage(
            run_id=run_id,
            objective="Summarize.",
            scope={"topics": ["macro"]},
            as_of=datetime.now(UTC),
            selection_policy_version="test",
            selection_budget={"max_tokens": 1000},
            queries=[],
            selected_chunks=[],
            coverage={"gaps": []},
            selected_count=0,
            retrieval_truncated=False,
            created_at=datetime.now(UTC),
        )
        session.add(package)
        session.flush()
        package_id = package.id
        session.add_all(
            [
                TaskStepResult(
                    run_id=run_id,
                    step_id="evidence",
                    position=0,
                    step_type="select_evidence",
                    status="succeeded",
                    output={
                        "evidence_package_id": str(package_id),
                        "items": [],
                    },
                    evidence_refs=[],
                    usage={},
                    created_at=datetime.now(UTC),
                ),
                TaskStepResult(
                    run_id=run_id,
                    step_id="research",
                    position=1,
                    step_type="research_agent",
                    status="succeeded",
                    output={"summary": "previous answer"},
                    evidence_refs=[],
                    usage={},
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        run = session.get(TaskRun, run_id)
        run.status = "failed"
        run.process_token = None
        run.error = {"code": "STEP_FAILED", "message": "failed later"}
        run.finished_at = datetime.now(UTC)
        session.commit()

    with Session(engine) as session:
        retried = retry_run(session, run_id)
        copied = session.scalars(
            select(TaskStepResult)
            .where(TaskStepResult.run_id == retried.run.id)
            .order_by(TaskStepResult.position)
        ).all()
        # The Evidence Selection result is reusable, but the Agent step is not:
        # its Analysis and Report belong to the earlier Run and must be rebuilt.
        assert [step.step_id for step in copied] == ["evidence"]
        assert copied[0].status == "succeeded"
        new_package = session.scalar(
            select(EvidencePackage).where(
                EvidencePackage.run_id == retried.run.id
            )
        )
        assert new_package is not None
        assert new_package.id != package_id
        assert copied[0].output["evidence_package_id"] == str(new_package.id)
        original = session.get(TaskRun, run_id)
        assert original.status == "failed"
