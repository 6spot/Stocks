import asyncio
import json
import os
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock
from uuid import UUID, uuid4

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
from stocks.processing.processors import StageRegistry
from stocks.repository import SearchHit
from stocks.models import (
    Analysis,
    Base,
    Document,
    DocumentChunk,
    DocumentParseBatch,
    DocumentParseState,
    EvidencePackage,
    ModelCall,
    Report,
    Source,
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


def write_config(
    tmp_path,
    *,
    condition: dict | None = None,
    queries: list[str] | None = None,
    source_ids: list[str] | None = None,
    change_page_limit: int | None = None,
    tools: list[str] | None = None,
) -> str:
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
    selection_extra = ""
    if source_ids is not None:
        selection_extra += (
            "\n          source_ids: "
            + json.dumps(source_ids, ensure_ascii=False)
        )
    if change_page_limit is not None:
        selection_extra += (
            f"\n          change_page_limit: {change_page_limit}"
        )
    query_text = json.dumps(
        queries if queries is not None else ["inflation", "macro policy"],
        ensure_ascii=False,
    )
    tools_yaml = ""
    if tools:
        tools_yaml = f"\n    tools: {json.dumps(tools, ensure_ascii=False)}"
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
        select_evidence:
          kind: select_evidence
          queries: {query_text}
          limit_per_query: 2
{selection_extra}
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
{tools_yaml}
    input_schema:
      type: object
      additionalProperties: false
      required: [run_id, objective, strategy, scope, as_of, evidence_package_id, evidence, coverage, truncated]
      properties:
        run_id: {{type: string}}
        objective: {{type: string}}
        strategy:
          anyOf:
            - {{type: string}}
            - {{type: "null"}}
        scope: {{type: object, additionalProperties: true}}
        as_of: {{type: string}}
        evidence_package_id: {{type: string}}
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
        findings:
          type: array
          items:
            type: object
            additionalProperties: false
            required: [kind, statement, rationale, uncertainty, evidence_refs]
            properties:
              kind: {{type: string}}
              statement: {{type: string}}
              rationale: {{type: string}}
              uncertainty: {{type: string}}
              evidence_refs:
                type: array
                minItems: 1
                items:
                  type: object
                  additionalProperties: false
                  required: [chunk_id, document_id]
                  properties:
                    chunk_id: {{type: string}}
                    document_id: {{type: string}}
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
    path = Path(write_config(tmp_path, source_ids=["sec", "media"]))
    search = load_task_configuration(path).tasks[0].steps[0].select_evidence
    assert search is not None
    assert search.source_ids == ["sec", "media"]


def test_config_rejects_duplicate_source_filters(tmp_path):
    path = Path(write_config(tmp_path, source_ids=["sec", "sec"]))
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


def write_tools_config(tmp_path, *, enabled: bool = True) -> str:
    path = tmp_path / "tools.yaml"
    path.write_text(
        f"""
tools:
  - id: research_search
    enabled: {str(enabled).lower()}
    endpoint: https://mcp.example.test/rpc
    source_id: tool_search_source
    timeout_seconds: 5
    input_schema:
      type: object
      additionalProperties: false
      required: [query]
      properties:
        query: {{type: string, minLength: 1, maxLength: 200}}
    budgets:
      max_calls: 2
      max_tokens: 16000
      max_request_bytes: 4096
      max_result_bytes: 32768
""",
        encoding="utf-8",
    )
    return str(path)


class _CountingToolClient:
    """A Tool client that records calls instead of reaching the network."""

    def __init__(self, configuration, tool_ids) -> None:
        self.tool_ids = tuple(tool_ids)
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, tool_id: str, arguments):
        self.calls.append((tool_id, dict(arguments)))
        raise AssertionError("No Tool call was expected in this test")

    def close(self) -> None:
        pass


class _ToolClientFactory:
    def __init__(self) -> None:
        self.instances: list[_CountingToolClient] = []

    def __call__(self, configuration, tool_ids) -> _CountingToolClient:
        client = _CountingToolClient(configuration, tool_ids)
        self.instances.append(client)
        return client

    @property
    def calls(self) -> list[tuple[str, dict]]:
        return [call for client in self.instances for call in client.calls]


def _plan_transport(
    plan: dict, output_content: str | None = None, statuses=None
):
    """Answer the Tool-planning call with a plan, everything else as research."""

    base = _mock_transport(output_content, statuses=statuses)

    def handler(request):
        body = json.loads(request.content)
        name = (
            body.get("response_format", {})
            .get("json_schema", {})
            .get("name")
        )
        if name == "research_tool_plan":
            return httpx.Response(
                200,
                json={
                    "model": "research-model",
                    "choices": [
                        {"message": {"content": json.dumps(plan)}}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )
        return base.handler(request)

    return httpx.MockTransport(handler)


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
        step_type="select_evidence",
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
        select_evidence: {kind: select_evidence, queries: [one]}
      - id: same
        select_evidence: {kind: select_evidence, queries: [two]}
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
        select_evidence: {kind: select_evidence, queries: [one]}
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
        select_evidence: {kind: select_evidence, queries: [one]}
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
        step_type="select_evidence",
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


def _make_task_config(tmp_path, condition=None, **overrides):
    return load_task_configuration(
        write_config(tmp_path, condition=condition, **overrides)
    )


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
                "findings": [
                    {
                        "kind": "fact",
                        "statement": "finding",
                        "rationale": "selected evidence supports it",
                        "uncertainty": "low",
                        "evidence_refs": [
                            {
                                "chunk_id": chunk_id,
                                "document_id": document_id,
                            }
                        ],
                    }
                ],
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


EVIDENCE_CHUNK_ID = "00000000-0000-0000-0000-000000000001"
EVIDENCE_DOCUMENT_ID = "00000000-0000-0000-0000-000000000002"
EVIDENCE_BATCH_ID = "00000000-0000-0000-0000-000000000003"


def _add_saved_evidence(session):
    """Persist one Document version with a single current Chunk."""
    session.add(
        Source(
            id="sec",
            name="SEC",
            connector="rss",
            publisher="SEC",
            trust_tier="T1",
        )
    )
    session.flush()
    document = Document(
        id=UUID(EVIDENCE_DOCUMENT_ID),
        source_id="sec",
        dedupe_key="doc-1",
        version=1,
        title="Evidence",
        collected_at=datetime.now(UTC),
        completeness="complete",
        content_type="text/plain",
        content_size=1,
        content_hash="a" * 64,
        snapshot_key="snapshot",
        trust_tier_snapshot="T1",
    )
    session.add(document)
    session.flush()
    batch = DocumentParseBatch(
        id=UUID(EVIDENCE_BATCH_ID),
        document_id=document.id,
        processor_version="test",
        config_fingerprint="f" * 64,
        content_hash=document.content_hash,
        attempt_number=1,
        status="completed",
        chunk_count=1,
        created_at=datetime.now(UTC),
    )
    session.add(batch)
    session.flush()
    session.add(
        DocumentChunk(
            id=UUID(EVIDENCE_CHUNK_ID),
            document_id=document.id,
            parse_batch_id=batch.id,
            position=0,
            text="Evidence",
            locator={},
        )
    )
    session.add(
        DocumentParseState(
            document_id=document.id,
            status="completed",
            processor_version="test",
            config_fingerprint="f" * 64,
            latest_batch_id=batch.id,
            current_batch_id=batch.id,
            chunk_count=1,
            attempted_at=datetime.now(UTC),
        )
    )
    session.commit()


def _evidence_selection_result(
    session,
    run,
    *,
    selected_chunks: list[dict],
    gaps: list[str],
):
    """Stand in for Evidence Selection, persisting the package for this Run."""
    change_scan = None
    if run.change_watermark_end is not None:
        change_scan = {
            "window": {
                "change_watermark_start": run.change_watermark_start,
                "change_watermark_end": run.change_watermark_end,
            },
            "completed": True,
            "after_watermark": run.change_watermark_end,
            "after_change_id": None,
            "raw_changes_scanned": 0,
            "candidate_documents_scanned": 0,
            "scanned_document_ids": [],
            "source_ids": [],
            "kinds": [],
        }
    package = EvidencePackage(
        run_id=run.id,
        objective=run.objective,
        scope=run.scope,
        as_of=run.created_at,
        selection_policy_version="test",
        selection_budget={"max_tokens": 1000},
        queries=[],
        selected_chunks=selected_chunks,
        coverage={"gaps": gaps},
        candidates_discovered=None,
        candidates_screened=len(selected_chunks),
        selected_count=len(selected_chunks),
        unexamined_count=None,
        retrieval_truncated=False,
        change_scan=change_scan,
        created_at=run.created_at,
    )
    session.add(package)
    session.flush()
    if run.change_scan_progress is not None:
        from stocks.changes import checkpoint_scan

        checkpoint_scan(
            session,
            run,
            page={
                "completed": True,
                "after_watermark": run.change_watermark_end,
                "after_change_id": None,
                "raw_changes_scanned": 0,
                "scanned_document_ids": [],
            },
            selection={
                "evidence_package_id": str(package.id),
                "selected_count": package.selected_count,
            },
        )
    session.commit()
    output = {
        "evidence_package_id": str(package.id),
        "items": selected_chunks,
        "coverage": package.coverage,
        "truncated": False,
    }
    if change_scan is not None:
        output["change_scan"] = deepcopy(change_scan)
    refs = [
        {
            "chunk_id": item["chunk_id"],
            "document_id": item["document_id"],
            "parse_batch_id": item["parse_batch_id"],
            "document_version": item["document_version"],
            "source_id": item["source_id"],
            "position": item["position"],
        }
        for item in selected_chunks
    ]
    return output, refs


def _selected_evidence_chunk():
    return {
        "chunk_id": EVIDENCE_CHUNK_ID,
        "document_id": EVIDENCE_DOCUMENT_ID,
        "parse_batch_id": EVIDENCE_BATCH_ID,
        "document_version": 1,
        "source_id": "sec",
        "position": 0,
    }


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


def test_binding_failure_fails_claimed_run(engine, tmp_path, monkeypatch):
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=write_models_config(tmp_path),
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)

        def fail_binding(*args, **kwargs):
            raise ExecutionError("WINDOW_INVALID", "window binding failed")

        monkeypatch.setattr(
            execution_service,
            "_bind_change_window",
            fail_binding,
        )
        with pytest.raises(ExecutionError) as exc_info:
            execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path="unused",
                ),
                run_id,
                process_token=PROCESS_A,
            )

        assert exc_info.value.code == "WINDOW_INVALID"
        session.expire_all()
        failed = session.get(TaskRun, run_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.process_token is None
        assert failed.error["code"] == "WINDOW_INVALID"


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

        _add_saved_evidence(session)
        selected_chunk = _selected_evidence_chunk()

        def fake_search(
            session,
            settings,
            config_path,
            step,
            *,
            run,
            model_snapshot=None,
            model_transport=None,
            embed=None,
        ):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected_chunk],
                gaps=[],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
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
            execution_service._run_evidence_selection = original_search
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
        assert results[1].evidence_refs == [selected_chunk]
        package = session.scalar(
            select(EvidencePackage).where(EvidencePackage.run_id == run_id)
        )
        analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == run_id)
        )
        report = session.scalar(select(Report).where(Report.run_id == run_id))
        assert package is not None and analysis is not None and report is not None
        assert analysis.evidence_package_id == package.id
        assert report.analysis_id == analysis.id
        assert report.content.startswith("# Brief\n")
        assert selected_chunk["document_id"] in report.content
        assert selected_chunk["chunk_id"] in report.content
        assert completed.output["analysis_id"] == str(analysis.id)
        assert completed.output["report_id"] == str(report.id)
        assert completed.change_scan_completed is True
        assert completed.change_scan_progress["status"] == "scanned"
        from stocks.changes import get_cursor

        cursor = get_cursor(session, task.id, task.run_scope)
        assert cursor is not None
        assert cursor.watermark == completed.change_watermark_end
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


def test_empty_evidence_run_succeeds_with_explicit_gaps(engine, tmp_path):
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    with Session(engine) as session:
        first = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
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
            config_path=models_path,
        )
        assert claim_run(
            session,
            second.run.id,
            process_token=PROCESS_A,
        )

        def empty_evidence(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[],
                gaps=["no_selected_evidence"],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = empty_evidence
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=models_path,
                    model_transport=_mock_transport(
                        json.dumps(
                            {
                                "summary": "No citable evidence was saved.",
                                "findings": [],
                                "open_questions": [
                                    "Was any primary release collected?"
                                ],
                                "evidence_refs": [],
                            }
                        )
                    ),
                ),
                second.run.id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search

        # V2-01: an explicit empty-evidence report succeeds with gaps and open
        # questions instead of failing the Run.
        assert completed.status == "succeeded", [
            (item.step_id, item.status, item.error)
            for item in session.scalars(
                select(TaskStepResult).where(
                    TaskStepResult.run_id == completed.id
                )
            )
        ]
        assert completed.output is not None
        assert session.get(TaskRun, first_id).output == {"summary": "last success"}
        step = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == completed.id,
                TaskStepResult.step_id == "research",
            )
        )
        assert step is not None
        assert step.status == "succeeded"
        analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == completed.id)
        )
        assert analysis is not None
        assert analysis.findings == []
        assert analysis.open_questions == ["Was any primary release collected?"]
        assert "no_selected_evidence" in analysis.coverage["gaps"]
        from stocks.changes import get_cursor

        cursor = get_cursor(session, task.id, task.run_scope)
        assert cursor is not None
        assert cursor.watermark == completed.change_watermark_end


def test_repository_sufficient_run_does_not_call_a_tool(engine, tmp_path):
    """A configured Tool is only reachable through the model's own request."""

    config = _make_task_config(tmp_path, tools=["research_search"])
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    tools_path = write_tools_config(tmp_path)
    selected = _selected_evidence_chunk()
    factory = _ToolClientFactory()

    def forbidden_snapshot_store(*args, **kwargs):
        raise AssertionError("No Tool ingestion was expected")

    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
            tools_config_path=tools_path,
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)
        assert claimed.run.model_snapshot["tools"]["researcher"][0]["tool_id"] == (
            "research_search"
        )

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected],
                gaps=[],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_plan_transport(
                        {
                            "assessment": "Saved evidence answers the question.",
                            "requests": [],
                        }
                    ),
                    session_factory=lambda: Session(engine),
                    tools_config_path=tools_path,
                    tool_client_factory=factory,
                    stage_registry=StageRegistry(),
                    snapshot_store=forbidden_snapshot_store,
                ),
                run_id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search

        assert completed.status == "succeeded"
        # The planning call ran, found the Repository sufficient and made no
        # external request at all.
        assert factory.calls == []
        assert len(factory.instances) == 1
        session.expire_all()
        assert completed.research_inputs == []
        analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == run_id)
        )
        assert analysis is not None
        assert analysis.research_inputs == []
        purposes = session.scalars(
            select(ModelCall.call_purpose).where(ModelCall.run_id == run_id)
        ).all()
        assert "tool_plan" in purposes
        assert "research" in purposes


def test_retry_keeps_the_ledger_and_appended_evidence_stays_citable(
    engine, tmp_path
):
    """A retry copies the source Run's ledger; its appended input stays citable."""

    from stocks.research.service import (
        LEDGER_KIND_INPUT,
        LEDGER_KIND_SELECTION,
        SUPPLEMENT_ROLE,
    )
    from stocks.research.supplement import append_research_entries

    config = _make_task_config(tmp_path, tools=["research_search"])
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    tools_path = write_tools_config(tmp_path)
    selected = _selected_evidence_chunk()
    appended_chunk_id = "00000000-0000-0000-0000-000000000004"
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
            tools_config_path=tools_path,
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)
        session.add(
            DocumentChunk(
                id=UUID(appended_chunk_id),
                document_id=UUID(EVIDENCE_DOCUMENT_ID),
                parse_batch_id=UUID(EVIDENCE_BATCH_ID),
                position=1,
                text="Supplemental evidence registered by an authorized Tool.",
                locator={},
            )
        )
        session.commit()

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            output, refs = _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected],
                gaps=[],
            )
            package = session.scalar(
                select(EvidencePackage).where(EvidencePackage.run_id == run.id)
            )
            append_research_entries(
                session,
                run,
                package,
                [
                    {
                        "kind": LEDGER_KIND_INPUT,
                        "input_id": "input-1",
                        "tool_id": "research_search",
                        "purpose": "Fetch the missing release.",
                        "role": SUPPLEMENT_ROLE,
                        "document_id": EVIDENCE_DOCUMENT_ID,
                        "source_id": "sec",
                        "document_version": 1,
                        "parse_batch_id": EVIDENCE_BATCH_ID,
                        "title": "Supplemental release",
                        "url": "https://www.example.gov/releases/one",
                        "chunks": [
                            {
                                "chunk_id": appended_chunk_id,
                                "position": 1,
                                "chunk_ids": [appended_chunk_id],
                                "fact_ids": [],
                            }
                        ],
                        "timing": ["collected_after_as_of"],
                        "recorded_at": datetime.now(UTC).isoformat(),
                    }
                ],
                process_token=PROCESS_A,
                now=datetime.now(UTC),
            )
            return output, refs

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
        empty_plan = {
            "assessment": "Saved evidence needs no lookup.",
            "requests": [],
        }
        try:
            failed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_plan_transport(
                        empty_plan,
                        output_content="not a research result",
                        statuses=[200, 200, 200],
                    ),
                    session_factory=lambda: Session(engine),
                    tools_config_path=tools_path,
                ),
                run_id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search
        assert failed.status == "failed"
        package = session.scalar(
            select(EvidencePackage).where(EvidencePackage.run_id == run_id)
        )
        assert package is not None
        source_package_id = package.id
        session.expire_all()
        source_ledger = deepcopy(failed.research_inputs)
        assert [entry["kind"] for entry in source_ledger] == [
            LEDGER_KIND_SELECTION,
            LEDGER_KIND_INPUT,
        ]

        # A later successful parse supersedes batch A. Both the initial
        # selection and the appended input still belong to A, so the retry must
        # keep reading them instead of silently switching to the new batch.
        document = session.get(Document, UUID(EVIDENCE_DOCUMENT_ID))
        assert document is not None
        newer = DocumentParseBatch(
            document_id=document.id,
            processor_version="test-v2",
            config_fingerprint="f" * 64,
            content_hash=document.content_hash,
            attempt_number=2,
            status="completed",
            chunk_count=1,
            created_at=datetime.now(UTC),
        )
        session.add(newer)
        session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_batch_id=newer.id,
                position=0,
                text="Evidence from the newer parse.",
                locator={},
            )
        )
        parse_state = session.get(DocumentParseState, document.id)
        assert parse_state is not None
        parse_state.current_batch_id = newer.id
        parse_state.latest_batch_id = newer.id
        session.commit()
        session.expire_all()

        retried = execution_service.retry_run(session, run_id)
        retry_run_id = retried.run.id
        retried_ledger = deepcopy(retried.run.research_inputs)
        session.expire_all()
        # The source Run keeps its own record and both copies pin their own
        # Evidence Package; every adopted input is preserved verbatim.
        assert failed.research_inputs == source_ledger
        assert len(retried_ledger) == 2
        assert retried_ledger[1] == source_ledger[1]
        assert retried_ledger[0]["evidence_package_hash"] == (
            source_ledger[0]["evidence_package_hash"]
        )
        retry_package = session.scalar(
            select(EvidencePackage).where(
                EvidencePackage.run_id == retry_run_id
            )
        )
        assert retry_package is not None
        assert retry_package.id != source_package_id
        assert retried_ledger[0]["evidence_package_id"] == str(retry_package.id)
        assert (
            retry_package.selected_chunks == [selected]
        )

        # The copied input is real citable evidence for the retry: the Agent
        # returns a finding citing it and the report keeps that reference.
        assert claim_run(session, retry_run_id, process_token=PROCESS_B)
        output_content = json.dumps(
            {
                "summary": "supplemental",
                "findings": [
                    {
                        "kind": "fact",
                        "statement": "supplemental finding",
                        "rationale": "appended evidence supports it",
                        "uncertainty": "low",
                        "evidence_refs": [
                            {
                                "chunk_id": appended_chunk_id,
                                "document_id": EVIDENCE_DOCUMENT_ID,
                            }
                        ],
                    }
                ],
                "open_questions": [],
                "evidence_refs": [
                    {
                        "chunk_id": appended_chunk_id,
                        "document_id": EVIDENCE_DOCUMENT_ID,
                    }
                ],
            }
        )
        completed = execute_claimed_run(
            ExecutionContext(
                session=session,
                settings=make_config(),
                config_path=str(models_path),
                model_transport=_plan_transport(
                    empty_plan, output_content=output_content
                ),
                session_factory=lambda: Session(engine),
                tools_config_path=tools_path,
            ),
            retry_run_id,
            process_token=PROCESS_B,
        )
        assert completed.status == "succeeded"
        session.expire_all()
        retry_analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == retry_run_id)
        )
        assert retry_analysis is not None
        assert [entry["kind"] for entry in retry_analysis.research_inputs] == [
            LEDGER_KIND_SELECTION,
            LEDGER_KIND_INPUT,
        ]
        assert (
            retry_analysis.research_inputs[1]["input_id"] == "input-1"
        )
        report = session.scalar(
            select(Report).where(Report.run_id == retry_run_id)
        )
        assert report is not None
        assert appended_chunk_id in report.content


def test_second_planning_round_continues_the_attempt_sequence(engine, tmp_path):
    """A later planning round adds cost rows instead of hiding round one."""

    from stocks.providers import load_chat_routing

    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    routing, targets = load_chat_routing(models_path, "research")
    attempts = [
        ModelAttempt(
            attempt_number=1,
            attempt_alias=routing.primary,
            attempt_model_name=targets[routing.primary].name,
            reported_model_name=targets[routing.primary].name,
            status="succeeded",
            duration_ms=5,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
    ]
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
        )
        step = task.steps[0]
        for _ in range(2):
            execution_service._persist_model_attempts(
                session,
                claimed.run,
                step,
                routing,
                targets,
                attempts,
                purpose="tool_plan",
            )
        rows = session.scalars(
            select(ModelCall)
            .where(
                ModelCall.run_id == claimed.run.id,
                ModelCall.call_purpose == "tool_plan",
            )
            .order_by(ModelCall.attempt_number)
        ).all()
        assert [row.attempt_number for row in rows] == [1, 2]
        assert [row.total_tokens for row in rows] == [15, 15]

def test_full_run_records_provider_and_provider_reported_cost(engine, tmp_path):
    """A real Run keeps the serving provider and the cost the provider reported."""
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    os.environ["TEST_MODEL_KEY"] = "secret-key-never-persist"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "research-model",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "summary",
                                    "findings": [],
                                    "open_questions": [],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": 0.00042,
                },
            },
        )

    with Session(engine) as session:
        claimed = create_or_get_run(
            session, task, list(config.agents), config_path=models_path
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)
        selected_chunk = _selected_evidence_chunk()

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session, run, selected_chunks=[selected_chunk], gaps=[]
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=httpx.MockTransport(handler),
                ),
                run_id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search

        assert completed.status == "succeeded"
        call = session.scalar(
            select(ModelCall).where(
                ModelCall.run_id == run_id,
                ModelCall.call_purpose == "research",
            )
        )
        assert call is not None
        assert call.provider == "default"
        assert call.cost_usd == Decimal("0.000420")
        step = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == run_id,
                TaskStepResult.step_id == "research",
            )
        )
        assert step is not None
        assert step.usage["cost_usd"] == 0.00042
        assert step.usage["cost_unknown_attempts"] == 0
        assert step.usage["total_tokens"] == 15


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
        def empty_evidence(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[],
                gaps=["no_selected_evidence"],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = empty_evidence
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
            execution_service._run_evidence_selection = original_search
        results = session.scalars(
            select(TaskStepResult).where(TaskStepResult.run_id == completed.id)
        ).all()
        assert completed.status == "failed"
        assert completed.error["code"] == "FINAL_RESEARCH_STEP_NOT_SUCCEEDED"
        assert {result.step_id: result.status for result in results} == {
            "evidence": "succeeded",
            "research": "skipped",
        }
        assert completed.change_scan_completed is False
        from stocks.changes import get_cursor

        assert get_cursor(session, task.id, task.run_scope) is None


def test_final_report_failure_rolls_back_final_step_and_keeps_scan_checkpoint(
    engine, tmp_path, monkeypatch
):
    """A report failure cannot commit an Analysis, final step, or cursor."""

    from stocks.changes import get_cursor

    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    selected = _selected_evidence_chunk()

    def fail_report(*args, **kwargs):
        raise ValueError("report rendering failed")

    monkeypatch.setattr(execution_service, "render_report", fail_report)
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected],
                gaps=[],
            )

        monkeypatch.setattr(
            execution_service,
            "_run_evidence_selection",
            fake_search,
        )
        failed = execute_claimed_run(
            ExecutionContext(
                session=session,
                settings=make_config(),
                config_path=str(models_path),
                model_transport=_mock_transport(),
            ),
            run_id,
            process_token=PROCESS_A,
        )

        assert failed.status == "failed"
        assert session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == run_id,
                TaskStepResult.step_id == "research",
            )
        ).status == "failed"
        assert session.scalar(
            select(Analysis).where(Analysis.run_id == run_id)
        ) is None
        assert session.scalar(
            select(Report).where(Report.run_id == run_id)
        ) is None
        assert failed.change_scan_completed is False
        assert failed.change_scan_progress["status"] == "scanned"
        assert get_cursor(session, task.id, task.run_scope) is None


def test_final_ownership_loss_commits_no_success_artifacts(
    engine, tmp_path, monkeypatch
):
    from stocks.changes import get_cursor

    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    selected = _selected_evidence_chunk()
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=write_models_config(tmp_path),
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected],
                gaps=[],
            )

        original_assert_ownership = execution_service._assert_run_ownership
        ownership_checks = 0

        def lose_ownership(session, owned_run_id, process_token):
            nonlocal ownership_checks
            ownership_checks += 1
            if ownership_checks < 3:
                return original_assert_ownership(
                    session, owned_run_id, process_token
                )
            assert ownership_checks == 3
            assert owned_run_id == run_id
            assert process_token == PROCESS_A
            # The final StepResult, Analysis and Report are flushed but not
            # committed here. Release their row locks, then let the Owner end
            # the Run before the authoritative final ownership fence.
            session.rollback()
            with Session(engine) as owner_session:
                owned = owner_session.get(TaskRun, run_id, with_for_update=True)
                assert owned is not None
                owned.status = "cancelled"
                owned.process_token = None
                owned.error = {
                    "code": "RUN_ENDED_BY_OWNER",
                    "message": "ended during final completion",
                }
                owned.finished_at = datetime.now(UTC)
                owner_session.commit()
            raise ExecutionError(
                "RUN_NOT_OWNED",
                "Task Run is no longer owned by this process",
            )

        monkeypatch.setattr(
            execution_service,
            "_run_evidence_selection",
            fake_search,
        )
        monkeypatch.setattr(
            execution_service,
            "_assert_run_ownership",
            lose_ownership,
        )
        with pytest.raises(ExecutionError) as exc_info:
            execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(write_models_config(tmp_path)),
                    model_transport=_mock_transport(),
                ),
                run_id,
                process_token=PROCESS_A,
            )

        assert exc_info.value.code == "RUN_NOT_OWNED"
        session.expire_all()
        ended = session.get(TaskRun, run_id)
        assert ended is not None
        assert ended.status == "cancelled"
        assert ended.process_token is None
        assert ended.error["code"] == "RUN_ENDED_BY_OWNER"
        assert ended.change_scan_completed is False
        research_step = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == run_id,
                TaskStepResult.step_id == "research",
            )
        )
        assert research_step is None
        assert session.scalar(
            select(Analysis).where(Analysis.run_id == run_id)
        ) is None
        assert session.scalar(
            select(Report).where(Report.run_id == run_id)
        ) is None
        assert get_cursor(session, task.id, task.run_scope) is None


def test_retry_recovers_a_failed_agent_without_rebinding_the_window(
    engine, tmp_path
):
    """A retry continues the source Run's exact window after its checkpoint."""

    from stocks.changes import get_cursor

    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    selected = _selected_evidence_chunk()
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected],
                gaps=[],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
        try:
            failed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_mock_transport(statuses=[500, 500, 500]),
                ),
                run_id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search
        assert failed.status == "failed"
        original_window = (
            failed.change_watermark_start,
            failed.change_watermark_end,
        )
        original_position = failed.change_scan_progress["after_watermark"]
        assert get_cursor(session, task.id, task.run_scope) is None

        retried = execution_service.retry_run(session, run_id)
        assert (
            retried.run.change_watermark_start,
            retried.run.change_watermark_end,
        ) == original_window
        assert (
            retried.run.change_scan_progress["after_watermark"]
            == original_position
        )
        package = session.scalar(
            select(EvidencePackage).where(
                EvidencePackage.run_id == retried.run.id
            )
        )
        assert package is not None
        assert len(package.selected_chunks) == 1
        assert package.selected_chunks[0]["chunk_id"] == selected["chunk_id"]
        retried_progress = retried.run.change_scan_progress
        assert retried_progress["selection"]["evidence_package_id"] == str(
            package.id
        )
        assert retried_progress["window"]["change_watermark_start"] == (
            retried.run.change_watermark_start
        )
        assert retried_progress["window"]["change_watermark_end"] == (
            retried.run.change_watermark_end
        )
        copied_step = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == retried.run.id,
                TaskStepResult.step_id == "evidence",
            )
        )
        assert copied_step is not None
        assert copied_step.output["evidence_package_id"] == str(package.id)
        assert package.change_scan["window"] == {
            "change_watermark_start": retried.run.change_watermark_start,
            "change_watermark_end": retried.run.change_watermark_end,
        }


def test_retry_reads_a_superseded_parse_batch_after_the_document_is_reparsed(
    engine, tmp_path
):
    """A frozen Run input stays citable after a newer current batch exists.

    New evidence selection must keep using the current batch; only the retry's
    already-recorded references are read through the historical batch.
    """

    from stocks.repository import current_document_hits

    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    selected = _selected_evidence_chunk()
    with Session(engine) as session:
        claimed = create_or_get_run(
            session,
            task,
            list(config.agents),
            config_path=models_path,
        )
        run_id = claimed.run.id
        assert claim_run(session, run_id, process_token=PROCESS_A)
        _add_saved_evidence(session)

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=[selected],
                gaps=[],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
        try:
            failed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_mock_transport(statuses=[500, 500, 500]),
                ),
                run_id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search
        assert failed.status == "failed"

        # The same Document is parsed again into a new current batch; the old
        # batch and its Chunk stay stored as immutable history.
        document = session.get(Document, UUID(EVIDENCE_DOCUMENT_ID))
        assert document is not None
        newer = DocumentParseBatch(
            document_id=document.id,
            processor_version="test-v2",
            config_fingerprint="f" * 64,
            content_hash=document.content_hash,
            attempt_number=2,
            status="completed",
            chunk_count=1,
            created_at=datetime.now(UTC),
        )
        session.add(newer)
        session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_batch_id=newer.id,
                position=0,
                text="Evidence from the newer parse.",
                locator={},
            )
        )
        state = session.get(DocumentParseState, document.id)
        assert state is not None
        state.current_batch_id = newer.id
        state.latest_batch_id = newer.id
        session.commit()
        session.expire_all()
        assert [hit.text for hit in current_document_hits(session, [document.id])] == [
            "Evidence from the newer parse."
        ]

        retried = execution_service.retry_run(session, run_id)
        retry_package = session.scalar(
            select(EvidencePackage).where(
                EvidencePackage.run_id == retried.run.id
            )
        )
        assert retry_package is not None
        # The retry keeps the frozen batch A reference instead of switching to B.
        assert retry_package.selected_chunks[0]["parse_batch_id"] == (
            EVIDENCE_BATCH_ID
        )

        assert claim_run(session, retried.run.id, process_token=PROCESS_B)
        completed = execute_claimed_run(
            ExecutionContext(
                session=session,
                settings=make_config(),
                config_path=str(models_path),
                model_transport=_mock_transport(),
            ),
            retried.run.id,
            process_token=PROCESS_B,
        )
        assert completed.status == "succeeded"
        session.expire_all()
        analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == retried.run.id)
        )
        assert analysis is not None
        assert analysis.evidence_refs[0]["chunk_id"] == EVIDENCE_CHUNK_ID
        assert analysis.evidence_refs[0]["parse_batch_id"] == EVIDENCE_BATCH_ID
        report = session.scalar(
            select(Report).where(Report.run_id == retried.run.id)
        )
        assert report is not None
        # The newer batch still feeds new selection, not this retry.
        assert [
            hit.text for hit in current_document_hits(session, [document.id])
        ] == ["Evidence from the newer parse."]


def test_normal_runs_advance_through_frozen_paged_scan(
    engine, tmp_path, monkeypatch
):
    """Three normal Runs consume pages 1->2->3 using one frozen scan snapshot."""

    from stocks.changes import record_change

    task_config = _make_task_config(
        tmp_path,
        queries=["live query after resume"],
        source_ids=["sec"],
        change_page_limit=1,
    )
    task = task_config.tasks[0]
    models_path = write_models_config(tmp_path)

    with Session(engine) as session:
        _add_saved_evidence(session)
        session.add(
            Source(
                id="media",
                name="Media",
                connector="rss",
                publisher="Media",
                trust_tier="T2",
            )
        )
        session.flush()
        documents = []
        for source_id, dedupe_key in (
            ("sec", "first"),
            ("sec", "second"),
            ("sec", "third"),
            ("media", "filtered"),
            ("sec", "fourth"),
        ):
            document = Document(
                source_id=source_id,
                dedupe_key=dedupe_key,
                version=1,
                title=dedupe_key,
                collected_at=datetime.now(UTC),
                completeness="complete",
                content_type="text/plain",
                content_size=1,
                content_hash=(
                    dedupe_key[0] * 64
                ),
                snapshot_key=f"snapshot-{dedupe_key}",
                trust_tier_snapshot="T1",
            )
            session.add(document)
            session.flush()
            record_change(
                session,
                kind="document_ingested",
                document_id=document.id,
                recorded_at=datetime.now(UTC),
            )
            documents.append(document)
        session.commit()
        expected_documents = [
            documents[0].id,
            documents[1].id,
            documents[2].id,
            documents[4].id,
        ]

        hit = SearchHit(
            chunk_id=UUID(EVIDENCE_CHUNK_ID),
            document_id=UUID(EVIDENCE_DOCUMENT_ID),
            parse_batch_id=UUID(EVIDENCE_BATCH_ID),
            position=0,
            text="Evidence",
            locator={},
            title="Evidence",
            source_id="sec",
            version=1,
            published_at=None,
            source_updated_at=None,
            collected_at=datetime.now(UTC),
            completeness="complete",
            similarity=0.9,
        )

        def semantic_result(*args, **kwargs):
            return type(
                "Result",
                (),
                {"hits": (hit,), "coverage": None, "truncated": False},
            )()

        monkeypatch.setattr(
            "stocks.research.service.semantic_search",
            semantic_result,
        )

        seen_sources: list[tuple[str, ...]] = []
        original_search_changes = (
            execution_service.select_evidence.__globals__["search_changes"]
        )

        def observe_page(session, window, **kwargs):
            seen_sources.append(tuple(kwargs["source_ids"]))
            return original_search_changes(session, window, **kwargs)

        monkeypatch.setitem(
            execution_service.select_evidence.__globals__,
            "search_changes",
            observe_page,
        )

        runs = []
        for index in range(3):
            claimed = create_or_get_run(
                session,
                task,
                list(task_config.agents),
                config_path=models_path,
            )
            assert claimed.created is True
            assert claim_run(
                session,
                claimed.run.id,
                process_token=f"{PROCESS_A}-{index}",
            )
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_mock_transport(),
                ),
                claimed.run.id,
                process_token=f"{PROCESS_A}-{index}",
                embed=_fake_embed,
            )
            runs.append(completed)

        assert [
            run.change_scan_progress["after_watermark"] for run in runs
        ] == [1, 2, 3]
        assert [
            run.change_scan_progress["counts"]["pages_scanned"]
            for run in runs
        ] == [1, 2, 3]
        assert [
            sorted(run.change_scan_progress["scanned_document_ids"])
            for run in runs
        ] == [
            sorted(str(value) for value in expected_documents[:1]),
            sorted(str(value) for value in expected_documents[:2]),
            sorted(str(value) for value in expected_documents[:3]),
        ]
        assert seen_sources == [("sec",), ("sec",), ("sec",)]
        assert all(not run.change_scan_completed for run in runs)
        from stocks.changes import get_cursor

        assert get_cursor(session, task.id, task.run_scope) is None

        # Mutating the live Task query after the first page must not alter the
        # frozen scan continuation.
        changed_config = _make_task_config(
            tmp_path,
            queries=["different live query"],
            source_ids=["media"],
            change_page_limit=99,
        )
        changed_task = changed_config.tasks[0]
        final = create_or_get_run(
            session,
            changed_task,
            list(changed_config.agents),
            config_path=models_path,
        )
        assert claim_run(
            session,
            final.run.id,
            process_token=PROCESS_B,
        )
        completed = execute_claimed_run(
            ExecutionContext(
                session=session,
                settings=make_config(),
                config_path=str(models_path),
                model_transport=_mock_transport(),
            ),
            final.run.id,
            process_token=PROCESS_B,
            embed=_fake_embed,
        )
        assert completed.change_scan_completed is True
        assert completed.change_scan_progress["after_watermark"] == 5
        expected_scanned = sorted(str(value) for value in expected_documents)
        assert completed.change_scan_progress["scanned_document_ids"] == (
            expected_scanned
        )
        assert completed.change_scan_progress["query_snapshot"][
            "source_ids"
        ] == ["sec"]
        assert completed.change_scan_progress["query_snapshot"][
            "page_limit"
        ] == 1
        assert seen_sources[-1] == ("sec",)
        cursor = get_cursor(session, task.id, task.run_scope)
        assert cursor is not None
        assert cursor.watermark == completed.change_watermark_end


def test_fabricated_citation_fails_and_keeps_package_and_previous_report(
    engine, tmp_path
):
    config = _make_task_config(tmp_path)
    task = config.tasks[0]
    models_path = write_models_config(tmp_path)
    fabricated = json.dumps(
        {
            "summary": "invented",
            "findings": [
                {
                    "kind": "fact",
                    "statement": "an unsourced claim",
                    "rationale": "no selected evidence",
                    "uncertainty": "high",
                    "evidence_refs": [
                        {
                            "chunk_id": str(uuid4()),
                            "document_id": str(uuid4()),
                        }
                    ],
                }
            ],
            "open_questions": [],
            "evidence_refs": [
                {"chunk_id": str(uuid4()), "document_id": str(uuid4())}
            ],
        }
    )

    with Session(engine) as session:
        previous = create_or_get_run(session, task, list(config.agents), config_path=models_path)
        previous_id = previous.run.id
        previous.run.status = "succeeded"
        previous.run.process_token = None
        previous.run.output = {"summary": "last success"}
        previous.run.finished_at = datetime.now(UTC)
        session.commit()
        earlier_package = EvidencePackage(
            run_id=previous_id,
            objective=task.objective,
            scope=task.scope,
            as_of=previous.run.created_at,
            selection_policy_version="test",
            selection_budget={"max_tokens": 1000},
            queries=[],
            selected_chunks=[],
            coverage={"gaps": []},
            candidates_discovered=0,
            candidates_screened=0,
            selected_count=0,
            unexamined_count=0,
            retrieval_truncated=False,
        change_scan=None,
            created_at=previous.run.created_at,
        )
        session.add(earlier_package)
        session.flush()
        earlier_analysis = Analysis(
            run_id=previous_id,
            evidence_package_id=earlier_package.id,
            summary="earlier analysis",
            findings=[],
            open_questions=[],
            evidence_refs=[],
            coverage={"gaps": []},
            created_at=datetime.now(UTC),
        )
        session.add(earlier_analysis)
        session.flush()
        session.add(
            Report(
                run_id=previous_id,
                analysis_id=earlier_analysis.id,
                task_id=task.id,
                title="Earlier report",
                content="# Earlier report\n",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

        claimed = create_or_get_run(session, task, list(config.agents), config_path=models_path)
        assert claim_run(session, claimed.run.id, process_token=PROCESS_A)
        _add_saved_evidence(session)
        selected_chunks = [_selected_evidence_chunk()]

        def fake_search(session, settings, config_path, step, *, run, **kwargs):
            return _evidence_selection_result(
                session,
                run,
                selected_chunks=selected_chunks,
                gaps=[],
            )

        original_search = execution_service._run_evidence_selection
        execution_service._run_evidence_selection = fake_search
        try:
            completed = execute_claimed_run(
                ExecutionContext(
                    session=session,
                    settings=make_config(),
                    config_path=str(models_path),
                    model_transport=_mock_transport(fabricated, statuses=[200] * 4),
                ),
                claimed.run.id,
                process_token=PROCESS_A,
            )
        finally:
            execution_service._run_evidence_selection = original_search

        assert completed.status == "failed"
        assert completed.output is None
        step = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == completed.id,
                TaskStepResult.step_id == "research",
            )
        )
        assert step.status == "failed"
        assert step.error["code"] == "MODEL_RESPONSE_INVALID"
        # The Evidence Package stays reviewable and no report is written or swapped.
        package = session.scalar(
            select(EvidencePackage).where(EvidencePackage.run_id == completed.id)
        )
        assert package is not None
        assert package.selected_count == 1
        assert session.scalar(
            select(Analysis).where(Analysis.run_id == completed.id)
        ) is None
        assert session.scalar(
            select(Report).where(Report.run_id == completed.id)
        ) is None
        earlier = session.get(TaskRun, previous_id)
        assert earlier.output == {"summary": "last success"}
        assert session.scalar(select(Report).where(Report.run_id == previous_id)) is not None


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


def test_cli_trigger_returns_existing_active_run_without_execution(
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
            "trigger",
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["run_id"] == str(active_id)
    assert output["status"] == "running"
    assert output["created"] is False
    assert output["triggered_by"] == "manual"


def test_cli_trigger_does_not_require_model_secret_when_active_run_exists(
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
            "trigger",
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    # The active Run is reported before the Task configuration is read, so a
    # repeated trigger never resolves model credentials.
    assert code == 0
    assert output["run_id"] == str(active.run.id)
    assert output["status"] == "queued"


def test_cli_trigger_queues_run_without_executing(
    engine, tmp_path, cli_session_factory, capsys
):
    models_path = write_models_config(tmp_path)
    code = execution_cli.main(
        [
            "--config",
            write_config(tmp_path),
            "--models-config",
            models_path,
            "trigger",
            "--task",
            "brief",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["status"] == "queued"
    assert output["created"] is True
    assert output["triggered_by"] == "manual"
    assert output["steps"] == []
    with Session(engine) as session:
        run = session.get(TaskRun, UUID(output["run_id"]))
        assert run is not None
        assert run.process_token is None


def test_cli_reports_unknown_task_and_invalid_limit(
    engine, tmp_path, cli_session_factory, capsys
):
    code = execution_cli.main(
        [
            "--config",
            write_config(tmp_path),
            "--models-config",
            write_models_config(tmp_path),
            "trigger",
            "--task",
            "missing",
        ]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "TASK_NOT_FOUND"

    code = execution_cli.main(["runs", "--limit", "0"])
    assert code == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "LIMIT_INVALID"


def test_cli_end_requires_owner_confirmation_and_retry_reuses_steps(
    engine, tmp_path, cli_session_factory, capsys
):
    config = _make_task_config(tmp_path)
    models_path = write_models_config(tmp_path)
    with Session(engine) as session:
        created = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=models_path,
        )
        assert claim_run(session, created.run.id, process_token=PROCESS_A)
        run_id = created.run.id
        session.add(
            TaskStepResult(
                run_id=run_id,
                step_id="evidence",
                position=0,
                step_type="select_evidence",
                status="succeeded",
                output={"evidence_package_id": str(uuid4()), "items": []},
                evidence_refs=[],
                usage={},
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    code = execution_cli.main(["end", "--run-id", str(run_id)])
    assert code == 2
    assert (
        json.loads(capsys.readouterr().err)["error"]["code"]
        == "OWNER_CONFIRMATION_REQUIRED"
    )

    code = execution_cli.main(
        ["end", "--run-id", str(run_id), "--confirm-stopped"]
    )
    ended = json.loads(capsys.readouterr().out)
    assert code == 0
    assert ended["status"] == "cancelled"
    assert ended["error"]["code"] == "RUN_ENDED_BY_OWNER"
    assert [step["status"] for step in ended["steps"]] == ["succeeded"]

    code = execution_cli.main(["retry", "--run-id", str(run_id)])
    retried = json.loads(capsys.readouterr().out)
    assert code == 0
    assert retried["status"] == "queued"
    assert retried["triggered_by"] == "retry"
    assert retried["retry_of_run_id"] == str(run_id)
    assert [step["status"] for step in retried["steps"]] == ["succeeded"]

    with Session(engine) as session:
        old = session.get(TaskRun, run_id)
        assert old is not None
        assert old.status == "cancelled"
        assert old.error["code"] == "RUN_ENDED_BY_OWNER"
        reused = session.scalar(
            select(TaskStepResult).where(
                TaskStepResult.run_id == UUID(retried["run_id"])
            )
        )
        assert reused is not None


def test_cli_runs_and_show_report_status_and_errors(
    engine, tmp_path, cli_session_factory, capsys
):
    config = _make_task_config(tmp_path)
    models_path = write_models_config(tmp_path)
    with Session(engine) as session:
        created = create_or_get_run(
            session,
            config.tasks[0],
            list(config.agents),
            config_path=models_path,
        )
        assert claim_run(session, created.run.id, process_token=PROCESS_A)
        run = session.get(TaskRun, created.run.id)
        run.status = "failed"
        run.process_token = None
        run.error = {"code": "EVIDENCE_UNAVAILABLE", "message": "No evidence"}
        run.finished_at = datetime.now(UTC)
        session.commit()
        failed_id = run.id

    code = execution_cli.main(["runs", "--task", "brief", "--status", "failed"])
    listed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [item["run_id"] for item in listed] == [str(failed_id)]
    assert listed[0]["error"]["code"] == "EVIDENCE_UNAVAILABLE"

    code = execution_cli.main(["show", "--run-id", str(failed_id)])
    shown = json.loads(capsys.readouterr().out)
    assert code == 0
    assert shown["status"] == "failed"
    assert shown["error"]["code"] == "EVIDENCE_UNAVAILABLE"


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
        # The failed attempt reported no cost, so it stays unknown.
        "cost_usd": None,
        "cost_unknown_attempts": 0,
    }
