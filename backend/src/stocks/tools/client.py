from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult

import httpcore
import httpx

from stocks.collection.errors import CollectionError
from stocks.collection.urls import (
    validate_https_url,
    validate_resolved_addresses,
)
from stocks.tools.config import (
    MCP_PROTOCOL_VERSION,
    ToolConfig,
    ToolConfiguration,
    validate_json_value,
    authorize_tools,
    validate_json_schema_value,
)
from stocks.tools.errors import (
    McpProtocolError,
    McpTransportError,
    ToolAuthorizationError,
    ToolBudgetError,
    ToolError,
)

REDIRECT_STATUSES = frozenset(range(300, 400))
JSON_CONTENT_TYPE = "application/json"
SSE_CONTENT_TYPE = "text/event-stream"
MAX_SESSION_ID_LENGTH = 1_024
MAX_LIST_PAGES = 20
MAX_DISCOVERED_TOOLS = 200

Resolver = Callable[[str, int], list[str]]
Environment = Mapping[str, str]


@dataclass(frozen=True)
class DiscoveredTool:
    id: str
    remote_name: str
    description: str | None
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResult:
    tool_id: str
    result: dict[str, Any]
    value: object
    is_error: bool
    response_bytes: bytes
    content_type: str


@dataclass(frozen=True)
class _RpcResponse:
    result: dict[str, Any]
    response_bytes: bytes
    content_type: str
    session_id: str | None


def resolve_host(host: str, port: int) -> list[str]:
    import socket

    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise CollectionError(
            "DNS_FAILED",
            "Host did not resolve",
            url=f"https://{host}/",
        ) from None
    return list(dict.fromkeys(record[4][0] for record in records))


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise McpProtocolError(
            "INVALID_JSON",
            "MCP message must contain finite JSON-compatible data",
        ) from None


def _reject_duplicate_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _parse_json(payload: bytes, *, max_depth: int) -> object:
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        raise McpProtocolError(
            "INVALID_JSON",
            "MCP response is not valid bounded JSON",
        ) from None
    try:
        validate_json_value(
            value,
            path="response",
            max_depth=max_depth,
        )
    except ValueError:
        raise McpProtocolError(
            "JSON_DEPTH_EXCEEDED",
            "MCP response exceeds the configured JSON depth",
        ) from None
    return value


def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _valid_session_id(value: str) -> str:
    if (
        not value
        or len(value) > MAX_SESSION_ID_LENGTH
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value)
    ):
        raise McpProtocolError(
            "INVALID_SESSION_ID",
            "MCP server returned an invalid session id",
        )
    return value


def _path_value(value: object, path: Sequence[str | int]) -> object:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                raise ToolError(
                    "TOOL_RESULT_MAPPING_FAILED",
                    "Configured tool result path does not exist",
                )
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                raise ToolError(
                    "TOOL_RESULT_MAPPING_FAILED",
                    "Configured tool result path does not exist",
                )
            current = current[part]
    return current


def _map_result(tool: ToolConfig, result: dict[str, Any]) -> object:
    mapping = tool.result_mapping
    if mapping.output_path is not None:
        return _path_value(result, mapping.output_path)
    if mapping.fields:
        return {
            key: _path_value(result, path)
            for key, path in mapping.fields.items()
        }
    return result


class _PinnedNetworkBackend(httpcore.SyncBackend):
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.backend = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        try:
            addresses = self.resolver(host, port)
            validate_resolved_addresses(f"https://{host}:{port}/", addresses)
        except CollectionError as exc:
            code = (
                "DNS_FAILED"
                if exc.code == "DNS_FAILED"
                else "UNSAFE_ADDRESS"
            )
            raise McpTransportError(
                code,
                "MCP host did not resolve exclusively to public addresses",
            ) from None
        last_error: Exception | None = None
        for address in addresses:
            try:
                return self.backend.connect_tcp(
                    f"[{address}]" if ":" in address else address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise McpTransportError(
                "REQUEST_FAILED",
                "MCP connection could not be established",
            ) from None
        raise McpTransportError(
            "DNS_FAILED",
            "MCP host did not resolve",
        )

    def connect_unix_socket(self, path: str, timeout: float | None = None):
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        self.backend.sleep(seconds)


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self, resolver: Resolver) -> None:
        super().__init__()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            network_backend=_PinnedNetworkBackend(resolver),
        )


class _EndpointClient:
    def __init__(
        self,
        tool: ToolConfig,
        *,
        resolver: Resolver,
        transport: httpx.BaseTransport | None,
        environment: Environment,
        clock: Callable[[], float],
    ) -> None:
        self.tool = tool
        self.resolver = resolver
        self.transport = transport
        self.environment = environment
        self.clock = clock
        self.session_id: str | None = None
        self.initialized = False
        self._next_request_id = 1
        self._http_client: httpx.Client | None = None

    def operation_deadline(self) -> float:
        return self.clock() + self.tool.timeout_seconds

    def initialize(self, deadline: float) -> None:
        if self.initialized:
            return
        self.session_id = None
        request_id = self._request_id()
        response = self._rpc(
            request_id=request_id,
            method="initialize",
            params={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "Stocks",
                    "title": "Stocks research system",
                    "version": "0.1.0",
                },
            },
            deadline=deadline,
            include_version=False,
            include_session=False,
        )
        result = response.result
        if result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise McpProtocolError(
                "PROTOCOL_VERSION_MISMATCH",
                "MCP server did not negotiate protocol 2025-11-25",
            )
        if not isinstance(result.get("capabilities"), dict):
            raise McpProtocolError(
                "INVALID_INITIALIZE_RESULT",
                "MCP initialize result has invalid capabilities",
            )
        if not isinstance(result.get("serverInfo"), dict):
            raise McpProtocolError(
                "INVALID_INITIALIZE_RESULT",
                "MCP initialize result has invalid server info",
            )
        self.session_id = response.session_id
        self._notify(
            method="notifications/initialized",
            deadline=deadline,
        )
        self.initialized = True

    def list_tools(self, deadline: float) -> tuple[dict[str, Any], ...]:
        self.initialize(deadline)
        discovered: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_LIST_PAGES):
            params = {"cursor": cursor} if cursor is not None else {}
            response = self._rpc(
                request_id=self._request_id(),
                method="tools/list",
                params=params,
                deadline=deadline,
            )
            result = response.result
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                raise McpProtocolError(
                    "INVALID_TOOLS_LIST",
                    "MCP tools/list result has no tools list",
                )
            if len(discovered) + len(raw_tools) > MAX_DISCOVERED_TOOLS:
                raise McpProtocolError(
                    "TOOL_LIST_TOO_LARGE",
                    "MCP tool discovery exceeds the configured limit",
                )
            for raw_tool in raw_tools:
                discovered.append(
                    self._validate_discovered_tool(raw_tool)
                )
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return tuple(discovered)
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > 2_000
                or next_cursor in seen_cursors
            ):
                raise McpProtocolError(
                    "INVALID_TOOL_CURSOR",
                    "MCP tools/list returned an invalid cursor",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise McpProtocolError(
            "TOOL_LIST_PAGINATION_EXCEEDED",
            "MCP tool discovery exceeded the page limit",
        )

    def call_tool(
        self,
        arguments: Mapping[str, object],
        deadline: float,
    ) -> _RpcResponse:
        if not isinstance(arguments, Mapping):
            raise McpProtocolError(
                "TOOL_ARGUMENTS_INVALID",
                "Tool arguments must be an object",
            )
        self.initialize(deadline)
        return self._rpc(
            request_id=self._request_id(),
            method="tools/call",
            params={
                "name": self.tool.mcp_name,
                "arguments": dict(arguments),
            },
            deadline=deadline,
        )

    def reset_session(self) -> None:
        self.session_id = None
        self.initialized = False

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def _request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _notify(self, *, method: str, deadline: float) -> None:
        message = {
            "jsonrpc": "2.0",
            "method": method,
        }
        body = _canonical_json(message)
        self._validate_request_body(body)
        headers = self._headers(
            include_version=True,
            include_session=True,
        )
        response = self._post(
            body=body,
            headers=headers,
            deadline=deadline,
        )
        try:
            if response.status_code in REDIRECT_STATUSES:
                raise McpTransportError(
                    "MCP_REDIRECT_REJECTED",
                    "MCP redirects are not allowed",
                    details={"status": response.status_code},
                )
            # Streamable HTTP servers answer a notification with 202 and no
            # body; accept any other successful status and ignore its body
            # instead of failing an otherwise healthy session.
            if not 200 <= response.status_code < 300:
                raise McpTransportError(
                    "MCP_NOTIFICATION_REJECTED",
                    "MCP server rejected the initialized notification",
                    details={"status": response.status_code},
                )
        finally:
            response.close()

    def _rpc(
        self,
        *,
        request_id: int,
        method: str,
        params: dict[str, Any],
        deadline: float,
        include_version: bool = True,
        include_session: bool = True,
    ) -> _RpcResponse:
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        body = _canonical_json(message)
        self._validate_request_body(body)
        headers = self._headers(
            include_version=include_version,
            include_session=include_session,
        )
        response = self._post(
            body=body,
            headers=headers,
            deadline=deadline,
        )
        try:
            if response.status_code in REDIRECT_STATUSES:
                raise McpTransportError(
                    "MCP_REDIRECT_REJECTED",
                    "MCP redirects are not allowed",
                    details={"status": response.status_code},
                )
            if response.status_code != 200:
                raise McpTransportError(
                    "MCP_HTTP_STATUS",
                    "MCP server returned an unsuccessful status",
                    details={"status": response.status_code},
                )
            content_type = _media_type(
                response.headers.get("Content-Type", "")
            )
            if content_type == JSON_CONTENT_TYPE:
                payload = self._read_body(response, deadline=deadline)
                parsed = _parse_json(
                    payload,
                    max_depth=self.tool.max_json_depth,
                )
                result = self._response_result(
                    parsed,
                    request_id=request_id,
                )
            elif content_type == SSE_CONTENT_TYPE:
                payload, result = self._read_sse(
                    response,
                    request_id=request_id,
                    deadline=deadline,
                )
            else:
                raise McpProtocolError(
                    "UNSUPPORTED_RESPONSE_TYPE",
                    "MCP response type must be JSON or SSE",
                )
            session_id = response.headers.get("MCP-Session-Id")
            if session_id is not None:
                session_id = _valid_session_id(session_id)
            return _RpcResponse(
                result=result,
                response_bytes=payload,
                content_type=content_type,
                session_id=session_id,
            )
        finally:
            response.close()

    def _post(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        deadline: float,
    ) -> httpx.Response:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise McpTransportError(
                "TIMEOUT",
                "MCP request exceeded its total timeout",
            )
        self._validate_destination()
        try:
            if self._http_client is None:
                client_kwargs: dict[str, object] = {
                    "follow_redirects": False,
                    "limits": httpx.Limits(
                        max_connections=1,
                        max_keepalive_connections=0,
                    ),
                }
                if self.transport is not None:
                    client_kwargs["transport"] = self.transport
                else:
                    client_kwargs["transport"] = _PinnedTransport(
                        self.resolver
                    )
                self._http_client = httpx.Client(**client_kwargs)
            request = self._http_client.build_request(
                "POST",
                self.tool.endpoint,
                headers=headers,
                content=body,
                # httpx applies a per-request timeout through the request
                # extension; ``send`` itself does not accept one.
                timeout=remaining,
            )
            return self._http_client.send(request, stream=True)
        except httpx.TimeoutException:
            self.close()
            raise McpTransportError(
                "TIMEOUT",
                "MCP request exceeded its timeout",
            ) from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            exc.response.close()
            self.close()
            raise McpTransportError(
                "MCP_HTTP_STATUS",
                "MCP server returned an unsuccessful status",
                details={"status": status},
            ) from None
        except (httpx.RequestError, httpcore.NetworkError):
            self.close()
            raise McpTransportError(
                "REQUEST_FAILED",
                "MCP request failed",
            ) from None

    def _headers(
        self,
        *,
        include_version: bool,
        include_session: bool,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
        }
        if include_version:
            headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        if include_session and self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if self.tool.bearer_token_env is not None:
            token = self.environment.get(self.tool.bearer_token_env)
            if not token:
                raise McpTransportError(
                    "TOOL_CREDENTIAL_MISSING",
                    "Configured MCP credential is unavailable",
                )
            if (
                len(token) > 8_192
                or any(
                    ord(char) < 0x21 or ord(char) > 0x7E
                    for char in token
                )
            ):
                raise McpTransportError(
                    "TOOL_CREDENTIAL_INVALID",
                    "Configured MCP credential is invalid",
                )
            headers["Authorization"] = f"{self.tool.authorization_prefix}{token}"
        return headers

    def _validate_request_body(self, body: bytes) -> None:
        if len(body) > self.tool.budgets.max_request_bytes:
            raise ToolBudgetError(
                "TOOL_REQUEST_TOO_LARGE",
                "MCP request exceeds the configured byte limit",
                details={
                    "limit_bytes": self.tool.budgets.max_request_bytes,
                },
            )

    def _validate_destination(self) -> None:
        try:
            parsed: SplitResult = validate_https_url(self.tool.endpoint)
            addresses = self.resolver(parsed.hostname or "", 443)
            validate_resolved_addresses(self.tool.endpoint, addresses)
        except CollectionError as exc:
            code = (
                "DNS_FAILED"
                if exc.code == "DNS_FAILED"
                else "UNSAFE_ADDRESS"
            )
            raise McpTransportError(
                code,
                "MCP endpoint is not a public HTTPS destination",
            ) from None

    def _read_body(
        self,
        response: httpx.Response,
        *,
        deadline: float,
    ) -> bytes:
        return b"".join(self._iter_body(response, deadline=deadline))

    def _iter_body(
        self,
        response: httpx.Response,
        *,
        deadline: float,
    ) -> Iterator[bytes]:
        limit = self.tool.budgets.max_result_bytes
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                content_length = int(declared)
            except ValueError:
                raise McpProtocolError(
                    "INVALID_CONTENT_LENGTH",
                    "MCP response has an invalid content length",
                ) from None
            if content_length < 0 or content_length > limit:
                raise McpTransportError(
                    "TOOL_RESPONSE_TOO_LARGE",
                    "MCP response exceeds the configured byte limit",
                    details={"limit_bytes": limit},
                )
        encoding = response.headers.get(
            "Content-Encoding",
            "identity",
        ).strip().lower()
        if encoding not in {"", "identity"}:
            raise McpProtocolError(
                "UNSUPPORTED_CONTENT_ENCODING",
                "MCP response content encoding is not allowed",
            )
        size = 0
        try:
            for chunk in response.iter_raw():
                if self.clock() > deadline:
                    raise McpTransportError(
                        "TIMEOUT",
                        "MCP response exceeded its total timeout",
                    )
                size += len(chunk)
                if size > limit:
                    raise McpTransportError(
                        "TOOL_RESPONSE_TOO_LARGE",
                        "MCP response exceeds the configured byte limit",
                        details={"limit_bytes": limit},
                    )
                yield chunk
        except ToolError:
            raise
        except (httpx.HTTPError, httpcore.NetworkError):
            raise McpTransportError(
                "RESPONSE_READ_FAILED",
                "MCP response could not be read",
            ) from None

    def _read_sse(
        self,
        response: httpx.Response,
        *,
        request_id: int,
        deadline: float,
    ) -> tuple[bytes, dict[str, Any]]:
        chunks: list[bytes] = []
        event_lines: list[bytes] = []
        line = bytearray()
        skip_lf = False
        event_count = 0

        def process_event(*, count_empty: bool = True) -> dict[str, Any] | None:
            nonlocal event_count
            try:
                text = b"\n".join(event_lines).decode("utf-8")
            except UnicodeDecodeError:
                raise McpProtocolError(
                    "INVALID_SSE",
                    "MCP SSE response is not UTF-8",
                ) from None
            event_lines.clear()
            data_lines: list[str] = []
            for event_line in text.split("\n"):
                if not event_line or event_line.startswith(":"):
                    continue
                if event_line.startswith("data:"):
                    value = event_line[5:]
                    if value.startswith(" "):
                        value = value[1:]
                    data_lines.append(value)
                elif (
                    event_line.startswith("event:")
                    or event_line.startswith("id:")
                    or event_line.startswith("retry:")
                ):
                    continue
                else:
                    raise McpProtocolError(
                        "INVALID_SSE",
                        "MCP SSE response contains an invalid field",
                    )
            if count_empty or data_lines:
                event_count += 1
                if event_count > self.tool.max_sse_events:
                    raise McpProtocolError(
                        "TOO_MANY_SSE_EVENTS",
                        "MCP SSE response exceeds the event limit",
                    )
            data = "\n".join(data_lines)
            if not data:
                return None
            parsed = _parse_json(
                data.encode("utf-8"),
                max_depth=self.tool.max_json_depth,
            )
            if not isinstance(parsed, dict):
                raise McpProtocolError(
                    "INVALID_JSON_RPC",
                    "MCP SSE data must be a JSON-RPC object",
                )
            if "method" in parsed:
                if parsed.get("jsonrpc") != "2.0":
                    raise McpProtocolError(
                        "INVALID_JSON_RPC",
                        "MCP notification is not JSON-RPC 2.0",
                    )
                return None
            return self._response_result(
                parsed,
                request_id=request_id,
            )

        for chunk in self._iter_body(response, deadline=deadline):
            chunks.append(chunk)
            for byte in chunk:
                # CR terminates a line immediately; swallow a following LF
                # even when CRLF is split across network chunks.
                if skip_lf:
                    skip_lf = False
                    if byte == 10:
                        continue
                if byte not in (10, 13):
                    line.append(byte)
                    continue
                skip_lf = byte == 13
                if line:
                    event_lines.append(bytes(line))
                    line.clear()
                else:
                    result = process_event()
                    if result is not None:
                        return b"".join(chunks), result

        # Preserve the former split("\n") parser's final line/empty event
        # and its acceptance of a final data event without a blank separator.
        if line:
            event_lines.append(bytes(line))
        result = process_event(count_empty=not line)
        if result is not None:
            return b"".join(chunks), result
        raise McpProtocolError(
            "MCP_RESPONSE_MISSING",
            "MCP SSE response ended before the requested result",
        )

    def _response_result(
        self,
        parsed: object,
        *,
        request_id: int,
    ) -> dict[str, Any]:
        if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
            raise McpProtocolError(
                "INVALID_JSON_RPC",
                "MCP response is not JSON-RPC 2.0",
            )
        response_id = parsed.get("id")
        if (
            not isinstance(response_id, int)
            or isinstance(response_id, bool)
            or response_id != request_id
        ):
            raise McpProtocolError(
                "INVALID_RESPONSE_ID",
                "MCP response id does not match the request",
            )
        has_result = "result" in parsed
        has_error = "error" in parsed
        if has_result == has_error:
            raise McpProtocolError(
                "INVALID_JSON_RPC",
                "MCP response must contain exactly one result or error",
            )
        if has_error:
            error = parsed.get("error")
            if not isinstance(error, dict):
                raise McpProtocolError(
                    "INVALID_JSON_RPC",
                    "MCP error response is invalid",
                )
            code = error.get("code")
            if not isinstance(code, int) or isinstance(code, bool):
                raise McpProtocolError(
                    "INVALID_JSON_RPC",
                    "MCP error code is invalid",
                )
            raise McpProtocolError(
                "MCP_RPC_ERROR",
                "MCP server returned a JSON-RPC error",
                details={"rpc_code": code},
            )
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(
                "INVALID_JSON_RPC",
                "MCP result must be an object",
            )
        return result

    def _validate_discovered_tool(self, raw_tool: object) -> dict[str, Any]:
        if not isinstance(raw_tool, dict):
            raise McpProtocolError(
                "INVALID_TOOLS_LIST",
                "MCP tools/list contains an invalid tool",
            )
        name = raw_tool.get("name")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 200
        ):
            raise McpProtocolError(
                "INVALID_TOOLS_LIST",
                "MCP tool name is invalid",
            )
        description = raw_tool.get("description")
        if description is not None and (
            not isinstance(description, str)
            or len(description) > 10_000
        ):
            raise McpProtocolError(
                "INVALID_TOOLS_LIST",
                "MCP tool description is invalid",
            )
        schema = raw_tool.get("inputSchema")
        if not isinstance(schema, dict):
            raise McpProtocolError(
                "INVALID_TOOLS_LIST",
                "MCP tool input schema is invalid",
            )
        try:
            validate_json_value(
                schema,
                path="tool.inputSchema",
                max_depth=self.tool.max_json_depth,
            )
        except ValueError:
            raise McpProtocolError(
                "INVALID_TOOLS_LIST",
                "MCP tool input schema exceeds the JSON limits",
            ) from None
        return {
            "name": name,
            "description": description,
        }


class McpClient:
    """Authorized MCP Streamable HTTP client for protocol 2025-11-25."""

    def __init__(
        self,
        configuration: ToolConfiguration,
        authorized_tool_ids: Sequence[str] | set[str] | frozenset[str],
        *,
        resolver: Resolver = resolve_host,
        transport: httpx.BaseTransport | None = None,
        environment: Environment | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # httpx logs every request URL at INFO. That would write the Tool
        # endpoint back into the logs this module promises never to contain it,
        # so keep httpx's own request log at warnings whenever Tools are used.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self.configuration = configuration
        self.authorized_tools = authorize_tools(
            configuration,
            authorized_tool_ids,
        )
        self._tools = {
            tool.id: tool
            for tool in self.authorized_tools
        }
        self._endpoints = {
            tool.id: _EndpointClient(
                tool,
                resolver=resolver,
                transport=transport,
                environment=(
                    os.environ if environment is None else environment
                ),
                clock=clock,
            )
            for tool in self.authorized_tools
        }
        self._calls: dict[str, int] = {
            tool.id: 0
            for tool in self.authorized_tools
        }

    def __enter__(self) -> McpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        for endpoint in self._endpoints.values():
            endpoint.close()

    def list_tools(self) -> tuple[DiscoveredTool, ...]:
        discovered: list[DiscoveredTool] = []
        for tool in self.authorized_tools:
            endpoint = self._endpoints[tool.id]
            deadline = endpoint.operation_deadline()
            try:
                remote_tools = endpoint.list_tools(deadline)
            except McpTransportError as exc:
                if exc.code != "MCP_HTTP_STATUS" or exc.details.get(
                    "status"
                ) != 404 or endpoint.session_id is None:
                    raise
                endpoint.reset_session()
                deadline = endpoint.operation_deadline()
                remote_tools = endpoint.list_tools(deadline)
            matched = [
                raw
                for raw in remote_tools
                if raw["name"] == tool.mcp_name
            ]
            if not matched:
                raise McpProtocolError(
                    "TOOL_NOT_DISCOVERED",
                    "Authorized MCP tool was not returned by tools/list",
                    details={"tool_id": tool.id},
                )
            if len(matched) != 1:
                raise McpProtocolError(
                    "DUPLICATE_REMOTE_TOOL",
                    "MCP tools/list returned a duplicate tool",
                    details={"tool_id": tool.id},
                )
            discovered.append(
                DiscoveredTool(
                    id=tool.id,
                    remote_name=tool.mcp_name,
                    description=matched[0]["description"],
                    input_schema=tool.input_schema,
                )
            )
        return tuple(discovered)

    def call_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, object],
    ) -> ToolCallResult:
        tool = self._tools.get(tool_id) if isinstance(tool_id, str) else None
        if tool is None:
            raise ToolAuthorizationError(
                "TOOL_NOT_AUTHORIZED",
                "Tool is not authorized for this run",
                details={
                    "tool_id": (
                        tool_id[:120]
                        if isinstance(tool_id, str)
                        else "<invalid>"
                    )
                },
            )
        if not isinstance(arguments, Mapping):
            raise McpProtocolError(
                "TOOL_ARGUMENTS_INVALID",
                "Tool arguments must be an object",
                details={"tool_id": tool_id},
            )
        if self._calls[tool_id] >= tool.budgets.max_calls:
            raise ToolBudgetError(
                "TOOL_CALL_BUDGET_EXCEEDED",
                "Tool call budget has been exhausted",
                details={
                    "tool_id": tool_id,
                    "max_calls": tool.budgets.max_calls,
                },
            )
        try:
            validate_json_value(
                dict(arguments),
                path="arguments",
                max_depth=tool.max_json_depth,
            )
            validate_json_schema_value(dict(arguments), tool.input_schema)
        except ValueError:
            raise McpProtocolError(
                "TOOL_ARGUMENTS_INVALID",
                "Tool arguments do not match the authorized schema",
                details={"tool_id": tool_id},
            ) from None

        endpoint = self._endpoints[tool_id]
        deadline = endpoint.operation_deadline()
        try:
            response = endpoint.call_tool(arguments, deadline)
        except McpTransportError as exc:
            if (
                exc.code == "MCP_HTTP_STATUS"
                and exc.details.get("status") == 404
                and endpoint.session_id is not None
            ):
                endpoint.reset_session()
                deadline = endpoint.operation_deadline()
                response = endpoint.call_tool(arguments, deadline)
            else:
                raise

        self._calls[tool_id] += 1
        result = response.result
        content = result.get("content")
        if not isinstance(content, list) or len(content) > 1_000:
            raise McpProtocolError(
                "INVALID_TOOL_RESULT",
                "MCP tool result has invalid content",
            )
        for item in content:
            if not isinstance(item, dict):
                raise McpProtocolError(
                    "INVALID_TOOL_RESULT",
                    "MCP tool result contains invalid content",
                )
        if result.get("isError") is True:
            raise McpProtocolError(
                "TOOL_CALL_FAILED",
                "MCP tool reported an execution error",
                details={"tool_id": tool_id},
            )
        value = _map_result(tool, result)
        if tool.output_schema is not None:
            try:
                validate_json_schema_value(value, tool.output_schema)
            except ValueError:
                raise McpProtocolError(
                    "TOOL_RESULT_SCHEMA_INVALID",
                    "MCP tool result does not match the authorized schema",
                    details={"tool_id": tool_id},
                ) from None
        return ToolCallResult(
            tool_id=tool_id,
            result=result,
            value=value,
            is_error=result.get("isError") is True,
            response_bytes=response.response_bytes,
            content_type=response.content_type,
        )


McpStreamableHttpClient = McpClient


__all__ = [
    "DiscoveredTool",
    "McpClient",
    "McpStreamableHttpClient",
    "ToolCallResult",
    "resolve_host",
]
