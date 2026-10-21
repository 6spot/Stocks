from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from stocks.tools import (
    MCP_PROTOCOL_VERSION,
    McpClient,
    McpProtocolError,
    McpTransportError,
    ToolAuthorizationError,
    ToolBudgetError,
    ToolCallResult,
    ToolConfig,
    ToolConfiguration,
    ToolConfigurationError,
    authorize_tools,
    load_tool_configuration,
)

ENDPOINT = "https://mcp.example.test/rpc"
SESSION_ID = "session-1"
TOKEN = "test-mcp-token"
TOKEN_VARIABLE = "STOCKS_TEST_MCP_TOKEN"
PUBLIC_ADDRESS = "93.184.216.34"


def _input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1_000,
            }
        },
    }


def _tool_payload() -> dict[str, Any]:
    return {
        "id": "search",
        "enabled": True,
        "endpoint": ENDPOINT,
        "source_id": "tool_search_source",
        "bearer_token_env": TOKEN_VARIABLE,
        "timeout_seconds": 5,
        "max_json_depth": 12,
        "max_sse_events": 10,
        "input_schema": _input_schema(),
        "budgets": {
            "max_calls": 2,
            "max_tokens": 16_000,
            "max_request_bytes": 4_096,
            "max_result_bytes": 32_768,
        },
    }


def _tool(**overrides: Any) -> ToolConfig:
    payload = _tool_payload()
    payload.update(overrides)
    return ToolConfig.model_validate(payload)


def _configuration(tool: ToolConfig | None = None) -> ToolConfiguration:
    return ToolConfiguration(tools=[tool or _tool()])


def _json_body(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _response(
    payload: object | bytes,
    *,
    status: int = 200,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = payload if isinstance(payload, bytes) else _json_body(payload)
    response_headers = {"Content-Type": content_type}
    if headers:
        response_headers.update(headers)
    return httpx.Response(
        status,
        headers=response_headers,
        stream=httpx.ByteStream(body),
    )


def _public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


def _initialized_payload() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test-server", "version": "1"},
        },
    }


class _MockServer:
    def __init__(
        self,
        *,
        list_response: str = "json",
        call_result: dict[str, Any] | None = None,
        init_protocol: str = MCP_PROTOCOL_VERSION,
        fail_call_with: int | None = None,
        on_request: Any = None,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.list_response = list_response
        self.call_result = call_result or {
            "content": [{"type": "text", "text": "remote data"}],
            "structuredContent": {
                "title": "Remote result",
                "text": "remote data",
                "source_url": "https://example.test/article",
            },
        }
        self.init_protocol = init_protocol
        self.fail_call_with = fail_call_with
        self.on_request = on_request

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_id = body.get("id", 2)
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.requests.append({"body": body, "headers": headers})
        if self.on_request is not None:
            self.on_request(request)
        method = body.get("method")
        if method == "initialize":
            payload = _initialized_payload()
            payload["result"]["protocolVersion"] = self.init_protocol
            return _response(
                payload,
                headers={"Mcp-Session-Id": SESSION_ID},
            )
        if method == "notifications/initialized":
            return _response(b"", status=202)
        if method == "tools/list":
            if self.list_response == "sse":
                payload = (
                    b": keepalive\n\n"
                    b"event: message\n"
                    b"data: {\"jsonrpc\":\"2.0\",\"method\":"
                    b"\"notifications/tools/list_changed\"}\n\n"
                    b"data: {\"jsonrpc\":\"2.0\",\"id\":2,\"result\":"
                    b"{\"tools\":[{\"name\":\"search\","
                    b"\"description\":\"Search the web\","
                    b"\"inputSchema\":{\"type\":\"object\"}}]}}\n\n"
                )
                return _response(
                    payload,
                    content_type="text/event-stream",
                )
            return _response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search the web",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                }
            )
        if method == "tools/call":
            if self.fail_call_with is not None:
                return _response(
                    b"",
                    status=self.fail_call_with,
                )
            return _response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": self.call_result,
                }
            )
        raise AssertionError(f"unexpected method: {method}")


def _client(
    server: _MockServer,
    *,
    tool: ToolConfig | None = None,
    resolver: Any = _public_resolver,
    environment: dict[str, str] | None = None,
    clock: Any = None,
) -> McpClient:
    kwargs: dict[str, Any] = {
        "configuration": _configuration(tool),
        "authorized_tool_ids": ["search"],
        "resolver": resolver,
        "transport": httpx.MockTransport(server),
        "environment": (
            {TOKEN_VARIABLE: TOKEN}
            if environment is None
            else environment
        ),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return McpClient(**kwargs)


def test_default_tool_configuration_is_deny_by_default(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("tools: []\n", encoding="utf-8")

    configuration = load_tool_configuration(path)

    assert configuration.tools == []
    assert configuration.enabled_tools == ()
    assert authorize_tools(configuration, []) == ()


def test_tool_configuration_rejects_duplicate_yaml_keys(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("tools: []\ntools: []\n", encoding="utf-8")

    with pytest.raises(ToolConfigurationError) as invalid:
        load_tool_configuration(path)

    assert invalid.value.code == "CONFIG_INVALID"


def test_tool_configuration_rejects_duplicate_or_unsafe_entries():
    tool = _tool()
    with pytest.raises(ValidationError):
        ToolConfiguration(tools=[tool, tool])

    with pytest.raises(ValidationError):
        ToolConfiguration(
            tools=[
                tool,
                _tool(id="other", remote_name="search"),
            ]
        )

    with pytest.raises(ValidationError):
        ToolConfig.model_validate(
            {**_tool_payload(), "endpoint": "https://127.0.0.1/rpc"}
        )

    with pytest.raises(ValidationError):
        ToolConfig.model_validate(
            {**_tool_payload(), "authorization": "Bearer literal-secret"}
        )

    missing_source = _tool_payload()
    del missing_source["source_id"]
    with pytest.raises(ValidationError):
        ToolConfig.model_validate(missing_source)

    with pytest.raises(ValidationError):
        ToolConfig.model_validate({**_tool_payload(), "source_id": "not a source"})

    with pytest.raises(ValidationError):
        _tool(input_schema={"type": "object", "additionalProperties": True})


def test_authorization_is_explicit_and_schema_failure_never_contacts_server():
    server = _MockServer()
    client = _client(server)

    with pytest.raises(ToolAuthorizationError) as unknown:
        client.call_tool("shell", {"query": "x"})
    assert unknown.value.code == "TOOL_NOT_AUTHORIZED"

    with pytest.raises(McpProtocolError) as invalid:
        client.call_tool("search", {"query": "x", "command": "whoami"})
    assert invalid.value.code == "TOOL_ARGUMENTS_INVALID"

    assert server.requests == []

    disabled = _tool(enabled=False)
    with pytest.raises(ToolAuthorizationError) as disabled_error:
        authorize_tools(_configuration(disabled), ["search"])
    assert disabled_error.value.code == "TOOL_DISABLED"


def test_tool_call_budget_is_enforced_before_an_extra_request():
    tool = _tool(
        budgets={
            "max_calls": 1,
            "max_tokens": 16_000,
            "max_request_bytes": 4_096,
            "max_result_bytes": 32_768,
        }
    )
    server = _MockServer()
    client = _client(server, tool=tool)

    client.call_tool("search", {"query": "first"})
    with pytest.raises(ToolBudgetError) as exhausted:
        client.call_tool("search", {"query": "second"})

    assert exhausted.value.code == "TOOL_CALL_BUDGET_EXCEEDED"
    methods = [item["body"]["method"] for item in server.requests]
    assert methods.count("tools/call") == 1


def test_mcp_initialize_list_and_call_wire_sequence_and_headers():
    server = _MockServer()
    client = _client(server)

    with client:
        discovered = client.list_tools()
        result = client.call_tool("search", {"query": "inflation"})

    assert [item.id for item in discovered] == ["search"]
    assert discovered[0].remote_name == "search"
    assert isinstance(result, ToolCallResult)
    assert result.tool_id == "search"
    assert result.is_error is False
    assert result.result["structuredContent"]["title"] == "Remote result"
    assert result.content_type == "application/json"

    methods = [item["body"]["method"] for item in server.requests]
    assert methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    initialize, initialized, listing, call = server.requests
    assert initialize["body"]["params"]["protocolVersion"] == (
        MCP_PROTOCOL_VERSION
    )
    assert "mcp-protocol-version" not in initialize["headers"]
    assert "mcp-session-id" not in initialize["headers"]
    assert initialize["headers"]["authorization"] == (
        f"Bearer {TOKEN}"
    )
    assert initialized["body"]["method"] == "notifications/initialized"
    assert initialized["headers"]["mcp-protocol-version"] == (
        MCP_PROTOCOL_VERSION
    )
    assert initialized["headers"]["mcp-session-id"] == SESSION_ID
    assert listing["headers"]["mcp-session-id"] == SESSION_ID
    assert call["body"]["params"]["name"] == "search"
    assert call["body"]["params"]["arguments"] == {"query": "inflation"}


def test_sse_response_accepts_notifications_and_finds_matching_response():
    server = _MockServer(list_response="sse")
    client = _client(server)

    discovered = client.list_tools()

    assert [item.id for item in discovered] == ["search"]


def test_result_mapping_and_output_schema_validate_mapped_data():
    tool = _tool(
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "text"],
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string"},
            },
        },
        result_mapping={
            "fields": {
                "title": ["structuredContent", "title"],
                "text": ["structuredContent", "text"],
            }
        },
    )
    server = _MockServer()
    client = _client(server, tool=tool)

    result = client.call_tool("search", {"query": "inflation"})

    assert result.value == {
        "title": "Remote result",
        "text": "remote data",
    }


def test_tool_error_results_do_not_become_usable_results():
    tool = _tool()
    server = _MockServer(
        call_result={
            "content": [{"type": "text", "text": "failure detail"}],
            "isError": True,
        }
    )
    client = _client(server, tool=tool)

    with pytest.raises(McpProtocolError) as failure:
        client.call_tool("search", {"query": "inflation"})

    assert failure.value.code == "TOOL_CALL_FAILED"
    assert "failure detail" not in str(failure.value)


def test_redirect_status_is_rejected_without_following():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _response(
            b"",
            status=307,
            content_type="text/plain",
            headers={"Location": "https://elsewhere.example/rpc"},
        )

    client = McpClient(
        _configuration(),
        ["search"],
        resolver=_public_resolver,
        transport=httpx.MockTransport(handler),
        environment={TOKEN_VARIABLE: TOKEN},
    )

    with pytest.raises(McpTransportError) as redirect:
        client.list_tools()

    assert redirect.value.code == "MCP_REDIRECT_REJECTED"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "addresses",
    [
        ["10.0.0.1"],
        [PUBLIC_ADDRESS, "127.0.0.1"],
        ["::1"],
    ],
)
def test_dns_rejects_private_and_mixed_answers_before_transport(addresses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _response(_initialized_payload())

    client = McpClient(
        _configuration(),
        ["search"],
        resolver=lambda host, port: addresses,
        transport=httpx.MockTransport(handler),
        environment={TOKEN_VARIABLE: TOKEN},
    )

    with pytest.raises(McpTransportError) as unsafe:
        client.list_tools()

    assert unsafe.value.code == "UNSAFE_ADDRESS"
    assert calls == []


def test_credentials_are_required_from_environment_before_request():
    server = _MockServer()
    client = _client(server, environment={})

    with pytest.raises(McpTransportError) as missing:
        client.list_tools()

    assert missing.value.code == "TOOL_CREDENTIAL_MISSING"
    assert server.requests == []


def test_response_size_json_depth_and_request_size_are_strict():
    server = _MockServer()
    client = _client(
        server,
        tool=_tool(
            budgets={
                "max_calls": 2,
                "max_tokens": 16_000,
                "max_request_bytes": 256,
                "max_result_bytes": 1_024,
            }
        ),
    )

    with pytest.raises(ToolBudgetError) as request_size:
        client.call_tool("search", {"query": "x" * 300})

    assert request_size.value.code == "TOOL_REQUEST_TOO_LARGE"
    assert [item["body"]["method"] for item in server.requests] == [
        "initialize",
        "notifications/initialized",
    ]

    def deep_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return _response(
                _initialized_payload(),
                headers={"Mcp-Session-Id": SESSION_ID},
            )
        if body.get("method") == "notifications/initialized":
            return _response(b"", status=202)
        # Deeper than the configured max_json_depth, so the whole response
        # must be rejected while it is parsed.
        nested: dict[str, Any] = {"leaf": 1}
        for _ in range(14):
            nested = {"nested": nested}
        return _response(
            {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {"tools": [], "serverInfo": nested},
            }
        )

    deep_client = McpClient(
        _configuration(),
        ["search"],
        resolver=_public_resolver,
        transport=httpx.MockTransport(deep_handler),
        environment={TOKEN_VARIABLE: TOKEN},
    )
    with pytest.raises(McpProtocolError) as depth:
        deep_client.list_tools()
    assert depth.value.code == "JSON_DEPTH_EXCEEDED"

    large_client = McpClient(
        _configuration(
            _tool(
                budgets={
                    "max_calls": 1,
                    "max_tokens": 16_000,
                    "max_request_bytes": 4_096,
                    "max_result_bytes": 1_024,
                }
            )
        ),
        ["search"],
        resolver=_public_resolver,
        transport=httpx.MockTransport(
            lambda request: _response(b"x" * 2_000)
        ),
        environment={TOKEN_VARIABLE: TOKEN},
    )
    with pytest.raises(McpTransportError) as oversized:
        large_client.list_tools()
    assert oversized.value.code == "TOOL_RESPONSE_TOO_LARGE"


def test_timeout_is_checked_while_reading_the_response():
    now = [0.0]

    def clock() -> float:
        return now[0]

    def handler(request: httpx.Request) -> httpx.Response:
        now[0] = 10.0
        return _response(_initialized_payload())

    client = McpClient(
        _configuration(_tool(timeout_seconds=1)),
        ["search"],
        resolver=_public_resolver,
        transport=httpx.MockTransport(handler),
        environment={TOKEN_VARIABLE: TOKEN},
        clock=clock,
    )

    with pytest.raises(McpTransportError) as timeout:
        client.list_tools()

    assert timeout.value.code == "TIMEOUT"


def test_session_404_restarts_initialize_without_double_counting_call():
    sessions: list[str] = []
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        body = json.loads(request.content)
        method = body["method"]
        if method == "initialize":
            session = f"session-{len(sessions) + 1}"
            sessions.append(session)
            payload = _initialized_payload()
            payload["id"] = body["id"]
            return _response(
                payload,
                headers={"Mcp-Session-Id": session},
            )
        if method == "notifications/initialized":
            return _response(b"", status=202)
        if method == "tools/call":
            call_count += 1
            if request.headers["mcp-session-id"] == "session-1":
                return _response(b"", status=404)
            return _response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "content": [{"type": "text", "text": "ok"}],
                    },
                }
            )
        raise AssertionError(method)

    client = McpClient(
        _configuration(_tool(budgets={
            "max_calls": 1,
            "max_tokens": 16_000,
            "max_request_bytes": 4_096,
            "max_result_bytes": 32_768,
        })),
        ["search"],
        resolver=_public_resolver,
        transport=httpx.MockTransport(handler),
        environment={TOKEN_VARIABLE: TOKEN},
    )

    result = client.call_tool("search", {"query": "x"})

    assert result.result["content"][0]["text"] == "ok"
    assert sessions == ["session-1", "session-2"]
    assert call_count == 2


def test_protocol_mismatch_is_rejected():
    client = _client(
        _MockServer(init_protocol="2025-03-26"),
    )

    with pytest.raises(McpProtocolError) as mismatch:
        client.list_tools()

    assert mismatch.value.code == "PROTOCOL_VERSION_MISMATCH"


def test_transport_errors_do_not_log_endpoint_headers_or_body(caplog):
    caplog.set_level(logging.DEBUG)
    body_secret = "body-secret"
    client = _client(
        _MockServer(
            call_result={
                "content": [
                    {
                        "type": "text",
                        "text": body_secret,
                    }
                ],
                "isError": True,
            }
        )
    )

    with pytest.raises(McpProtocolError) as error:
        client.call_tool("search", {"query": "x"})

    rendered = str(error.value) + json.dumps(error.value.details)
    assert ENDPOINT not in rendered
    assert TOKEN not in rendered
    assert body_secret not in rendered
    records = " ".join(record.getMessage() for record in caplog.records)
    assert ENDPOINT not in records
    assert TOKEN not in records
    assert body_secret not in records
