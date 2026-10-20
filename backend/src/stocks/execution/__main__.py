from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.db import session_factory
from stocks.execution.config import (
    ExecutionConfigError,
    load_task_configuration,
)
from stocks.execution.service import (
    ExecutionContext,
    ExecutionError,
    create_claim_and_execute,
    fail_claimed_run,
    get_active_run,
)
from stocks.models import ModelCall, TaskRun, TaskStepResult
from stocks.settings import settings


def _run_payload(
    session,
    run: TaskRun,
    *,
    created: bool | None = None,
    executed: bool,
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
    payload = {
        "run_id": str(run.id),
        "task_id": run.task_id,
        "status": run.status,
        "created": created,
        "executed": executed,
        "objective": run.objective,
        "strategy": run.strategy,
        "scope": run.scope,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "output": run.output,
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
    return payload


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create, claim and execute a configured manual research Task."
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
    parser.add_argument("--task", required=True, help="Task identifier.")
    args = parser.parse_args(argv)

    try:
        with session_factory()() as session:
            active = get_active_run(session, args.task)
            if active is not None:
                print(
                    json.dumps(
                        _run_payload(
                            session,
                            active,
                            created=False,
                            executed=False,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
    except (ExecutionError, ValueError, OSError) as exc:
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
                        "message": "Task execution could not be initialized",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2

    try:
        task, relevant_agents = _configured_task(args.config, args.task)
    except ExecutionConfigError as exc:
        print(json.dumps({"error": exc.to_dict()}), file=sys.stderr)
        return 2

    process_token = uuid.uuid4().hex
    try:
        with session_factory()() as session:
            try:
                run, created, executed = create_claim_and_execute(
                    ExecutionContext(
                        session=session,
                        settings=settings(),
                        config_path=args.models_config,
                    ),
                    task,
                    relevant_agents,
                    process_token=process_token,
                )
            except Exception as exc:
                serialize_error = getattr(exc, "to_dict", None)
                session.rollback()
                active_id = None
                active_token = None
                with Session(session.get_bind()) as recovery:
                    recovery_row = recovery.scalar(
                        select(TaskRun)
                        .where(
                            TaskRun.task_id == task.id,
                            TaskRun.status == "running",
                            TaskRun.process_token == process_token,
                        )
                        .limit(1)
                    )
                    if recovery_row is not None:
                        active_id = recovery_row.id
                        active_token = recovery_row.process_token
                if active_id is not None and active_token is not None:
                    fail_claimed_run(
                        session,
                        active_id,
                        process_token=active_token,
                        error=(
                            serialize_error()
                            if callable(serialize_error)
                            else {
                                "code": "RUN_EXECUTION_FAILED",
                                "message": "Task Run could not be completed",
                            }
                        ),
                    )
                raise ExecutionError(
                    "RUN_EXECUTION_FAILED",
                    "Task Run could not be completed",
                ) from exc
            payload = _run_payload(
                session,
                run,
                created=created,
                executed=executed,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            if not executed:
                return 0
            return 0 if run.status == "succeeded" else 1
    except (ExecutionError, ValueError, OSError) as exc:
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
