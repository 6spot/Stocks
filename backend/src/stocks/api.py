from __future__ import annotations

import hashlib
import logging
import secrets
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from heapq import merge
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    String,
    and_,
    column,
    func,
    literal,
    literal_column,
    or_,
    select,
    values,
)
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
from stocks.research.comparison import compare_report_sides
from stocks.research.service import (
    final_metric_comparisons,
    merged_research_fact_refs,
    merged_research_refs,
    research_ledger_gaps,
)
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
        now = datetime.now(UTC)
        library = _overview_library(session, source_summaries, now=now)
        attention = _overview_attention(
            session,
            source_summaries,
            failed_sources=failed_sources,
            backlog=backlog,
            source_error=source_error,
            task_error=task_error,
            now=now,
        )
        recent_calls = (
            session.scalars(
                select(ModelCall)
                .where(ModelCall.run_id.in_(recent_run_ids))
                .order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
                .limit(500)
            ).all()
            if recent_run_ids
            else []
        )
        return {
            "library": library,
            "attention": attention,
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
            "research": {
                "latest": _latest_research_coverage(session),
                "usage": {
                    **_usage_summary(list(recent_calls)),
                    "run_count": len(recent_run_rows),
                },
            },
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


def _utc_day(column: object) -> object:
    """Calendar day of a timestamptz column, free of DB session TZ."""

    return func.date(func.timezone(literal_column("'UTC'"), column))


def _overview_library(
    session: Session,
    source_summaries: list[dict],
    *,
    now: datetime,
) -> dict:
    """Deterministic stored-library aggregates for the Overview.

    Chunks count the current parse batch of each Document version, so a failed
    reprocess never inflates what the library claims to hold.
    """

    week_start = now - timedelta(days=7)
    first_day = now.date() - timedelta(days=13)
    documents = int(session.scalar(select(func.count(Document.id))) or 0)
    chunks = int(
        session.scalar(
            select(func.count(DocumentChunk.id))
            .join(
                DocumentParseState,
                DocumentParseState.document_id == DocumentChunk.document_id,
            )
            .where(
                DocumentParseState.current_batch_id
                == DocumentChunk.parse_batch_id
            )
        )
        or 0
    )
    facts = int(session.scalar(select(func.count(Fact.id))) or 0)
    reports = int(session.scalar(select(func.count(Report.id))) or 0)
    earliest, latest = session.execute(
        select(
            func.min(Document.collected_at),
            func.max(Document.collected_at),
        )
    ).one()
    documents_this_week = int(
        session.scalar(
            select(func.count(Document.id)).where(
                Document.collected_at >= week_start
            )
        )
        or 0
    )
    facts_this_week = int(
        session.scalar(
            select(func.count(Fact.id)).where(Fact.created_at >= week_start)
        )
        or 0
    )
    documents_by_source = {
        source_id: int(count)
        for source_id, count in session.execute(
            select(Document.source_id, func.count(Document.id)).group_by(
                Document.source_id
            )
        ).all()
    }
    facts_by_source = {
        source_id: int(count)
        for source_id, count in session.execute(
            select(Document.source_id, func.count(Fact.id))
            .join(Document, Document.id == Fact.document_id)
            .group_by(Document.source_id)
        ).all()
    }
    # The latest attempt that stored complete content is the last successful
    # collection this repository can evidence per Source.
    last_success = {
        source_id: collected_at
        for source_id, collected_at in session.execute(
            select(Document.source_id, func.max(Document.collected_at))
            .where(Document.completeness == "complete")
            .group_by(Document.source_id)
        ).all()
    }
    document_days = {
        _day_key(day): int(count)
        for day, count in session.execute(
            select(_utc_day(Document.collected_at), func.count(Document.id))
            .where(_utc_day(Document.collected_at) >= first_day)
            .group_by(_utc_day(Document.collected_at))
        ).all()
    }
    fact_days = {
        _day_key(day): int(count)
        for day, count in session.execute(
            select(_utc_day(Fact.created_at), func.count(Fact.id))
            .where(_utc_day(Fact.created_at) >= first_day)
            .group_by(_utc_day(Fact.created_at))
        ).all()
    }
    by_source: list[dict] = []
    for item in source_summaries:
        source_id = item["source_id"]
        source_documents = documents_by_source.get(source_id, 0)
        source_facts = facts_by_source.get(source_id, 0)
        if not source_documents and not source_facts:
            continue
        by_source.append(
            {
                "source_id": source_id,
                "name": item["name"],
                "documents": source_documents,
                "facts": source_facts,
                "last_success_at": _iso(last_success.get(source_id)),
                "freshness": item["freshness"]["status"],
                "expected_freshness": item["freshness"][
                    "expected_freshness"
                ],
            }
        )
    daily = [
        {
            "date": day.isoformat(),
            "documents": document_days.get(day.isoformat(), 0),
            "facts": fact_days.get(day.isoformat(), 0),
        }
        for day in (
            first_day + timedelta(days=offset) for offset in range(14)
        )
    ]
    return {
        "documents": documents,
        "chunks": chunks,
        "facts": facts,
        "reports": reports,
        "sources": len(by_source),
        "earliest_collected_at": _iso(earliest),
        "latest_collected_at": _iso(latest),
        "documents_this_week": documents_this_week,
        "facts_this_week": facts_this_week,
        "by_source": by_source,
        "daily": daily,
    }


def _day_key(value: object) -> str:
    """Normalize a DB date/day value into ``YYYY-MM-DD``."""

    isoformat = getattr(value, "isoformat", None)
    return str(isoformat())[:10] if callable(isoformat) else str(value)


def _overview_attention(
    session: Session,
    source_summaries: list[dict],
    *,
    failed_sources: list[dict],
    backlog: dict,
    source_error: dict | None,
    task_error: dict | None,
    now: datetime,
) -> list[dict]:
    """Deterministic Owner-facing exceptions; no model judgement involved."""

    items: list[dict] = []
    for error, link, label in (
        (source_error, "/sources", "来源配置读取失败"),
        (task_error, "/tasks", "任务配置读取失败"),
    ):
        if not error:
            continue
        code = error.get("code") if isinstance(error, dict) else None
        items.append(
            {
                "kind": "config_error",
                "severity": "error",
                "message": f"{label}（{code}）" if code else label,
                "link": link,
            }
        )
    for source in failed_sources:
        items.append(
            {
                "kind": "source_failure",
                "severity": "warning",
                "message": f"{source['name']} 最近一次采集未完成",
                "link": "/sources",
                "source_id": source["source_id"],
            }
        )
    for source in source_summaries:
        freshness = source["freshness"]
        expected = freshness["expected_freshness"]
        # An unconfigured cadence is not a defect: only report a Source that
        # declared how fresh it should be and now misses it.
        if expected is None or freshness["status"] != "stale":
            continue
        items.append(
            {
                "kind": "source_stale",
                "severity": "warning",
                "message": f"{source['name']} 超过预期新鲜度 {expected}",
                "link": "/sources",
                "source_id": source["source_id"],
            }
        )
    latest_runs = session.scalars(
        select(TaskRun)
        .ext(distinct_on(TaskRun.task_id))
        .order_by(
            TaskRun.task_id,
            TaskRun.created_at.desc(),
            TaskRun.id.desc(),
        )
    ).all()
    for run in latest_runs:
        if run.status != "failed":
            continue
        items.append(
            {
                "kind": "task_failed",
                "severity": "error",
                "message": f"任务 {run.task_id} 最近一次运行失败",
                "link": f"/runs?task={run.task_id}&status=failed",
                "task_id": run.task_id,
            }
        )
    # Only failures that will not retry themselves need the Owner. A failed row
    # with an automatic retry still scheduled, and a pending row parked on a
    # capability gap (no processor, unsupported content, waiting for a verdict),
    # are states the system already explains: counting them would make
    # ``attention`` never empty in a deployment that has those gaps.
    failed_stages = int(
        session.scalar(
            select(func.count(DocumentProcessingState.document_id)).where(
                DocumentProcessingState.status == "failed",
                or_(
                    DocumentProcessingState.auto_retry.is_(False),
                    DocumentProcessingState.attempts
                    >= DocumentProcessingState.max_attempts,
                    DocumentProcessingState.next_attempt_at.is_(None),
                ),
            )
        )
        or 0
    )
    stale_pending = int(
        session.scalar(
            select(func.count(DocumentProcessingState.document_id)).where(
                DocumentProcessingState.status == "pending",
                DocumentProcessingState.wait_reason.is_(None),
                DocumentProcessingState.updated_at
                < now - timedelta(hours=1),
            )
        )
        or 0
    )
    if failed_stages or stale_pending:
        parts = []
        if failed_stages:
            parts.append(f"{failed_stages} 个处理阶段失败")
        if stale_pending:
            parts.append(f"{stale_pending} 个阶段待处理超过 1 小时")
        items.append(
            {
                "kind": "processing_backlog",
                "severity": "error" if failed_stages else "warning",
                "message": "处理积压：" + "，".join(parts),
                "link": "/runs?tab=processing",
            }
        )
    return items


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
            "usage": _usage_summary(calls),
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
        calls = session.scalars(
            select(ModelCall)
            .where(ModelCall.run_id == report.run_id)
            .order_by(ModelCall.step_id, ModelCall.attempt_number)
        ).all()
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
            "evidence_package": _package_payload(
                package, _research_ledger(analysis, run)
            ),
            "research_inputs": _research_inputs_payload(analysis, run),
            "evidence": entries,
            "cited_chunk_ids": [
                entry["chunk_id"]
                for entry in entries
                if (entry["chunk_id"], entry["document_id"]) in cited
            ],
            "usage": _usage_summary(list(calls)),
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


@app.get("/api/reports/{report_id}/comparison")
def report_comparison(
    report_id: UUID,
    other_report_id: UUID = Query(...),
) -> dict:
    """Deterministic comparison of two saved reports, earlier side first.

    Both sides are read from their own stored Analysis, Evidence Package and
    cited Document versions; the endpoint never re-runs research and never
    rewrites the older report.
    """

    if report_id == other_report_id:
        raise HTTPException(
            status_code=400, detail="A report cannot be compared with itself"
        )
    config = settings()
    with session_factory()() as session:
        first = _report_side(session, config, report_id)
        second = _report_side(session, config, other_report_id)
    for side in (first, second):
        # A report always belongs to a successful Run; the guard keeps a
        # failed or unfinished Run from ever standing in as a successful history.
        if side["run_status"] != "succeeded":
            raise HTTPException(
                status_code=409,
                detail="Report comparison requires successful Runs",
            )
    ordered = sorted(
        (first, second),
        key=lambda side: (
            side["report"]["created_at"],
            side["report"]["report_id"],
        ),
    )
    earlier, later = ordered
    # ``swapped`` is True when the report named in the path is not the earlier
    # side, so a caller can tell that the response order differs from its own.
    earlier = {
        **earlier,
        "ordering": {
            "requested_report_id": str(report_id),
            "swapped": earlier["report"]["report_id"] != str(report_id),
        },
    }
    return compare_report_sides(earlier, later)


def _report_side(session: Session, config: Settings, report_id: UUID) -> dict:
    """One report's stored snapshot, readable without touching current data."""

    report, analysis, package = _report_chain(session, report_id)
    run = session.get(TaskRun, report.run_id)
    ledger = _research_ledger(analysis, run)
    refs = _report_research_refs(analysis, package, run)
    # A comparison states what each report actually cited, not everything its
    # Evidence Package happened to select or adopt. The saved Analysis refs are
    # the authority; the frozen input only supplies their readable identity.
    cited_refs = _cited_research_refs(analysis, refs)
    entries, _states = _resolve_evidence(session, config, cited_refs)
    return {
        "report": {
            "report_id": str(report.id),
            "run_id": str(report.run_id),
            "analysis_id": str(report.analysis_id),
            "task_id": report.task_id,
            "title": report.title,
            "created_at": report.created_at,
        },
        "run_status": run.status if run is not None else None,
        "analysis": {
            "summary": analysis.summary,
            "findings": _safe_external_payload(analysis.findings),
            "open_questions": deepcopy(analysis.open_questions),
            "evidence_refs": _safe_external_payload(analysis.evidence_refs),
            "created_at": analysis.created_at,
        },
        "package": {
            "evidence_package_id": str(package.id),
            "as_of": package.as_of,
            "scope": _safe_external_payload(package.scope),
            "selection_policy_version": package.selection_policy_version,
            "coverage": _safe_external_payload(package.coverage),
            "selected_count": package.selected_count,
            "candidates_discovered": package.candidates_discovered,
            "candidates_screened": package.candidates_screened,
            "unexamined_count": package.unexamined_count,
            "retrieval_truncated": package.retrieval_truncated,
        },
        "documents": _report_document_refs(entries, cited_refs),
        "facts": _cited_fact_snapshots(package, ledger, cited_refs),
        "research_input_count": sum(
            1
            for item in ledger
            if isinstance(item, dict) and item.get("kind") == "input"
        ),
    }


def _report_document_refs(entries: list[dict], refs: list[dict]) -> list[dict]:
    """One entry per cited Document version, with every citation it carries."""

    documents: dict[str, dict] = {}
    order: list[str] = []
    for index, entry in enumerate(entries):
        reference = refs[index] if index < len(refs) else {}
        payload = entry.get("document") if isinstance(entry.get("document"), dict) else {}
        key = str(entry.get("document_id") or payload.get("id") or "")
        if not key:
            continue
        item = documents.get(key)
        if item is None:
            item = {
                "document_id": key,
                "version": payload.get("version"),
                "content_hash": payload.get("content_hash"),
                "source_id": payload.get("source_id"),
                "dedupe_key": payload.get("dedupe_key"),
                "title": payload.get("title"),
                "original_url": payload.get("original_url"),
                "published_at": payload.get("published_at"),
                "collected_at": payload.get("collected_at"),
                "origin": entry.get("origin"),
                "origin_id": entry.get("origin_id"),
                "version_matches": entry.get("version_matches"),
                "role": entry.get("role"),
                "chunk_ids": [],
                "fact_ids": [],
            }
            documents[key] = item
            order.append(key)
        chunk_id = entry.get("chunk_id")
        if chunk_id and chunk_id not in item["chunk_ids"]:
            item["chunk_ids"].append(chunk_id)
        for fact_id in reference.get("fact_ids") or []:
            if fact_id not in item["fact_ids"]:
                item["fact_ids"].append(fact_id)
    return [documents[key] for key in order]


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


def _package_payload(
    package: EvidencePackage, ledger: list | None = None
) -> dict:
    """The frozen package plus the final comparison of everything it adopted.

    ``metric_comparisons`` keeps its initial-selection meaning; the report also
    exposes the same final comparison the Agent received, so an adopted Tool
    input's Fact is never visible in one view and missing from another.
    """

    try:
        final_comparisons = final_metric_comparisons(package, ledger)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Report research inputs are invalid"
        ) from exc
    return {
        "evidence_package_id": str(package.id),
        "run_id": str(package.run_id),
        "objective": package.objective,
        "scope": _safe_external_payload(package.scope),
        "as_of": package.as_of,
        "selection_policy_version": package.selection_policy_version,
        "selection_budget": package.selection_budget,
        "queries": _safe_external_payload(package.queries),
        "metric_comparisons": _safe_external_payload(package.metric_comparisons),
        "final_metric_comparisons": _safe_external_payload(final_comparisons),
        "coverage": _safe_external_payload(package.coverage),
        "candidates_discovered": package.candidates_discovered,
        "candidates_screened": package.candidates_screened,
        "selected_count": package.selected_count,
        "unexamined_count": package.unexamined_count,
        "retrieval_truncated": package.retrieval_truncated,
        "created_at": package.created_at,
    }


def _cited_research_refs(
    analysis: Analysis, refs: list[dict]
) -> list[dict]:
    """The frozen input references this Analysis actually cited, once each.

    Top-level references and every finding's references form one set keyed by
    Chunk and Document, so a citation repeated across findings is compared once.
    A saved reference that is not part of this Run's frozen input is a corrupt
    snapshot, not extra evidence: it is refused rather than silently resolved.
    """

    cited_keys = _cited_keys(analysis)
    by_key = {
        (str(item.get("chunk_id")), str(item.get("document_id"))): item
        for item in refs
        if isinstance(item, dict)
    }
    missing = cited_keys - set(by_key)
    if missing:
        raise HTTPException(
            status_code=409,
            detail="Report cites evidence outside its frozen inputs",
        )
    # Follow the frozen input order so a repeated request produces the same
    # comparison order instead of one derived from set iteration.
    return [
        item
        for item in refs
        if isinstance(item, dict)
        and (str(item.get("chunk_id")), str(item.get("document_id")))
        in cited_keys
    ]


def _cited_fact_snapshots(
    package: EvidencePackage,
    ledger: list | None,
    cited_refs: list[dict],
) -> list[dict]:
    """The Facts attached to the Chunks this Analysis actually cited.

    A citation names a Chunk; the frozen input for that Chunk names the Fact IDs
    it carried. Only those Facts are compared, so a wider selection or a new Fact
    on the same Document never enters the comparison. Values come from the frozen
    projection, so a later reprocess cannot rewrite what the report cited.
    """

    try:
        frozen = merged_research_fact_refs(package, ledger)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Report research inputs are invalid"
        ) from exc
    cited_fact_ids: list[str] = []
    for reference in cited_refs:
        for value in reference.get("fact_ids") or []:
            fact_id = str(value)
            if fact_id not in cited_fact_ids:
                cited_fact_ids.append(fact_id)
    if not cited_fact_ids:
        return []
    by_id = {str(fact.get("fact_id")): fact for fact in frozen}
    snapshots: list[dict] = []
    for fact_id in cited_fact_ids:
        fact = by_id.get(fact_id)
        if fact is None:
            # The citation is legal but the frozen projection does not explain
            # its value. Keep the identity with an explicitly unknown value
            # instead of reading today's row or inventing a zero.
            snapshots.append(
                {
                    "fact_id": fact_id,
                    "value": None,
                    "unit": None,
                    "currency": None,
                    "period_start": None,
                    "period_end": None,
                    "available_at": None,
                    "statement": None,
                    "unavailable": True,
                }
            )
            continue
        attributes = fact.get("attributes")
        currency = (
            attributes.get("currency") if isinstance(attributes, dict) else None
        )
        snapshots.append(
            {
                **deepcopy(fact),
                "currency": currency if isinstance(currency, str) else None,
            }
        )
    return sorted(snapshots, key=lambda row: str(row.get("fact_id")))


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
                # The confirmed-origin cluster this Document belongs to, so a
                # reader can see that several entries are one original.
                "origin_id": item.get("origin_id"),
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
                "origin": _safe_external_payload(
                    document.extra.get("origin") if document is not None else None
                ),
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
        "provider": call.provider,
        "status": call.status,
        "duration_ms": call.duration_ms,
        "prompt_tokens": call.prompt_tokens,
        "completion_tokens": call.completion_tokens,
        "total_tokens": call.total_tokens,
        "cost_usd": (
            float(call.cost_usd) if call.cost_usd is not None else None
        ),
        "fallback_reason": call.fallback_reason,
        "error": _safe_error(call.error),
        "created_at": _iso(call.created_at),
    }


def _usage_summary(calls: list[ModelCall]) -> dict:
    """Deterministic usage built only from recorded ModelCall rows.

    Sums come from stored attempts, provider labels come from the Run's frozen
    model snapshot, and a missing provider-reported cost stays ``null`` instead
    of a fabricated zero. ``cost_unknown_calls`` counts the calls without one.
    """

    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    recorded = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for call in calls:
        for field in totals:
            value = getattr(call, field)
            if value is not None:
                totals[field] += value
                recorded[field] += 1

    costs = [call.cost_usd for call in calls if call.cost_usd is not None]
    grouped: dict[tuple[str | None, str], dict] = {}
    for call in calls:
        entry = grouped.setdefault(
            (call.provider, call.attempt_model_name),
            {
                "provider": call.provider,
                "model_name": call.attempt_model_name,
                "calls": 0,
                "recorded_tokens": 0,
                "total_tokens": 0,
                "cost_usd": Decimal("0"),
                "cost_known_calls": 0,
                "cost_unknown_calls": 0,
            },
        )
        entry["calls"] += 1
        if call.total_tokens is not None:
            entry["total_tokens"] += call.total_tokens
            entry["recorded_tokens"] += 1
        if call.cost_usd is not None:
            entry["cost_usd"] += call.cost_usd
            entry["cost_known_calls"] += 1
        else:
            entry["cost_unknown_calls"] += 1
    models = [
        {
            "provider": entry["provider"],
            "model_name": entry["model_name"],
            "calls": entry["calls"],
            "total_tokens": (
                entry["total_tokens"] if entry["recorded_tokens"] else None
            ),
            "cost_usd": (
                float(entry["cost_usd"]) if entry["cost_known_calls"] else None
            ),
            "cost_unknown_calls": entry["cost_unknown_calls"],
        }
        for entry in sorted(
            grouped.values(),
            key=lambda item: (
                -item["calls"],
                str(item["model_name"]),
                str(item["provider"]),
            ),
        )
    ]
    return {
        "call_count": len(calls),
        "succeeded_calls": sum(
            1 for call in calls if call.status == "succeeded"
        ),
        "failed_calls": sum(1 for call in calls if call.status == "failed"),
        "fallback_calls": sum(1 for call in calls if call.fallback_reason),
        "prompt_tokens": (
            totals["prompt_tokens"] if recorded["prompt_tokens"] else None
        ),
        "completion_tokens": (
            totals["completion_tokens"]
            if recorded["completion_tokens"]
            else None
        ),
        "total_tokens": (
            totals["total_tokens"] if recorded["total_tokens"] else None
        ),
        "duration_ms": sum(call.duration_ms for call in calls),
        "cost_usd": float(sum(costs)) if costs else None,
        "cost_known_calls": len(costs),
        "cost_unknown_calls": len(calls) - len(costs),
        "models": models[:10],
        "models_truncated": len(models) > 10,
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
    packages = {
        package.run_id: package
        for package in (
            session.scalars(
                select(EvidencePackage).where(
                    EvidencePackage.run_id.in_(
                        [report.run_id for report in rows]
                    )
                )
            ).all()
            if rows
            else []
        )
    }
    items: list[dict] = []
    for report in rows:
        package = packages.get(report.run_id)
        coverage = (
            package.coverage
            if package is not None and isinstance(package.coverage, dict)
            else {}
        )
        gaps = coverage.get("gaps")
        items.append(
            {
                "report_id": str(report.id),
                "run_id": str(report.run_id),
                "task_id": report.task_id,
                "title": report.title,
                "created_at": _iso(report.created_at),
                # Independent originals behind the report and how many recorded
                # gaps it carries; both stay null when no package is stored.
                "origin_group_count": (
                    coverage.get("origin_group_count")
                    if package is not None
                    else None
                ),
                "gap_count": len(gaps) if isinstance(gaps, list) else None,
            }
        )
    return items


def _latest_research_coverage(session: Session) -> dict | None:
    """Coverage of the newest successful research Run, read from its package.

    Unknown counts stay ``null``: the Overview must not present an unscanned
    candidate pool as a completed one.
    """

    row = session.execute(
        select(EvidencePackage, Report, TaskRun)
        .join(Report, Report.run_id == EvidencePackage.run_id)
        .join(TaskRun, TaskRun.id == EvidencePackage.run_id)
        .where(TaskRun.status == "succeeded")
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    package, report, run = row
    coverage = package.coverage if isinstance(package.coverage, dict) else {}
    ledger = _research_ledger(None, run)
    return {
        "report_id": str(report.id),
        "run_id": str(run.id),
        "task_id": report.task_id,
        "title": report.title,
        "created_at": _iso(report.created_at),
        "as_of": _iso(package.as_of),
        "candidates_discovered": package.candidates_discovered,
        "candidates_screened": package.candidates_screened,
        "selected_count": package.selected_count,
        "evidence_group_count": coverage.get("evidence_group_count"),
        "origin_group_count": coverage.get("origin_group_count"),
        "merged_origin_group_count": coverage.get("merged_origin_group_count"),
        "fact_count": coverage.get("fact_count"),
        "unexamined_count": package.unexamined_count,
        "retrieval_truncated": package.retrieval_truncated,
        "research_input_count": sum(
            1
            for item in ledger
            if isinstance(item, dict) and item.get("kind") == "input"
        ),
        "gaps": [
            gap for gap in coverage.get("gaps") or [] if isinstance(gap, str)
        ],
    }


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
