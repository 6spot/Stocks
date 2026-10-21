from __future__ import annotations

import hashlib
import logging
import secrets
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from heapq import merge
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, and_, column, func, literal, or_, select, values
from sqlalchemy.dialects.postgresql import JSONPATH, distinct_on
from sqlalchemy.orm import Session

from stocks.auth import (
    SESSION_COOKIE,
    AuthError,
    OwnerSession,
    OwnerSessionManager,
)
from stocks.collection.config import load_sources
from stocks.collection.errors import ConfigurationError
from stocks.db import session_factory
from stocks.execution.config import (
    ExecutionConfigError,
    load_task_configuration,
)
from stocks.execution.service import (
    ExecutionError,
    create_or_get_run,
    end_run,
    get_active_run,
    retry_run,
)
from stocks.facts import FactFilters, fact_to_dict, list_document_facts
from stocks.indexing.service import load_embedding_client
from stocks.models import (
    Analysis,
    Document,
    DocumentChunk,
    DocumentCollectionState,
    DocumentEmbeddingState,
    DocumentParseState,
    DocumentProcessingState,
    EvidencePackage,
    Fact,
    ModelCall,
    PROCESSING_STAGES,
    PROCESSING_STAGE_STATUSES,
    Report,
    Source,
    TaskDefinition,
    TaskRun,
    TaskStepResult,
)
from stocks.processing.processors import build_stage_registry
from stocks.processing.parser import HTML_CONTENT_TYPES, TEXT_CONTENT_TYPES
from stocks.processing.runner import describe_stages, run_stages_for_documents
from stocks.research.service import merged_research_refs, research_ledger_gaps
from stocks.repository import (
    SearchFilters,
    get_document,
    get_embedding_coverage,
    list_document_versions,
    list_parse_chunks,
    read_document_bytes,
)
from stocks.search.query import context_search, hits_to_dicts
from stocks.search.service import SearchResult, exact_search, semantic_search
from stocks.settings import Settings, settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stocks",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

PUBLIC_PATHS = frozenset({"/health", "/api/session/login"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
FRESHNESS_UNITS = {"m": 60, "h": 3600, "d": 86400}
# One Run's research-input ledger is a bounded list; the API never returns an
# unbounded projection of it.
MAX_RESEARCH_INPUT_ENTRIES = 50


@lru_cache(maxsize=4)
def _owner_session_manager(
    password: str,
    signing_secret: str,
    ttl_seconds: int,
) -> OwnerSessionManager:
    return OwnerSessionManager(
        password,
        signing_secret,
        ttl_seconds=ttl_seconds,
    )


def _manager_from_settings(config: Settings) -> OwnerSessionManager:
    if config.owner_password is None or config.owner_session_secret is None:
        raise AuthError(
            "OWNER_AUTH_UNAVAILABLE",
            "Owner authentication is not configured on this server",
            status_code=503,
        )
    return _owner_session_manager(
        config.owner_password.get_secret_value(),
        config.owner_session_secret.get_secret_value(),
        config.owner_session_ttl_seconds,
    )


def _task_definition_row(
    config: Settings, task_id: str
) -> tuple[TaskDefinition | None, dict | None]:
    """Read the current YAML definition without touching the Session.

    A read-only fallback Task row must never trigger an INSERT from a console GET.
    """

    loaded, error = _configured_tasks(config)
    for item in loaded:
        if item["task_id"] == task_id:
            return _configured_task_definition(item), error
    return None, error


class SessionLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=4096)


class RunAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_stopped: bool = False


class ProcessRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    stages: list[str] = Field(
        min_length=1,
        max_length=4,
        json_schema_extra={"uniqueItems": True},
    )
    confirm_stopped: bool = False


class RunContinue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumed_from_run_id: UUID


@app.middleware("http")
async def owner_access_control(request: Request, call_next):
    """Authenticate every API route except health and login.

    Keeping this as middleware covers later routes as well as the existing
    report/original routes. It also makes a future omission of a route-level
    dependency fail closed.
    """

    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    try:
        config = settings()
        manager = _manager_from_settings(config)
        token = request.cookies.get(SESSION_COOKIE)
        session = manager.verify(token or "")
        if (
            request.method in MUTATING_METHODS
        ):
            csrf = request.headers.get("X-CSRF-Token", "")
            if not csrf or not secrets.compare_digest(csrf, session.csrf_token):
                raise AuthError(
                    "CSRF_TOKEN_INVALID",
                    "A valid CSRF token is required",
                    status_code=403,
                )
        request.state.owner_session = session
        request.state.owner_manager = manager
        request.state.settings = config
    except AuthError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    return await call_next(request)


SNAPSHOT_UNAVAILABLE = "SNAPSHOT_STORAGE_UNAVAILABLE"
SAFE_TEXT_CONTENT_TYPES = HTML_CONTENT_TYPES | TEXT_CONTENT_TYPES
_SAFE_DOWNLOAD_SUFFIXES = frozenset(
    {
        ".csv",
        ".htm",
        ".html",
        ".json",
        ".pdf",
        ".txt",
        ".xml",
        ".xhtml",
    }
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/session/login")
def login(
    payload: SessionLogin,
    request: Request,
    response: Response,
) -> dict:
    config = settings()
    try:
        manager = _manager_from_settings(config)
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.to_dict()["detail"],
        ) from exc
    client_key = request.client.host if request.client is not None else "unknown"
    if not manager.login_allowed(client_key):
        response = JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "OWNER_LOGIN_RATE_LIMITED",
                    "message": "Too many failed Owner login attempts",
                }
            },
        )
        response.headers["Retry-After"] = str(
            manager.retry_after_seconds(client_key)
        )
        return response
    if not manager.verify_password(payload.password):
        manager.record_failed_login(client_key)
        raise HTTPException(
            status_code=401,
            detail={
                "code": "OWNER_CREDENTIALS_INVALID",
                "message": "The Owner credentials are invalid",
            },
        )
    manager.clear_failed_logins(client_key)
    token, session = manager.issue()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=manager.ttl_seconds,
        expires=session.expires_at,
        httponly=True,
        secure=config.owner_cookie_secure,
        samesite="strict",
        path="/",
    )
    return {
        "authenticated": True,
        "session": {
            "issued_at": session.issued_at,
            "expires_at": session.expires_at,
            "csrf_token": session.csrf_token,
        },
    }


@app.post("/api/session/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings().owner_cookie_secure,
        samesite="strict",
    )
    return {"authenticated": False}


@app.get("/api/session")
def session_detail(request: Request) -> dict:
    session: OwnerSession = request.state.owner_session
    return {
        "authenticated": True,
        "session": {
            "issued_at": session.issued_at,
            "expires_at": session.expires_at,
            "csrf_token": session.csrf_token,
        },
    }


@app.get("/api/overview")
def overview() -> dict:
    config = settings()
    configured_sources, source_error = _configured_sources(config)
    configured_tasks, task_error = _configured_tasks(config)
    with session_factory()() as session:
        recent_run_rows = session.scalars(
            select(TaskRun)
            .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            .limit(8)
        ).all()
        recent_run_ids = [run.id for run in recent_run_rows]
        recent_step_counts = (
            dict(
                session.execute(
                    select(
                        TaskStepResult.run_id,
                        func.count(TaskStepResult.id),
                    )
                    .where(TaskStepResult.run_id.in_(recent_run_ids))
                    .group_by(TaskStepResult.run_id)
                ).all()
            )
            if recent_run_ids
            else {}
        )
        recent_documents = session.scalars(
            select(Document)
            .order_by(Document.collected_at.desc(), Document.id.desc())
            .limit(5)
        ).all()
        recent_stage_rows: dict[
            UUID, dict[str, DocumentProcessingState]
        ] = defaultdict(dict)
        for state in (
            session.scalars(
                select(DocumentProcessingState).where(
                    DocumentProcessingState.document_id.in_(
                        [document.id for document in recent_documents]
                    )
                )
            ).all()
            if recent_documents
            else []
        ):
            recent_stage_rows[state.document_id][state.stage] = state
        source_summaries = _source_payloads(session, config, limit=500)
        failed_sources = sorted(
            (
                item
                for item in source_summaries
                if item["collection"]["status"]
                in {"source_failure", "partial"}
            ),
            key=lambda item: item["collection"].get("latest_attempt_at") or "",
            reverse=True,
        )[:10]
        active_run_count = (
            session.scalar(
                select(func.count(TaskRun.id)).where(
                    TaskRun.status.in_(("queued", "running"))
                )
            )
            or 0
        )
        failed_run_count = (
            session.scalar(
                select(func.count(TaskRun.id)).where(TaskRun.status == "failed")
            )
            or 0
        )
        backlog = _backlog_counts(session)
        return {
            "collection": {
                "configured_sources": len(configured_sources),
                "partial_sources": failed_sources,
                "source_errors": source_error,
            },
            "processing": backlog,
            "processing_state": _processing_overview(backlog),
            "runs": {
                "active_count": active_run_count,
                "failed_count": failed_run_count,
                "recent": [
                    _run_summary(
                        session,
                        run,
                        step_count=int(
                            recent_step_counts.get(run.id, 0)
                        ),
                    )
                    for run in recent_run_rows
                ],
            },
            "reports": _recent_reports(session),
            "recent_documents": [
                {
                    **_document_payload(document),
                    "processing_stages": _processing_stage_map_from_rows(
                        recent_stage_rows[document.id]
                    ),
                }
                for document in recent_documents
            ],
            "task_count": len(configured_tasks),
            "configuration_errors": {
                "sources": source_error,
                "tasks": task_error,
            },
        }


def _processing_overview(backlog: dict) -> dict:
    by_status = backlog.get("by_status", {})
    pending = int(by_status.get("pending", 0))
    running = int(by_status.get("running", 0))
    failed = int(by_status.get("failed", 0))
    return {
        "status": (
            "failed"
            if failed
            else "running"
            if running
            else "pending"
            if pending
            else "clear"
        ),
        "pending": pending,
        "running": running,
        "failed": failed,
    }


@app.get("/api/sources")
def sources(
    limit: int = Query(200, ge=1, le=500),
    cursor: str | None = Query(None, max_length=120),
) -> dict:
    config = settings()
    with session_factory()() as session:
        items = _source_payloads(
            session,
            config,
            after_source_id=cursor,
            limit=limit + 1,
        )
    has_more = len(items) > limit
    page = items[:limit]
    return {
        "items": page,
        "next_cursor": (
            page[-1]["source_id"]
            if has_more and page
            else None
        ),
        "has_more": has_more,
        "limit": len(page),
        "configuration_error": _configured_sources(config)[1],
    }


def _ordered_unique_page(
    sequences: Iterable[Iterable[str]],
    *,
    limit: int,
) -> list[str]:
    output: list[str] = []
    previous: str | None = None
    sorted_sequences = [
        sorted_values
        for sequence in sequences
        if (sorted_values := sorted(sequence))
    ]
    for value in merge(*sorted_sequences):
        if value == previous:
            continue
        previous = value
        output.append(value)
        if len(output) >= limit:
            break
    return output


@app.get("/api/tasks")
def tasks(
    limit: int = Query(200, ge=1, le=500),
    cursor: str | None = Query(None, max_length=120),
) -> dict:
    config = settings()
    configured, error = _configured_tasks(config)
    configured_by_id = {item["task_id"]: item for item in configured}
    fallback_definitions = {
        item["task_id"]: _configured_task_definition(item)
        for item in configured
    }
    with session_factory()() as session:
        statement = select(TaskDefinition).order_by(TaskDefinition.id)
        if cursor is not None:
            statement = statement.where(TaskDefinition.id > cursor)
        persisted_ids = list(
            session.scalars(statement.limit(limit + 1)).all()
        )
        persisted_ids = [definition.id for definition in persisted_ids]
        configured_ids = [
            item["task_id"]
            for item in configured
            if cursor is None or item["task_id"] > cursor
        ]
        configured_ids.sort()
        task_ids = _ordered_unique_page(
            (persisted_ids, configured_ids),
            limit=limit + 1,
        )
        definitions = {
            definition.id: definition
            for definition in (
                session.scalars(
                    select(TaskDefinition).where(
                        TaskDefinition.id.in_(task_ids)
                    )
                ).all()
                if task_ids
                else []
            )
        }
        history = _task_run_history(session, task_ids=task_ids)
        history_step_counts = (
            dict(
                session.execute(
                    select(
                        TaskStepResult.run_id,
                        func.count(TaskStepResult.id),
                    )
                    .where(
                        TaskStepResult.run_id.in_(
                            [run.id for run in history]
                        )
                    )
                    .group_by(TaskStepResult.run_id)
                ).all()
            )
            if history
            else {}
        )
        items: list[dict] = []
        for task_id in task_ids:
            definition = definitions.get(task_id)
            task_config = configured_by_id.get(task_id)
            if definition is None and task_config is not None:
                definition = fallback_definitions.get(task_id)
            if definition is None:
                continue
            run_summaries = [
                _run_summary(
                    session,
                    run,
                    step_count=int(history_step_counts.get(run.id, 0)),
                )
                for run in history
                if run.task_id == task_id
            ]
            items.append(
                _task_payload(
                    definition,
                    run_summaries,
                    configured=task_config is not None,
                    task_config=task_config,
                )
            )
    has_more = len(items) > limit
    page = items[:limit]
    return {
        "items": page,
        "next_cursor": (
            page[-1]["task_id"]
            if has_more and page
            else None
        ),
        "has_more": has_more,
        "limit": len(page),
        "configuration_error": error,
    }


@app.get("/api/runs")
def runs(
    task_id: str | None = Query(None, max_length=120),
    status: str | None = Query(None, max_length=16),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
) -> dict:
    with session_factory()() as session:
        statement = select(TaskRun).order_by(
            TaskRun.created_at.desc(), TaskRun.id.desc()
        )
        if task_id is not None:
            statement = statement.where(TaskRun.task_id == task_id)
        if status is not None:
            statement = statement.where(TaskRun.status == status)
        after = _parse_cursor(cursor, name="Run")
        if after is not None:
            statement = statement.where(
                or_(
                    TaskRun.created_at < after[0],
                    and_(
                        TaskRun.created_at == after[0],
                        TaskRun.id < after[1],
                    ),
                )
            )
        rows = session.scalars(statement.limit(limit + 1)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        run_ids = [run.id for run in rows]
        step_counts = (
            dict(
                session.execute(
                    select(
                        TaskStepResult.run_id,
                        func.count(TaskStepResult.id),
                    )
                    .where(TaskStepResult.run_id.in_(run_ids))
                    .group_by(TaskStepResult.run_id)
                ).all()
            )
            if run_ids
            else {}
        )
        items = [
            _run_summary(
                session,
                run,
                step_count=int(step_counts.get(run.id, 0)),
            )
            for run in rows
        ]
    last = rows[-1] if rows else None
    return _page(
        items,
        cursor=_cursor(last.created_at, last.id) if last is not None else None,
        has_more=has_more,
    )


@app.get("/api/runs/{run_id}")
def run_detail(run_id: UUID) -> dict:
    with session_factory()() as session:
        run = session.get(TaskRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Unknown Task Run")
        steps = session.scalars(
            select(TaskStepResult)
            .where(TaskStepResult.run_id == run.id)
            .order_by(TaskStepResult.position)
        ).all()
        calls = session.scalars(
            select(ModelCall)
            .where(ModelCall.run_id == run.id)
            .order_by(ModelCall.step_id, ModelCall.attempt_number)
        ).all()
        report = session.scalar(
            select(Report).where(Report.run_id == run.id)
        )
        package = session.scalar(
            select(EvidencePackage).where(EvidencePackage.run_id == run.id)
        )
        failed_steps = session.scalars(
            select(TaskStepResult)
            .where(
                TaskStepResult.run_id == run.id,
                TaskStepResult.status == "failed",
            )
            .order_by(TaskStepResult.position)
        ).all()
        retry_of = (
            session.get(TaskRun, run.retry_of_run_id)
            if run.retry_of_run_id is not None
            else None
        )
        return {
            **_run_summary(session, run),
            "configuration": _safe_configuration_snapshot(
                run.config_snapshot
            ),
            "model_snapshot": _safe_model_snapshot(run.model_snapshot),
            "steps": [_step_payload(step) for step in steps],
            "model_calls": [_model_call_payload(call) for call in calls],
            "retry_of": (
                {
                    "run_id": str(retry_of.id),
                    "failed_steps": _failed_steps_preview(
                        list(
                            session.scalars(
                                select(TaskStepResult)
                                .where(
                                    TaskStepResult.run_id == retry_of.id,
                                    TaskStepResult.status == "failed",
                                )
                                .order_by(TaskStepResult.position)
                            ).all()
                        )
                    ),
                }
                if retry_of is not None
                else None
            ),
            "evidence_package": (
                {
                    "evidence_package_id": str(package.id),
                    "selected_count": package.selected_count,
                    "evidence_group_count": package.coverage.get(
                        "evidence_group_count"
                    )
                    if isinstance(package.coverage, dict)
                    else None,
                    "coverage": package.coverage,
                }
                if package is not None
                else None
            ),
            "report": (
                {
                    "report_id": str(report.id),
                    "title": report.title,
                    "created_at": _iso(report.created_at),
                }
                if report is not None
                else None
            ),
            "actions": {
                "can_retry": run.status in {"failed", "cancelled"},
                "can_continue": (
                    run.status in {"failed", "cancelled", "succeeded"}
                    and run.change_scan_completed is False
                ),
                "can_end": run.status in {"queued", "running"},
            },
            "failed_steps": _failed_steps_preview(failed_steps),
        }


@app.get("/api/processing")
def processing(
    stage: str | None = Query(None),
    status: str | None = Query(None),
    document_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=300),
) -> dict:
    stages = [stage] if stage is not None else None
    statuses = [status] if status is not None else None
    if stage is not None and stage not in PROCESSING_STAGES:
        raise HTTPException(status_code=422, detail="Unknown processing stage")
    if status is not None and status not in PROCESSING_STAGE_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown processing status")
    config = settings()
    try:
        registry = build_stage_registry(
            config.models_config_path,
            sources_config_path=config.sources_config_path,
        )
        with session_factory()() as session:
            after = _parse_processing_cursor(cursor)
            rows = _processing_page(
                session,
                document_ids=[document_id] if document_id is not None else None,
                stages=stages,
                statuses=statuses,
                after=after,
                limit=limit,
            )
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = describe_stages(
                session,
                registry,
                document_ids=[row.document_id for row in rows],
                stages=stages,
                statuses=statuses,
                limit=max(len(rows), 1),
            )
            order = {
                (row.document_id, row.stage): index
                for index, row in enumerate(rows)
            }
            items.sort(
                key=lambda item: order.get(
                    (UUID(item["document_id"]), item["stage"]),
                    len(order),
                )
            )
            page_document_ids = {row.document_id for row in rows}
            processing_rows = (
                list(
                    session.scalars(
                        select(DocumentProcessingState).where(
                            DocumentProcessingState.document_id.in_(
                                page_document_ids
                            )
                        )
                    ).all()
                )
                if page_document_ids
                else []
            )
            stage_rows: dict[
                UUID, dict[str, DocumentProcessingState]
            ] = defaultdict(dict)
            for state in processing_rows:
                stage_rows[state.document_id][state.stage] = state
            documents = {
                row.id: {
                    **_document_payload(row),
                    "processing_stages": _processing_stage_map_from_rows(
                        stage_rows[row.id]
                    ),
                }
                for row in session.scalars(
                    select(Document).where(
                        Document.id.in_(page_document_ids)
                    )
                ).all()
            }
            counts = _backlog_counts(session)
            last_cursor = (
                _processing_cursor(session, rows[-1])
                if has_more and rows
                else None
            )
        for item in items:
            item["document"] = documents.get(UUID(item["document_id"]))
            item["next_attempt_at"] = item.get("next_attempt_at")
        return {
            "items": items,
            "next_cursor": last_cursor,
            "has_more": has_more,
            "limit": len(items),
            "available_stages": registry.available_stages(),
            "counts": counts,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - capability setup stays safe
        logger.warning(
            "Processing backlog query failed: %s", type(exc).__name__
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROCESSING_UNAVAILABLE",
                "message": "Processing backlog could not be read",
            },
        ) from exc


@app.post("/api/processing/retry")
def processing_retry(payload: ProcessRetry) -> dict:
    invalid = [
        stage for stage in payload.stages if stage not in PROCESSING_STAGES
    ]
    if invalid or not payload.stages or len(set(payload.stages)) != len(payload.stages):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROCESSING_STAGE_INVALID",
                "message": "Provide unique supported processing stages",
            },
        )
    config = settings()
    try:
        with session_factory()() as session:
            if get_document(session, payload.document_id) is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "DOCUMENT_NOT_FOUND",
                        "message": "Unknown Document",
                    },
                )
        registry = build_stage_registry(
            config.models_config_path,
            sources_config_path=config.sources_config_path,
        )
        executions, errors = run_stages_for_documents(
            session_factory(),
            config,
            registry,
            [payload.document_id],
            payload.stages,
            owner_token=f"owner-api-{secrets.token_hex(16)}",
            execute=False,
            confirm_stopped=payload.confirm_stopped,
        )
        if not executions and not errors:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "Unknown Document",
                },
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - processing capability setup
        logger.warning(
            "Processing retry setup failed: %s", type(exc).__name__
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROCESSING_UNAVAILABLE",
                "message": "Processing retry could not be prepared",
            },
        ) from exc
    return {
        "document_id": str(payload.document_id),
        "accepted": not errors,
        "executions": [item.to_dict() for item in executions],
        "errors": errors,
    }


@app.post("/api/tasks/{task_id}/runs")
def trigger_task(task_id: str) -> dict:
    config = settings()
    with session_factory()() as session:
        active = get_active_run(session, task_id)
        if active is not None:
            return {
                **_run_summary(session, active),
                "created": False,
            }
    try:
        configuration = load_task_configuration(config.tasks_config_path)
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
    except ExecutionConfigError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "TASK_NOT_FOUND" else 409,
            detail=exc.to_dict(),
        ) from exc
    with session_factory()() as session:
        try:
            claimed = create_or_get_run(
                session,
                task,
                agents,
                config_path=config.models_config_path if agents else None,
                tools_config_path=config.tools_config_path,
                triggered_by="manual",
            )
        except ValueError as exc:
            logger.warning(
                "Task queue model configuration is unavailable for task %s: %s",
                task_id,
                type(exc).__name__,
            )
            raise _model_configuration_unavailable(exc) from exc
        return {
            **_run_summary(session, claimed.run),
            "created": claimed.created,
        }


@app.post("/api/runs/{run_id}/retry")
def retry_task_run(run_id: UUID) -> dict:
    with session_factory()() as session:
        try:
            claimed = retry_run(session, run_id)
        except ExecutionError as exc:
            raise _execution_error(exc) from exc
        steps = session.scalars(
            select(TaskStepResult)
            .where(TaskStepResult.run_id == run_id)
            .order_by(TaskStepResult.position)
        ).all()
        return {
            **_run_summary(session, claimed.run),
            "created": claimed.created,
            "retried_from": {
                "run_id": str(run_id),
                "failed_steps": _failed_steps_preview(steps),
            },
        }


@app.post("/api/runs/{run_id}/continue")
def continue_task_run(run_id: UUID, payload: RunContinue) -> dict:
    config = settings()
    if payload.resumed_from_run_id != run_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_CONTINUATION_MISMATCH",
                "message": (
                    "The continuation confirmation must name this same Run"
                ),
            },
        )
    with session_factory()() as session:
        source = session.get(TaskRun, run_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Unknown Task Run")
        task_definition = session.get(TaskDefinition, source.task_id)
        yaml_definition = None
        if task_definition is None:
            yaml_definition, _ = _task_definition_row(config, source.task_id)
        if (
            (task_definition is None and yaml_definition is None)
            or (
                task_definition is not None
                and task_definition.type != "research"
            )
            or (
                yaml_definition is not None
                and yaml_definition.type != "research"
            )
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_CONTINUATION_UNSUPPORTED",
                    "message": "Only a research Run can continue a scan",
                },
            )
        if source.change_scan_completed is not False:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_SCAN_COMPLETE",
                    "message": (
                        "This Run has no unfinished scan to continue"
                    ),
                },
            )
        active = get_active_run(session, source.task_id)
        if active is not None:
            if active.id == source.id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "RUN_CONTINUATION_ACTIVE",
                        "message": (
                            "The source Run is still active and cannot be "
                            "continued"
                        ),
                    },
                )
            return _continuation_payload(
                _run_summary(session, active),
                source_run_id=source.id,
                resumed_from_run_id=active.retry_of_run_id,
                created=False,
            )
        try:
            configuration = load_task_configuration(config.tasks_config_path)
            task = configuration.task_map.get(source.task_id)
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
            claimed = create_or_get_run(
                session,
                task,
                agents,
                config_path=config.models_config_path if agents else None,
                tools_config_path=config.tools_config_path,
                triggered_by="manual",
            )
        except ExecutionConfigError as exc:
            raise HTTPException(
                status_code=409 if exc.code != "TASK_NOT_FOUND" else 404,
                detail=exc.to_dict(),
            ) from exc
        except ValueError as exc:
            logger.warning(
                "Task queue model configuration is unavailable for run %s: %s",
                run_id,
                type(exc).__name__,
            )
            raise _model_configuration_unavailable(exc) from exc
        except ExecutionError as exc:
            raise _execution_error(exc) from exc
        return _continuation_payload(
            _run_summary(session, claimed.run),
            source_run_id=source.id,
            resumed_from_run_id=claimed.run.retry_of_run_id,
            created=claimed.created,
        )


@app.post("/api/runs/{run_id}/end")
def end_task_run(run_id: UUID, payload: RunAction) -> dict:
    if not payload.confirm_stopped:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OWNER_CONFIRMATION_REQUIRED",
                "message": (
                    "Confirm that the old execution process stopped before "
                    "ending the Run"
                ),
            },
        )
    with session_factory()() as session:
        try:
            ended = end_run(session, run_id)
        except ExecutionError as exc:
            raise _execution_error(exc) from exc
        return _run_summary(session, ended)


@app.get("/api/documents")
def documents(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
) -> dict:
    with session_factory()() as session:
        return _document_list_page(
            session,
            limit=limit,
            after=_parse_cursor(cursor, name="Document"),
        )


@app.get("/api/facts")
def facts(
    source_id: list[str] | None = Query(None),
    document_id: list[UUID] | None = Query(None),
    fact_type: list[str] | None = Query(None),
    symbol: list[str] | None = Query(None),
    metric_id: list[str] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
) -> dict:
    """Read stored Facts by source, document, type, symbol or metric.

    This is a bounded structured query over already-extracted data. It never runs
    extraction, and a Fact always keeps the Document version and field or Chunk it
    came from, so a reader can resolve it back to the original bytes.
    """

    filters = FactFilters(
        source_ids=tuple(source_id or ()),
        document_ids=tuple(document_id or ()),
        fact_types=tuple(fact_type or ()),
        symbols=tuple(symbol or ()),
        metric_ids=tuple(metric_id or ()),
    )
    after = _parse_cursor(cursor, name="Fact")
    with session_factory()() as session:
        rows = _search_facts_page(
            session,
            filters,
            limit=limit,
            after=after,
        )
    page_rows = rows[:limit]
    return {
        "count": len(page_rows),
        "truncated": len(rows) > limit,
        "items": [fact_to_dict(row) for row in rows[:limit]],
        "next_cursor": (
            _cursor(page_rows[-1].created_at, page_rows[-1].id)
            if len(rows) > limit
            else None
        ),
        "has_more": len(rows) > limit,
        "limit": len(page_rows),
    }


@app.get("/api/documents/{document_id}/facts")
def document_facts(
    document_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
) -> dict:
    """Every stored Fact of one Document version, in creation order."""

    after = _parse_cursor(cursor, name="Document Fact")
    with session_factory()() as session:
        if get_document(session, document_id) is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        rows = list_document_facts(
            session,
            document_id,
            limit=limit + 1,
            after=after,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1] if rows else None
    return {
        "count": len(rows),
        "items": [fact_to_dict(row) for row in rows],
        "next_cursor": (
            _cursor(last.created_at, last.id)
            if has_more and last is not None
            else None
        ),
        "has_more": has_more,
        "limit": len(rows),
    }


@app.get("/api/facts/{fact_id}")
def fact_detail(
    fact_id: UUID,
    version_cursor: int | None = Query(None, ge=0),
    version_limit: int = Query(100, ge=1, le=500),
) -> dict:
    with session_factory()() as session:
        fact = session.get(Fact, fact_id)
        if fact is None:
            raise HTTPException(status_code=404, detail="Unknown fact")
        document = get_document(session, fact.document_id)
        versions = (
            list_document_versions(
                session,
                document.source_id,
                document.dedupe_key,
                after_version=version_cursor,
                limit=version_limit + 1,
            )
            if document is not None
            else []
        )
        has_more_versions = len(versions) > version_limit
        versions = versions[:version_limit]
        return {
            "fact": fact_to_dict(fact),
            "document": _document_payload(document) if document else None,
            "document_versions": {
                "items": [
                    {
                        "document_id": str(version.id),
                        "version": version.version,
                        "content_hash": version.content_hash,
                        "completeness": version.completeness,
                        "collected_at": version.collected_at,
                    }
                    for version in versions
                ],
                "next_cursor": (
                    versions[-1].version
                    if has_more_versions and versions
                    else None
                ),
                "has_more": has_more_versions,
                "limit": version_limit,
            },
        }


@app.get("/api/documents/{document_id}")
def document_detail(
    document_id: UUID,
    chunk_cursor: int | None = Query(None, ge=0),
    chunk_limit: int = Query(100, ge=1, le=500),
    version_cursor: int | None = Query(None, ge=0),
    version_limit: int = Query(100, ge=1, le=500),
) -> dict:
    config = settings()
    with session_factory()() as session:
        document = get_document(session, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        states, parse_states, embedding_states, current_chunk_counts = _states(
            session, [document.id]
        )
        versions = list_document_versions(
            session,
            document.source_id,
            document.dedupe_key,
            after_version=version_cursor,
            limit=version_limit + 1,
        )
        has_more_versions = len(versions) > version_limit
        versions = versions[:version_limit]
        pointer = states.get((document.source_id, document.dedupe_key))
        current_chunk_count = _current_chunk_count(session, document.id)
        first_chunk_position = -1 if chunk_cursor is None else chunk_cursor
        chunks = _parse_chunks_after(
            session,
            document.id,
            after_position=first_chunk_position,
            limit=chunk_limit + 1,
        )
        has_more_chunks = len(chunks) > chunk_limit
        chunks = chunks[:chunk_limit]
        return {
            "document": _document_payload(document),
            "processing_stages": _processing_stage_map(session, document.id),
            "collection_state": _collection_state(states, document),
            "parse_state": _parse_state(parse_states.get(document.id)),
            "embedding_state": _embedding_state(
                embedding_states.get(document.id),
                parse_states.get(document.id),
                current_chunk_counts.get(document.id, 0),
            ),
            "is_current_collection_pointer": bool(
                pointer is not None and pointer.latest_document_id == document.id
            ),
            "versions": {
                "items": [
                    {
                        "document_id": str(version.id),
                        "version": version.version,
                        "content_hash": version.content_hash,
                        "content_size": version.content_size,
                        "content_type": version.content_type,
                        "completeness": version.completeness,
                        "collected_at": version.collected_at,
                        "is_current_collection_pointer": bool(
                            pointer is not None
                            and pointer.latest_document_id == version.id
                        ),
                    }
                    for version in versions
                ],
                "next_cursor": (
                    versions[-1].version
                    if has_more_versions and versions
                    else None
                ),
                "has_more": has_more_versions,
                "limit": version_limit,
            },
            "current_chunks": {
                "count": current_chunk_count,
                "items": [
                    {
                        "chunk_id": str(chunk.id),
                        "position": chunk.position,
                        "text": chunk.text,
                        "locator": chunk.locator,
                    }
                    for chunk in chunks
                ],
                "next_cursor": (
                    str(chunks[-1].position)
                    if has_more_chunks and chunks
                    else None
                ),
                "limit": chunk_limit,
            },
            "original": _original_descriptor(config, document),
        }


@app.get("/api/documents/{document_id}/original")
def document_original(document_id: UUID, download: bool = Query(False)):
    config = settings()
    with session_factory()() as session:
        document = get_document(session, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        payload = _read_original_bytes(config, document)
        recorded_hash = document.content_hash
        content_type = document.content_type
        version = document.version
        original_url = _safe_source_url(document.original_url)
        title = document.title

    digest = hashlib.sha256(payload).hexdigest()
    safe_headers: dict[str, str] = {}
    if original_url:
        safe_headers["X-Source-Url"] = original_url.encode(
            "ascii", "backslashreplace"
        ).decode("ascii")
    headers = {
        # The body is the stored bytes unchanged, so the recorded hash must match.
        "X-Content-Sha256": digest,
        "X-Content-Hash-Match": "true" if digest == recorded_hash else "false",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
        **safe_headers,
    }
    if digest != recorded_hash:
        logger.warning(
            "Snapshot bytes do not match the stored hash for document %s", document_id
        )
    media_type = _serve_media_type(content_type)
    if download:
        suffix = Path(title).suffix.lower()
        if suffix not in _SAFE_DOWNLOAD_SUFFIXES or len(suffix) > 10:
            suffix = (
                ".txt"
                if media_type == "text/plain"
                else ".json"
                if content_type.lower().startswith("application/json")
                else ".bin"
            )
        headers["Content-Disposition"] = (
            f'attachment; filename="document-{document_id}-v{version}{suffix}"'
        )
    return Response(content=payload, media_type=media_type, headers=headers)


@app.get("/api/search")
def search(
    query: str | None = Query(None, max_length=4000),
    exact: str | None = Query(None, max_length=64),
    chunk_id: UUID | None = Query(None),
    radius: int = Query(1, ge=0, le=5),
    source_id: list[str] | None = Query(None),
    published_from: datetime | None = Query(None),
    published_to: datetime | None = Query(None),
    collected_from: datetime | None = Query(None),
    collected_to: datetime | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    """Search saved evidence only.

    Semantic search creates one query embedding and reads the current pgvector
    index. Exact and context modes never parse, index or write a Document.
    """

    modes = sum(bool(value) for value in (query, exact, chunk_id))
    if source_id is not None and (
        len(source_id) > 20
        or any(not item.strip() or len(item) > 120 for item in source_id)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SEARCH_FILTER_INVALID",
                "message": "Search source filters are invalid",
            },
        )
    if modes != 1:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SEARCH_MODE_INVALID",
                "message": "Choose exactly one of query, exact or chunk_id",
            },
        )
    if chunk_id is not None and any(
        (
            source_id,
            published_from,
            published_to,
            collected_from,
            collected_to,
        )
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SEARCH_FILTER_INVALID",
                "message": "Source and time filters require semantic or exact search",
            },
        )
    try:
        filters = SearchFilters(
            source_ids=tuple(source_id or ()),
            published_from=published_from,
            published_to=published_to,
            collected_from=collected_from,
            collected_to=collected_to,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SEARCH_FILTER_INVALID",
                "message": "Search filters are invalid",
            },
        ) from exc
    config = settings()
    try:
        with session_factory()() as session:
            if query is not None:
                targets, client = load_embedding_client(
                    config.models_config_path
                )
                result = semantic_search(
                    session,
                    config,
                    targets,
                    client,
                    query,
                    filters=filters,
                    limit=limit,
                )
            elif exact is not None:
                result = _exact_result_with_coverage(
                    session, config, exact, filters, limit
                )
            else:
                assert chunk_id is not None
                result = context_search(session, str(chunk_id), radius=radius)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SEARCH_INVALID",
                "message": "Search request or model configuration is invalid",
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 - model/storage failures stay safe
        logger.warning("Evidence search failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SEARCH_UNAVAILABLE",
                "message": "Evidence search could not be completed",
            },
        ) from exc
    return {
        "mode": (
            "semantic" if query is not None else "exact" if exact else "context"
        ),
        "query": query or exact or str(chunk_id),
        "filters": _search_filter_payload(result.filters),
        "coverage": _search_coverage_payload(result.coverage),
        "truncated": result.truncated,
        "hits": hits_to_dicts(result.hits),
    }


@app.get("/api/reports")
def reports(
    limit: int = Query(50, ge=1, le=200),
    task_id: str | None = Query(None, max_length=120),
    cursor: str | None = Query(None, max_length=200),
) -> dict:
    with session_factory()() as session:
        statement = select(Report).order_by(
            Report.created_at.desc(), Report.id.desc()
        )
        if task_id is not None:
            statement = statement.where(Report.task_id == task_id)
        after = _parse_cursor(cursor, name="Report")
        if after is not None:
            statement = statement.where(
                or_(
                    Report.created_at < after[0],
                    and_(
                        Report.created_at == after[0],
                        Report.id < after[1],
                    ),
                )
            )
        rows = session.scalars(statement.limit(limit + 1)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        runs = _runs(session, [report.run_id for report in rows])
        items = [
            {
                "report_id": str(report.id),
                "run_id": str(report.run_id),
                "analysis_id": str(report.analysis_id),
                "task_id": report.task_id,
                "title": report.title,
                "created_at": report.created_at,
                "run_status": (
                    runs[report.run_id].status if report.run_id in runs else None
                ),
            }
            for report in rows
        ]
    last = rows[-1] if rows else None
    return _page(
        items,
        cursor=_cursor(last.created_at, last.id) if last is not None else None,
        has_more=has_more,
    )


@app.get("/api/reports/{report_id}")
def report_detail(report_id: UUID) -> dict:
    config = settings()
    with session_factory()() as session:
        report, analysis, package = _report_chain(session, report_id)
        run = session.get(TaskRun, report.run_id)
        entries, states = _resolve_evidence(
            session, config, _report_research_refs(analysis, package, run)
        )
        cited = _cited_keys(analysis)
        return {
            "report": {
                "report_id": str(report.id),
                "run_id": str(report.run_id),
                "analysis_id": str(report.analysis_id),
                "task_id": report.task_id,
                "title": report.title,
                "content": report.content,
                "created_at": report.created_at,
            },
            "run": _run_payload(run),
            "analysis": {
                "summary": analysis.summary,
                "findings": analysis.findings,
                "open_questions": analysis.open_questions,
                "evidence_refs": analysis.evidence_refs,
                "created_at": analysis.created_at,
            },
            "evidence_package": _package_payload(package),
            "research_inputs": _research_inputs_payload(analysis, run),
            "evidence": entries,
            "cited_chunk_ids": [
                entry["chunk_id"]
                for entry in entries
                if (entry["chunk_id"], entry["document_id"]) in cited
            ],
            "coverage_gaps": _gaps(
                package, entries, run, analysis.research_inputs
            ),
            "latest_collection_states": [
                _collection_state_payload(state)
                for state in sorted(
                    states.values(),
                    key=lambda item: (item.source_id, item.dedupe_key),
                )
            ],
        }


@app.get("/api/reports/{report_id}/evidence/{chunk_id}")
def report_evidence(
    report_id: UUID,
    chunk_id: UUID,
    version_cursor: int | None = Query(None, ge=0),
    version_limit: int = Query(100, ge=1, le=500),
) -> dict:
    config = settings()
    with session_factory()() as session:
        report, analysis, package = _report_chain(session, report_id)
        run = session.get(TaskRun, report.run_id)
        matching = [
            item
            for item in _report_research_refs(analysis, package, run)
            if _parse_id(item.get("chunk_id")) == chunk_id
        ]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail="Chunk is not part of this report's Evidence Package",
            )
        entries, states = _resolve_evidence(session, config, matching)
        entry = entries[0]
        document = entry["document"]
        versions: list[dict] = []
        version_rows = []
        has_more_versions = False
        if document["source_id"] and document["dedupe_key"]:
            version_rows = list_document_versions(
                session,
                document["source_id"],
                document["dedupe_key"],
                after_version=version_cursor,
                limit=version_limit + 1,
            )
            has_more_versions = len(version_rows) > version_limit
            version_rows = version_rows[:version_limit]
            versions = [
                {
                    "document_id": str(version.id),
                    "version": version.version,
                    "content_hash": version.content_hash,
                    "completeness": version.completeness,
                    "collected_at": version.collected_at,
                }
                for version in version_rows
            ]
    return {
            "report_id": str(report.id),
            "run_id": str(report.run_id),
            "evidence_package_id": str(package.id),
            "as_of": package.as_of,
            "evidence": entry,
            "document_versions": {
                "items": versions,
                "next_cursor": (
                    version_rows[-1].version
                    if has_more_versions and version_rows
                    else None
                ),
                "has_more": has_more_versions,
                "limit": version_limit,
            },
            "latest_collection_state": _collection_state_payload(
                states.get((document["source_id"], document["dedupe_key"]))
            ),
        }


def _report_chain(
    session: Session, report_id: UUID
) -> tuple[Report, Analysis, EvidencePackage]:
    """Read a report with the Analysis and Evidence Package it was built from."""
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Unknown report")
    analysis = session.get(Analysis, report.analysis_id)
    if analysis is None:
        raise HTTPException(
            status_code=409, detail="Report is missing its saved Analysis"
        )
    package = session.get(EvidencePackage, analysis.evidence_package_id)
    if package is None:
        raise HTTPException(
            status_code=409, detail="Analysis is missing its Evidence Package"
        )
    return report, analysis, package


def _document_payload(document: Document) -> dict:
    return {
        "id": str(document.id),
        "source_id": document.source_id,
        "dedupe_key": document.dedupe_key,
        "version": document.version,
        "title": document.title,
        "original_url": _safe_source_url(document.original_url),
        "published_at": document.published_at,
        "source_updated_at": document.source_updated_at,
        "collected_at": document.collected_at,
        "trust_tier": document.trust_tier_snapshot,
        "completeness": document.completeness,
        "fetch_error": _safe_external_payload(document.fetch_error),
        "content_type": document.content_type,
        "content_size": document.content_size,
        "content_hash": document.content_hash,
        "snapshot_key": document.snapshot_key,
        # The current lightweight classification of this content version, or
        # ``None`` when no classification result exists yet. It is an attribute of
        # the stored version, never a replacement for the original text.
        "classification": _safe_external_payload(document.classification),
    }


def _stored_document_payload(document: Document | None, item: dict) -> dict:
    if document is not None:
        return _document_payload(document)
    # A saved Evidence Package keeps the identity and acquisition times it used.
    # They stay readable when the row itself is missing, marked as not stored.
    return {
        "id": item.get("document_id"),
        "source_id": item.get("source_id"),
        "dedupe_key": None,
        "version": item.get("document_version"),
        "title": item.get("title"),
        "original_url": None,
        "published_at": item.get("published_at"),
        "source_updated_at": item.get("source_updated_at"),
        "collected_at": item.get("collected_at"),
        "trust_tier": None,
        "completeness": item.get("completeness"),
        "fetch_error": None,
        "content_type": None,
        "content_size": None,
        "content_hash": None,
        "snapshot_key": None,
    }


def _original_descriptor(config: Settings, document: Document | None) -> dict:
    if document is None:
        return {
            "stored": False,
            "readable": False,
            "storage_key": None,
            "content_type": None,
            "content_size": None,
            "content_hash": None,
            "endpoint": None,
        }
    return {
        "stored": True,
        "readable": _snapshot_configured(config),
        "storage_key": document.snapshot_key,
        "content_type": document.content_type,
        "content_size": document.content_size,
        "content_hash": document.content_hash,
        # This is an internal, authenticated path for the exact stored version.
        # It is intentionally relative so the response never exposes a storage
        # or Provider endpoint.
        "endpoint": f"/api/documents/{document.id}/original",
        "document_id": str(document.id),
    }


def _snapshot_configured(config: Settings) -> bool:
    return bool(
        config.s3_endpoint_url
        and config.s3_bucket.strip()
        and config.s3_access_key
        and config.s3_secret_key
    )


def _read_original_bytes(config: Settings, document: Document) -> bytes:
    if not _snapshot_configured(config):
        raise HTTPException(
            status_code=503,
            detail={
                "code": SNAPSHOT_UNAVAILABLE,
                "message": "Snapshot storage is not configured on this server",
            },
        )
    try:
        return read_document_bytes(config, document)
    except Exception as exc:  # noqa: BLE001 - storage failure is a stable 503
        logger.warning(
            "Snapshot read failed for document %s: %s", document.id, type(exc).__name__
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": SNAPSHOT_UNAVAILABLE,
                "message": (
                    "The stored original bytes could not be read from snapshot storage"
                ),
            },
        ) from exc


def _serve_media_type(content_type: str) -> str:
    """Never hand a stored HTML page back as HTML.

    The exact stored bytes are served, so the response hash keeps matching the
    Document's recorded evidence; only the media type is downgraded to safe text
    or an opaque stream, and `nosniff` plus the empty CSP stop a browser from
    executing anything the source published.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in SAFE_TEXT_CONTENT_TYPES:
        return "text/plain"
    return "application/octet-stream"


def _runs(session: Session, run_ids: list[UUID]) -> dict[UUID, TaskRun]:
    if not run_ids:
        return {}
    return {
        run.id: run
        for run in session.scalars(select(TaskRun).where(TaskRun.id.in_(run_ids))).all()
    }


def _run_payload(run: TaskRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "run_id": str(run.id),
        "task_id": run.task_id,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "retry_of_run_id": (
            str(run.retry_of_run_id) if run.retry_of_run_id else None
        ),
        "objective": run.objective,
        "strategy": run.strategy,
        "scope": _safe_external_payload(run.scope),
        "config_hash": run.config_hash,
        "model_snapshot": _safe_model_snapshot(run.model_snapshot),
        "research_inputs": _research_inputs_payload(None, run),
        "output": _safe_error(run.output),
        "error": _safe_error(run.error),
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _selected_chunks(package: EvidencePackage) -> list[dict]:
    selected = package.selected_chunks
    if not isinstance(selected, list):
        raise HTTPException(
            status_code=409, detail="Evidence Package selected chunks are invalid"
        )
    return [item for item in selected if isinstance(item, dict)]


def _research_ledger(
    analysis: Analysis | None,
    run: TaskRun | None,
) -> list:
    """The ledger frozen onto the report's Analysis, or the Run's own record."""

    if analysis is not None and isinstance(analysis.research_inputs, list):
        return analysis.research_inputs
    if run is not None and isinstance(run.research_inputs, list):
        return run.research_inputs
    return []


def _report_research_refs(
    analysis: Analysis,
    package: EvidencePackage,
    run: TaskRun | None,
) -> list[dict]:
    """Initial selection plus the Tool inputs the report actually adopted."""

    try:
        return merged_research_refs(package, _research_ledger(analysis, run))
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Report research inputs are invalid"
        ) from exc


def _research_inputs_payload(
    analysis: Analysis | None,
    run: TaskRun | None,
) -> dict:
    """Bounded, credential-free projection of one Run's research inputs."""

    ledger = _research_ledger(analysis, run)
    adopted: list[dict] = []
    gaps: list[dict] = []
    for item in ledger:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "input":
            adopted.append(
                _safe_external_payload(
                    {
                        "input_id": item.get("input_id"),
                        "tool_id": item.get("tool_id"),
                        "purpose": item.get("purpose"),
                        "document_id": item.get("document_id"),
                        "source_id": item.get("source_id"),
                        "document_version": item.get("document_version"),
                        "title": item.get("title"),
                        "url": _safe_source_url(item.get("url")),
                        "chunk_ids": [
                            chunk.get("chunk_id")
                            for chunk in item.get("chunks", [])
                            if isinstance(chunk, dict)
                        ],
                        "timing": item.get("timing"),
                        "retrieved_at": item.get("retrieved_at"),
                        "published_at": item.get("published_at"),
                        "collected_at": item.get("collected_at"),
                        "truncated": item.get("truncated"),
                        "token_estimate": item.get("token_estimate"),
                    }
                )
            )
        elif item.get("kind") == "gap":
            gaps.append(
                {
                    "code": item.get("code"),
                    "message": item.get("message"),
                    "tool_id": item.get("tool_id"),
                    "round": item.get("round"),
                    "recorded_at": item.get("recorded_at"),
                }
            )
    return {
        "count": len(adopted),
        "entries": adopted[:MAX_RESEARCH_INPUT_ENTRIES],
        "gaps": gaps[:MAX_RESEARCH_INPUT_ENTRIES],
    }


def _package_payload(package: EvidencePackage) -> dict:
    return {
        "evidence_package_id": str(package.id),
        "run_id": str(package.run_id),
        "objective": package.objective,
        "scope": _safe_external_payload(package.scope),
        "as_of": package.as_of,
        "selection_policy_version": package.selection_policy_version,
        "selection_budget": package.selection_budget,
        "queries": _safe_external_payload(package.queries),
        "coverage": _safe_external_payload(package.coverage),
        "candidates_discovered": package.candidates_discovered,
        "candidates_screened": package.candidates_screened,
        "selected_count": package.selected_count,
        "unexamined_count": package.unexamined_count,
        "retrieval_truncated": package.retrieval_truncated,
        "created_at": package.created_at,
    }


def _cited_keys(analysis: Analysis) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for ref in analysis.evidence_refs or []:
        if isinstance(ref, dict):
            keys.add((str(ref.get("chunk_id")), str(ref.get("document_id"))))
    for finding in analysis.findings or []:
        if not isinstance(finding, dict):
            continue
        for ref in finding.get("evidence_refs") or []:
            if isinstance(ref, dict):
                keys.add((str(ref.get("chunk_id")), str(ref.get("document_id"))))
    return keys


def _gaps(
    package: EvidencePackage,
    entries: list[dict],
    run: TaskRun | None,
    ledger: list | None = None,
) -> list[str]:
    """Combine recorded selection gaps with what reading the evidence shows now."""
    recorded = package.coverage.get("gaps", []) if package.coverage else []
    if not isinstance(recorded, list):
        raise HTTPException(
            status_code=409, detail="Evidence Package coverage gaps are invalid"
        )
    observed: list[str] = []
    for entry in entries:
        if not entry["original"]["stored"]:
            observed.append("document_not_stored")
        if not entry["chunk_available"]:
            observed.append("chunk_unavailable")
        if entry["document"]["completeness"] == "partial":
            observed.append("partial_source_content")
        if entry["original"]["stored"] and not entry["original"]["readable"]:
            observed.append("snapshot_storage_unconfigured")
        if not entry["parse_is_current"]:
            observed.append("chunk_not_in_current_parse_batch")
        if entry["embedding_state"] is None or entry["embedding_state"].get(
            "current"
        ) is not True:
            observed.append("chunk_not_in_current_index")
    if run is not None and run.status != "succeeded":
        observed.append("run_not_succeeded")
    try:
        supplement_gaps = research_ledger_gaps(ledger)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Report research inputs are invalid"
        ) from exc
    return list(
        dict.fromkeys(
            [
                *(str(gap) for gap in recorded),
                *observed,
                *supplement_gaps,
            ]
        )
    )


def _ids(values: list[object]) -> list[UUID]:
    return list(dict.fromkeys(parsed for parsed in map(_parse_id, values) if parsed))


def _parse_id(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _states(
    session: Session, document_ids: list[UUID]
) -> tuple[
    dict[tuple[str, str], DocumentCollectionState],
    dict[UUID, DocumentParseState],
    dict[UUID, DocumentEmbeddingState],
    dict[UUID, int],
]:
    unique_ids = list(dict.fromkeys(document_ids))
    if not unique_ids:
        return {}, {}, {}, {}
    documents = list(
        session.scalars(select(Document).where(Document.id.in_(unique_ids))).all()
    )
    parse_states = {
        state.document_id: state
        for state in session.scalars(
            select(DocumentParseState).where(
                DocumentParseState.document_id.in_(unique_ids)
            )
        ).all()
    }
    embedding_states = {
        state.document_id: state
        for state in session.scalars(
            select(DocumentEmbeddingState).where(
                DocumentEmbeddingState.document_id.in_(unique_ids)
            )
        ).all()
    }
    current_chunk_counts = dict(
        session.execute(
            select(
                DocumentChunk.document_id,
                func.count(DocumentChunk.id),
            )
            .join(
                DocumentParseState,
                DocumentParseState.document_id == DocumentChunk.document_id,
            )
            .where(
                DocumentParseState.current_batch_id == DocumentChunk.parse_batch_id,
                DocumentChunk.document_id.in_(unique_ids),
            )
            .group_by(DocumentChunk.document_id)
        ).all()
    )
    keys = {(document.source_id, document.dedupe_key) for document in documents}
    collection_states: dict[tuple[str, str], DocumentCollectionState] = {}
    if keys:
        source_ids = {source_id for source_id, _ in keys}
        dedupe_keys = {dedupe_key for _, dedupe_key in keys}
        for state in session.scalars(
            select(DocumentCollectionState).where(
                DocumentCollectionState.source_id.in_(source_ids),
                DocumentCollectionState.dedupe_key.in_(dedupe_keys),
            )
        ).all():
            if (state.source_id, state.dedupe_key) in keys:
                collection_states[(state.source_id, state.dedupe_key)] = state
    return collection_states, parse_states, embedding_states, current_chunk_counts


def _resolve_evidence(
    session: Session,
    config: Settings,
    selected: list[dict],
) -> tuple[list[dict], dict[tuple[str, str], DocumentCollectionState]]:
    """Read each selected Chunk at the Document version the report actually used.

    A report is always shown with its own evidence. The current collection
    pointer is returned separately, so a newer Document version can be visible
    without ever rewriting what an older report cited.
    """
    chunk_ids = _ids([item.get("chunk_id") for item in selected])
    document_ids = _ids([item.get("document_id") for item in selected])
    chunks = (
        {
            chunk.id: chunk
            for chunk in session.scalars(
                select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
            ).all()
        }
        if chunk_ids
        else {}
    )
    documents = (
        {
            document.id: document
            for document in session.scalars(
                select(Document).where(Document.id.in_(document_ids))
            ).all()
        }
        if document_ids
        else {}
    )
    states, parse_states, embedding_states, current_chunk_counts = _states(
        session, list(documents)
    )

    entries: list[dict] = []
    for item in selected:
        chunk_id = _parse_id(item.get("chunk_id"))
        document_id = _parse_id(item.get("document_id"))
        batch_id = _parse_id(item.get("parse_batch_id"))
        document = documents.get(document_id) if document_id else None
        chunk = chunks.get(chunk_id) if chunk_id else None
        chunk_matches = (
            chunk is not None
            and document is not None
            and chunk.document_id == document.id
            and (batch_id is None or chunk.parse_batch_id == batch_id)
            and (
                item.get("position") is None
                or chunk.position == int(item["position"])
            )
        )
        parse_state = parse_states.get(document.id) if document else None
        embedding_state = _embedding_state(
            embedding_states.get(document.id) if document else None,
            parse_state,
            current_chunk_counts.get(document.id, 0) if document else 0,
        )
        payload = _stored_document_payload(document, item)
        key = (payload["source_id"], payload["dedupe_key"])
        state = states.get(key) if all(key) else None
        entries.append(
            {
                "chunk_id": str(chunk_id) if chunk_id else item.get("chunk_id"),
                "document_id": (
                    str(document_id) if document_id else item.get("document_id")
                ),
                "parse_batch_id": (
                    str(batch_id) if batch_id else item.get("parse_batch_id")
                ),
                "position": chunk.position if chunk_matches else item.get("position"),
                "document_version": payload["version"],
                "cited_document_version": item.get("document_version"),
                "version_matches": (
                    document is not None
                    and item.get("document_version") is not None
                    and document.version == int(item["document_version"])
                ),
                "role": item.get("role"),
                "reason": item.get("reason"),
                "similarity": item.get("similarity"),
                "query_rank": item.get("query_rank"),
                "estimated_tokens": item.get("estimated_tokens"),
                "chunk_available": bool(chunk_matches),
                "text": chunk.text if chunk_matches else None,
                "locator": chunk.locator if chunk_matches else None,
                "parse_is_current": bool(
                    parse_state is not None
                    and chunk is not None
                    and parse_state.current_batch_id == chunk.parse_batch_id
                ),
                "parse_state": _parse_state(parse_state),
                "embedding_state": embedding_state,
                "document": payload,
                "original": _original_descriptor(config, document),
                "is_current_collection_pointer": bool(
                    state is not None and str(state.latest_document_id) == payload["id"]
                ),
                "collection_state": _collection_state_payload(state),
            }
        )
    return entries, states


def _collection_state_payload(
    state: DocumentCollectionState | None,
) -> dict | None:
    if state is None:
        return None
    return {
        "source_id": state.source_id,
        "dedupe_key": state.dedupe_key,
        "latest_document_id": str(state.latest_document_id),
        "completeness": state.completeness,
        "fetch_error": _safe_external_payload(state.fetch_error),
        "published_at": state.published_at,
        "source_updated_at": state.source_updated_at,
        "last_attempted_at": state.last_attempted_at,
    }


def _collection_state(
    states: dict[tuple[str, str], DocumentCollectionState], document: Document
) -> dict[str, object] | None:
    return _collection_state_payload(
        states.get((document.source_id, document.dedupe_key))
    )


def _parse_state(state: DocumentParseState | None) -> dict[str, object] | None:
    if state is None:
        return None
    return {
        "status": state.status,
        "processor_version": state.processor_version,
        "latest_batch_id": (
            str(state.latest_batch_id) if state.latest_batch_id else None
        ),
        "current_batch_id": (
            str(state.current_batch_id) if state.current_batch_id else None
        ),
        "chunk_count": state.chunk_count,
        "error": _safe_error(state.error),
        "attempted_at": state.attempted_at,
    }


def _embedding_state(
    state: DocumentEmbeddingState | None,
    parse_state: DocumentParseState | None,
    current_chunk_count: int,
) -> dict[str, object] | None:
    if (
        parse_state is None
        or parse_state.current_batch_id is None
        or current_chunk_count == 0
    ):
        return None
    is_current_batch = (
        state is not None
        and state.parse_batch_id == parse_state.current_batch_id
    )
    indexed_count = state.indexed_count if is_current_batch else 0
    return {
        "status": state.status if is_current_batch else "pending",
        "current": is_current_batch,
        "parse_batch_id": str(parse_state.current_batch_id),
        "last_attempt_parse_batch_id": (
            str(state.parse_batch_id) if state is not None else None
        ),
        "model_alias": state.model_alias if state is not None else None,
        "model_name": state.model_name if state is not None else None,
        "dimensions": state.dimensions if state is not None else None,
        "chunk_count": current_chunk_count,
        "indexed_count": indexed_count,
        "unindexed_count": current_chunk_count - indexed_count,
        "error": (
            _safe_error(state.error) if is_current_batch else None
        ),
        "attempted_at": state.attempted_at if state is not None else None,
    }


def _page(
    items: list[dict],
    *,
    cursor: str | None,
    has_more: bool,
) -> dict:
    return {
        "items": items,
        "next_cursor": cursor if has_more else None,
        "has_more": has_more,
        "limit": len(items),
    }


def _document_list_page(
    session: Session,
    *,
    limit: int,
    after: tuple[datetime, UUID] | None,
) -> dict:
    statement = select(Document).order_by(
        Document.collected_at.desc(), Document.id.desc()
    )
    if after is not None:
        statement = statement.where(
            or_(
                Document.collected_at < after[0],
                and_(
                    Document.collected_at == after[0],
                    Document.id < after[1],
                ),
            )
        )
    rows = list(session.scalars(statement.limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    processing_rows = (
        session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id.in_(
                    [document.id for document in rows]
                )
            )
        ).all()
        if rows
        else []
    )
    stage_rows: dict[UUID, dict[str, DocumentProcessingState]] = defaultdict(dict)
    for state in processing_rows:
        stage_rows[state.document_id][state.stage] = state
    states, parse_states, embedding_states, current_chunk_counts = _states(
        session, [document.id for document in rows]
    )
    items = [
        {
            **_document_payload(document),
            "processing_stages": _processing_stage_map_from_rows(
                stage_rows[document.id]
            ),
            "collection_state": _collection_state(states, document),
            "parse_state": _parse_state(parse_states.get(document.id)),
            "embedding_state": _embedding_state(
                embedding_states.get(document.id),
                parse_states.get(document.id),
                current_chunk_counts.get(document.id, 0),
            ),
        }
        for document in rows
    ]
    last = rows[-1] if rows else None
    return _page(
        items,
        cursor=_cursor(last.collected_at, last.id) if last is not None else None,
        has_more=has_more,
    )


def _parse_chunks_after(
    session: Session,
    document_id: UUID,
    *,
    after_position: int,
    limit: int,
) -> list[DocumentChunk]:
    state = session.get(DocumentParseState, document_id)
    if state is None or state.current_batch_id is None:
        return []
    return list_parse_chunks(
        session,
        document_id,
        after_position=after_position,
        limit=limit,
    )


def _parse_cursor(value: str | None, *, name: str) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        timestamp, identifier = value.split("|", 1)
        parsed = datetime.fromisoformat(timestamp)
        if parsed.utcoffset() is None:
            raise ValueError("cursor timestamp must include a timezone")
        parsed = parsed.astimezone(UTC)
        return parsed, UUID(identifier)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CURSOR_INVALID",
                "message": f"{name} cursor is invalid",
            },
        ) from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _cursor(timestamp: datetime, identifier: UUID) -> str:
    return f"{timestamp.astimezone(UTC).isoformat()}|{identifier}"


def _processing_page(
    session: Session,
    *,
    document_ids: list[UUID] | None,
    stages: list[str] | None,
    statuses: list[str] | None,
    after: tuple[datetime, UUID, str] | None,
    limit: int,
) -> list[DocumentProcessingState]:
    statement = (
        select(DocumentProcessingState)
        .join(Document, Document.id == DocumentProcessingState.document_id)
        .order_by(
            Document.collected_at.desc(),
            DocumentProcessingState.document_id.desc(),
            DocumentProcessingState.stage.desc(),
        )
    )
    if document_ids:
        statement = statement.where(
            DocumentProcessingState.document_id.in_(document_ids)
        )
    if stages:
        statement = statement.where(DocumentProcessingState.stage.in_(stages))
    if statuses:
        statement = statement.where(DocumentProcessingState.status.in_(statuses))
    if after is not None:
        statement = statement.where(
            or_(
                Document.collected_at < after[0],
                and_(
                    Document.collected_at == after[0],
                    DocumentProcessingState.document_id < after[1],
                ),
                and_(
                    Document.collected_at == after[0],
                    DocumentProcessingState.document_id == after[1],
                    DocumentProcessingState.stage < after[2],
                ),
            )
        )
    return list(session.scalars(statement.limit(limit + 1)).all())


def _processing_cursor(
    session: Session, state: DocumentProcessingState
) -> str:
    collected_at = session.scalar(
        select(Document.collected_at).where(
            Document.id == state.document_id
        )
    )
    if collected_at is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROCESSING_DOCUMENT_MISSING",
                "message": "Processing row has no stored Document",
            },
        )
    return "|".join(
        (
            collected_at.astimezone(UTC).isoformat(),
            str(state.document_id),
            state.stage,
        )
    )


def _parse_processing_cursor(
    value: str | None,
) -> tuple[datetime, UUID, str] | None:
    if value is None:
        return None
    try:
        timestamp, identifier, stage = value.split("|")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.utcoffset() is None:
            raise ValueError("cursor timestamp must include a timezone")
        parsed = parsed.astimezone(UTC)
        if stage not in PROCESSING_STAGES:
            raise ValueError("unknown processing stage")
        return parsed, UUID(identifier), stage
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CURSOR_INVALID",
                "message": "Processing cursor is invalid",
            },
        ) from exc


def _finite(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _task_payload(
    definition: TaskDefinition,
    runs: list[dict],
    *,
    configured: bool = False,
    task_config: dict | None = None,
) -> dict:
    return {
        "task_id": definition.id,
        "name": definition.name,
        "type": definition.type,
        "trigger": definition.trigger,
        "enabled": definition.enabled,
        "strategy": definition.strategy,
        "scope": _safe_external_payload(definition.scope),
        "updated_at": _iso(definition.updated_at),
        "active_run": next(
            (
                run
                for run in runs
                if run["status"] in {"queued", "running"}
            ),
            None,
        ),
        "last_run": next(
            (
                run for run in runs
            ),
            None,
        ),
        "configured": configured,
        "configuration": task_config,
    }


def _run_summary(
    session: Session,
    run: TaskRun,
    *,
    step_count: int | None = None,
) -> dict:
    step_count = (
        int(
            session.scalar(
                select(func.count(TaskStepResult.id)).where(
                    TaskStepResult.run_id == run.id
                )
            )
            or 0
        )
        if step_count is None
        else step_count
    )
    return {
        **_run_payload(run),
        "step_count": step_count,
        "change_scan": {
            "change_watermark_start": run.change_watermark_start,
            "change_watermark_end": run.change_watermark_end,
            "window_start": _iso(run.window_start),
            "window_end": _iso(run.window_end),
            "change_scan_completed": run.change_scan_completed,
            "change_scan_progress": _safe_external_payload(
                _finite(run.change_scan_progress)
            ),
        }
        if run.change_watermark_start is not None
        else None,
    }


def _safe_configuration_snapshot(value: object) -> dict:
    """Keep only display-safe Task/Agent identifiers from a Run snapshot."""

    snapshot = _finite(value)
    task = _finite(snapshot.get("task"))
    agents = snapshot.get("agents")
    return {
        "task": {
            key: _safe_external_payload(task.get(key))
            for key in (
                "id",
                "name",
                "type",
                "trigger",
                "enabled",
                "objective",
                "strategy",
                "scope",
                "sources",
            )
            if key in task
        },
        "agents": [
            {
                key: item.get(key)
                for key in ("id", "role", "model_ref", "tools")
                if key in item
            }
            for item in agents or []
            if isinstance(item, dict)
        ]
        if isinstance(agents, list)
        else [],
    }


def _safe_model_snapshot(value: object) -> dict:
    """Remove endpoint and credential-shaped values from a model snapshot."""

    snapshot = _finite(value)
    agents = snapshot.get("agents")
    result = {
        "agents": {},
        "embedding": snapshot.get("embedding"),
    }
    if isinstance(agents, dict):
        for key, item in agents.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                continue
            result["agents"][key] = {
                field: item.get(field)
                for field in (
                    "model_ref",
                    "protocol",
                    "aliases",
                    "required_capabilities",
                    "timeout_seconds",
                    "max_attempts",
                    "routing",
                )
                if field in item
            }
    tools = snapshot.get("tools")
    if isinstance(tools, dict):
        # The authorization identity holds hashes and budgets only; the Tool
        # credentials and endpoint values never enter a Run snapshot.
        result["tools"] = {
            agent_id: [
                {
                    field: entry.get(field)
                    for field in (
                        "tool_id",
                        "remote_name",
                        "endpoint_hash",
                        "source_id",
                        "input_schema_hash",
                        "output_schema_hash",
                        "credential_required",
                        "timeout_seconds",
                        "budgets",
                    )
                    if field in entry
                }
                for entry in entries
                if isinstance(entry, dict)
            ]
            for agent_id, entries in tools.items()
            if isinstance(agent_id, str) and isinstance(entries, list)
        }
    embedding = result.get("embedding")
    if isinstance(embedding, dict):
        result["embedding"] = {
            field: embedding.get(field)
            for field in (
                "aliases",
                "model_name",
                "dimensions",
                "required_capabilities",
            )
            if field in embedding
        }
    return result


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credentials",
        "endpoint",
        "endpoint_url",
        "password",
        "secret",
        "session",
        "token",
    }
)


def _safe_external_payload(value: object) -> object:
    """Recursively remove credential and endpoint fields from stored JSON.

    Task outputs are useful for locating actual Documents and stages, but the
    payloads may have been generated by older or alternate callers. Filtering by
    field name at read time keeps endpoint and credential material out of API
    responses without changing the durable Run record.
    """

    if isinstance(value, dict):
        result: dict = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            normalized = key.lower().replace("-", "_")
            if (
                normalized in _SENSITIVE_KEYS
                or "authorization" in normalized
                or "api_key" in normalized
                or "credential" in normalized
                or "password" in normalized
                or "secret" in normalized
                or "token" in normalized
                or normalized.endswith("endpoint")
            ):
                continue
            result[key] = (
                _safe_error(item)
                if normalized == "error" and isinstance(item, dict)
                else _safe_external_payload(item)
            )
        return result
    if isinstance(value, list):
        return [_safe_external_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_external_payload(item) for item in value]
    if isinstance(value, str):
        # Error and Run payloads can carry a URL under a generic key such as
        # ``details.url``. Keep a traceable source address but never echo query
        # parameters or fragments, which commonly contain a token or session.
        if value.lower().startswith(("http://", "https://")):
            return _safe_source_url(value)
    return value


def _safe_error(value: object) -> object:
    """Keep stable error codes/messages and omit nested provider detail."""

    filtered = _safe_external_payload(value)
    if not isinstance(filtered, dict):
        return filtered
    return {
        key: item
        for key, item in filtered.items()
        if key
        not in {
            "body",
            "details",
            "error",
            "exception",
            "response",
            "stack",
            "trace",
            "traceback",
        }
    }


def _safe_source_url(value: str | None) -> str | None:
    """Keep a traceable source address without query or fragment material."""

    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname
        host_text = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            host_text = f"{host_text}:{parsed.port}"
        return urlunsplit(
            (parsed.scheme, host_text, parsed.path, "", "")
        )
    except ValueError:
        return None


def _model_call_payload(call: ModelCall) -> dict:
    return {
        "step_id": call.step_id,
        "attempt_number": call.attempt_number,
        "requested_alias": call.requested_alias,
        "attempt_alias": call.attempt_alias,
        "requested_model_name": call.requested_model_name,
        "attempt_model_name": call.attempt_model_name,
        "reported_model_name": call.reported_model_name,
        "status": call.status,
        "duration_ms": call.duration_ms,
        "prompt_tokens": call.prompt_tokens,
        "completion_tokens": call.completion_tokens,
        "total_tokens": call.total_tokens,
        "fallback_reason": call.fallback_reason,
        "error": _safe_error(call.error),
        "created_at": _iso(call.created_at),
    }


def _step_payload(step: TaskStepResult) -> dict:
    return {
        "step_id": step.step_id,
        "position": step.position,
        "type": step.step_type,
        "status": step.status,
        "output": _safe_error(step.output),
        "evidence_refs": _safe_external_payload(step.evidence_refs),
        "usage": _safe_external_payload(step.usage),
        "error": _safe_error(step.error),
        "created_at": _iso(step.created_at),
    }


def _failed_steps_preview(steps: list[TaskStepResult]) -> list[dict]:
    return [
        {
            "step_id": step.step_id,
            "position": step.position,
            "type": step.step_type,
            "status": step.status,
            "error": _safe_error(step.error),
        }
        for step in steps
        if step.status == "failed"
    ]


def _execution_error(exc: ExecutionError) -> HTTPException:
    status = 404 if exc.code == "RUN_NOT_FOUND" else 409
    return HTTPException(status_code=status, detail=exc.to_dict())


def _model_configuration_unavailable(_exc: ValueError) -> HTTPException:
    """Return a stable error without exposing model or environment details."""

    return HTTPException(
        status_code=503,
        detail={
            "code": "MODEL_CONFIGURATION_UNAVAILABLE",
            "message": (
                "Task could not be queued because model "
                "configuration is unavailable"
            ),
        },
    )


def _continuation_payload(
    run: dict,
    *,
    source_run_id: UUID,
    resumed_from_run_id: UUID | None,
    created: bool | None = None,
) -> dict:
    return {
        **run,
        "created": (
            created
            if created is not None
            else resumed_from_run_id is not None
            and run["status"] in {"queued", "running"}
        ),
        "continued_from": str(source_run_id),
        "resumed_from": (
            str(resumed_from_run_id)
            if resumed_from_run_id is not None
            else None
        ),
    }


def _source_freshness(
    source: object,
    *,
    has_stored_content: bool,
    latest_disclosure_at: datetime | None,
    latest_collected_at: datetime | None,
    now: datetime,
) -> dict:
    expected = getattr(source, "expected_freshness", None)
    latest = _iso(latest_disclosure_at)
    if expected is None:
        return {
            "basis": "source.expected_freshness",
            "expected_freshness": None,
            "status": "unconfigured",
            "age_seconds": None,
            "latest_disclosure_at": latest,
            "latest_collected_at": _iso(latest_collected_at),
        }
    if not has_stored_content:
        return {
            "basis": "source.expected_freshness",
            "expected_freshness": expected,
            "status": "no_content",
            "age_seconds": None,
            "latest_disclosure_at": None,
            "latest_collected_at": None,
        }
    if latest_disclosure_at is None:
        return {
            "basis": "source.expected_freshness",
            "expected_freshness": expected,
            "status": "unknown_content_time",
            "age_seconds": None,
            "latest_disclosure_at": None,
            "latest_collected_at": _iso(latest_collected_at),
        }
    seconds = int(expected[:-1]) * FRESHNESS_UNITS[expected[-1]]
    age_seconds = max(0, int((now - latest_disclosure_at).total_seconds()))
    return {
        "basis": "source.expected_freshness",
        "expected_freshness": expected,
        "status": "stale" if age_seconds > seconds else "fresh",
        "age_seconds": age_seconds,
        "latest_disclosure_at": latest,
        "latest_collected_at": _iso(latest_collected_at),
    }


def _search_facts_page(
    session: Session,
    filters: FactFilters,
    *,
    limit: int,
    after: tuple[datetime, UUID] | None,
) -> list[Fact]:
    statement = (
        select(Fact)
        .join(Document, Document.id == Fact.document_id)
        .order_by(Fact.created_at.desc(), Fact.id.desc())
    )
    if filters.document_ids:
        statement = statement.where(Fact.document_id.in_(filters.document_ids))
    if filters.source_ids:
        statement = statement.where(Document.source_id.in_(filters.source_ids))
    if filters.fact_types:
        statement = statement.where(Fact.fact_type.in_(filters.fact_types))
    if filters.symbols:
        statement = statement.where(Fact.symbol.in_(filters.symbols))
    if filters.subject_names:
        statement = statement.where(
            Fact.subject_name.in_(filters.subject_names)
        )
    if filters.metric_ids:
        statement = statement.where(Fact.metric_id.in_(filters.metric_ids))
    if filters.origin_methods:
        statement = statement.where(
            Fact.origin_method.in_(filters.origin_methods)
        )
    if filters.period_start_from is not None:
        statement = statement.where(
            Fact.period_start >= filters.period_start_from
        )
    if filters.period_start_to is not None:
        statement = statement.where(
            Fact.period_start <= filters.period_start_to
        )
    if filters.available_from is not None:
        statement = statement.where(Fact.available_at >= filters.available_from)
    if filters.available_to is not None:
        statement = statement.where(Fact.available_at <= filters.available_to)
    if after is not None:
        statement = statement.where(
            or_(
                Fact.created_at < after[0],
                and_(Fact.created_at == after[0], Fact.id < after[1]),
            )
        )
    return list(session.scalars(statement.limit(limit + 1)).all())


def _exact_result_with_coverage(
    session: Session,
    config: Settings,
    term: str,
    filters: SearchFilters,
    limit: int,
) -> SearchResult:
    result = exact_search(session, term, filters=filters, limit=limit)
    try:
        targets, _client = load_embedding_client(config.models_config_path)
    except (ValueError, OSError):
        coverage = None
    else:
        coverage = get_embedding_coverage(
            session,
            model_name=targets.model_name,
            dimensions=targets.dimensions,
            filters=filters,
        )
    return SearchResult(
        hits=result.hits,
        coverage=coverage,
        filters=result.filters,
        truncated=result.truncated,
    )


def _search_filter_payload(filters: SearchFilters) -> dict:
    return {
        "source_ids": list(filters.source_ids),
        "published_from": _iso(filters.published_from),
        "published_to": _iso(filters.published_to),
        "collected_from": _iso(filters.collected_from),
        "collected_to": _iso(filters.collected_to),
    }


def _recent_reports(session: Session, limit: int = 5) -> list[dict]:
    rows = session.scalars(
        select(Report)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "report_id": str(report.id),
            "run_id": str(report.run_id),
            "task_id": report.task_id,
            "title": report.title,
            "created_at": _iso(report.created_at),
        }
        for report in rows
    ]


def _backlog_counts(session: Session) -> dict:
    counts = {
        status: count
        for status, count in session.execute(
            select(DocumentProcessingState.status, func.count())
            .group_by(DocumentProcessingState.status)
        ).all()
    }
    by_stage = {
        stage: {
            status: count
            for status, count in session.execute(
                select(
                    DocumentProcessingState.status,
                    func.count(),
                )
                .where(DocumentProcessingState.stage == stage)
                .group_by(DocumentProcessingState.status)
            ).all()
        }
        for stage in PROCESSING_STAGES
    }
    return {
        "by_status": {
            status: int(counts.get(status, 0))
            for status in PROCESSING_STAGE_STATUSES
        },
        "by_stage": by_stage,
    }


def _source_payloads(
    session: Session,
    config: Settings,
    *,
    now: datetime | None = None,
    after_source_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    sources, error = _configured_sources(config)
    definitions = {
        item.source_id: item.model_dump(mode="json") for item in sources
    }
    page_limit = limit if limit is not None else 500
    persisted_statement = select(Source).order_by(Source.id)
    if after_source_id is not None:
        persisted_statement = persisted_statement.where(
            Source.id > after_source_id
        )
    persisted = {
        row.id: row
        for row in session.scalars(
            persisted_statement.limit(page_limit)
        ).all()
    }
    configured_ids = [
        source_id
        for source_id in definitions
        if after_source_id is None or source_id > after_source_id
    ]
    configured_ids.sort()
    page_source_ids = _ordered_unique_page(
        (list(persisted), configured_ids),
        limit=page_limit,
    )
    if not page_source_ids:
        return []
    # Resolve one latest Collection Run for each Source on this page. The
    # correlated LIMIT 1 avoids expanding every historical Run scope while the
    # GIN index on TaskRun.scope makes each Source's containment lookup bounded.
    page_sources = values(
        column("source_id", String(120)),
        name="page_sources",
    ).data([(source_id,) for source_id in page_source_ids])
    latest_run_id = (
        select(TaskRun.id)
        .join(TaskDefinition, TaskDefinition.id == TaskRun.task_id)
        .where(
            TaskDefinition.type == "collection",
            func.jsonb_path_exists(
                TaskRun.scope,
                literal("$.source_ids[*] ? (@ == $source)").cast(JSONPATH),
                func.jsonb_build_object(
                    "source",
                    page_sources.c.source_id,
                ),
            ),
        )
        .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
        .limit(1)
        .correlate(page_sources)
        .scalar_subquery()
    )
    latest_runs = (
        select(page_sources.c.source_id, TaskRun)
        .select_from(page_sources)
        .join(TaskRun, TaskRun.id == latest_run_id)
        .order_by(page_sources.c.source_id)
    )
    last_runs: dict[str, TaskRun] = {}
    for source_id, run in session.execute(
        latest_runs,
        execution_options={"synchronize_session": False},
    ).unique().all():
        last_runs[source_id] = run
    content_times = {
        source_id: (bool(stored), disclosure_at, collected_at)
        for source_id, stored, disclosure_at, collected_at in session.execute(
            select(
                Document.source_id,
                func.count(Document.id) > 0,
                func.max(
                    func.coalesce(
                        Document.source_updated_at,
                        Document.published_at,
                    )
                ),
                func.max(Document.collected_at),
            )
            .where(Document.source_id.in_(page_source_ids))
            .group_by(Document.source_id)
        ).all()
    }
    source_processing = _source_processing_status(session, page_source_ids)
    collection_summaries: dict[str, list[dict]] = {}
    for run in last_runs.values():
        summaries: list[dict] = []
        raw_outputs: list[object] = []
        if isinstance(run.output, dict):
            raw_outputs.append(run.output)
            nested_output = run.output.get("output")
            if isinstance(nested_output, dict):
                raw_outputs.append(nested_output)
        steps = session.scalars(
            select(TaskStepResult)
            .where(
                TaskStepResult.run_id == run.id,
                TaskStepResult.step_type == "collect_sources",
            )
            .order_by(TaskStepResult.position)
        ).all()
        raw_outputs.extend(step.output for step in steps if step.output is not None)
        for raw_output in raw_outputs:
            if not isinstance(raw_output, dict):
                continue
            raw = raw_output.get("sources")
            if isinstance(raw, list):
                summaries.extend(
                    item for item in raw if isinstance(item, dict)
                )
        for summary in summaries:
            source_id = summary.get("source_id")
            if isinstance(source_id, str) and source_id in last_runs:
                collection_summaries.setdefault(source_id, []).append(summary)
    latest_states = (
        select(DocumentCollectionState)
        .ext(distinct_on(DocumentCollectionState.source_id))
        .where(DocumentCollectionState.source_id.in_(page_source_ids))
        .order_by(
            DocumentCollectionState.source_id,
            DocumentCollectionState.last_attempted_at.desc(),
            DocumentCollectionState.dedupe_key,
        )
    )
    states_by_source: dict[str, list[DocumentCollectionState]] = defaultdict(list)
    for state in session.scalars(latest_states).all():
        states_by_source[state.source_id].append(state)
    recent_states = {
        source_id: source_states[0]
        for source_id, source_states in states_by_source.items()
        if source_states
    }
    summary_source_ids = set(collection_summaries)
    for source_id in page_source_ids:
        if source_id in last_runs or source_id in summary_source_ids:
            continue
        candidates = states_by_source.get(source_id, [])
        if not candidates:
            continue
        state = candidates[0]
        if state.fetch_error is not None:
            collection_summaries[source_id] = [
                {
                    "source_id": source_id,
                    "state_error": {
                        "code": "SOURCE_FETCH_FAILED",
                        "message": "The latest source attempt failed",
                    },
                }
            ]
    items: list[dict] = []
    for source_id in page_source_ids:
        definition = definitions.get(source_id)
        row = persisted.get(source_id)
        state = recent_states.get(source_id)
        run = last_runs.get(source_id)
        configured_source = next(
            (item for item in sources if item.source_id == source_id),
            None,
        )
        freshness = _source_freshness(
            configured_source,
            has_stored_content=content_times.get(source_id, (False, None, None))[0],
            latest_disclosure_at=content_times.get(
                source_id, (False, None, None)
            )[1],
            latest_collected_at=content_times.get(
                source_id, (False, None, None)
            )[2],
            now=now or datetime.now(UTC),
        )
        collection_outcome = _collection_outcome(
            run,
            source_id,
            summaries=collection_summaries.get(source_id),
        )
        collection_status = {
            **collection_outcome,
            "latest_attempt_at": _iso(
                state.last_attempted_at if state is not None else None
            ),
            "latest_document_id": (
                str(state.latest_document_id) if state is not None else None
            ),
            "completeness": state.completeness if state is not None else None,
            "error": collection_outcome.get("error")
            or (
                _safe_error(state.fetch_error)
                if state is not None
                else None
            ),
            "last_collection_run": _run_payload(run),
        }
        if state is not None and state.fetch_error is not None:
            collection_status["last_attempt_error"] = _safe_error(
                state.fetch_error
            )
        processing = source_processing[source_id]
        collection_status = {
            **collection_status,
            "processing_backlog": processing,
        }
        items.append(
            {
                "source_id": source_id,
                "name": (
                    definition.get("name")
                    if definition is not None
                    else row.name if row is not None else source_id
                ),
                "connector": (
                    definition.get("connector")
                    if definition is not None
                    else row.connector if row is not None else None
                ),
                "publisher": (
                    definition.get("publisher")
                    if definition is not None
                    else row.publisher if row is not None else None
                ),
                "trust_tier": (
                    definition.get("trust_tier")
                    if definition is not None
                    else row.trust_tier if row is not None else None
                ),
                "enabled": (
                    definition.get("enabled") if definition is not None else None
                ),
                "configured": definition is not None,
                "persisted": row is not None,
                "collection": collection_status,
                "freshness": freshness,
            }
        )
    return items


def _source_processing_status(
    session: Session,
    source_ids: list[str],
) -> dict[str, dict]:
    """Return one bounded aggregate query for every Source on the page."""

    if not source_ids:
        return {}
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for source_id, status, count in session.execute(
        select(
            Document.source_id,
            DocumentProcessingState.status,
            func.count(),
        )
        .join(
            DocumentProcessingState,
            DocumentProcessingState.document_id == Document.id,
        )
        .where(Document.source_id.in_(source_ids))
        .group_by(Document.source_id, DocumentProcessingState.status)
    ).all():
        counts[source_id][status] = int(count)
    return {
        source_id: {
            "pending": int(
                counts[source_id].get("pending", 0)
                + counts[source_id].get("running", 0)
                + counts[source_id].get("failed", 0)
            ),
            "failed": int(counts[source_id].get("failed", 0)),
        }
        for source_id in source_ids
    }


def _collection_outcome(
    run: TaskRun | None,
    source_id: str,
    *,
    summaries: list[dict] | None = None,
) -> dict:
    state_error = summaries[0].get("state_error") if summaries else None
    if run is None:
        return _collection_outcome_fields(
            status="source_failure" if state_error is not None else "no_attempt",
            request_success=False if state_error is not None else None,
            no_update=False if state_error is not None else None,
            error=_safe_error(state_error),
        )
    collected_summaries = list(summaries or [])
    raw_outputs: list[object] = []
    if isinstance(run.output, dict):
        raw_outputs.append(run.output)
        nested_output = run.output.get("output")
        if isinstance(nested_output, dict):
            raw_outputs.append(nested_output)
    for raw_output in raw_outputs:
        if not isinstance(raw_output, dict):
            continue
        raw = raw_output.get("sources")
        if isinstance(raw, list):
            collected_summaries.extend(
                item for item in raw if isinstance(item, dict)
            )
    summary = next(
        (
            item
            for item in collected_summaries
            if item.get("source_id") == source_id
        ),
        None,
    )
    if summary is None:
        if run.status in {"queued", "running"}:
            return _collection_outcome_fields(
                status="in_progress",
                request_success=None,
                no_update=None,
            )
        if run.status == "failed":
            return _collection_outcome_fields(
                status="source_failure",
                request_success=False,
                no_update=False,
                error=(
                    _safe_error(state_error)
                    if state_error is not None
                    else _safe_error(run.error)
                ),
            )
        return _collection_outcome_fields(
            status="no_attempt",
            request_success=None,
            no_update=None,
        )
    failed = _nonnegative_int(summary.get("failed")) or 0
    error = summary.get("error")
    if run.status in {"queued", "running"}:
        return _collection_outcome_fields(
            status="in_progress",
            request_success=None,
            no_update=None,
            discovered=summary.get("discovered"),
            ingested=summary.get("ingested"),
            reused=summary.get("reused"),
            skipped=summary.get("skipped"),
            failed=summary.get("failed"),
        )
    if error is not None:
        return _collection_outcome_fields(
            status="source_failure",
            request_success=False,
            no_update=False,
            discovered=summary.get("discovered"),
            ingested=summary.get("ingested"),
            reused=summary.get("reused"),
            skipped=summary.get("skipped"),
            failed=summary.get("failed"),
            error=_safe_error(error),
        )
    discovered = _nonnegative_int(summary.get("discovered"))
    ingested = _nonnegative_int(summary.get("ingested"))
    reused = _nonnegative_int(summary.get("reused"))
    skipped = _nonnegative_int(summary.get("skipped"))
    no_update = bool(
        discovered == 0
        or (
            discovered is not None
            and ingested == 0
            and reused == 0
            and skipped == discovered
        )
    )
    if failed:
        status = "partial"
    elif no_update:
        status = "no_update"
    else:
        status = "success"
    return _collection_outcome_fields(
        status=status,
        request_success=True,
        no_update=no_update,
        discovered=discovered,
        ingested=ingested,
        reused=reused,
        skipped=skipped,
        failed=failed,
    )


def _collection_outcome_fields(
    *,
    status: str,
    request_success: bool | None,
    no_update: bool | None,
    discovered: object = None,
    ingested: object = None,
    reused: object = None,
    skipped: object = None,
    failed: object = None,
    error: object = None,
) -> dict:
    return {
        "status": status,
        "request_success": request_success,
        "no_update": no_update,
        "discovered": _nonnegative_int(discovered),
        "ingested": _nonnegative_int(ingested),
        "reused": _nonnegative_int(reused),
        "skipped": _nonnegative_int(skipped),
        "failed": _nonnegative_int(failed),
        "error": error,
    }


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _search_coverage_payload(coverage: object) -> dict | None:
    if coverage is None:
        return None
    return {
        "current_documents": coverage.current_documents,
        "indexed_documents": coverage.indexed_documents,
        "failed_documents": coverage.failed_documents,
        "documents_without_complete_index": (
            coverage.documents_without_complete_index
        ),
        "current_chunks": coverage.current_chunks,
        "indexed_chunks": coverage.indexed_chunks,
        "unindexed_chunks": coverage.unindexed_chunks,
    }


def _processing_stage_map_from_rows(
    states: dict[str, DocumentProcessingState],
) -> list[dict]:
    result: list[dict] = []
    for stage in PROCESSING_STAGES:
        state = states.get(stage)
        result.append(
            {
                "stage": stage,
                "status": state.status if state is not None else "missing",
                "attempts": state.attempts if state is not None else 0,
                "wait_reason": state.wait_reason if state is not None else None,
                "error": (
                    _safe_error(state.error)
                    if state is not None
                    else None
                ),
            }
        )
    return result


def _processing_stage_map(session: Session, document_id: UUID) -> list[dict]:
    states = {
        state.stage: state
        for state in session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id == document_id
            )
        ).all()
    }
    return _processing_stage_map_from_rows(states)


def _current_chunk_count(session: Session, document_id: UUID) -> int:
    state = session.get(DocumentParseState, document_id)
    if state is None or state.current_batch_id is None:
        return 0
    return (
        session.scalar(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.parse_batch_id == state.current_batch_id,
            )
        )
        or 0
    )


def _configured_tasks(config: Settings) -> tuple[list[dict], dict | None]:
    try:
        configuration = load_task_configuration(config.tasks_config_path)
    except ExecutionConfigError as exc:
        return [], exc.to_dict()
    items: list[dict] = []
    for task in configuration.tasks:
        items.append(
            {
                "task_id": task.id,
                "name": task.name,
                "type": task.type,
                "trigger": task.trigger.model_dump(mode="json"),
                "enabled": task.enabled,
                "strategy": task.strategy,
                "scope": task.run_scope,
                "steps": [
                    {
                        "step_id": step.id,
                        "type": step.kind,
                    }
                    for step in task.steps
                ],
            }
        )
    return items, None


def _task_run_history(
    session: Session,
    *,
    task_ids: list[str] | None = None,
) -> list[TaskRun]:
    if task_ids is not None and not task_ids:
        return []
    if task_ids is None:
        return list(
            session.scalars(
                select(TaskRun)
                .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            ).all()
        )
    ranked = (
        select(
            TaskRun.id.label("id"),
            func.row_number()
            .over(
                partition_by=TaskRun.task_id,
                order_by=(TaskRun.created_at.desc(), TaskRun.id.desc()),
            )
            .label("rank"),
        )
        .where(TaskRun.task_id.in_(task_ids))
        .subquery()
    )
    return list(
        session.scalars(
            select(TaskRun)
            .join(ranked, ranked.c.id == TaskRun.id)
            .where(ranked.c.rank == 1)
            .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
        ).all()
    )


def _configured_task_definition(task_config: dict) -> TaskDefinition:
    trigger = task_config.get("trigger")
    return TaskDefinition(
        id=task_config["task_id"],
        name=task_config["name"],
        type=task_config["type"],
        trigger=(
            trigger.get("kind")
            if isinstance(trigger, dict)
            else "manual"
        ),
        enabled=task_config["enabled"],
        strategy=task_config["strategy"],
        scope=task_config["scope"],
        config_snapshot={},
        config_hash="0" * 64,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _configured_sources(config: Settings) -> tuple[list[object], dict | None]:
    try:
        return load_sources(config.sources_config_path), None
    except ConfigurationError as exc:
        return [], exc.to_dict()
