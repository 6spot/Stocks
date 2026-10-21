from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from stocks.models import (
    Base,
    Document,
    DocumentChunk,
    EvidencePackage,
    TaskDefinition,
    TaskRun,
)
from stocks.processing.processors import StageRegistry, build_parse_processor
from stocks.research.service import (
    LEDGER_KIND_GAP,
    LEDGER_KIND_INPUT,
    LEDGER_KIND_SELECTION,
    evidence_package_hash,
    merged_research_refs,
)
from stocks.research.supplement import (
    GAP_TOOL_BUDGET_EXHAUSTED,
    GAP_TOOL_CALL_DUPLICATE,
    GAP_TOOL_CONTENT_UNAVAILABLE,
    GAP_TOOL_TIMEOUT,
    GAP_TOOL_TRANSPORT_FAILED,
    SupplementBudget,
    ToolRuntime,
    execute_plan,
)
from stocks.settings import Settings
from stocks.tools.client import ToolCallResult
from stocks.tools.config import ToolConfig
from stocks.tools.errors import McpTransportError

PROCESS_TOKEN = "process-supplement"
SOURCE_ID = "tool_search_source"
TOOL_ID = "research_search"
CREATED_AT = datetime(2026, 10, 21, 9, tzinfo=UTC)


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
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL tests")
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
def snapshot_store():
    """Stand in for S3: keep exact bytes and return their SHA-256."""

    objects: dict[str, bytes] = {}

    def store(config, source_id, payload, *, content_type):
        digest = sha256(payload).hexdigest()
        key = f"sources/{source_id}/{digest}"
        objects[key] = payload
        return key, digest

    def read(config, key):
        return objects[key]

    return objects, store, read


def write_sources_config(tmp_path) -> str:
    path = tmp_path / "sources.yaml"
    path.write_text(
        f"""
sources:
  - source_id: {SOURCE_ID}
    name: Retrieved research material
    connector: rss
    enabled: true
    publisher: Retrieved Publisher
    trust_tier: T3
    feed_url: https://www.example.gov/feed.xml
    timeout_seconds: 30
    max_response_bytes: 5000000
    max_redirects: 3
    max_items: 20
    user_agent: StocksIntelligence/0.1 (owner@example.com)
""",
        encoding="utf-8",
    )
    return str(path)


def _tool(**overrides) -> ToolConfig:
    payload = {
        "id": TOOL_ID,
        "enabled": True,
        "endpoint": "https://mcp.example.test/rpc",
        "source_id": SOURCE_ID,
        "timeout_seconds": 5,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200}
            },
        },
        "budgets": {
            "max_calls": 2,
            "max_tokens": 16_000,
            "max_request_bytes": 4_096,
            "max_result_bytes": 32_768,
        },
    }
    payload.update(overrides)
    return ToolConfig.model_validate(payload)


class _FakeToolClient:
    def __init__(self, *, result=None, error=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._result = result
        self._error = error
        self.closed = False

    def call_tool(self, tool_id: str, arguments: dict):
        self.calls.append((tool_id, dict(arguments)))
        if self._error is not None:
            raise self._error
        return self._result

    def close(self) -> None:
        self.closed = True


def _result(value: dict) -> ToolCallResult:
    return ToolCallResult(
        tool_id=TOOL_ID,
        result={"content": [{"type": "text", "text": json.dumps(value)}]},
        value=value,
        is_error=False,
        response_bytes=b"{}",
        content_type="application/json",
    )


def _make_run_and_package(
    session: Session, *, task_id: str = "brief"
) -> tuple[TaskRun, EvidencePackage]:
    session.add(
        TaskDefinition(
            id=task_id,
            name="Brief",
            type="research",
            trigger="manual",
            enabled=True,
            scope={"topics": ["macro"]},
            config_snapshot={},
            config_hash="a" * 64,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
    )
    run = TaskRun(
        task_id=task_id,
        status="running",
        process_token=PROCESS_TOKEN,
        objective="Summarize current evidence.",
        strategy=None,
        scope={"topics": ["macro"]},
        config_snapshot={},
        model_snapshot={},
        prompt_snapshot={},
        config_hash="a" * 64,
        created_at=CREATED_AT,
        started_at=CREATED_AT,
    )
    session.add(run)
    session.flush()
    package = EvidencePackage(
        run_id=run.id,
        objective=run.objective,
        scope=run.scope,
        as_of=CREATED_AT,
        selection_policy_version="test",
        selection_budget={"max_tokens": 1000},
        queries=[],
        selected_chunks=[],
        coverage={"gaps": []},
        candidates_discovered=None,
        candidates_screened=0,
        selected_count=0,
        unexamined_count=None,
        retrieval_truncated=False,
        created_at=CREATED_AT,
    )
    session.add(package)
    session.commit()
    return run, package


def _plan(*requests: dict) -> dict:
    return {
        "assessment": "Saved evidence is missing the requested release.",
        "requests": list(requests),
    }


def _request(query: str = "cpi release") -> dict:
    return {
        "tool_id": TOOL_ID,
        "purpose": "Fetch the missing release.",
        "arguments": {"query": query},
    }


def _runtime(engine, snapshot_store, client, tool: ToolConfig) -> ToolRuntime:
    _objects, _store, read = snapshot_store
    registry = StageRegistry(
        parse=build_parse_processor(
            read_bytes=lambda config, document: read(
                config, document.snapshot_key
            )
        )
    )
    return ToolRuntime(
        client=client,
        tools={tool.id: tool},
        session_factory=lambda: Session(engine),
        stage_registry=registry,
        snapshot_store=_store,
        owner_token="supplement-owner",
    )


def test_tool_content_is_ingested_and_appended_before_it_can_be_cited(
    engine, tmp_path, snapshot_store
):
    objects, _store, _read = snapshot_store
    sources_path = write_sources_config(tmp_path)
    content = (
        "<html><body><p>Consumer prices rose 2.4 percent in September "
        "2026.</p></body></html>"
    )
    value = {
        "title": "CPI release",
        "url": "https://www.example.gov/releases/cpi-2026-09",
        "content": content,
        "content_type": "text/html; charset=utf-8",
        "publisher": "Bureau of Labor Statistics",
        "published_at": "2026-10-01T12:30:00Z",
        "source_updated_at": "2026-10-02T00:00:00Z",
        "summary": "September CPI release",
    }
    client = _FakeToolClient(result=_result(value))
    tool = _tool()
    with Session(engine) as session:
        run, package = _make_run_and_package(session)
        runtime = _runtime(engine, snapshot_store, client, tool)
        package_hash = evidence_package_hash(package)
        outcome = execute_plan(
            session,
            make_config(),
            run,
            package,
            _plan(_request()),
            runtime=runtime,
            budget=SupplementBudget(),
            sources_config_path=sources_path,
            as_of=package.as_of,
            now=datetime.now(UTC),
            round_index=1,
            process_token=PROCESS_TOKEN,
        )

        assert outcome.appended and len(outcome.appended) == 1
        assert client.calls == [(TOOL_ID, {"query": "cpi release"})]
        # The exact response bytes are what got registered, under the Tool's
        # configured Source. Nothing is cited that is not in that store.
        assert list(objects.values()) == [content.encode("utf-8")]
        session.expire_all()
        document = session.scalar(select(Document))
        assert document is not None
        assert document.source_id == SOURCE_ID
        assert document.original_url == value["url"]
        assert document.content_hash == sha256(content.encode("utf-8")).hexdigest()
        assert document.version == 1
        assert document.published_at == datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
        assert document.source_updated_at == datetime(2026, 10, 2, tzinfo=UTC)
        assert document.summary == "September CPI release"
        chunks = session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        ).all()
        assert chunks and "2.4 percent" in chunks[0].text
        # Only after the Document version and its current Chunk exist is the
        # input appended, and the package boundary comes first.
        ledger = run.research_inputs
        assert [entry["kind"] for entry in ledger[:2]] == [
            LEDGER_KIND_SELECTION,
            LEDGER_KIND_INPUT,
        ]
        # The parse-only registry in this test cannot classify or extract
        # Facts, so those stages stay visible as bounded gaps next to the
        # adopted input instead of blocking it.
        assert [
            entry["code"] for entry in ledger[2:]
        ] == [
            "supplement_classify_unavailable",
            "supplement_facts_unavailable",
        ]
        assert ledger[0]["evidence_package_hash"] == package_hash
        assert ledger[0]["evidence_package_id"] == str(package.id)
        adopted = ledger[1]
        assert adopted["input_id"] == outcome.appended[0]
        assert adopted["document_id"] == str(document.id)
        assert adopted["document_version"] == 1
        assert adopted["tool_id"] == TOOL_ID
        assert adopted["url"] == value["url"]
        assert adopted["chunks"]
        assert adopted["chunks"][0]["chunk_id"] == str(chunks[0].id)
        refs = merged_research_refs(package, ledger)
        assert [ref["origin"] for ref in refs] == ["research_input"]
        assert refs[0]["chunk_id"] == str(chunks[0].id)
        assert refs[0]["source_id"] == SOURCE_ID
        # The initial selection snapshot is never rewritten by an append.
        assert package.selected_chunks == []
        assert package.selected_count == 0


def test_summary_only_result_is_a_gap_and_cannot_be_cited(
    engine, tmp_path, snapshot_store
):
    sources_path = write_sources_config(tmp_path)
    value = {
        "title": "CPI release",
        "url": "https://www.example.gov/releases/cpi-2026-09",
        "summary": "Consumer prices rose 2.4 percent last month.",
    }
    client = _FakeToolClient(result=_result(value))
    tool = _tool()
    with Session(engine) as session:
        run, package = _make_run_and_package(session)
        runtime = _runtime(engine, snapshot_store, client, tool)
        outcome = execute_plan(
            session,
            make_config(),
            run,
            package,
            _plan(_request()),
            runtime=runtime,
            budget=SupplementBudget(),
            sources_config_path=sources_path,
            as_of=package.as_of,
            now=datetime.now(UTC),
            round_index=1,
            process_token=PROCESS_TOKEN,
        )

        assert outcome.appended == ()
        assert outcome.gaps == (GAP_TOOL_CONTENT_UNAVAILABLE,)
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        ledger = run.research_inputs
        assert [entry["kind"] for entry in ledger] == [
            LEDGER_KIND_SELECTION,
            LEDGER_KIND_GAP,
        ]
        assert ledger[1]["code"] == GAP_TOOL_CONTENT_UNAVAILABLE
        assert merged_research_refs(package, ledger) == []


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (McpTransportError("TIMEOUT", "no response"), GAP_TOOL_TIMEOUT),
        (
            McpTransportError("CONNECT_FAILED", "no route"),
            GAP_TOOL_TRANSPORT_FAILED,
        ),
    ],
)
def test_tool_failure_is_one_bounded_attempt_with_a_named_gap(
    engine, tmp_path, snapshot_store, error, expected_code
):
    sources_path = write_sources_config(tmp_path)
    client = _FakeToolClient(error=error)
    tool = _tool()
    with Session(engine) as session:
        run, package = _make_run_and_package(session)
        runtime = _runtime(engine, snapshot_store, client, tool)
        outcome = execute_plan(
            session,
            make_config(),
            run,
            package,
            _plan(_request()),
            runtime=runtime,
            budget=SupplementBudget(),
            sources_config_path=sources_path,
            as_of=package.as_of,
            now=datetime.now(UTC),
            round_index=1,
            process_token=PROCESS_TOKEN,
        )

        assert outcome.appended == ()
        assert outcome.gaps == (expected_code,)
        assert len(client.calls) == 1
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert run.research_inputs[-1]["code"] == expected_code


def test_duplicate_and_exhausted_requests_never_reach_the_client(
    engine, tmp_path, snapshot_store
):
    sources_path = write_sources_config(tmp_path)
    value = {
        "title": "CPI release",
        "url": "https://www.example.gov/releases/cpi-2026-09",
        "content": "Consumer prices rose 2.4 percent in September 2026.",
        "content_type": "text/plain",
    }
    client = _FakeToolClient(result=_result(value))
    tool = _tool()
    with Session(engine) as session:
        run, package = _make_run_and_package(session)
        runtime = _runtime(engine, snapshot_store, client, tool)
        outcome = execute_plan(
            session,
            make_config(),
            run,
            package,
            _plan(_request(), _request()),
            runtime=runtime,
            budget=SupplementBudget(),
            sources_config_path=sources_path,
            as_of=package.as_of,
            now=datetime.now(UTC),
            round_index=1,
            process_token=PROCESS_TOKEN,
        )

        assert len(client.calls) == 1
        assert len(outcome.appended) == 1
        assert outcome.gaps.count(GAP_TOOL_CALL_DUPLICATE) == 1

    exhausted_client = _FakeToolClient(result=_result(value))
    with Session(engine) as session:
        run, package = _make_run_and_package(session, task_id="brief-exhausted")
        runtime = _runtime(engine, snapshot_store, exhausted_client, tool)
        outcome = execute_plan(
            session,
            make_config(),
            run,
            package,
            _plan(_request()),
            runtime=runtime,
            budget=SupplementBudget(tokens={TOOL_ID: tool.budgets.max_tokens}),
            sources_config_path=sources_path,
            as_of=package.as_of,
            now=datetime.now(UTC),
            round_index=1,
            process_token=PROCESS_TOKEN,
        )

        assert exhausted_client.calls == []
        assert outcome.appended == ()
        assert outcome.gaps == (GAP_TOOL_BUDGET_EXHAUSTED,)
