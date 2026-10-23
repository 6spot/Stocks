"""Owner console API: access control, bounded reads and recovery boundaries."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import stocks.api as api_module
from stocks.auth import SESSION_COOKIE, OwnerSessionManager
from stocks.models import (
    Analysis,
    Base,
    Document,
    DocumentProcessingState,
    EvidencePackage,
    Fact,
    ModelCall,
    Report,
    Source,
    TaskDefinition,
    TaskRun,
    TaskStepResult,
)
from stocks.repository import DocumentInput, ingest_document
from stocks.settings import Settings
from stocks.embedding_space import VECTOR_DIMENSIONS

NOW = datetime(2026, 10, 22, 8, tzinfo=UTC)
OWNER_PASSWORD = "test-owner-password"
OWNER_SECRET = "test-owner-session-secret-32-characters"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "s3_endpoint_url": "https://storage.invalid",
        "s3_bucket": "stocks-test",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "owner_password": OWNER_PASSWORD,
        "owner_session_secret": OWNER_SECRET,
        "owner_cookie_secure": True,
        "sources_config_path": str(tmp_path / "sources.yaml"),
        "tasks_config_path": str(tmp_path / "tasks.yaml"),
        "models_config_path": str(tmp_path / "models.yaml"),
    }
    values.update(overrides)
    return Settings(**values)


def write_sources(path: Path) -> None:
    path.write_text(
        """
sources:
  - source_id: alpha
    name: Alpha Feed
    connector: rss
    enabled: true
    expected_freshness: 2h
    publisher: Alpha Publisher
    trust_tier: T1
    feed_url: https://example.com/alpha.xml
    timeout_seconds: 10
    max_response_bytes: 100000
    max_redirects: 1
    max_items: 10
    user_agent: StocksTest/0.1 (owner@example.com)
  - source_id: beta
    name: Beta Feed
    connector: rss
    enabled: true
    expected_freshness: 1h
    publisher: Beta Publisher
    trust_tier: T2
    feed_url: https://example.com/beta.xml
    timeout_seconds: 10
    max_response_bytes: 100000
    max_redirects: 1
    max_items: 10
    user_agent: StocksTest/0.1 (owner@example.com)
""",
        encoding="utf-8",
    )


def write_tasks(path: Path) -> None:
    path.write_text(
        """
tasks:
  - id: collection_task
    name: Collection
    type: collection
    trigger: manual
    sources: [alpha, beta]
    steps:
      - id: collect
        collect_sources:
          kind: collect_sources
  - id: research_task
    name: Research
    type: research
    trigger: manual
    objective: Review stored evidence.
    scope: {topics: [macro]}
    steps:
      - id: evidence
        select_evidence:
          kind: select_evidence
          queries: [macro]
      - id: research
        research_agent:
          kind: research_agent
          agent: writer
          input: evidence
agents:
  - id: writer
    role: Writer
    prompt: Analyze the supplied evidence.
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
            - type: string
            - type: "null"
        scope: {type: object, additionalProperties: true}
        as_of: {type: string}
        evidence_package_id: {type: string}
        evidence: {type: array, items: {type: object, additionalProperties: true}}
        coverage: {type: object, additionalProperties: true}
        truncated: {type: boolean}
    output_schema:
      type: object
      additionalProperties: false
      required: [summary, findings, open_questions, evidence_refs]
      properties:
        summary: {type: string}
        findings: {type: array, items: {type: object, additionalProperties: false, required: [kind, statement, rationale, uncertainty, evidence_refs], properties: {kind: {type: string}, statement: {type: string}, rationale: {type: string}, uncertainty: {type: string}, evidence_refs: {type: array, minItems: 1, items: {type: object, additionalProperties: false, required: [chunk_id, document_id], properties: {chunk_id: {type: string}, document_id: {type: string}}}}}}}
        open_questions: {type: array, items: {type: string}}
        evidence_refs: {type: array, items: {type: object, additionalProperties: false, required: [chunk_id, document_id], properties: {chunk_id: {type: string}, document_id: {type: string}}}}
""",
        encoding="utf-8",
    )


def write_models(path: Path) -> None:
    path.write_text(
        f"""
providers:
  default:
    protocol: openai_compatible
    base_url: https://models.invalid/v1
    api_key: test-key
    capabilities: [chat, text, structured_output]
    timeout_seconds: 10
models:
  research:
    provider: default
    name: research-model
    capabilities: [chat, text, structured_output]
  embedding:
    provider: default
    name: embedding-model
    dimensions: {VECTOR_DIMENSIONS}
embedding:
  models: [embedding]
agents:
  research:
    routing:
      primary: research
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 10
      retries: 0
""",
        encoding="utf-8",
    )


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for console API tests")
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
def config(tmp_path):
    write_sources(tmp_path / "sources.yaml")
    write_tasks(tmp_path / "tasks.yaml")
    write_models(tmp_path / "models.yaml")
    return make_settings(tmp_path)


@pytest.fixture
def snapshot(monkeypatch):
    objects: dict[str, bytes] = {}

    def store(_config, source_id, payload, *, content_type):
        del content_type
        key = f"sources/{source_id}/{sha256(payload).hexdigest()}"
        objects[key] = payload
        return key, sha256(payload).hexdigest()

    def read(_config, key):
        return objects[key]

    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    return objects, store


@pytest.fixture
def client(engine, config, monkeypatch):
    monkeypatch.setattr(
        api_module,
        "session_factory",
        lambda: sessionmaker(bind=engine),
    )
    monkeypatch.setattr(api_module, "settings", lambda: config)
    with TestClient(
        api_module.app,
        base_url="https://testserver",
    ) as test_client:
        yield test_client


def login(client: TestClient, password: str = OWNER_PASSWORD) -> str:
    response = client.post(
        "/api/session/login",
        json={"password": password},
    )
    assert response.status_code == 200
    return response.json()["session"]["csrf_token"]


def create_document(
    engine,
    config: Settings,
    store,
    *,
    source_id: str = "alpha",
    dedupe_key: str = "release",
    title: str = "Release",
    payload: bytes | None = None,
    collected_at: datetime = NOW,
    published_at: datetime | None | object = ...,
) -> UUID:
    payload = payload or b"<html><body><p>Stored evidence</p></body></html>"
    with Session(engine) as session:
        if session.get(Source, source_id) is None:
            session.add(
                Source(
                    id=source_id,
                    name=source_id.title(),
                    connector="rss",
                    publisher=f"{source_id.title()} Publisher",
                    trust_tier="T1",
                )
            )
            session.commit()
        document = ingest_document(
            session,
            config,
            DocumentInput(
                source_id=source_id,
                dedupe_key=dedupe_key,
                title=title,
                payload=payload,
                content_type="text/html; charset=utf-8",
                collected_at=collected_at,
                published_at=(
                    collected_at if published_at is ... else published_at
                ),
                original_url=(
                    "https://example.com/path?token=secret#fragment"
                ),
                completeness="complete",
            ),
            store=store,
        ).document
        document_id = document.id
        session.expunge_all()
        return document_id


def add_task_definition(
    session: Session,
    task_id: str,
    *,
    task_type: str = "collection",
    enabled: bool = True,
) -> None:
    session.merge(
        TaskDefinition(
            id=task_id,
            name=task_id,
            type=task_type,
            trigger="manual",
            enabled=enabled,
            strategy=None,
            scope={"source_ids": ["alpha", "beta"]}
            if task_type == "collection"
            else {"topics": ["macro"]},
            config_snapshot={"task": {"id": task_id}},
            config_hash="a" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def test_public_login_cookie_and_private_routes_fail_closed(
    engine, config, monkeypatch
):
    monkeypatch.setattr(api_module, "settings", lambda: config)
    with TestClient(
        api_module.app,
        base_url="https://testserver",
    ) as anonymous:
        assert anonymous.get("/health").status_code == 200
        private = anonymous.get("/api/reports")
        assert private.status_code == 401
        assert private.json()["detail"]["code"] == "OWNER_AUTH_REQUIRED"

        wrong = anonymous.post(
            "/api/session/login",
            json={"password": "wrong"},
        )
        assert wrong.status_code == 401
        assert (
            wrong.json()["detail"]["code"]
            == "OWNER_CREDENTIALS_INVALID"
        )

        success = anonymous.post(
            "/api/session/login",
            json={"password": OWNER_PASSWORD},
        )
        assert success.status_code == 200
        set_cookie = success.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "secure" in set_cookie
        assert "samesite=strict" in set_cookie
        csrf = success.json()["session"]["csrf_token"]

        no_csrf = anonymous.post("/api/processing/retry", json={})
        assert no_csrf.status_code == 403
        assert no_csrf.json()["detail"]["code"] == "CSRF_TOKEN_INVALID"

        logout_no_csrf = anonymous.post("/api/session/logout")
        assert logout_no_csrf.status_code == 403
        assert (
            logout_no_csrf.json()["detail"]["code"]
            == "CSRF_TOKEN_INVALID"
        )

        logout = anonymous.post(
            "/api/session/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert logout.status_code == 200
        assert SESSION_COOKIE not in anonymous.cookies
        assert anonymous.get("/api/documents").status_code == 401


def test_login_rate_limit_corrupt_cookie_and_expiry(
    engine, config, monkeypatch
):
    monkeypatch.setattr(api_module, "settings", lambda: config)
    clock = {"now": 1_000.0}
    manager = OwnerSessionManager(
        OWNER_PASSWORD,
        OWNER_SECRET,
        ttl_seconds=300,
        clock=lambda: clock["now"],
    )
    monkeypatch.setattr(
        api_module,
        "_owner_session_manager",
        lambda *_args: manager,
    )
    with TestClient(
        api_module.app,
        base_url="https://testserver",
    ) as client:
        for _ in range(10):
            assert (
                client.post(
                    "/api/session/login",
                    json={"password": "wrong"},
                ).status_code
                == 401
            )
        limited = client.post(
            "/api/session/login",
            json={"password": OWNER_PASSWORD},
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1

    token, _session = manager.issue()
    with TestClient(
        api_module.app,
        base_url="https://testserver",
    ) as client:
        client.cookies.set(SESSION_COOKIE, token + "corrupt")
        corrupt = client.get("/api/session")
        assert corrupt.status_code == 401
        assert corrupt.json()["detail"]["code"] == "OWNER_SESSION_INVALID"

    with TestClient(
        api_module.app,
        base_url="https://testserver",
    ) as client:
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/session").status_code == 200

    clock["now"] = 1_301.0
    with TestClient(
        api_module.app,
        base_url="https://testserver",
    ) as client:
        client.cookies.set(SESSION_COOKIE, token)
        expired = client.get("/api/session")
        assert expired.status_code == 401
        assert expired.json()["detail"]["code"] == "OWNER_SESSION_EXPIRED"


def test_pagination_is_stable_and_invalid_cursors_are_rejected(
    engine, config, client, snapshot
):
    _objects, store = snapshot
    login(client)
    document_ids = [
        create_document(
            engine,
            config,
            store,
            dedupe_key=f"release-{index}",
            collected_at=NOW + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    with Session(engine) as session:
        add_task_definition(session, "collection_task")
        for index in range(3):
            session.add(
                TaskRun(
                    task_id="collection_task",
                    status="succeeded",
                    objective="Collect",
                    strategy=None,
                    scope={"source_ids": ["alpha"]},
                    config_snapshot={},
                    model_snapshot={},
                    prompt_snapshot={},
                    config_hash="a" * 64,
                    created_at=NOW + timedelta(minutes=index),
                )
            )
        session.commit()

    first = client.get(
        "/api/documents",
        params={"limit": 2},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert [item["id"] for item in first_payload["items"]] == [
        str(document_ids[2]),
        str(document_ids[1]),
    ]
    assert first_payload["has_more"] is True
    second = client.get(
        "/api/documents",
        params={
            "limit": 2,
            "cursor": first_payload["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == [
        str(document_ids[0])
    ]
    assert second.json()["has_more"] is False

    runs = client.get(
        "/api/runs",
        params={"task_id": "collection_task", "limit": 2},
    ).json()
    assert runs["has_more"] is True
    assert len(runs["items"]) == 2
    runs_page_two = client.get(
        "/api/runs",
        params={
            "task_id": "collection_task",
            "limit": 2,
            "cursor": runs["next_cursor"],
        },
    )
    assert runs_page_two.status_code == 200
    assert len(runs_page_two.json()["items"]) == 1

    invalid = client.get(
        "/api/runs",
        params={"cursor": "not-a-cursor"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "CURSOR_INVALID"
    invalid_processing = client.get(
        "/api/processing",
        params={"cursor": "not-a-processing-cursor"},
    )
    assert invalid_processing.status_code == 422
    assert (
        invalid_processing.json()["detail"]["code"]
        == "CURSOR_INVALID"
    )


def test_overview_sources_distinguish_failure_no_update_freshness_and_backlog(
    engine, config, client, snapshot
):
    _objects, store = snapshot
    login(client)
    now = datetime.now(UTC)
    alpha_document = create_document(
        engine,
        config,
        store,
        source_id="alpha",
        collected_at=now - timedelta(minutes=30),
    )
    create_document(
        engine,
        config,
        store,
        source_id="beta",
        dedupe_key="beta-release",
        collected_at=now - timedelta(minutes=5),
        published_at=now - timedelta(hours=5),
    )
    with Session(engine) as session:
        add_task_definition(session, "collection_task")
        add_task_definition(session, "research_task", task_type="research")
        collection_run = TaskRun(
            task_id="collection_task",
            status="succeeded",
            objective="Collect",
            strategy=None,
            scope={"source_ids": ["alpha", "beta"]},
            config_snapshot={
                "task": {
                    "api_key": "must-not-leak",
                    "endpoint": "https://private.invalid",
                }
            },
            model_snapshot={
                "agents": {
                    "writer": {
                        "endpoint_url": "https://private.invalid",
                        "targets": [
                            {
                                "base_url": "https://private.invalid",
                                "api_key": "must-not-leak",
                            }
                        ],
                    }
                }
            },
            prompt_snapshot={},
            config_hash="a" * 64,
            output={
                "sources": [
                    {
                        "source_id": "alpha",
                        "discovered": 1,
                        "ingested": 0,
                        "reused": 1,
                        "skipped": 0,
                        "failed": 0,
                    },
                    {
                        "source_id": "beta",
                        "discovered": 1,
                        "ingested": 0,
                        "reused": 0,
                        "skipped": 0,
                        "failed": 1,
                        "error": {
                            "code": "HTTP_STATUS",
                            "message": "Request failed",
                            "details": {"url": "https://secret.invalid"},
                            "endpoint": "https://private.invalid",
                        },
                    },
                ]
            },
            error={
                "code": "COLLECTION_SOURCE_FAILED",
                "response": "private provider body",
            },
            created_at=now,
            started_at=now,
            finished_at=now,
        )
        session.add(collection_run)
        session.flush()
        session.add(
            TaskStepResult(
                run_id=collection_run.id,
                step_id="collect",
                position=0,
                step_type="collect_sources",
                status="failed",
                output={
                    "api_key": "must-not-leak",
                    "sources": [
                        {
                            "source_id": "beta",
                            "failed": 1,
                            "error": {
                                "code": "HTTP_STATUS",
                                "message": "Request failed",
                                "details": {"password": "must-not-leak"},
                            },
                        }
                    ],
                },
                evidence_refs=[],
                usage={},
                error={
                    "code": "COLLECTION_SOURCE_FAILED",
                    "traceback": "private traceback",
                },
                created_at=now,
            )
        )
        state = session.get(
            DocumentProcessingState,
            (alpha_document, "index"),
        )
        assert state is not None
        state.status = "failed"
        state.error = {
            "code": "INDEX_FAILED",
            "message": "Index could not finish",
            "response": "private model body",
        }
        session.commit()

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["processing_state"]["failed"] >= 1
    assert body["collection"]["source_errors"] is None
    assert body["configuration_errors"] == {
        "sources": None,
        "tasks": None,
    }
    assert body["task_count"] == 2
    assert "must-not-leak" not in overview.text
    assert "private.invalid" not in overview.text

    sources = client.get("/api/sources")
    assert sources.status_code == 200
    items = {item["source_id"]: item for item in sources.json()["items"]}
    assert items["alpha"]["collection"]["status"] == "success"
    assert items["alpha"]["collection"]["no_update"] is False
    assert items["alpha"]["freshness"]["status"] == "fresh"
    assert items["beta"]["collection"]["status"] == "source_failure"
    assert items["beta"]["collection"]["failed"] == 1
    assert items["beta"]["freshness"]["status"] == "stale"
    assert items["beta"]["freshness"]["expected_freshness"] == "1h"
    assert items["beta"]["freshness"]["latest_collected_at"] is not None
    assert items["beta"]["freshness"]["latest_disclosure_at"] is not None
    assert items["alpha"]["collection"]["processing_backlog"]["failed"] >= 1
    assert "secret.invalid" not in sources.text

    tasks_page = client.get("/api/tasks", params={"limit": 1})
    assert tasks_page.status_code == 200
    assert tasks_page.json()["has_more"] is True
    tasks_page_two = client.get(
        "/api/tasks",
        params={
            "limit": 1,
            "cursor": tasks_page.json()["next_cursor"],
        },
    )
    assert tasks_page_two.status_code == 200
    assert tasks_page_two.json()["items"][0]["task_id"] == "research_task"


def test_duplicate_trigger_returns_active_run_even_with_broken_yaml(
    engine, config, client, tmp_path
):
    login(client)
    with Session(engine) as session:
        add_task_definition(session, "collection_task")
        active = TaskRun(
            task_id="collection_task",
            status="queued",
            objective="Collect",
            strategy=None,
            scope={"source_ids": ["alpha"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            created_at=NOW,
        )
        session.add(active)
        session.commit()
        active_id = active.id
    # A repeated trigger must return the occupied Run without parsing YAML.
    tmp_path.joinpath("tasks.yaml").write_text("not: [valid", encoding="utf-8")
    response = client.post(
        "/api/tasks/collection_task/runs",
        headers={"X-CSRF-Token": login(client)},
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == str(active_id)
    assert response.json()["created"] is False
    assert response.json()["status"] == "queued"


def test_trigger_returns_safe_error_when_model_configuration_is_unavailable(
    engine, config, client, tmp_path, monkeypatch
):
    tmp_path.joinpath("models.yaml").write_text(
        """
providers:
  default:
    protocol: openai_compatible
    base_url: https://private-model.invalid/v1
    api_key: ${MODEL_API_KEY}
    capabilities: [chat, text, structured_output]
    timeout_seconds: 10
models:
  research:
    provider: default
    name: research-model
    capabilities: [chat, text, structured_output]
agents:
  research:
    routing:
      primary: research
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 10
      retries: 0
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    csrf = login(client)

    response = client.post(
        "/api/tasks/research_task/runs",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "MODEL_CONFIGURATION_UNAVAILABLE",
            "message": (
                "Task could not be queued because model configuration "
                "is unavailable"
            ),
        }
    }
    assert "MODEL_API_KEY" not in response.text
    assert "private-model.invalid" not in response.text
    assert "Missing environment variable" not in response.text
    with Session(engine) as session:
        assert session.query(TaskRun).count() == 0


@pytest.mark.parametrize("failure", ["missing", "invalid_utf8"])
def test_trigger_returns_safe_error_when_model_configuration_cannot_be_read(
    engine, config, client, tmp_path, failure
):
    models_path = tmp_path / "models.yaml"
    if failure == "missing":
        models_path.unlink()
    else:
        models_path.write_bytes(b"\xff")
    csrf = login(client)

    response = client.post(
        "/api/tasks/research_task/runs",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "MODEL_CONFIGURATION_UNAVAILABLE",
            "message": (
                "Task could not be queued because model configuration "
                "is unavailable"
            ),
        }
    }
    assert str(models_path) not in response.text
    assert "codec" not in response.text
    with Session(engine) as session:
        assert session.query(TaskRun).count() == 0


def test_run_detail_redacts_model_errors_but_keeps_locators(
    engine, config, client
):
    login(client)
    with Session(engine) as session:
        add_task_definition(session, "research_task", task_type="research")
        run = TaskRun(
            task_id="research_task",
            status="failed",
            objective="Research",
            strategy="evidence_first",
            scope={"topics": ["macro"]},
            config_snapshot={
                "task": {
                    "id": "research_task",
                    "endpoint": "https://private.invalid",
                    "api_key": "must-not-leak",
                },
                "agents": [],
            },
            model_snapshot={
                "agents": {
                    "writer": {
                        "routing": {"primary": "research"},
                        "endpoint": "https://private.invalid",
                    }
                },
                "embedding": {
                    "model_name": "embedding-model",
                    "endpoint_url": "https://private.invalid",
                },
            },
            prompt_snapshot={},
            config_hash="a" * 64,
            output={
                "missing_document_id": str(uuid4()),
                "url": (
                    "https://example.com/article"
                    "?token=must-not-leak#private"
                ),
                "api_key": "must-not-leak",
            },
            error={
                "code": "MODEL_FAILED",
                "authorization": "Bearer secret",
                "details": {"response": "private provider body"},
            },
            created_at=NOW,
            started_at=NOW,
            finished_at=NOW,
        )
        session.add(run)
        session.flush()
        session.add(
            TaskStepResult(
                run_id=run.id,
                step_id="research",
                position=1,
                step_type="research_agent",
                status="failed",
                output={
                    "document_id": str(uuid4()),
                    "stage": "facts",
                    "url": (
                        "https://example.com/source"
                        "?secret=must-not-leak#location"
                    ),
                    "token": "must-not-leak",
                },
                evidence_refs=[],
                usage={},
                error={
                    "code": "MODEL_FAILED",
                    "message": "Model request failed",
                    "exception": "private stack",
                },
                created_at=NOW,
            )
        )
        session.add(
            ModelCall(
                run_id=run.id,
                step_id="research",
                attempt_number=1,
                requested_alias="research",
                attempt_alias="research",
                requested_model_name="research-model",
                attempt_model_name="research-model",
                reported_model_name=None,
                status="failed",
                duration_ms=100,
                fallback_reason="HTTP_STATUS",
                error={
                    "code": "MODEL_FAILED",
                    "body": "private provider body",
                    "api_key": "must-not-leak",
                },
                created_at=NOW,
            )
        )
        session.commit()
        run_id = run.id

    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == "MODEL_FAILED"
    assert body["failed_steps"][0]["error"]["code"] == "MODEL_FAILED"
    assert body["model_calls"][0]["error"]["code"] == "MODEL_FAILED"
    assert body["steps"][0]["output"]["stage"] == "facts"
    assert body["output"]["url"] == "https://example.com/article"
    assert body["steps"][0]["output"]["url"] == "https://example.com/source"
    assert "?secret=" not in response.text
    assert "#location" not in response.text
    assert "must-not-leak" not in response.text


def test_owner_api_does_not_expose_documentation_or_provider_urls(
    engine, config, client
):
    login(client)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    with Session(engine) as session:
        add_task_definition(session, "collection_task")
        session.add(
            TaskRun(
                task_id="collection_task",
                status="failed",
                objective="Collect",
                strategy=None,
                scope={"source_ids": ["alpha"]},
                config_snapshot={},
                model_snapshot={},
                prompt_snapshot={},
                config_hash="a" * 64,
                output={
                    "sources": [
                        {
                            "source_id": "alpha",
                            "error": {
                                "code": "HTTP_STATUS",
                                "details": {
                                    "url": (
                                        "https://example.com/article"
                                        "?token=must-not-leak#private"
                                    )
                                },
                            },
                        }
                    ]
                },
                created_at=NOW,
                finished_at=NOW,
            )
        )
        session.commit()

    response = client.get("/api/sources")
    assert response.status_code == 200
    assert "must-not-leak" not in response.text
    assert "#private" not in response.text
    assert '"url"' not in response.text
    assert "private.invalid" not in response.text
    assert "private provider body" not in response.text


def test_end_retry_and_processing_actions_enforce_owner_boundaries(
    engine, config, client, snapshot
):
    _objects, store = snapshot
    csrf = login(client)
    document_id = create_document(engine, config, store)
    with Session(engine) as session:
        add_task_definition(session, "research_task", task_type="research")
        run = TaskRun(
            task_id="research_task",
            status="running",
            process_token="process-that-must-stop",
            objective="Research",
            strategy=None,
            scope={"topics": ["macro"]},
            config_snapshot={
                "task": {
                    "id": "research_task",
                    "name": "Research",
                    "type": "research",
                    "trigger": {"kind": "manual"},
                    "enabled": True,
                    "objective": "Research",
                    "strategy": None,
                    "scope": {"topics": ["macro"]},
                    "steps": [
                        {
                            "id": "evidence",
                            "select_evidence": {
                                "kind": "select_evidence"
                            },
                        },
                        {
                            "id": "research",
                            "research_agent": {
                                "kind": "research_agent",
                                "agent": "writer",
                                "input": "evidence",
                            },
                        },
                    ],
                },
                "agents": [
                    {
                        "id": "writer",
                        "role": "Writer",
                        "prompt": "Analyze evidence.",
                        "model_ref": "research",
                        "tools": [],
                        "input_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["run_id"],
                            "properties": {"run_id": {"type": "string"}},
                        },
                        "output_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["summary"],
                            "properties": {"summary": {"type": "string"}},
                        },
                    }
                ],
            },
            model_snapshot={"agents": {}, "embedding": None},
            prompt_snapshot={"writer": "Analyze evidence."},
            config_hash="a" * 64,
            created_at=NOW,
            started_at=NOW,
        )
        session.add(run)
        stage = session.get(
            DocumentProcessingState,
            (document_id, "parse"),
        )
        assert stage is not None
        stage.status = "running"
        stage.owner_token = "stage-process-that-must-stop"
        session.commit()
        run_id = run.id

    no_confirm = client.post(
        f"/api/runs/{run_id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"confirm_stopped": False},
    )
    assert no_confirm.status_code == 409
    assert (
        no_confirm.json()["detail"]["code"]
        == "OWNER_CONFIRMATION_REQUIRED"
    )

    stage_no_confirm = client.post(
        "/api/processing/retry",
        headers={"X-CSRF-Token": csrf},
        json={
            "document_id": str(document_id),
            "stages": ["parse"],
        },
    )
    assert stage_no_confirm.status_code == 200
    assert stage_no_confirm.json()["accepted"] is False
    assert (
        stage_no_confirm.json()["errors"][0]["error"]["code"]
        == "OWNER_CONFIRMATION_REQUIRED"
    )

    ended = client.post(
        f"/api/runs/{run_id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"confirm_stopped": True},
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "cancelled"

    retry = client.post(
        f"/api/runs/{run_id}/retry",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert retry.status_code == 200
    retried = retry.json()
    assert retried["created"] is True
    assert retried["triggered_by"] == "retry"
    assert retried["retry_of_run_id"] == str(run_id)
    assert retried["retried_from"]["run_id"] == str(run_id)

    retry_again = client.post(
        f"/api/runs/{run_id}/retry",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert retry_again.status_code == 409
    assert (
        retry_again.json()["detail"]["code"]
        == "RUN_ACTIVE_EXISTS"
    )

    stage_retry = client.post(
        "/api/processing/retry",
        headers={"X-CSRF-Token": csrf},
        json={
            "document_id": str(document_id),
            "stages": ["parse"],
            "confirm_stopped": True,
        },
    )
    assert stage_retry.status_code == 200
    assert stage_retry.json()["accepted"] is True
    assert stage_retry.json()["executions"][0]["status"] == "pending"


def test_latest_collection_run_is_selected_per_source(engine, config, client):
    login(client)
    with Session(engine) as session:
        add_task_definition(session, "collection_task")
        older = TaskRun(
            task_id="collection_task",
            status="succeeded",
            objective="Collect",
            strategy=None,
            scope={"source_ids": ["alpha"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            output={
                "sources": [
                    {
                        "source_id": "alpha",
                        "discovered": 1,
                        "ingested": 1,
                        "reused": 0,
                        "skipped": 0,
                        "failed": 0,
                    }
                ]
            },
            created_at=NOW - timedelta(minutes=2),
            finished_at=NOW - timedelta(minutes=2),
        )
        newer_alpha = TaskRun(
            task_id="collection_task",
            status="succeeded",
            objective="Collect",
            strategy=None,
            scope={"source_ids": ["alpha"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            output={
                "sources": [
                    {
                        "source_id": "alpha",
                        "discovered": 0,
                        "ingested": 0,
                        "reused": 0,
                        "skipped": 0,
                        "failed": 0,
                    }
                ]
            },
            created_at=NOW - timedelta(minutes=1),
            finished_at=NOW - timedelta(minutes=1),
        )
        beta = TaskRun(
            task_id="collection_task",
            status="succeeded",
            objective="Collect",
            strategy=None,
            scope={"source_ids": ["beta"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            output={
                "sources": [
                    {
                        "source_id": "beta",
                        "discovered": 1,
                        "ingested": 1,
                        "reused": 0,
                        "skipped": 0,
                        "failed": 0,
                    }
                ]
            },
            created_at=NOW,
            finished_at=NOW,
        )
        session.add_all((older, newer_alpha, beta))
        session.commit()
        older_id = older.id
        newer_alpha_id = newer_alpha.id
        beta_id = beta.id

    response = client.get("/api/sources")
    assert response.status_code == 200
    items = {item["source_id"]: item for item in response.json()["items"]}
    assert (
        items["alpha"]["collection"]["last_collection_run"]["run_id"]
        == str(newer_alpha_id)
    )
    assert (
        items["alpha"]["collection"]["last_collection_run"]["run_id"]
        != str(older_id)
    )
    assert items["alpha"]["collection"]["status"] == "no_update"
    assert (
        items["beta"]["collection"]["last_collection_run"]["run_id"]
        == str(beta_id)
    )
    assert items["beta"]["collection"]["status"] == "success"


def test_failed_collection_run_without_source_output_is_not_reported_as_no_attempt(
    engine, config, client
):
    login(client)
    with Session(engine) as session:
        add_task_definition(session, "collection_task")
        session.add(
            TaskRun(
                task_id="collection_task",
                status="failed",
                objective="Collect",
                strategy=None,
                scope={"source_ids": ["alpha"]},
                config_snapshot={},
                model_snapshot={},
                prompt_snapshot={},
                config_hash="a" * 64,
                error={
                    "code": "COLLECTION_SOURCE_FAILED",
                    "message": "A configured Source could not be collected",
                    "response": "must-not-leak",
                },
                created_at=NOW,
                finished_at=NOW,
            )
        )
        session.commit()

    response = client.get("/api/sources")
    assert response.status_code == 200
    alpha = next(
        item for item in response.json()["items"] if item["source_id"] == "alpha"
    )
    assert alpha["collection"]["status"] == "source_failure"
    assert alpha["collection"]["error"]["code"] == "COLLECTION_SOURCE_FAILED"
    assert "must-not-leak" not in response.text


def test_continue_uses_source_run_and_newer_scan(
    engine, config, client
):
    csrf = login(client)
    with Session(engine) as session:
        add_task_definition(session, "research_task", task_type="research")
        source = TaskRun(
            task_id="research_task",
            status="succeeded",
            objective="Research",
            strategy=None,
            scope={"topics": ["macro"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            change_watermark_start=1,
            change_watermark_end=2,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            change_scan_completed=False,
            change_scan_progress={"status": "page_complete"},
            created_at=NOW,
            finished_at=NOW,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    response = client.post(
        f"/api/runs/{source_id}/continue",
        headers={"X-CSRF-Token": csrf},
        json={"resumed_from_run_id": str(source_id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["continued_from"] == str(source_id)
    assert body["task_id"] == "research_task"
    assert body["status"] == "queued"

    with Session(engine) as session:
        source = session.get(TaskRun, source_id)
        source.change_scan_completed = True
        source.change_scan_progress = {"status": "scanned"}
        session.commit()
    complete = client.post(
        f"/api/runs/{source_id}/continue",
        headers={"X-CSRF-Token": csrf},
        json={"resumed_from_run_id": str(source_id)},
    )
    assert complete.status_code == 409
    assert complete.json()["detail"]["code"] == "RUN_SCAN_COMPLETE"


def test_continue_returns_safe_error_when_model_configuration_is_unavailable(
    engine, config, client, tmp_path, monkeypatch
):
    tmp_path.joinpath("models.yaml").write_text(
        """
providers:
  default:
    protocol: openai_compatible
    base_url: https://private-model.invalid/v1
    api_key: ${MODEL_API_KEY}
    capabilities: [chat, text, structured_output]
    timeout_seconds: 10
models:
  research:
    provider: default
    name: research-model
    capabilities: [chat, text, structured_output]
agents:
  research:
    routing:
      primary: research
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 10
      retries: 0
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    csrf = login(client)
    with Session(engine) as session:
        add_task_definition(session, "research_task", task_type="research")
        source = TaskRun(
            task_id="research_task",
            status="succeeded",
            objective="Research",
            strategy=None,
            scope={"topics": ["macro"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            change_watermark_start=1,
            change_watermark_end=2,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            change_scan_completed=False,
            change_scan_progress={"status": "page_complete"},
            created_at=NOW,
            finished_at=NOW,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    response = client.post(
        f"/api/runs/{source_id}/continue",
        headers={"X-CSRF-Token": csrf},
        json={"resumed_from_run_id": str(source_id)},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "MODEL_CONFIGURATION_UNAVAILABLE",
            "message": (
                "Task could not be queued because model configuration "
                "is unavailable"
            ),
        }
    }
    assert "MODEL_API_KEY" not in response.text
    assert "private-model.invalid" not in response.text
    assert "Missing environment variable" not in response.text
    with Session(engine) as session:
        assert session.query(TaskRun).count() == 1


def test_original_is_authenticated_safe_and_does_not_expose_query_secrets(
    engine, config, client, snapshot
):
    _objects, store = snapshot
    payload = b"<html><script>alert(1)</script><p>Evidence</p></html>"
    document_id = create_document(
        engine,
        config,
        store,
        title="filing.html",
        payload=payload,
    )
    assert client.get(f"/api/documents/{document_id}/original").status_code == 401
    login(client)
    response = client.get(
        f"/api/documents/{document_id}/original",
        params={"download": True},
    )
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["x-content-sha256"] == sha256(payload).hexdigest()
    assert response.headers["x-content-hash-match"] == "true"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["x-source-url"] == "https://example.com/path"
    assert "token=secret" not in response.headers["x-source-url"]
    detail = client.get(f"/api/documents/{document_id}").json()
    assert detail["document"]["original_url"] == "https://example.com/path"
    assert "token=secret" not in str(detail)


def test_processing_and_detail_pages_are_bounded(
    engine, config, client, snapshot
):
    _objects, store = snapshot
    login(client)
    document_id = create_document(engine, config, store)
    first = client.get(
        "/api/processing",
        params={"document_id": str(document_id), "limit": 2},
    )
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    assert first.json()["has_more"] is True
    second = client.get(
        "/api/processing",
        params={
            "document_id": str(document_id),
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 2
    assert second.json()["has_more"] is False

    detail = client.get(
        f"/api/documents/{document_id}",
        params={"chunk_limit": 1, "version_limit": 1},
    )
    assert detail.status_code == 200
    assert detail.json()["versions"]["has_more"] is False
    assert detail.json()["current_chunks"]["count"] == 0

    invalid = client.get(
        "/api/processing",
        params={"cursor": "not-a-processing-cursor"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "CURSOR_INVALID"


def test_report_evidence_versions_and_fact_pages_are_bounded(
    engine, config, client, snapshot
):
    _objects, store = snapshot
    login(client)
    first_id = create_document(
        engine,
        config,
        store,
        dedupe_key="versioned",
        payload=b"first",
        collected_at=NOW,
    )
    second_id = create_document(
        engine,
        config,
        store,
        dedupe_key="versioned",
        payload=b"second",
        collected_at=NOW + timedelta(minutes=1),
    )
    with Session(engine) as session:
        for index in range(201):
            session.add(
                Fact(
                    document_id=first_id,
                    fact_key=f"{index:064x}",
                    fact_type="note",
                    origin_method="source_mapping",
                    evidence_ref={"pointer": f"/value/{index}"},
                    statement=f"Saved fact {index}",
                    attributes={},
                    created_at=NOW,
                )
            )
        session.commit()
        fact_id = session.query(Fact).filter_by(
            document_id=first_id
        ).order_by(Fact.id).first().id

    facts = client.get(
        "/api/facts",
        params={"document_id": str(first_id), "limit": 1},
    )
    assert facts.status_code == 200
    assert facts.json()["has_more"] is True
    first_document_fact = client.get(
        f"/api/documents/{first_id}/facts",
        params={"limit": 1},
    )
    assert first_document_fact.status_code == 200
    second_document_fact = client.get(
        f"/api/documents/{first_id}/facts",
        params={
            "limit": 1,
            "cursor": first_document_fact.json()["next_cursor"],
        },
    )
    assert second_document_fact.status_code == 200
    assert second_document_fact.json()["count"] == 1
    assert (
        second_document_fact.json()["items"][0]["id"]
        != first_document_fact.json()["items"][0]["id"]
    )

    second_fact_page = client.get(
        "/api/facts",
        params={"limit": 1, "cursor": facts.json()["next_cursor"]},
    )
    assert second_fact_page.status_code == 200
    assert second_fact_page.json()["count"] == 1
    assert second_fact_page.json()["items"][0]["id"] != facts.json()["items"][0]["id"]

    at_limit = client.get(
        f"/api/documents/{first_id}/facts",
        params={"limit": 200},
    )
    assert at_limit.status_code == 200
    assert at_limit.json()["count"] == 200
    assert at_limit.json()["has_more"] is True
    final_fact_page = client.get(
        f"/api/documents/{first_id}/facts",
        params={"limit": 200, "cursor": at_limit.json()["next_cursor"]},
    )
    assert final_fact_page.status_code == 200
    assert final_fact_page.json()["count"] == 1
    assert final_fact_page.json()["has_more"] is False
    fact_detail = client.get(f"/api/facts/{fact_id}")
    assert fact_detail.status_code == 200
    assert fact_detail.json()["document"]["id"] == str(first_id)
    assert [
        item["document_id"]
        for item in fact_detail.json()["document_versions"]["items"]
    ] == [str(first_id), str(second_id)]

    with Session(engine) as session:
        add_task_definition(session, "research_task", task_type="research")
        run = TaskRun(
            task_id="research_task",
            status="succeeded",
            objective="Research",
            strategy=None,
            scope={"topics": ["macro"]},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="a" * 64,
            created_at=NOW,
        )
        session.add(run)
        session.flush()
        package = EvidencePackage(
            run_id=run.id,
            objective="Research",
            scope={"topics": ["macro"]},
            as_of=NOW,
            selection_policy_version="test",
            selection_budget={"max_tokens": 10},
            queries=[],
            selected_chunks=[],
            evidence_groups=[],
            coverage={"gaps": []},
            candidates_discovered=0,
            candidates_screened=0,
            selected_count=0,
            unexamined_count=0,
            retrieval_truncated=False,
            created_at=NOW,
        )
        session.add(package)
        session.flush()
        analysis = Analysis(
            run_id=run.id,
            evidence_package_id=package.id,
            summary="No selected evidence.",
            findings=[],
            open_questions=[],
            evidence_refs=[],
            coverage={"gaps": []},
            created_at=NOW,
        )
        session.add(analysis)
        session.flush()
        report = Report(
            run_id=run.id,
            analysis_id=analysis.id,
            task_id="research_task",
            title="Report",
            content="Report body",
            created_at=NOW,
        )
        session.add(report)
        session.commit()
        report_id = report.id

    detail = client.get(f"/api/reports/{report_id}")
    assert detail.status_code == 200
    assert detail.json()["report"]["content"] == "Report body"
    assert detail.json()["evidence"] == []
    reports = client.get("/api/reports", params={"limit": 1})
    assert reports.status_code == 200
    assert reports.json()["items"][0]["report_id"] == str(report_id)
