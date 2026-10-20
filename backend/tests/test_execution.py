import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import stocks.execution.__main__ as execution_cli
import stocks.execution.service as execution_service
from stocks.execution.config import (
    ExecutionConfigError,
    StepCondition,
    load_task_configuration,
)
from stocks.execution.service import (
    ExecutionContext,
    ExecutionError,
    active_run_count,
    claim_run,
    create_or_get_run,
    execute_claimed_run,
)
from stocks.providers import (
    ChatRouting,
    ModelAttempt,
    ModelClient,
    ModelError,
    ModelTarget,
)
from stocks.models import (
    Base,
    ModelCall,
    TaskDefinition,
    TaskRun,
    TaskStepResult,
)
from stocks.settings import Settings

PROCESS_A = "process-a"
PROCESS_B = "process-b"


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
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL execution tests")
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
def cli_session_factory(engine, monkeypatch):
    monkeypatch.setattr(
        execution_cli,
        "session_factory",
        lambda: lambda: Session(engine),
    )
    monkeypatch.setattr(execution_cli, "settings", lambda: make_config())


def write_config(tmp_path, *, condition: dict | None = None) -> str:
    condition_yaml = ""
    if condition is not None:
        condition_lines = [
            "when:",
            f"  step: {condition['step']}",
            f"  op: {condition['op']}",
        ]
        if condition.get("field"):
            condition_lines.append(f"  field: {condition['field']}")
        if "value" in condition:
            value = condition["value"]
            if isinstance(value, bool):
                value = str(value).lower()
            elif isinstance(value, str):
                value = json.dumps(value)
            condition_lines.append(f"  value: {value}")
        condition_yaml = "\n".join(
            ["        " + line for line in condition_lines]
        )
    path = tmp_path / "tasks.yaml"
    path.write_text(
        f"""
tasks:
  - id: brief
    name: Brief
    type: research
    trigger: manual
    objective: Summarize current evidence.
    scope: {{topics: [macro]}}
    steps:
      - id: evidence
        repository_search:
          kind: repository_search
          query: inflation
          limit: 2
      - id: research
{condition_yaml}
        research_agent:
          kind: research_agent
          agent: researcher
          input: evidence
agents:
  - id: researcher
    role: Researcher
    prompt: Use only supplied evidence.
    model_ref: research
    input_schema:
      type: object
      additionalProperties: false
      required: [run_id, objective, strategy, scope, evidence, coverage, truncated]
      properties:
        run_id: {{type: string}}
        objective: {{type: string}}
        strategy:
          anyOf:
            - {{type: string}}
            - {{type: "null"}}
        scope: {{type: object, additionalProperties: true}}
        evidence:
          type: array
          items: {{type: object, additionalProperties: true}}
        coverage: {{type: object, additionalProperties: true}}
        truncated: {{type: boolean}}
    output_schema:
      type: object
      additionalProperties: false
      required: [summary, findings, open_questions, evidence_refs]
      properties:
        summary: {{type: string}}
        findings: {{type: array, items: {{type: string}}}}
        open_questions: {{type: array, items: {{type: string}}}}
        evidence_refs:
          type: array
          items:
            type: object
            additionalProperties: false
            required: [chunk_id, document_id]
            properties:
              chunk_id: {{type: string}}
              document_id: {{type: string}}
""",
        encoding="utf-8",
    )
    return str(path)


def test_config_accepts_bounded_source_filters(tmp_path):
    path = Path(write_config(tmp_path))
    with open(path, encoding="utf-8") as stream:
        valid = stream.read()
    path.write_text(
        valid.replace(
            "query: inflation\n          limit: 2",
            "query: inflation\n          limit: 2\n          source_ids: [sec, media]",
        ),
        encoding="utf-8",
    )
    search = load_task_configuration(path).tasks[0].steps[0].repository_search
    assert search is not None
    assert search.source_ids == ["sec", "media"]


def test_config_rejects_duplicate_source_filters(tmp_path):
    path = Path(write_config(tmp_path))
    with open(path, encoding="utf-8") as stream:
        valid = stream.read()
    path.write_text(
        valid.replace(
            "query: inflation\n          limit: 2",
            (
                "query: inflation\n"
                "          limit: 2\n"
                "          source_ids: [sec, sec]"
            ),
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="unique"):
        load_task_configuration(path)


def write_models_config(tmp_path, *, retryable_statuses=None) -> str:
    path = tmp_path / "models.yaml"
    statuses = retryable_statuses or [429, 500]
    path.write_text(
        f"""
providers:
  default:
    protocol: openai_compatible
    base_url: https://models.example/v1
    api_key: ${{TEST_MODEL_KEY}}
    capabilities: [chat, text, structured_output]
    retryable_statuses: {statuses}
models:
  primary:
    provider: default
    name: primary-model
    capabilities: [chat, text, structured_output]
  fallback:
    provider: default
    name: fallback-model
    capabilities: [chat, text, structured_output]
  research:
    provider: default
    name: fallback-model
    capabilities: [chat, text, structured_output]
  embedding:
    provider: default
    name: embedding
    dimensions: 1536
embedding:
  models: [embedding]
agents:
  research:
    routing:
      primary: research
      fallbacks: [fallback]
      required_capabilities: [chat, text, structured_output]
      timeout_seconds: 5
      retries: 1
      retry_backoff_seconds: 0
""",
        encoding="utf-8",
    )
    return str(path)


def test_config_accepts_whitelisted_condition(tmp_path):
    path = write_config(
        tmp_path,
        condition={
            "step": "evidence",
            "op": "nonempty",
            "field": "output.items",
        },
    )
    config = load_task_configuration(path)
    assert config.tasks[0].steps[1].condition is not None


@pytest.mark.parametrize(
    "field, op, value",
    [
        ("output.items", "equals", "[]"),
        ("output", "equals", "{}"),
        ("output.truncated", "nonempty", None),
    ],
)
def test_config_rejects_condition_operator_mismatch(
    tmp_path, field, op, value
):
    condition = {"step": "evidence", "op": op, "field": field}
    if value is not None:
        condition["value"] = value
    path = write_config(tmp_path, condition=condition)
    with pytest.raises(ExecutionConfigError, match="does not support"):
        load_task_configuration(path)


@pytest.mark.parametrize(
    "condition, message",
    [
        (
            {"step": "evidence", "op": "eval", "field": "output"},
            "Input should be",
        ),
        (
            {
                "step": "research",
                "op": "equals",
                "field": "output.truncated",
                "value": True,
            },
            "earlier step",
        ),
        (
            {
                "step": "evidence",
                "op": "equals",
                "field": "python.__class__",
                "value": "x",
            },
            "not supported",
        ),
        (
            {
                "step": "evidence",
                "op": "equals",
                "field": "output.truncated",
                "value": "false",
            },
            "boolean value",
        ),
    ],
)
def test_config_rejects_unsafe_or_forward_conditions(
    tmp_path, condition, message
):
    path = write_config(tmp_path, condition=condition)
    with pytest.raises(ExecutionConfigError, match=message):
        load_task_configuration(path)


@pytest.mark.parametrize(
    "field, value, matches",
    [
        ("output.truncated", False, True),
        ("output.truncated", True, False),
        ("evidence_refs", None, False),
    ],
)
def test_condition_evaluation_uses_only_whitelisted_values(field, value, matches):
    condition = StepCondition(
        step="evidence",
        op="equals" if field == "output.truncated" else "nonempty",
        field=field,
        value=value if field == "output.truncated" else None,
    )
    result = TaskStepResult(
        run_id=uuid4(),
        step_id="evidence",
        position=0,
        step_type="repository_search",
        status="succeeded",
        output={"items": [], "truncated": False},
        evidence_refs=[],
        usage={},
        created_at=datetime.now(UTC),
    )
    assert (
        execution_service._evaluate_condition(
            condition, {result.step_id: result}
        )
        is matches
    )


def test_config_rejects_duplicate_step_and_unknown_field(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(
        """
tasks:
  - id: x
    name: X
    type: research
    trigger: manual
    objective: Test
    scope: {topics: [x]}
    steps:
      - id: same
        repository_search: {kind: repository_search, query: one}
      - id: same
        repository_search: {kind: repository_search, query: two}
    unexpected: true
agents: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="invalid"):
        load_task_configuration(path)


def test_config_rejects_duplicate_yaml_key(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        """
tasks:
  - id: x
    name: X
    type: research
    trigger: manual
    objective: first
    objective: second
    scope: {topics: [x]}
    steps:
      - id: evidence
        repository_search: {kind: repository_search, query: one}
agents: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="valid YAML"):
        load_task_configuration(path)


def test_config_rejects_duplicate_step_ids_after_valid_references(tmp_path):
    path = Path(write_config(tmp_path))
    with open(path, encoding="utf-8") as stream:
        content = stream.read()
    path.write_text(
        content.replace(
            "      - id: research\n",
            "      - id: evidence\n",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="duplicate step id"):
        load_task_configuration(path)


def test_config_rejects_unsafe_yaml_tag(tmp_path):
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        """
tasks:
  - id: x
    name: X
    type: research
    trigger: manual
    objective: !!python/object/apply:os.system ["true"]
    scope: {topics: [x]}
    steps:
      - id: evidence
        repository_search: {kind: repository_search, query: one}
agents: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="valid YAML"):
        load_task_configuration(path)


def test_config_rejects_unknown_step_and_unsupported_schema_keyword(tmp_path):
    path = tmp_path / "unsupported.yaml"
    path.write_text(
        """
tasks:
  - id: x
    name: X
    type: research
    trigger: manual
    objective: Test
    scope: {topics: [x]}
    steps:
      - id: evidence
        python_eval: {expression: "1 + 1"}
agents: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="invalid"):
        load_task_configuration(path)

    config = write_config(tmp_path)
    with open(config, encoding="utf-8") as stream:
        valid = stream.read()
    path.write_text(
        valid.replace(
            "summary: {type: string}",
            "summary: {type: string, pattern: '.*'}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionConfigError, match="schema keyword"):
        load_task_configuration(path)


def test_condition_whitelist_skips_later_step(tmp_path):
    condition = load_task_configuration(
        write_config(
            tmp_path,
            condition={
                "step": "evidence",
                "op": "nonempty",
                "field": "output.items",
            },
        )
    ).tasks[0].steps[1].condition
    assert condition is not None
    result = TaskStepResult(
        run_id=uuid4(),
        step_id="evidence",
        position=0,
        step_type="repository_search",
        status="succeeded",
        output={"items": []},
        evidence_refs=[],
        usage={},
        created_at=datetime.now(UTC),
    )
    assert (
        execution_service._evaluate_condition(
            condition, {result.step_id: result}
        )
        is False
    )


def _make_task_config(tmp_path, condition=None):
    return load_task_configuration(write_config(tmp_path, condition=condition))


def _fake_embed(client, targets, texts):
    class Response:
        model_alias = targets.primary
        embeddings = ((1.0,) * 1536,)

    return Response()


def _mock_transport(output_content: str | None = None, statuses=None):
    statuses = list(statuses or [200])

    def handler(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "model": "embedding",
                    "data": [{"index": 0, "embedding": [0.0] * 1536}],
                },
            )
        status = statuses.pop(0)
        if status != 200:
            return httpx.Response(status)
        chunk_id = "00000000-0000-0000-0000-000000000001"
        document_id = "00000000-0000-0000-0000-000000000002"
        content = output_content or json.dumps(
            {
                "summary": "summary",
                "findings": ["finding"],
                "open_questions": [],
                "evidence_refs": [
                    {"chunk_id": chunk_id, "document_id": document_id}
                ],
            }
        )
        return httpx.Response(
            200,
            json={
                "model": "research-model",
                "private_response_marker": "raw-response-body-never-persist",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    return httpx.MockTransport(handler)


def test_active_run_is_atomic_and_new_run_after_completion(engine, tmp_path):
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    agents = list(config.agents)
    models_path = write_models_config(tmp_path)

    with Session(engine) as session:
        first = create_or_get_run(
            session, task, agents, config_path=models_path
        )
        duplicate = create_or_get_run(
            session, task, agents, config_path=models_path
        )
        assert first.created is True
        assert duplicate.created is False
        assert duplicate.run.id == first.run.id
        assert active_run_count(session, task.id) == 1
        assert (
            claim_run(
                session,
                first.run.id,
                process_token=PROCESS_A,
            )
            is True
        )
        assert (
            claim_run(
                session,
                first.run.id,
                process_token=PROCESS_B,
            )
            is False
        )
        first.run.status = "succeeded"
        first.run.process_token = None
        first.run.finished_at = datetime.now(UTC)
        session.commit()

    concurrent_start = Barrier(2)

    def trigger(_):
        with Session(engine) as session:
            concurrent_start.wait(timeout=10)
            claimed = create_or_get_run(
                session, task, agents, config_path=models_path
            )
            return claimed.run.id, claimed.created

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(trigger, range(2)))
    assert ids[0][0] == ids[1][0]
    assert sorted(created for _, created in ids) == [False, True]

    completed_id = ids[0][0]
    with Session(engine) as session:
        run = session.get(TaskRun, completed_id)
        run.status = "succeeded"
        run.process_token = None
        run.finished_at = datetime.now(UTC)
        session.commit()
        new_run = create_or_get_run(
            session, task, agents, config_path=models_path
        )
        assert new_run.created is True
        assert new_run.run.id != completed_id


def test_run_creation_can_claim_atomically(engine, tmp_path):
    config = _make_task_config(tmp_path)
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=write_models_config(tmp_path),
            process_token=PROCESS_A,
        )

        assert claimed.created is True
        assert claimed.run.status == "running"
        assert claimed.run.process_token == PROCESS_A
        assert claimed.run.started_at is not None


def test_concurrent_cli_claims_return_same_active_run(engine):
    barrier = Barrier(2)

    def claim(_):
        with Session(engine) as session:
            barrier.wait(timeout=10)
            values = {
                "id": uuid4(),
                "task_id": "race-task",
                "status": "queued",
                "objective": "race",
                "scope": {},
                "config_snapshot": {},
                "model_snapshot": {},
                "prompt_snapshot": {},
                "config_hash": "hash",
                "created_at": datetime.now(UTC),
            }
            inserted = session.execute(
                insert(TaskRun)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["task_id"],
                    index_where=text("status IN ('queued', 'running')"),
                )
                .returning(TaskRun.id)
            ).scalar_one_or_none()
            session.commit()
            active = session.scalar(
                select(TaskRun).where(
                    TaskRun.task_id == "race-task",
                    TaskRun.status.in_(("queued", "running")),
                )
            )
            return inserted, active.id

    with engine.begin() as connection:
        connection.execute(
            TaskDefinition.__table__.insert().values(
                id="race-task",
                name="Race",
                type="research",
                trigger="manual",
                enabled=True,
                scope={},
                strategy=None,
                config_snapshot={},
                config_hash="hash",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))

    assert results[0][1] == results[1][1]
    assert sum(value is not None for value, _ in results) == 1


def test_concurrent_trigger_resolves_model_config_once(engine, tmp_path, monkeypatch):
    config = _make_task_config(tmp_path)
    models_path = write_models_config(tmp_path)
    original = execution_service.load_chat_routing
    calls = 0
    counter_lock = Lock()

    def load_routing(*args, **kwargs):
        nonlocal calls
        with counter_lock:
            calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        execution_service,
        "load_chat_routing",
        load_routing,
    )
    barrier = Barrier(2)

    def trigger(_):
        with Session(engine) as session:
            barrier.wait(timeout=10)
            claimed = create_or_get_run(
                session,
                config.tasks[0],
                list(config.agents),
                config_path=models_path,
            )
            return claimed.run.id, claimed.created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(trigger, range(2)))

    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    assert calls == 1


def test_claim_and_execution_reject_wrong_process_owner(engine, tmp_path):
    config = _make_task_config(tmp_path)
    with Session(engine) as session:
        created = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=write_models_config(tmp_path),
        )
        assert claim_run(
            session,
            created.run.id,
            process_token=PROCESS_A,
        )
        with pytest.raises(ExecutionError, match="not owned") as exc_info:
            execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path="unused",
                ),
                created.run.id,
                process_token=PROCESS_B,
            )
        assert exc_info.value.code == "RUN_NOT_OWNED"


def test_successful_run_persists_snapshot_steps_and_never_overwrites_success(
    engine, tmp_path
):
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    agents = list(config.agents)
    models_path = write_models_config(tmp_path)
    os.environ["TEST_MODEL_KEY"] = "secret-key-never-persist"

    with Session(engine) as session:
        claimed = create_or_get_run(
            session, task, agents, config_path=models_path
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        run = session.get(TaskRun, run_id)
        before = json.loads(
            json.dumps(
                {
                    "config": run.config_snapshot,
                    "model": run.model_snapshot,
                    "prompt": run.prompt_snapshot,
                }
            )
        )

        def fake_search(
            session,
            settings,
            config_path,
            step,
            *,
            model_snapshot=None,
            model_transport=None,
            embed=None,
        ):
            return (
                {
                    "items": [
                        {
                            "chunk_id": "00000000-0000-0000-0000-000000000001",
                            "document_id": "00000000-0000-0000-0000-000000000002",
                            "parse_batch_id": "00000000-0000-0000-0000-000000000003",
                            "version": 1,
                            "source_id": "sec",
                        }
                    ],
                    "coverage": {},
                    "truncated": False,
                },
                [
                    {
                        "chunk_id": "00000000-0000-0000-0000-000000000001",
                        "document_id": "00000000-0000-0000-0000-000000000002",
                        "parse_batch_id": "00000000-0000-0000-0000-000000000003",
                        "version": 1,
                        "source_id": "sec",
                    }
                ],
            )

        original_search = execution_service._run_search
        execution_service._run_search = fake_search
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_mock_transport(),
                ),
                run_id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_search = original_search
        assert completed.status == "succeeded"
        assert completed.model_snapshot
        assert {
            "config": completed.config_snapshot,
            "model": completed.model_snapshot,
            "prompt": completed.prompt_snapshot,
        } == before
        assert completed.output is not None
        results = session.scalars(
            select(TaskStepResult)
            .where(TaskStepResult.run_id == run_id)
            .order_by(TaskStepResult.position)
        ).all()
        assert [result.status for result in results] == ["succeeded", "succeeded"]
        assert results[1].evidence_refs == [
            {
                "chunk_id": "00000000-0000-0000-0000-000000000001",
                "document_id": "00000000-0000-0000-0000-000000000002",
                "parse_batch_id": "00000000-0000-0000-0000-000000000003",
                "version": 1,
                "source_id": "sec",
            }
        ]
        calls = session.scalars(
            select(ModelCall).where(ModelCall.run_id == run_id)
        ).all()
        assert calls[0].status == "succeeded"
        serialized = json.dumps(
            {
                "run": {
                    "config": completed.config_snapshot,
                    "model": completed.model_snapshot,
                    "prompt": completed.prompt_snapshot,
                    "objective": completed.objective,
                    "strategy": completed.strategy,
                    "scope": completed.scope,
                    "output": completed.output,
                    "error": completed.error,
                },
                "steps": [
                    {
                        "output": result.output,
                        "evidence_refs": result.evidence_refs,
                        "usage": result.usage,
                        "error": result.error,
                    }
                    for result in results
                ],
                "calls": [
                    {
                        "requested_alias": call.requested_alias,
                        "attempt_alias": call.attempt_alias,
                        "requested_model_name": call.requested_model_name,
                        "attempt_model_name": call.attempt_model_name,
                        "reported_model_name": call.reported_model_name,
                        "fallback_reason": call.fallback_reason,
                        "error": call.error,
                    }
                    for call in calls
                ],
            }
        )
        assert "secret-key-never-persist" not in serialized
        assert "raw-response-body-never-persist" not in serialized
        assert "https://models.example/v1" not in serialized
        assert "Authorization" not in serialized


def test_empty_evidence_fails_without_overwriting_success(engine, tmp_path):
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    with Session(engine) as session:
        first = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=write_models_config(tmp_path),
        )
        first.run.status = "succeeded"
        first.run.output = {"summary": "last success"}
        first.run.finished_at = datetime.now(UTC)
        session.commit()
        first_id = first.run.id

        second = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=write_models_config(tmp_path),
        )
        assert claim_run(
            session,
            second.run.id,
            process_token=PROCESS_A,
        )

        original_search = execution_service._run_search
        execution_service._run_search = lambda *args, **kwargs: (
            {"items": [], "coverage": {}, "truncated": False},
            [],
        )
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path="unused",
                ),
                second.run.id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_search = original_search

        assert completed.status == "failed"
        assert completed.output is None
        assert session.get(TaskRun, first_id).output == {"summary": "last success"}
        step = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == completed.id,
                TaskStepResult.step_id == "research",
            )
        )
        assert step is not None
        assert step.status == "failed"
        assert step.error["code"] == "EVIDENCE_UNAVAILABLE"


def test_skipped_step_is_persisted(engine, tmp_path):
    condition = {
        "step": "evidence",
        "op": "nonempty",
        "field": "output.items",
    }
    config = _make_task_config(tmp_path, condition=condition)
    task = config.tasks[0]
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=write_models_config(tmp_path),
        )
        assert claim_run(
            session,
            claimed.run.id,
            process_token=PROCESS_A,
        )
        original_search = execution_service._run_search
        execution_service._run_search = lambda *args, **kwargs: (
            {"items": [], "coverage": {}, "truncated": False},
            [],
        )
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path="unused",
                ),
                claimed.run.id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_search = original_search
        results = session.scalars(
            select(TaskStepResult).where(TaskStepResult.run_id == completed.id)
        ).all()
        assert completed.status == "succeeded"
        assert {result.step_id: result.status for result in results} == {
            "evidence": "succeeded",
            "research": "skipped",
        }


def test_model_calls_never_persist_credentials(engine):
    with Session(engine) as session:
        definition = TaskDefinition(
            id="x",
            name="x",
            type="research",
            trigger="manual",
            enabled=True,
            scope={},
            config_snapshot={},
            config_hash="x",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(definition)
        session.commit()
        run = TaskRun(
            task_id="x",
            status="running",
            process_token=PROCESS_A,
            objective="x",
            scope={},
            config_snapshot={},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="x",
            created_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        session.add(
            ModelCall(
                run_id=run.id,
                step_id="research",
                attempt_number=1,
                requested_alias="research",
                attempt_alias="research",
                requested_model_name="model",
                attempt_model_name="model",
                reported_model_name="model",
                status="succeeded",
                duration_ms=1,
                created_at=datetime.now(UTC),
            )
        )
        result = TaskStepResult(
            run_id=run.id,
            step_id="research",
            position=0,
            step_type="research_agent",
            status="succeeded",
            output={"summary": "safe"},
            evidence_refs=[],
            usage={},
            error=None,
            created_at=datetime.now(UTC),
        )
        session.add(result)
        session.commit()
        call = session.scalar(select(ModelCall))
        step = session.scalar(select(TaskStepResult))
        assert not hasattr(call, "api_key")
        assert "Authorization" not in ModelCall.__table__.columns
        serialized = json.dumps(
            {
                "run": {
                    "config": run.config_snapshot,
                    "model": run.model_snapshot,
                    "prompt": run.prompt_snapshot,
                    "objective": run.objective,
                    "strategy": run.strategy,
                    "scope": run.scope,
                    "output": run.output,
                    "error": run.error,
                },
                "step": {
                    "output": step.output,
                    "evidence_refs": step.evidence_refs,
                    "usage": step.usage,
                    "error": step.error,
                },
                "call": {
                    "requested_alias": call.requested_alias,
                    "attempt_alias": call.attempt_alias,
                    "requested_model_name": call.requested_model_name,
                    "attempt_model_name": call.attempt_model_name,
                    "reported_model_name": call.reported_model_name,
                    "error": call.error,
                },
            }
        )
        assert "secret-key-never-persist" not in serialized
        assert "Authorization" not in serialized


def test_cli_returns_process_owned_active_run_without_execution(
    engine, tmp_path, cli_session_factory, capsys
):
    config = _make_task_config(tmp_path)
    models_path = write_models_config(tmp_path)
    with Session(engine) as session:
        active = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=models_path,
        )
        assert claim_run(
            session,
            active.run.id,
            process_token=PROCESS_A,
        )
        active_id = active.run.id

    code = execution_cli.main(
        [
            "--config",
            write_config(tmp_path),
            "--models-config",
            models_path,
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["run_id"] == str(active_id)
    assert output["status"] == "running"
    assert output["created"] is False
    assert output["executed"] is False


def test_cli_does_not_require_model_secret_when_active_run_exists(
    engine, tmp_path, monkeypatch, cli_session_factory, capsys
):
    monkeypatch.delenv("TEST_MODEL_KEY", raising=False)
    config_path = write_config(tmp_path)
    config = load_task_configuration(config_path)
    with Session(engine) as session:
        active = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=None,
        )

    code = execution_cli.main(
        [
            "--config",
            str(tmp_path / "missing-or-invalid-tasks.yaml"),
            "--models-config",
            str(tmp_path / "missing-models.yaml"),
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["run_id"] == str(active.run.id)
    assert output["status"] == "queued"


def test_cli_executes_new_run_with_same_owner_token(
    engine, tmp_path, monkeypatch, cli_session_factory, capsys
):
    models_path = write_models_config(tmp_path)

    def create_and_execute(
        context,
        task,
        agents,
        *,
        process_token,
    ):
        assert process_token
        run = execution_service.create_or_get_run(
            context.session,
            task,
            agents,
            config_path=context.config_path,
        ).run
        assert execution_service.claim_run(
            context.session,
            run.id,
            process_token=process_token,
        )
        run = context.session.get(TaskRun, run.id)
        run.status = "succeeded"
        run.process_token = None
        run.finished_at = datetime.now(UTC)
        context.session.commit()
        return run, True, True

    monkeypatch.setattr(
        execution_cli,
        "create_claim_and_execute",
        create_and_execute,
    )
    code = execution_cli.main(
        [
            "--config",
            write_config(tmp_path),
            "--models-config",
            models_path,
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["status"] == "succeeded"
    assert output["created"] is True
    assert output["executed"] is True


def test_cli_returns_failure_exit_code_for_new_failed_run(
    engine, tmp_path, monkeypatch, cli_session_factory, capsys
):
    config_path = write_config(tmp_path)
    models_path = write_models_config(tmp_path)
    config = load_task_configuration(config_path)
    with Session(engine) as session:
        failed = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=models_path,
        )
        assert claim_run(
            session,
            failed.run.id,
            process_token=PROCESS_A,
        )
        run = session.get(TaskRun, failed.run.id)
        run.status = "failed"
        run.process_token = None
        run.error = {"code": "EVIDENCE_UNAVAILABLE", "message": "No evidence"}
        run.finished_at = datetime.now(UTC)
        session.commit()
        failed_id = run.id

    def return_failed(context, task, agents, *, process_token):
        run = context.session.get(TaskRun, failed_id)
        return run, True, True

    monkeypatch.setattr(
        execution_cli,
        "create_claim_and_execute",
        return_failed,
    )
    code = execution_cli.main(
        [
            "--config",
            config_path,
            "--models-config",
            models_path,
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["status"] == "failed"
    assert output["created"] is True
    assert output["executed"] is True
    assert output["error"]["code"] == "EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize("status", [408, 429, 500])
def test_provider_retryable_status_then_falls_back(status):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(status)
        return httpx.Response(
            200,
            json={
                "model": "fallback-model",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "primary-model",
            capabilities=("chat", "text", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "fallback-model",
            capabilities=("chat", "text", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "text", "structured_output"),
        timeout_seconds=2,
        max_attempts=1,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(handler)).research_chat(
            routing,
            [{"role": "user", "content": "test"}],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "fallback"
    assert [attempt.fallback_reason for attempt in response.attempts] == [
        "retryable_status",
        None,
    ]
    assert len(requests) == 2


def test_provider_timeout_uses_fallback():
    def handler(request):
        if request.url.host == "primary.example":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
                "model": "primary-model",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "text", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "text", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "text", "structured_output"),
        timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(handler)).research_chat(
            routing,
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array"},
                    "open_questions": {"type": "array"},
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "fallback"
    assert response.attempts[0].fallback_reason == "timeout"


def test_provider_remote_protocol_error_uses_fallback():
    requests = []

    def handler(request):
        requests.append(request.url.host)
        if request.url.host == "primary.example":
            raise httpx.RemoteProtocolError("connection dropped", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(handler)).research_chat(
            routing,
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "fallback"
    assert requests == ["primary.example", "fallback.example"]
    assert response.attempts[0].fallback_reason == "connection_error"


def test_provider_uses_each_target_timeout():
    requested_timeouts = []

    def handler(request):
        requested_timeouts.append(request.extensions["timeout"]["read"])
        if request.url.host == "primary.example":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "structured_output"),
            timeout_seconds=1,
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "structured_output"),
            timeout_seconds=9,
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(handler)).research_chat(
            routing,
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "fallback"
    assert requested_timeouts == [1.0, 9.0]


def test_provider_invalid_response_retries_within_bound():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps({"summary": "wrong"})}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
            },
        )

    target = ModelTarget(
        "https://primary.example/v1",
        "key",
        "one",
        capabilities=("chat", "structured_output"),
    )
    routing = ChatRouting(
        primary="primary",
        fallbacks=(),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=1,
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(
            {"primary": target},
            httpx.MockTransport(handler),
        ).research_chat(
            routing,
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array"},
                    "open_questions": {"type": "array"},
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "primary"
    assert [attempt.fallback_reason for attempt in response.attempts] == [
        "invalid_response",
        None,
    ]
    assert len(requests) == 2


def test_provider_content_validation_retries_within_bound():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
                "model": "primary-model",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    def validate_content(content):
        if len(requests) == 1:
            raise ValueError("invalid evidence reference")
        json.loads(content)

    target = ModelTarget(
        "https://primary.example/v1",
        "key",
        "one",
        capabilities=("chat", "structured_output"),
    )
    response = asyncio.run(
        ModelClient(
            {"primary": target},
            httpx.MockTransport(handler),
        ).research_chat(
            ChatRouting(
                primary="primary",
                fallbacks=(),
                required_capabilities=("chat", "structured_output"),
                timeout_seconds=1,
                max_attempts=2,
                retry_backoff_seconds=0,
            ),
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_refs": {"type": "array"},
                },
            },
            validate_content=validate_content,
        )
    )
    assert response.model_alias == "primary"
    assert len(requests) == 2
    assert [attempt.fallback_reason for attempt in response.attempts] == [
        "invalid_response",
        None,
    ]
    assert response.attempts[0].reported_model_name == "primary-model"
    assert response.attempts[0].prompt_tokens == 10
    assert response.attempts[0].completion_tokens == 5
    assert response.attempts[0].total_tokens == 15


def test_provider_invalid_response_uses_fallback_after_retries():
    requests = []

    def handler(request):
        requests.append(request.url.host)
        if request.url.host == "primary.example":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not-json"}}]},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=1,
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(handler)).research_chat(
            routing,
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array"},
                    "open_questions": {"type": "array"},
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "fallback"
    assert requests == [
        "primary.example",
        "primary.example",
        "fallback.example",
    ]
    assert [attempt.fallback_reason for attempt in response.attempts] == [
        "invalid_response",
        "invalid_response",
        None,
    ]


def test_provider_invalid_response_falls_back_without_retry():
    requests = []

    def handler(request):
        requests.append(request.url.host)
        if request.url.host == "primary.example":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not-json"}}]},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "ok",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=0,
    )
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(handler)).research_chat(
            routing,
            [],
            schema_name="test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "findings",
                    "open_questions",
                    "evidence_refs",
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_refs": {"type": "array"},
                },
            },
        )
    )
    assert response.model_alias == "fallback"
    assert requests == ["primary.example", "fallback.example"]
    assert [attempt.fallback_reason for attempt in response.attempts] == [
        "invalid_response",
        None,
    ]


def test_provider_auth_and_capability_errors_do_not_fall_back():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(401)

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "text", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "text", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "text", "structured_output"),
        timeout_seconds=1,
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    with pytest.raises(ModelError) as exc_info:
        asyncio.run(
            ModelClient(targets, httpx.MockTransport(handler)).research_chat(
                routing,
                [],
                schema_name="test",
                schema={"type": "object", "properties": {}},
            )
        )
    assert exc_info.value.code == "MODEL_AUTHENTICATION_FAILED"
    assert len(requests) == 1
    assert exc_info.value.attempts[0].fallback_reason is None

    requests.clear()
    incompatible = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "text", "structured_output"),
        ),
        "fallback": ModelTarget(
            "https://fallback.example/v1",
            "key",
            "two",
            capabilities=("chat", "text"),
        ),
    }
    with pytest.raises(ValueError, match="required capabilities"):
        asyncio.run(
            ModelClient(
                incompatible, httpx.MockTransport(handler)
            ).research_chat(
                routing,
                [],
                schema_name="test",
                schema={"type": "object", "properties": {}},
            )
        )
    assert requests == []


def test_provider_rejects_non_retryable_status_configuration(tmp_path):
    for status in (400, 401, 403, 404, 422):
        path = tmp_path / f"models-{status}.yaml"
        path.write_text(
            f"""
providers:
  default:
    protocol: openai_compatible
    base_url: https://models.example/v1
    api_key: test
    retryable_statuses: [{status}]
models:
  primary:
    provider: default
    name: primary
    capabilities: [chat, structured_output]
agents:
  research:
    routing:
      primary: primary
      required_capabilities: [chat, structured_output]
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="retryable statuses"):
            execution_service.load_chat_routing(
                str(path),
                "research",
            )


def test_model_configuration_rejects_duplicate_yaml_key(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        """
providers:
  default:
    protocol: openai_compatible
    base_url: https://models.example/v1
    api_key: test
    api_key: duplicate
models:
  primary:
    provider: default
    name: primary
    capabilities: [chat, structured_output]
agents:
  research:
    routing:
      primary: primary
      required_capabilities: [chat, structured_output]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not valid YAML"):
        execution_service.load_chat_routing(str(path), "research")


def test_model_configuration_rejects_oversized_model_name(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        f"""
providers:
  default:
    protocol: openai_compatible
    base_url: https://models.example/v1
    api_key: test
models:
  primary:
    provider: default
    name: {"x" * 256}
    capabilities: [chat, structured_output]
agents:
  research:
    routing:
      primary: primary
      required_capabilities: [chat, structured_output]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name"):
        execution_service.load_chat_routing(str(path), "research")


def test_provider_rejects_oversized_timeout():
    def handler(request):
        raise AssertionError("request must not be sent")

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "primary-model",
            capabilities=("chat", "structured_output"),
        ),
    }
    with pytest.raises(ValueError, match="routing is invalid"):
        asyncio.run(
            ModelClient(targets, httpx.MockTransport(handler)).research_chat(
                ChatRouting(
                    primary="primary",
                    fallbacks=(),
                    required_capabilities=("chat", "structured_output"),
                    timeout_seconds=601,
                    max_attempts=1,
                    retry_backoff_seconds=0,
                ),
                [],
                schema_name="test",
                schema={"type": "object", "properties": {}},
            )
        )


def test_provider_retry_is_bounded():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(429)

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "primary-model",
            capabilities=("chat", "text", "structured_output"),
        ),
    }
    routing = ChatRouting(
        primary="primary",
        fallbacks=(),
        required_capabilities=("chat", "text", "structured_output"),
        timeout_seconds=1,
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    with pytest.raises(ModelError) as exc_info:
        asyncio.run(
            ModelClient(targets, httpx.MockTransport(handler)).research_chat(
                routing,
                [],
                schema_name="test",
                schema={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                },
            )
        )
    assert exc_info.value.code == "MODEL_RATE_LIMITED"
    assert len(requests) == 2
    assert len(exc_info.value.attempts) == 2


def test_route_snapshot_rejects_changed_routing():
    snapshot = {
        "routing": {
            "primary": "primary",
            "fallbacks": [],
            "required_capabilities": ["chat", "structured_output"],
            "timeout_seconds": 5,
            "max_attempts": 2,
            "retry_backoff_seconds": 0,
        },
        "targets": [
            {"alias": "primary"},
            {"alias": "fallback"},
        ],
        "model_ref": "primary",
    }
    configured = ChatRouting(
        primary="primary",
        fallbacks=("fallback",),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=5,
        max_attempts=2,
        retry_backoff_seconds=0,
    )

    with pytest.raises(execution_service._StepFailure) as exc_info:
        execution_service._snapshot_routing(snapshot, configured, "primary")

    assert exc_info.value.code == "MODEL_ROUTE_SNAPSHOT_INVALID"


def test_route_snapshot_separates_route_reference_from_primary_alias():
    snapshot = {
        "routing": {
            "primary": "primary",
            "fallbacks": [],
            "required_capabilities": ["chat", "structured_output"],
            "timeout_seconds": 5,
            "max_attempts": 2,
            "retry_backoff_seconds": 0,
        },
        "targets": [{"alias": "primary"}],
        "model_ref": "research",
    }
    configured = ChatRouting(
        primary="primary",
        fallbacks=(),
        required_capabilities=("chat", "structured_output"),
        timeout_seconds=5,
        max_attempts=2,
        retry_backoff_seconds=0,
    )

    assert (
        execution_service._snapshot_routing(snapshot, configured, "research")
        == configured
    )


def test_unavailable_attempt_usage_is_not_reported_as_zero():
    usage = execution_service._usage_from_attempts(
        [
            ModelAttempt(
                attempt_number=1,
                attempt_alias="primary",
                attempt_model_name="model",
                reported_model_name=None,
                status="failed",
                duration_ms=12,
                error={"code": "MODEL_TIMEOUT", "message": "timed out"},
            )
        ]
    )

    assert usage == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "duration_ms": 12,
        "model_attempts": 1,
    }
