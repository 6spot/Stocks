from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
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
    server: Callable[[httpx.Request], httpx.Response],
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


class _ControlledStream(httpx.SyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        *,
        after_chunks: Exception | None = None,
        on_chunk: Callable[[int], None] | None = None,
    ) -> None:
        self.chunks = chunks
        self.after_chunks = after_chunks
        self.on_chunk = on_chunk
        self.reads = 0
        self.tail_read = False
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            if self.on_chunk is not None:
                self.on_chunk(self.reads)
            yield chunk
        self.tail_read = True
        if self.after_chunks is not None:
            raise self.after_chunks

    def close(self) -> None:
        self.closed = True


def _sse_client(
    stream: _ControlledStream,
    *,
    method: str = "tools/call",
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> McpClient:
    server = _MockServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["method"] == method:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream", **(headers or {})},
                stream=stream,
            )
        return server(request)

    return _client(handler, **kwargs)


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


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
@pytest.mark.parametrize("tail", ["open", "timeout", "disconnect"])
def test_sse_complete_response_returns_without_reading_tail(method, tail):
    result = {"content": [{"type": "text", "text": "complete"}]}
    if method == "initialize":
        message = _initialized_payload()
    elif method == "tools/list":
        message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": "search", "inputSchema": _input_schema()}]},
        }
    else:
        message = {"jsonrpc": "2.0", "id": 2, "result": result}
    payload = b"data: " + _json_body(message) + b"\n\n"
    # A sentinel represents a connection held open without blocking the test.
    # Either transport failure would break a client that requested another chunk.
    failures = {
        "open": AssertionError("response stream must not be read again"),
        "timeout": httpx.ReadTimeout("controlled timeout"),
        "disconnect": httpx.RemoteProtocolError("controlled disconnect"),
    }
    stream = _ControlledStream([payload], after_chunks=failures[tail])
    client = _sse_client(
        stream,
        method=method,
        headers={"Mcp-Session-Id": SESSION_ID},
    )

    with client:
        if method in {"initialize", "tools/list"}:
            assert [item.id for item in client.list_tools()] == ["search"]
        else:
            response = client.call_tool("search", {"query": "x"})
            assert response.result == result
            assert response.response_bytes == payload
            assert response.content_type == "text/event-stream"
        assert stream.closed is True

    assert stream.reads == 1
    assert stream.tail_read is False


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("chunk_size", [1, 7])
def test_sse_handles_split_utf8_lines_multiline_events_and_notifications(newline, chunk_size):
    result = {"content": [{"type": "text", "text": "营收📈"}]}
    lines = [
        b": keepalive",
        b"",
        b"event: message",
        b"id: ignored",
        b"retry: 1000",
        b'data: {"jsonrpc":"2.0","method":"notifications/progress"}',
        b"",
        b'data: {"jsonrpc":"2.0",',
        b'data: "id":2,"result":' + _json_body(result) + b"}",
        b"",
        b"",
    ]
    payload = newline.join(lines)
    chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
    stream = _ControlledStream(chunks, after_chunks=AssertionError("unexpected read"))

    with _sse_client(stream) as client:
        response = client.call_tool("search", {"query": "x"})

    assert response.result == result
    assert response.response_bytes == b"".join(chunks[:stream.reads])
    assert stream.tail_read is False
    assert stream.closed is True


def test_sse_mixed_line_endings_and_same_chunk_tail_are_not_parsed():
    payload = (
        b": keepalive\r\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\r\r"
        b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n'
        b"data: invalid trailing JSON\n\n\xff"
    )
    stream = _ControlledStream([payload], after_chunks=AssertionError("unexpected read"))

    with _sse_client(stream) as client:
        response = client.call_tool("search", {"query": "x"})

    assert response.result == {"content": []}
    assert response.response_bytes == payload
    assert stream.tail_read is False
    assert stream.closed is True


@pytest.mark.parametrize("suffix", [b"", b"\n", b"\r", b"\r\n"])
def test_sse_preserves_final_event_without_blank_separator_at_eof(suffix):
    payload = b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}' + suffix
    stream = _ControlledStream([payload])

    with _sse_client(stream) as client:
        response = client.call_tool("search", {"query": "x"})

    assert response.result == {"content": []}
    assert stream.tail_read is True
    assert stream.closed is True


@pytest.mark.parametrize(
    "data, code",
    [
        (b"data: {", "INVALID_JSON"),
        (b"data: \xff", "INVALID_SSE"),
        (b": \xff", "INVALID_SSE"),
        (b"unknown: field", "INVALID_SSE"),
        (b"data: []", "INVALID_JSON_RPC"),
        (b'data: {"jsonrpc":"1.0","method":"notification"}', "INVALID_JSON_RPC"),
        (b'data: {"jsonrpc":"1.0","id":2,"result":{}}', "INVALID_JSON_RPC"),
        (b'data: {"jsonrpc":"2.0","id":99,"result":{}}', "INVALID_RESPONSE_ID"),
        (b'data: {"jsonrpc":"2.0","id":true,"result":{}}', "INVALID_RESPONSE_ID"),
        (b'data: {"jsonrpc":"2.0","id":2}', "INVALID_JSON_RPC"),
        (b'data: {"jsonrpc":"2.0","id":2,"result":[]}', "INVALID_JSON_RPC"),
        (b'data: {"jsonrpc":"2.0","id":2,"result":{},"error":{}}', "INVALID_JSON_RPC"),
        (b'data: {"jsonrpc":"2.0","id":2,"error":{"code":-32603,"message":"secret"}}', "MCP_RPC_ERROR"),
    ],
)
def test_sse_rejects_invalid_events_before_a_later_matching_result(data, code):
    stream = _ControlledStream([
        data + b"\n\n",
        b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n',
    ])

    with _sse_client(stream) as client:
        with pytest.raises(McpProtocolError) as invalid:
            client.call_tool("search", {"query": "x"})

    assert invalid.value.code == code
    assert "secret" not in str(invalid.value)
    assert stream.reads == 1
    assert stream.closed is True


@pytest.mark.parametrize(
    "payload, code",
    [
        (b"", "MCP_RESPONSE_MISSING"),
        (b": keepalive\n\n", "MCP_RESPONSE_MISSING"),
        (b'data: {"jsonrpc":"2.0","method":"notification"}\n\n', "MCP_RESPONSE_MISSING"),
        (b"data: {", "INVALID_JSON"),
        (b"data: \xe8\x90", "INVALID_SSE"),
    ],
)
def test_sse_eof_without_a_complete_result_is_rejected(payload, code):
    stream = _ControlledStream([payload])

    with _sse_client(stream) as client:
        with pytest.raises(McpProtocolError) as invalid:
            client.call_tool("search", {"query": "x"})

    assert invalid.value.code == code
    assert stream.tail_read is True
    assert stream.closed is True


@pytest.mark.parametrize("limit", [2, 3])
def test_sse_event_limit_counts_empty_events(limit):
    payload = b'\n\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n'
    stream = _ControlledStream([payload])

    with _sse_client(stream, tool=_tool(max_sse_events=limit)) as client:
        if limit == 2:
            with pytest.raises(McpProtocolError) as invalid:
                client.call_tool("search", {"query": "x"})
            assert invalid.value.code == "TOO_MANY_SSE_EVENTS"
        else:
            assert client.call_tool("search", {"query": "x"}).result == {"content": []}

    assert stream.closed is True


@pytest.mark.parametrize(
    "payload, code",
    [
        (b": comment\n\n", "TOO_MANY_SSE_EVENTS"),
        (b"\n: trailing comment", "MCP_RESPONSE_MISSING"),
    ],
)
def test_sse_eof_preserves_empty_event_counting(payload, code):
    stream = _ControlledStream([payload])

    with _sse_client(stream, tool=_tool(max_sse_events=1)) as client:
        with pytest.raises(McpProtocolError) as invalid:
            client.call_tool("search", {"query": "x"})

    assert invalid.value.code == code
    assert stream.closed is True


@pytest.mark.parametrize("content_length", [None, "2048"])
def test_sse_response_byte_limit_checks_declared_and_cumulative_size(content_length):
    stream = _ControlledStream([
        b":" + b"x" * 600 + b"\n",
        b":" + b"y" * 600 + b"\n",
        b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n',
    ])
    tool = _tool(budgets={**_tool_payload()["budgets"], "max_result_bytes": 1024})
    headers = {"Content-Length": content_length} if content_length else None

    with _sse_client(stream, tool=tool, headers=headers) as client:
        with pytest.raises(McpTransportError) as oversized:
            client.call_tool("search", {"query": "x"})

    assert oversized.value.code == "TOOL_RESPONSE_TOO_LARGE"
    assert stream.reads == (0 if content_length else 2)
    assert stream.closed is True


def test_sse_byte_limit_includes_tail_already_received_with_result():
    payload = (
        b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n'
        + b"x" * 1024
    )
    stream = _ControlledStream([payload])
    tool = _tool(budgets={**_tool_payload()["budgets"], "max_result_bytes": 1024})

    with _sse_client(stream, tool=tool) as client:
        with pytest.raises(McpTransportError) as oversized:
            client.call_tool("search", {"query": "x"})

    assert oversized.value.code == "TOOL_RESPONSE_TOO_LARGE"
    assert stream.closed is True


@pytest.mark.parametrize("notification", [True, False])
def test_sse_json_depth_limit_applies_to_every_message(notification):
    nested: dict[str, Any] = {}
    for _ in range(14):
        nested = {"nested": nested}
    message = (
        {"jsonrpc": "2.0", "method": "notification", "params": nested}
        if notification
        else {"jsonrpc": "2.0", "id": 2, "result": nested}
    )
    payload = b"data: " + _json_body(message) + b"\n\n"
    stream = _ControlledStream([
        payload,
        b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n',
    ])

    with _sse_client(stream) as client:
        with pytest.raises(McpProtocolError) as invalid:
            client.call_tool("search", {"query": "x"})

    assert invalid.value.code == "JSON_DEPTH_EXCEEDED"
    assert stream.reads == 1
    assert stream.closed is True


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_sse_read_failure_before_result_remains_a_safe_transport_error(error_type):
    stream = _ControlledStream(
        [b": keepalive\n\n"],
        after_chunks=error_type("remote body secret"),
    )

    with _sse_client(stream) as client:
        with pytest.raises(McpTransportError) as failure:
            client.call_tool("search", {"query": "x"})

    assert failure.value.code == "RESPONSE_READ_FAILED"
    assert "remote body secret" not in str(failure.value)
    assert stream.tail_read is True
    assert stream.closed is True


def test_sse_deadline_is_checked_between_chunks_before_accepting_result():
    now = [0.0]

    def on_chunk(reads: int) -> None:
        if reads == 2:
            now[0] = 10.0

    stream = _ControlledStream([
        b": keepalive\n\n",
        b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n',
    ], on_chunk=on_chunk)

    with _sse_client(stream, clock=lambda: now[0]) as client:
        with pytest.raises(McpTransportError) as timeout:
            client.call_tool("search", {"query": "x"})

    assert timeout.value.code == "TIMEOUT"
    assert stream.reads == 2
    assert stream.closed is True


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
