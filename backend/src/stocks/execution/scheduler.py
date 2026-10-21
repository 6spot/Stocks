"""Persistent due scheduling for configured Tasks.

The scheduler keeps each Task's next due instant in PostgreSQL, so a restarted
Worker, a second Worker process or a long research Run cannot lose or duplicate a
trigger. Every due decision and the ``next_due_at`` advance for one Task happen in
the same Task-level serialized transaction as the Run enqueue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from stocks.execution.config import AgentConfig, TaskConfig, TriggerConfig
from stocks.execution.service import (
    TRIGGER_SCHEDULE,
    _enqueue_run,
    _lock_task,
    get_active_run,
    register_task_definition,
)
from stocks.models import TaskSchedule

MAX_CRON_PROBES = 64


class ScheduleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ScheduleOutcome:
    task_id: str
    action: str
    next_due_at: datetime | None = None
    run_id: UUID | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        payload: dict = {"task_id": self.task_id, "action": self.action}
        if self.next_due_at is not None:
            payload["next_due_at"] = self.next_due_at.isoformat()
        if self.run_id is not None:
            payload["run_id"] = str(self.run_id)
        if self.error is not None:
            payload["error"] = self.error
        return payload


def next_due_at(trigger: TriggerConfig, after: datetime) -> datetime:
    """Return the next future trigger instant strictly after ``after``.

    ``every`` keeps its fixed interval and never re-anchors on completion.
    ``cron`` is evaluated on the Task timezone's wall clock: a local time that
    does not exist (spring-forward gap) is skipped, and a repeated local time
    (fall-back overlap) fires once at its first occurrence.
    """
    if after.utcoffset() is None:
        raise ValueError("scheduling requires a timezone-aware instant")
    anchored = after.astimezone(UTC)
    if trigger.every_seconds is not None:
        return anchored + timedelta(seconds=trigger.every_seconds)
    if trigger.cron is None or trigger.timezone is None:
        raise ScheduleError(
            "SCHEDULE_CONFIG_INVALID", "schedule trigger is incomplete"
        )
    zone = ZoneInfo(trigger.timezone)
    local_after = anchored.astimezone(zone)
    iterator = croniter(
        trigger.cron,
        local_after.replace(tzinfo=None),
        ret_type=datetime,
    )
    for _ in range(MAX_CRON_PROBES):
        candidate = _resolve_local_time(iterator.get_next(datetime), zone)
        if candidate is not None and candidate > anchored:
            return candidate
    raise ScheduleError(
        "SCHEDULE_CONFIG_INVALID",
        "cron schedule produced no future trigger instant",
    )


def _resolve_local_time(local: datetime, zone) -> datetime | None:
    """Map a cron wall-clock value to UTC, or ``None`` in a DST gap.

    Only the first occurrence of a repeated local time is returned so a
    fall-back overlap does not run the Task twice.
    """
    aware = local.replace(tzinfo=zone, fold=0)
    utc = aware.astimezone(UTC)
    if utc.astimezone(zone).replace(tzinfo=None) == local:
        return utc
    return None


def advance_due(
    trigger: TriggerConfig, current_due: datetime, now: datetime
) -> datetime:
    """Advance a due instant, collapsing every missed trigger into one.

    A Worker that was down or busy for several intervals backfills nothing: the
    due instant jumps to the first future grid point of the fixed interval.
    """
    if trigger.every_seconds is not None:
        if current_due > now:
            return current_due
        elapsed = int((now - current_due).total_seconds())
        missed = elapsed // trigger.every_seconds + 1
        return current_due + timedelta(
            seconds=missed * trigger.every_seconds
        )
    return next_due_at(trigger, now)


def run_scheduler_round(
    session: Session,
    tasks: list[TaskConfig],
    agents_by_id: dict[str, AgentConfig],
    *,
    now: datetime,
    model_config_path: str,
) -> list[ScheduleOutcome]:
    """Register, fire and retire schedules for the current configuration.

    Every Task is handled in its own transaction, so one Task with an
    unavailable model route or a failing trigger cannot stop the round. A Task
    that is disabled, removed from the configuration or switched back to a
    manual trigger loses its schedule row and stops being scheduled, while its
    current definition is still recorded so stored Runs can be read.
    """

    if now.utcoffset() is None:
        raise ValueError("scheduling requires a timezone-aware instant")
    scheduled = {
        task.id: task
        for task in tasks
        if task.enabled and task.trigger.kind == "schedule"
    }
    _retire_stale_schedules(session, tasks, scheduled, agents_by_id, now=now)
    outcomes: list[ScheduleOutcome] = []
    for task_id in sorted(scheduled):
        outcomes.append(
            _run_task_schedule(
                session,
                scheduled[task_id],
                agents_by_id,
                now=now,
                model_config_path=model_config_path,
            )
        )
    return outcomes


def _task_agents(task: TaskConfig, agents_by_id: dict[str, AgentConfig]):
    return [
        agents_by_id[step.research_agent.agent]
        for step in task.steps
        if step.research_agent is not None
    ]


def _retire_stale_schedules(
    session: Session,
    tasks: list[TaskConfig],
    scheduled: dict[str, TaskConfig],
    agents_by_id: dict[str, AgentConfig],
    *,
    now: datetime,
) -> None:
    """Delete leftover schedule rows and keep configured definitions current.

    A disabled, removed or manual Task must never keep firing from a persisted
    row, and its stored definition still records the current ``enabled`` and
    ``trigger`` so Run readers do not see a stale schedule. Deleting the rows
    happens in its own transaction so a later per-Task failure cannot leave a
    retired Task scheduled.
    """

    try:
        session.rollback()
        statement = delete(TaskSchedule)
        if scheduled:
            statement = statement.where(
                TaskSchedule.task_id.not_in(sorted(scheduled))
            )
        session.execute(statement)
        session.commit()
    except Exception:  # noqa: BLE001 - the round must not die here
        session.rollback()
        return
    for task in sorted(tasks, key=lambda item: item.id):
        if task.id in scheduled:
            continue
        try:
            session.rollback()
            register_task_definition(
                session,
                task,
                _task_agents(task, agents_by_id),
                now=now,
            )
            session.commit()
        except Exception:  # noqa: BLE001 - one definition must not block others
            session.rollback()


def _run_task_schedule(
    session: Session,
    task: TaskConfig,
    agents_by_id: dict[str, AgentConfig],
    *,
    now: datetime,
    model_config_path: str,
) -> ScheduleOutcome:
    trigger = task.trigger
    agents: list[AgentConfig] = []
    try:
        session.rollback()
        # Resolve the Agents inside the per-Task boundary, so one Task that
        # references an unknown Agent cannot terminate the scheduling round.
        agents = _task_agents(task, agents_by_id)
        _lock_task(session, task.id)
        register_task_definition(session, task, agents, now=now)
        schedule = session.scalar(
            select(TaskSchedule)
            .where(TaskSchedule.task_id == task.id)
            .with_for_update()
        )
        fingerprint = trigger.fingerprint()
        if schedule is None or schedule.schedule_fingerprint != fingerprint:
            due = next_due_at(trigger, now)
            values = {
                "trigger_kind": trigger.kind,
                "schedule_fingerprint": fingerprint,
                "schedule_snapshot": _trigger_snapshot(trigger),
                "next_due_at": due,
                "last_error": None,
            }
            if schedule is None:
                session.add(TaskSchedule(task_id=task.id, **values))
                action = "registered"
            else:
                for key, value in values.items():
                    setattr(schedule, key, value)
                action = "rescheduled"
            session.commit()
            return ScheduleOutcome(
                task_id=task.id,
                action=action,
                next_due_at=due,
            )
        if schedule.next_due_at > now:
            session.commit()
            return ScheduleOutcome(
                task_id=task.id,
                action="not_due",
                next_due_at=schedule.next_due_at,
            )
        due = advance_due(trigger, schedule.next_due_at, now)
        # Decide this Task is due before resolving anything model-related: an
        # already active Run must be skipped and only the due instant advanced,
        # so a scheduled trigger never loads research credentials or reports a
        # configuration failure for a Task that cannot take a new Run. The
        # per-Task advisory lock held here is the same one the manual trigger
        # uses, and the partial unique index still backstops the final race.
        if get_active_run(session, task.id) is not None:
            schedule.next_due_at = due
            schedule.last_attempt_at = now
            schedule.last_error = None
            session.commit()
            return ScheduleOutcome(
                task_id=task.id,
                action="skipped_active_run",
                next_due_at=due,
            )
        run_id = _enqueue_run(
            session,
            task,
            agents,
            config_path=model_config_path if agents else None,
            now=now,
            process_token=None,
            triggered_by=TRIGGER_SCHEDULE,
            retry_of_run_id=None,
        )
        schedule = session.get(TaskSchedule, task.id)
        assert schedule is not None
        schedule.next_due_at = due
        schedule.last_attempt_at = now
        schedule.last_error = None
        if run_id is not None:
            schedule.last_fired_at = now
        session.commit()
        return ScheduleOutcome(
            task_id=task.id,
            action="triggered" if run_id is not None else "skipped_active_run",
            next_due_at=due,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 - isolate one Task failure
        session.rollback()
        error = (
            exc.to_dict()
            if hasattr(exc, "to_dict")
            else {
                "code": "SCHEDULE_FAILED",
                "message": "Task schedule could not be evaluated",
            }
        )
        _record_failure(
            session, task, agents, trigger, error, now=now
        )
        return ScheduleOutcome(
            task_id=task.id,
            action="failed",
            next_due_at=None,
            error=error,
        )


def _record_failure(
    session: Session,
    task: TaskConfig,
    agents: list[AgentConfig],
    trigger: TriggerConfig,
    error: dict,
    *,
    now: datetime,
) -> None:
    """Keep a failing Task from retrying on every round.

    The due instant still advances and the safe error stays visible, so the
    Owner can inspect it and retry the Run manually; the scheduler keeps
    serving the other Tasks.
    """
    try:
        session.rollback()
        _lock_task(session, task.id)
        register_task_definition(session, task, agents, now=now)
        schedule = session.scalar(
            select(TaskSchedule)
            .where(TaskSchedule.task_id == task.id)
            .with_for_update()
        )
        if schedule is None:
            schedule = TaskSchedule(
                task_id=task.id,
                trigger_kind=trigger.kind,
                schedule_fingerprint=trigger.fingerprint(),
                schedule_snapshot=_trigger_snapshot(trigger),
                next_due_at=next_due_at(trigger, now),
            )
            session.add(schedule)
        else:
            schedule.next_due_at = advance_due(trigger, schedule.next_due_at, now)
        schedule.last_attempt_at = now
        schedule.last_error = dict(error)
        session.commit()
    except Exception:  # noqa: BLE001 - the round must not die here
        session.rollback()


def _trigger_snapshot(trigger: TriggerConfig) -> dict:
    return {
        "kind": trigger.kind,
        "cron": trigger.cron,
        "timezone": trigger.timezone,
        "every_seconds": trigger.every_seconds,
    }

