"""Independent Worker: schedule due Tasks and execute queued Runs.

The Worker runs as its own process next to the API, shares the same backend code
and configuration, and coordinates only through PostgreSQL:

* a scheduler round advances each scheduled Task's persisted due instant and
  enqueues a queued Run,
* ``run_once`` atomically claims at most one queued Run, executes it and returns,
  so a test or a deployment smoke check can drive a single cycle,
* ``run_forever`` evaluates the due schedules once per bounded interval and keeps
  ``max_concurrent_runs`` persistent execution slots: a slot that finished a Run
  claims the next one immediately, a slot that finds an empty queue waits for the
  scheduler to queue work or for the interval to elapse instead of re-polling, and
  a long Run never delays the schedule evaluation or another slot.

Nothing here takes over a ``running`` Run another process may still own. An
interrupted Worker leaves its last persisted status, so the Owner has to confirm
the old process stopped before ending and retrying that Run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.db import session_factory
from stocks.execution.config import (
    ExecutionConfigError,
    load_task_configuration,
)
from stocks.execution.scheduler import ScheduleOutcome, run_scheduler_round
from stocks.execution.service import (
    ExecutionContext,
    ExecutionError,
    claim_run,
    execute_claimed_run,
    fail_claimed_run,
)
from stocks.models import TaskRun
from stocks.settings import Settings, settings

logger = logging.getLogger(__name__)

MAX_POLL_SECONDS = 300.0
MIN_POLL_SECONDS = 0.1
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_MAX_CONCURRENT_RUNS = 2
MAX_WORKER_CONCURRENCY = 16
MAX_BACKOFF_EXTRA_SECONDS = 60.0


@dataclass(frozen=True)
class WorkerContext:
    session_factory: object
    settings: Settings
    task_config_path: str
    model_config_path: str
    sources_config_path: str
    model_transport: object | None = None
    max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrent_runs <= MAX_WORKER_CONCURRENCY:
            raise ValueError(
                "max_concurrent_runs must be between 1 and "
                f"{MAX_WORKER_CONCURRENCY}"
            )

    @classmethod
    def from_settings(cls, **overrides) -> WorkerContext:
        config = settings()
        values = {
            "session_factory": session_factory(),
            "settings": config,
            "task_config_path": "config/tasks.yaml",
            "model_config_path": "config/models.yaml",
            "sources_config_path": "config/sources.yaml",
            "max_concurrent_runs": config.worker_max_concurrent_runs,
        }
        values.update(overrides)
        return cls(**values)


def _now() -> datetime:
    return datetime.now(UTC)


def claim_next_run(
    session: Session,
    *,
    process_token: str,
    now: datetime | None = None,
) -> UUID | None:
    """Atomically claim the oldest queued Run.

    ``FOR UPDATE SKIP LOCKED`` makes concurrent Workers claim different Runs
    instead of waiting on, or duplicating, the same row.
    """
    candidate = session.scalar(
        select(TaskRun.id)
        .where(TaskRun.status == "queued")
        .order_by(TaskRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if candidate is None:
        session.rollback()
        return None
    claimed = claim_run(
        session, candidate, process_token=process_token, now=now
    )
    return candidate if claimed else None


def run_once(
    context: WorkerContext,
    *,
    process_token: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Run one scheduler round and execute at most one queued Run."""
    token = process_token or uuid4().hex
    scheduled = schedule_round(context, now=now)
    executed = execute_next_run(context, process_token=token, now=now)
    return {
        "process_token": token,
        "scheduler": [item.to_dict() for item in scheduled],
        "executed": executed,
    }


def schedule_round(
    context: WorkerContext,
    *,
    now: datetime | None = None,
) -> list[ScheduleOutcome]:
    instant = now or _now()
    try:
        configuration = load_task_configuration(context.task_config_path)
    except ExecutionConfigError as exc:
        # A broken Task configuration must not stop the Worker from executing
        # Runs that are already queued.
        logger.warning(
            "Task configuration unavailable for scheduling error_code=%s",
            exc.code,
        )
        return [
            ScheduleOutcome(
                task_id="*",
                action="configuration_failed",
                error=exc.to_dict(),
            )
        ]
    with context.session_factory() as session:
        return run_scheduler_round(
            session,
            list(configuration.tasks),
            configuration.agent_map,
            now=instant,
            model_config_path=context.model_config_path,
        )


def execute_next_run(
    context: WorkerContext,
    *,
    process_token: str,
    now: datetime | None = None,
) -> dict | None:
    with context.session_factory() as session:
        run_id = claim_next_run(
            session, process_token=process_token, now=now
        )
        if run_id is None:
            return None
    try:
        with context.session_factory() as session:
            run = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=context.settings,
                    config_path=context.model_config_path,
                    sources_config_path=context.sources_config_path,
                    model_transport=context.model_transport,
                ),
                run_id,
                process_token=process_token,
            )
            return {
                "run_id": str(run.id),
                "task_id": run.task_id,
                "status": run.status,
                "error": run.error,
            }
    except ExecutionError as exc:
        if exc.code in {"RUN_NOT_FOUND", "RUN_NOT_CLAIMED", "RUN_NOT_OWNED"}:
            return {
                "run_id": str(run_id),
                "status": "not_owned",
                "error": exc.to_dict(),
            }
        return {
            "run_id": str(run_id),
            "status": "failed",
            "error": _fail_run(context, run_id, process_token, exc),
        }
    except Exception as exc:  # noqa: BLE001 - keep the Worker alive
        logger.warning(
            "Queued Run execution failed run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        return {
            "run_id": str(run_id),
            "status": "failed",
            "error": _fail_run(context, run_id, process_token, exc),
        }


def _fail_run(
    context: WorkerContext,
    run_id: UUID,
    process_token: str,
    exc: Exception,
) -> dict:
    """Record one safe failure on the Run and return that safe error payload.

    ``_fail_claimed_run`` only writes while the Run is still ``running`` under
    this token, so an ended Run keeps the Owner's status instead of this error.
    """

    error = (
        exc.to_dict()
        if hasattr(exc, "to_dict")
        else {
            "code": "RUN_EXECUTION_FAILED",
            "message": "Task Run could not be completed",
        }
    )
    with context.session_factory() as session:
        fail_claimed_run(
            session,
            run_id,
            error=error,
            process_token=process_token,
        )
    return error


def _claim_and_execute(
    context: WorkerContext, process_token: str
) -> dict | None:
    """Claim and execute at most one queued Run in this thread's own Session.

    Returns the executed Run summary, or ``None`` when the queue held nothing,
    which is what lets the calling slot sleep instead of polling again.
    """

    return execute_next_run(context, process_token=process_token)


async def _call_in_executor(executor, func, /, *args):
    """Run one blocking Worker step in the Worker's own thread pool.

    ``asyncio.to_thread`` would use the event loop's default executor, which is
    sized by CPU count and can be smaller than ``max_concurrent_runs``; a full set
    of long Runs would then starve the scheduler round. A dedicated pool of
    ``slots + 1`` threads keeps scheduling independent from execution.
    """

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, partial(func, *args))


async def _wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep up to ``seconds``, returning early once the Worker is stopping."""

    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _idle_wait(
    stop: asyncio.Event, wakeup: asyncio.Event, seconds: float
) -> None:
    """Wait while the queue is idle, up to ``seconds``.

    Returns as soon as the Worker is stopping or the scheduler has queued a Run,
    so an idle slot neither re-polls PostgreSQL continuously nor waits out the
    whole interval before picking up work that was just enqueued. ``wakeup`` is a
    coalescing signal: one round that queues several Runs wakes the waiting slots
    once, and a slot that starts waiting after the signal was already cleared
    still picks that work up when its own interval ends.
    """

    waiters = [
        asyncio.create_task(stop.wait()),
        asyncio.create_task(wakeup.wait()),
    ]
    try:
        await asyncio.wait(
            waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        wakeup.clear()


async def _run_slot(
    context: WorkerContext,
    process_token: str,
    stop: asyncio.Event,
    wakeup: asyncio.Event,
    poll_seconds: float,
    executor: ThreadPoolExecutor,
) -> None:
    """One persistent execution slot.

    A slot that executed a Run looks for the next one immediately, so a short
    collection Run releases its slot without waiting for the slowest Run. A slot
    that found nothing waits instead of re-polling: ``poll_seconds`` normally, a
    bounded exponential backoff while unexpected failures keep repeating, and any
    interval interrupted by the scheduler queueing work. Once the Worker starts
    stopping, a slot takes at most one more claim, so the Run queued by the last
    scheduler round of ``--rounds`` is picked up without a shutdown chasing an
    unbounded backlog. Failures of a Run are recorded on that Run by
    ``execute_next_run``; anything escaping it is logged so one slot cannot
    terminate the Worker.
    """

    failures = 0
    while True:
        draining = stop.is_set()
        try:
            executed = await _call_in_executor(
                executor, _claim_and_execute, context, process_token
            )
        except Exception:  # noqa: BLE001 - a slot failure must not kill the Worker
            logger.exception("Worker execution slot failed")
            if draining:
                return
            failures += 1
            await _wait_or_stop(
                stop,
                min(
                    poll_seconds * (2 ** min(failures, 6)),
                    poll_seconds + MAX_BACKOFF_EXTRA_SECONDS,
                ),
            )
            continue
        failures = 0
        if draining:
            # Already took the one final claim allowed after stopping.
            return
        if executed is None:
            await _idle_wait(stop, wakeup, poll_seconds)


async def run_forever(
    context: WorkerContext,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    max_rounds: int | None = None,
) -> int:
    """Poll due schedules and execute queued Runs until stopped.

    Scheduler rounds and execution slots are independent: the due schedules are
    evaluated at most once per ``poll_seconds`` while the
    ``max_concurrent_runs`` execution slots keep claiming work, so an in-flight Run
    cannot delay a schedule round and an idle queue costs one claim per interval
    instead of a busy loop. A round that queued a Run wakes the idle slots right
    away, and a stopping Worker still takes one last claim per slot, which is what
    makes ``--rounds`` execute the Run its own last round enqueued. Each slot opens
    its own Session because a Session is not shared between concurrently executing
    Runs.
    """

    if not MIN_POLL_SECONDS <= poll_seconds <= MAX_POLL_SECONDS:
        raise ValueError(
            f"poll_seconds must be between {MIN_POLL_SECONDS} and "
            f"{MAX_POLL_SECONDS}"
        )
    process_token = uuid4().hex
    slots = min(max(1, context.max_concurrent_runs), MAX_WORKER_CONCURRENCY)
    stop = asyncio.Event()
    wakeup = asyncio.Event()
    executor = ThreadPoolExecutor(
        max_workers=slots + 1, thread_name_prefix="stocks-worker"
    )
    in_flight = [
        asyncio.create_task(
            _run_slot(
                context, process_token, stop, wakeup, poll_seconds, executor
            )
        )
        for _ in range(slots)
    ]
    rounds = 0
    try:
        while max_rounds is None or rounds < max_rounds:
            rounds += 1
            try:
                outcomes = await _call_in_executor(
                    executor, schedule_round, context
                )
            except Exception:  # noqa: BLE001 - scheduling must not stop execution
                logger.exception("Scheduler round %s failed", rounds)
            else:
                if any(item.run_id is not None for item in outcomes):
                    wakeup.set()
            if max_rounds is not None and rounds >= max_rounds:
                break
            await _wait_or_stop(stop, poll_seconds)
    finally:
        # A Run that was already claimed is allowed to finish and persist its
        # own results; the Worker never abandons a Run it still owns.
        stop.set()
        await asyncio.gather(*in_flight, return_exceptions=True)
        executor.shutdown(wait=True)
    return rounds


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Schedule due Tasks and execute queued Task Runs. Use --once for a "
            "single cycle or run continuously with bounded polling."
        )
    )
    parser.add_argument(
        "--config",
        default="config/tasks.yaml",
        help="Path to the Task/Agent configuration YAML.",
    )
    parser.add_argument(
        "--models-config",
        default="config/models.yaml",
        help="Path to the Provider/model configuration YAML.",
    )
    parser.add_argument(
        "--sources-config",
        default="config/sources.yaml",
        help="Path to the Source configuration YAML.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scheduler round and execute at most one queued Run.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Stop after this many polling rounds instead of running forever.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="Bounded wait between polling rounds.",
    )
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=None,
        help=(
            "Maximum Task Runs this Worker executes at the same time "
            f"(1-{MAX_WORKER_CONCURRENCY})."
        ),
    )
    args = parser.parse_args(argv)
    if args.rounds is not None and args.rounds < 1:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "ROUNDS_INVALID",
                        "message": "rounds must be a positive integer",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    concurrency = args.max_concurrent_runs
    if concurrency is not None and not 1 <= concurrency <= MAX_WORKER_CONCURRENCY:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "CONCURRENCY_INVALID",
                        "message": (
                            "max-concurrent-runs must be between 1 and "
                            f"{MAX_WORKER_CONCURRENCY}"
                        ),
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    if not MIN_POLL_SECONDS <= args.poll_seconds <= MAX_POLL_SECONDS:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "POLL_SECONDS_INVALID",
                        "message": (
                            "poll-seconds must be between "
                            f"{MIN_POLL_SECONDS} and {MAX_POLL_SECONDS}"
                        ),
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    try:
        overrides: dict = {
            "task_config_path": args.config,
            "model_config_path": args.models_config,
            "sources_config_path": args.sources_config,
        }
        if concurrency is not None:
            overrides["max_concurrent_runs"] = concurrency
        context = WorkerContext.from_settings(**overrides)
    except (ValueError, OSError) as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "WORKER_CONFIG_UNAVAILABLE",
                        "message": getattr(
                            exc, "message", "Worker configuration is invalid"
                        ),
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    if args.once:
        payload = run_once(context)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        failed = any(
            item.get("status") not in {None, "succeeded"}
            for item in [payload.get("executed") or {}]
        ) or any(
            item.get("action") in {"failed", "configuration_failed"}
            for item in payload.get("scheduler", [])
        )
        return 1 if failed else 0
    try:
        rounds = asyncio.run(
            run_forever(
                context,
                poll_seconds=args.poll_seconds,
                max_rounds=args.rounds,
            )
        )
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "WORKER_CONFIG_INVALID",
                        "message": str(exc),
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"rounds": rounds}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
