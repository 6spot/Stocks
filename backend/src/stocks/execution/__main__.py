"""Trigger, inspect, end and retry configured Tasks from the command line."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from stocks.changes import current_watermark, scope_fingerprint
from stocks.db import session_factory
from stocks.execution.config import (
    ExecutionConfigError,
    load_task_configuration,
)
from stocks.execution.service import (
    ExecutionError,
    create_or_get_run,
    end_run,
    find_run,
    get_active_run,
    retry_run,
)
from stocks.models import (
    ModelCall,
    ResearchCursor,
    TaskDefinition,
    TaskRun,
    TaskStepResult,
)


def _run_payload(
    session,
    run: TaskRun,
    *,
    created: bool | None = None,
) -> dict:
    steps = list(
        session.scalars(
            select(TaskStepResult)
            .where(TaskStepResult.run_id == run.id)
            .order_by(TaskStepResult.position)
        ).all()
    )
    calls = list(
        session.scalars(
            select(ModelCall)
            .where(ModelCall.run_id == run.id)
            .order_by(ModelCall.step_id, ModelCall.attempt_number)
        ).all()
    )
    return {
        "run_id": str(run.id),
        "task_id": run.task_id,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "retry_of_run_id": (
            str(run.retry_of_run_id) if run.retry_of_run_id else None
        ),
        "created": created,
        "objective": run.objective,
        "strategy": run.strategy,
        "scope": run.scope,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "output": run.output,
        "change_window": (
            {
                "change_watermark_start": run.change_watermark_start,
                "change_watermark_end": run.change_watermark_end,
                "scope_fingerprint": run.scope_fingerprint,
                "change_scan_completed": run.change_scan_completed,
            }
            if run.change_watermark_end is not None
            else None
        ),
        "steps": [
            {
                "step_id": item.step_id,
                "position": item.position,
                "type": item.step_type,
                "status": item.status,
                "output": item.output,
                "evidence_refs": item.evidence_refs,
                "usage": item.usage,
                "error": item.error,
                "created_at": item.created_at.isoformat(),
            }
            for item in steps
        ],
        "model_calls": [
            {
                "step_id": item.step_id,
                "attempt_number": item.attempt_number,
                "requested_alias": item.requested_alias,
                "attempt_alias": item.attempt_alias,
                "requested_model_name": item.requested_model_name,
                "attempt_model_name": item.attempt_model_name,
                "reported_model_name": item.reported_model_name,
                "status": item.status,
                "duration_ms": item.duration_ms,
                "prompt_tokens": item.prompt_tokens,
                "completion_tokens": item.completion_tokens,
                "total_tokens": item.total_tokens,
                "fallback_reason": item.fallback_reason,
                "error": item.error,
            }
            for item in calls
        ],
    }


def _configured_task(config_path: str, task_id: str):
    configuration = load_task_configuration(config_path)
    task = configuration.task_map.get(task_id)
    if task is None:
        raise ExecutionConfigError(
            "TASK_NOT_FOUND", "Configured Task was not found"
        )
    if not task.enabled:
        raise ExecutionConfigError(
            "TASK_DISABLED", "Configured Task is disabled"
        )
    agents = [
        configuration.agent_map[step.research_agent.agent]
        for step in task.steps
        if step.research_agent is not None
    ]
    return task, agents


def _parse_run_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ExecutionError(
            "RUN_ID_INVALID", "Run identifier must be a UUID"
        ) from exc


def _trigger(args: argparse.Namespace) -> int:
    with session_factory()() as session:
        # Report an existing active Run before reading configuration or model
        # routes, so a repeated trigger never waits on or resolves credentials.
        active = get_active_run(session, args.task)
        if active is not None:
            print(
                json.dumps(
                    _run_payload(session, active, created=False),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        task, agents = _configured_task(args.config, args.task)
        claimed = create_or_get_run(
            session,
            task,
            agents,
            config_path=args.models_config if agents else None,
            tools_config_path=args.tools_config,
            triggered_by="manual",
        )
        print(
            json.dumps(
                _run_payload(session, claimed.run, created=claimed.created),
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _list_runs(args: argparse.Namespace) -> int:
    with session_factory()() as session:
        statement = select(TaskRun).order_by(TaskRun.created_at.desc())
        if args.task:
            statement = statement.where(TaskRun.task_id == args.task)
        if args.status:
            statement = statement.where(TaskRun.status == args.status)
        runs = list(session.scalars(statement.limit(args.limit)).all())
        print(
            json.dumps(
                [
                    {
                        "run_id": str(run.id),
                        "task_id": run.task_id,
                        "status": run.status,
                        "triggered_by": run.triggered_by,
                        "retry_of_run_id": (
                            str(run.retry_of_run_id)
                            if run.retry_of_run_id
                            else None
                        ),
                        "created_at": run.created_at.isoformat(),
                        "started_at": (
                            run.started_at.isoformat() if run.started_at else None
                        ),
                        "finished_at": (
                            run.finished_at.isoformat()
                            if run.finished_at
                            else None
                        ),
                        "error": run.error,
                    }
                    for run in runs
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _show_run(args: argparse.Namespace) -> int:
    run_id = _parse_run_id(args.run_id)
    with session_factory()() as session:
        run = find_run(session, run_id)
        if run is None:
            raise ExecutionError("RUN_NOT_FOUND", "Task Run was not found")
        print(
            json.dumps(
                _run_payload(session, run),
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _show_cursor(args: argparse.Namespace) -> int:
    """Read-only view of a Task's watermark and stored cursor.

    A missing cursor is reported as ``null`` rather than as 0: "never scanned" and
    "scanned up to the beginning" are different states, and the next Run treats them
    differently.
    """

    with session_factory()() as session:
        task = session.get(TaskDefinition, args.task)
        if task is None:
            raise ExecutionError("TASK_NOT_FOUND", "Task was not found")
        scope = task.scope if isinstance(task.scope, dict) else {}
        fingerprint = scope_fingerprint(args.task, scope)
        stored = session.get(ResearchCursor, (args.task, fingerprint))
        print(
            json.dumps(
                {
                    "task_id": args.task,
                    "scope_fingerprint": fingerprint,
                    "committed_watermark": current_watermark(session),
                    "cursor": (
                        {
                            "watermark": stored.watermark,
                            "updated_at": stored.updated_at.isoformat(),
                        }
                        if stored is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _end_run(args: argparse.Namespace) -> int:
    if not args.confirm_stopped:
        raise ExecutionError(
            "OWNER_CONFIRMATION_REQUIRED",
            "Ending a Run requires --confirm-stopped after verifying that no "
            "process is still executing it",
        )
    run_id = _parse_run_id(args.run_id)
    with session_factory()() as session:
        run = end_run(session, run_id)
        print(
            json.dumps(
                _run_payload(session, run),
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _retry_run(args: argparse.Namespace) -> int:
    run_id = _parse_run_id(args.run_id)
    with session_factory()() as session:
        claimed = retry_run(session, run_id)
        print(
            json.dumps(
                _run_payload(session, claimed.run, created=claimed.created),
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trigger configured Tasks into the database queue and manage "
            "their Runs. A separate Worker executes queued Runs."
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
        "--tools-config",
        default="config/tools.yaml",
        help="Path to the authorized Tool configuration YAML.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    trigger = commands.add_parser(
        "trigger", help="Queue a Run for one configured Task."
    )
    trigger.add_argument("--task", required=True, help="Task identifier.")
    trigger.set_defaults(handler=_trigger)

    runs = commands.add_parser(
        "runs", help="List Run status, steps and safe errors."
    )
    runs.add_argument("--task", default=None, help="Filter by Task identifier.")
    runs.add_argument(
        "--status",
        default=None,
        help="Filter by Run status (queued, running, succeeded, failed, cancelled).",
    )
    runs.add_argument("--limit", type=int, default=20)
    runs.set_defaults(handler=_list_runs)

    show = commands.add_parser("show", help="Show one Run with its step results.")
    show.add_argument("--run-id", required=True)
    show.set_defaults(handler=_show_run)

    end = commands.add_parser(
        "end",
        help=(
            "End a queued or running Run after confirming its process stopped. "
            "Stored steps, evidence and errors are kept."
        ),
    )
    end.add_argument("--run-id", required=True)
    end.add_argument(
        "--confirm-stopped",
        action="store_true",
        help="Confirm that no process is still executing this Run.",
    )
    end.set_defaults(handler=_end_run)

    retry = commands.add_parser(
        "retry",
        help="Queue a new Run that reuses the ended Run's completed steps.",
    )
    retry.add_argument("--run-id", required=True)
    retry.set_defaults(handler=_retry_run)

    cursor = commands.add_parser(
        "cursor",
        help=(
            "Show this Task's incremental change cursor and the committed "
            "watermark, without scanning anything."
        ),
    )
    cursor.add_argument("--task", required=True, help="Task identifier.")
    cursor.set_defaults(handler=_show_cursor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not 1 <= getattr(args, "limit", 1) <= 200:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "LIMIT_INVALID",
                        "message": "limit must be between 1 and 200",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    try:
        return args.handler(args)
    except (ExecutionError, ExecutionConfigError) as exc:
        print(json.dumps({"error": exc.to_dict()}), file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        code = getattr(exc, "code", "EXECUTION_UNAVAILABLE")
        message = getattr(
            exc,
            "message",
            "Task execution could not be initialized",
        )
        print(
            json.dumps({"error": {"code": code, "message": message}}),
            file=sys.stderr,
        )
        return 2
    except Exception:  # noqa: BLE001 - CLI must return safe diagnostics
        print(
            json.dumps(
                {
                    "error": {
                        "code": "EXECUTION_UNAVAILABLE",
                        "message": "Task execution could not be completed",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
