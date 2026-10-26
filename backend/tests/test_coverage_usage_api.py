"""PostgreSQL-backed tests for V2-05 coverage and model-usage views.

Overview, Run and Report views must read stored Evidence Package counts and
ModelCall rows only: an unknown candidate pool or an unreported provider cost
stays unknown instead of being shown as zero, and a failed Run never stands in
as the latest successful research coverage.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stocks.api import app
from stocks.auth import SESSION_COOKIE
from stocks.models import (
    Analysis,
    Base,
    EvidencePackage,
    ModelCall,
    Report,
    TaskDefinition,
    TaskRun,
)
from stocks.settings import Settings

TASK_ID = "coverage_usage_brief"
CREATED_AT = datetime(2026, 10, 22, 2, tzinfo=UTC)


def make_config() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_bucket="evidence",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
        owner_password="test-owner-password",
        owner_session_secret="test-owner-session-secret-32-characters",
    )


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for coverage/usage API tests")
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
def client(engine, monkeypatch):
    config = make_config()
    monkeypatch.setattr(
        "stocks.api.session_factory", lambda: sessionmaker(bind=engine)
    )
    monkeypatch.setattr("stocks.api.settings", lambda: config)
    with TestClient(app) as client:
        login = client.post(
            "/api/session/login",
            json={"password": config.owner_password.get_secret_value()},
        )
        assert login.status_code == 200
        assert SESSION_COOKIE in client.cookies
        yield client


def persist_task(session: Session) -> None:
    if session.get(TaskDefinition, TASK_ID) is not None:
        return
    session.add(
        TaskDefinition(
            id=TASK_ID,
            name="Coverage Usage Brief",
            type="research",
            trigger="schedule",
            enabled=True,
            scope={"regions": ["US"]},
            config_snapshot={},
            config_hash="a" * 64,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
    )
    session.commit()


def persist_run(
    session: Session,
    *,
    status: str = "succeeded",
    created_at: datetime = CREATED_AT,
    candidates_discovered: int | None = None,
    candidates_screened: int | None = 3,
    selected_count: int = 2,
    unexamined_count: int | None = None,
    retrieval_truncated: bool = True,
    coverage: dict | None = None,
    research_inputs: list | None = None,
) -> dict:
    """Save a Run with its package, plus Analysis and Report on success."""

    persist_task(session)
    run = TaskRun(
        task_id=TASK_ID,
        status=status,
        objective="Summarize saved US macro evidence.",
        strategy="evidence_first",
        scope={"regions": ["US"], "topics": ["macro"]},
        config_snapshot={},
        model_snapshot={
            "agents": {
                "research": {
                    "routing": {"primary": "research"},
                    "targets": [
                        {
                            "alias": "research",
                            "provider": "default",
                            "model_name": "research-model",
                        }
                    ],
                }
            }
        },
        prompt_snapshot={"research_writer": "Analyze only supplied evidence."},
        config_hash="a" * 64,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        error=None if status == "succeeded" else {"code": "MODEL_UNAVAILABLE"},
        research_inputs=research_inputs or [],
    )
    session.add(run)
    session.flush()
    package = EvidencePackage(
        run_id=run.id,
        objective=run.objective,
        scope=run.scope,
        as_of=created_at,
        selection_policy_version="v0.1",
        selection_budget={"max_tokens": 30000, "limit_per_query": 8},
        queries=[],
        selected_chunks=[],
        coverage=coverage
        or {
            "gaps": ["partial_source_content", "chunk_not_in_current_index"],
            "evidence_group_count": 2,
            "origin_group_count": 1,
            "merged_origin_group_count": 1,
            "fact_count": 3,
        },
        candidates_discovered=candidates_discovered,
        candidates_screened=candidates_screened,
        selected_count=selected_count,
        unexamined_count=unexamined_count,
        retrieval_truncated=retrieval_truncated,
        created_at=created_at,
    )
    session.add(package)
    report_id = None
    if status == "succeeded":
        session.flush()
        analysis = Analysis(
            run_id=run.id,
            evidence_package_id=package.id,
            title="研究资料与覆盖边界",
            summary="Two selected groups support the statement.",
            sections=[{"heading": "资料覆盖", "body": "Two selected groups support the statement.", "finding_ids": []}],
            findings=[],
            open_questions=[],
            evidence_refs=[],
            research_inputs=list(run.research_inputs or []),
            coverage=package.coverage,
            created_at=created_at,
        )
        session.add(analysis)
        session.flush()
        report = Report(
            run_id=run.id,
            analysis_id=analysis.id,
            task_id=TASK_ID,
            title="Coverage Usage Brief",
            content="# Coverage Usage Brief\n",
            created_at=created_at,
        )
        session.add(report)
        session.flush()
        report_id = str(report.id)
    session.commit()
    return {"run_id": str(run.id), "report_id": report_id}


def persist_call(
    session: Session,
    run_id: str,
    *,
    model_name: str = "research-model",
    provider: str | None = "default",
    attempt_number: int = 1,
    step_id: str = "research_agent",
    prompt_tokens: int | None = 100,
    completion_tokens: int | None = 50,
    total_tokens: int | None = 150,
    cost_usd: Decimal | None = None,
    duration_ms: int = 1000,
    fallback_reason: str | None = None,
) -> None:
    session.add(
        ModelCall(
            run_id=UUID(run_id),
            step_id=step_id,
            attempt_number=attempt_number,
            requested_alias="research",
            attempt_alias="research",
            requested_model_name="research-model",
            attempt_model_name=model_name,
            provider=provider,
            status="succeeded",
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            fallback_reason=fallback_reason,
            created_at=CREATED_AT,
        )
    )
    session.commit()


def test_overview_shows_stored_coverage_and_keeps_unknowns_unknown(engine, client):
    with Session(engine) as session:
        saved = persist_run(
            session,
            research_inputs=[
                {
                    "kind": "selection_snapshot",
                    "recorded_at": CREATED_AT.isoformat(),
                },
                {
                    "kind": "input",
                    "input_id": "input-1",
                    "tool_id": "research_search",
                    "document_id": "d1",
                    "source_id": "sec",
                    "recorded_at": CREATED_AT.isoformat(),
                },
                {
                    "kind": "gap",
                    "code": "tool_budget_exhausted",
                    "recorded_at": CREATED_AT.isoformat(),
                },
            ],
        )
        persist_call(
            session,
            saved["run_id"],
            cost_usd=Decimal("0.002500"),
            duration_ms=1200,
        )
        persist_call(
            session,
            saved["run_id"],
            model_name="cheap-model",
            attempt_number=2,
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            duration_ms=800,
            fallback_reason="retryable_status",
        )

    body = client.get("/api/overview").json()
    latest = body["research"]["latest"]
    assert latest["report_id"] == saved["report_id"]
    assert latest["run_id"] == saved["run_id"]
    # An unexamined, unknown candidate pool stays null instead of becoming 0.
    assert latest["candidates_discovered"] is None
    assert latest["unexamined_count"] is None
    assert latest["retrieval_truncated"] is True
    assert latest["selected_count"] == 2
    assert latest["candidates_screened"] == 3
    assert latest["evidence_group_count"] == 2
    assert latest["origin_group_count"] == 1
    assert latest["merged_origin_group_count"] == 1
    assert latest["research_input_count"] == 1
    assert latest["gaps"] == [
        "partial_source_content",
        "chunk_not_in_current_index",
    ]

    usage = body["research"]["usage"]
    assert usage["call_count"] == 2
    assert usage["succeeded_calls"] == 2
    assert usage["failed_calls"] == 0
    assert usage["fallback_calls"] == 1
    assert usage["total_tokens"] == 180
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 60
    assert usage["duration_ms"] == 2000
    # Only the provider-reported cost is summed; the unreported call is counted
    # as unknown rather than priced locally.
    assert usage["cost_usd"] == pytest.approx(0.0025)
    assert usage["cost_known_calls"] == 1
    assert usage["cost_unknown_calls"] == 1
    assert usage["run_count"] == 1
    models = {model["model_name"]: model for model in usage["models"]}
    assert set(models) == {"research-model", "cheap-model"}
    assert models["research-model"]["provider"] == "default"
    assert models["research-model"]["cost_usd"] == pytest.approx(0.0025)
    assert models["cheap-model"]["cost_usd"] is None
    assert models["cheap-model"]["cost_unknown_calls"] == 1


def test_overview_ignores_a_failed_run_without_a_report(engine, client):
    with Session(engine) as session:
        saved = persist_run(session, created_at=CREATED_AT)
        persist_run(
            session,
            status="failed",
            created_at=CREATED_AT + timedelta(days=1),
            coverage={"gaps": [], "evidence_group_count": 0},
        )

    latest = client.get("/api/overview").json()["research"]["latest"]
    # The newest package belongs to a failed Run, so it is not coverage history.
    assert latest["report_id"] == saved["report_id"]
    assert latest["created_at"].startswith("2026-10-22T02:00:00")


def test_run_and_report_usage_expose_provider_and_unknown_cost(engine, client):
    with Session(engine) as session:
        saved = persist_run(session)
        persist_call(session, saved["run_id"], cost_usd=Decimal("0.000123"))
        persist_call(
            session,
            saved["run_id"],
            model_name="cheap-model",
            attempt_number=2,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    detail = client.get(f"/api/runs/{saved['run_id']}").json()
    calls = detail["model_calls"]
    assert [call["provider"] for call in calls] == ["default", "default"]
    assert calls[0]["cost_usd"] == pytest.approx(0.000123)
    # A provider that reported no cost is unknown, not a zero amount.
    assert calls[1]["cost_usd"] is None
    assert detail["usage"]["cost_usd"] == pytest.approx(0.000123)
    assert detail["usage"]["cost_unknown_calls"] == 1
    assert detail["usage"]["total_tokens"] == 150
    models = {model["model_name"]: model for model in detail["usage"]["models"]}
    assert models["cheap-model"]["total_tokens"] is None

    report = client.get(f"/api/reports/{saved['report_id']}").json()
    assert report["usage"] == detail["usage"]
    assert report["coverage_gaps"] == [
        "partial_source_content",
        "chunk_not_in_current_index",
    ]
    assert report["run"]["run_id"] == saved["run_id"]
