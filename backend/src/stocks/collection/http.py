from __future__ import annotations

import socket
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin

import httpx

from stocks.collection.config import CONTACT_USER_AGENT
from stocks.collection.errors import CollectionError
from stocks.collection.urls import validate_https_url, validate_resolved_addresses

import httpcore

FEED_CONTENT_TYPES = frozenset(
    {
        "application/atom+xml",
        "application/rdf+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    }
)
ARTICLE_CONTENT_TYPES = frozenset(
    {
        "application/xhtml+xml",
        "text/html",
        "text/plain",
    }
)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class FetchedResponse:
    url: str
    content_type: str
    payload: bytes


Resolver = Callable[[str, int], list[str]]


def resolve_host(host: str, port: int) -> list[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise CollectionError(
            "DNS_FAILED",
            "Host could not be resolved",
            url=f"https://{host}/",
        ) from exc
    return list(dict.fromkeys(record[4][0] for record in records))


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_host,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.resolver = resolver
        self.transport = transport

    def fetch(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        max_redirects: int,
        accept: str,
        allowed_content_types: Iterable[str],
        user_agent: str,
    ) -> FetchedResponse:
        if (
            any(ord(char) < 32 or ord(char) == 127 for char in user_agent)
            or CONTACT_USER_AGENT.fullmatch(user_agent) is None
        ):
            raise CollectionError(
                "INVALID_USER_AGENT",
                "User-Agent must identify the client and include a contact email",
                url=url,
            )
        current_url = url
        deadline = time.monotonic() + timeout_seconds
        allowed = {content_type.lower() for content_type in allowed_content_types}
        visited: set[str] = set()

        for redirect_count in range(max_redirects + 1):
            if current_url in visited:
                raise CollectionError("REDIRECT_LOOP", "Redirect loop detected", url=current_url)
            visited.add(current_url)
            self._validate_destination(current_url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionError("TIMEOUT", "Request exceeded its total timeout", url=current_url)

            headers = {
                "User-Agent": user_agent,
                "Accept": accept,
                "Accept-Encoding": "gzip, deflate",
            }
            try:
                client_kwargs: dict[str, object] = {
                    "timeout": remaining,
                    "follow_redirects": False,
                }
                if self.transport is not None:
                    client_kwargs["transport"] = self.transport
                else:
                    client_kwargs["transport"] = _PinnedTransport(self.resolver)
                with httpx.Client(**client_kwargs) as client:
                    with client.stream("GET", current_url, headers=headers) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("Location")
                            if not location:
                                raise CollectionError(
                                    "REDIRECT_WITHOUT_LOCATION",
                                    "Redirect response did not include a location",
                                    url=current_url,
                                )
                            if redirect_count >= max_redirects:
                                raise CollectionError(
                                    "TOO_MANY_REDIRECTS",
                                    "Response exceeded the redirect limit",
                                    url=current_url,
                                )
                            current_url = urljoin(current_url, location)
                            continue
                        if not 200 <= response.status_code < 300:
                            raise CollectionError(
                                "HTTP_STATUS",
                                "Remote server returned an unsuccessful status",
                                url=current_url,
                                details={"status": response.status_code},
                            )
                        content_type = _content_type(response)
                        if content_type not in allowed:
                            raise CollectionError(
                                "UNSUPPORTED_CONTENT_TYPE",
                                "Response content type is not allowed",
                                url=current_url,
                                details={"content_type": content_type[:255]},
                            )
                        payload = self._read_limited(
                            response,
                            max_response_bytes=max_response_bytes,
                            deadline=deadline,
                            url=current_url,
                        )
                        return FetchedResponse(
                            url=current_url,
                            content_type=content_type,
                            payload=payload,
                        )
            except CollectionError:
                raise
            except httpx.TimeoutException as exc:
                raise CollectionError(
                    "TIMEOUT",
                    "Request exceeded its timeout",
                    url=current_url,
                ) from exc
            except (httpx.RequestError, httpcore.NetworkError) as exc:
                raise CollectionError(
                    "REQUEST_FAILED",
                    "Request failed before a response was received",
                    url=current_url,
                ) from exc

        raise CollectionError(
            "TOO_MANY_REDIRECTS",
            "Response exceeded the redirect limit",
            url=current_url,
        )

    def _validate_destination(self, url: str) -> None:
        parsed = validate_https_url(url)
        host = parsed.hostname or ""
        try:
            addresses = self.resolver(host, 443)
        except CollectionError as exc:
            if exc.code != "DNS_FAILED":
                raise
            addresses = []
        validate_resolved_addresses(url, addresses)

    def _read_limited(
        self,
        response: httpx.Response,
        *,
        max_response_bytes: int,
        deadline: float,
        url: str,
    ) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = 0
            if declared_length > max_response_bytes:
                raise CollectionError(
                    "RESPONSE_TOO_LARGE",
                    "Response exceeded the configured byte limit",
                    url=url,
                    details={"limit_bytes": max_response_bytes},
                )

        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise CollectionError(
                "UNSUPPORTED_CONTENT_ENCODING",
                "Response content encoding is not supported",
                url=url,
            )

        raw_size = 0
        decoded_size = 0
        chunks: list[bytes] = []
        if encoding in {"", "identity"}:
            for chunk in response.iter_raw():
                self._check_deadline(deadline, url)
                raw_size += len(chunk)
                decoded_size += len(chunk)
                self._check_size(raw_size, decoded_size, max_response_bytes, url)
                chunks.append(chunk)
            return b"".join(chunks)

        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else zlib.decompressobj()
        try:
            for chunk in response.iter_raw():
                self._check_deadline(deadline, url)
                raw_size += len(chunk)
                if raw_size > max_response_bytes:
                    raise CollectionError(
                        "RESPONSE_TOO_LARGE",
                        "Compressed response exceeded the configured byte limit",
                        url=url,
                        details={"limit_bytes": max_response_bytes},
                    )
                decoded = decompressor.decompress(chunk, max_response_bytes - decoded_size + 1)
                decoded_size += len(decoded)
                self._check_size(raw_size, decoded_size, max_response_bytes, url)
                chunks.append(decoded)
            decoded = decompressor.flush(max_response_bytes - decoded_size + 1)
            decoded_size += len(decoded)
            self._check_size(raw_size, decoded_size, max_response_bytes, url)
            chunks.append(decoded)
        except zlib.error as exc:
            raise CollectionError(
                "INVALID_CONTENT_ENCODING",
                "Compressed response could not be decoded",
                url=url,
            ) from exc
        if not decompressor.eof or decompressor.unused_data:
            raise CollectionError(
                "INVALID_CONTENT_ENCODING",
                "Compressed response was incomplete or malformed",
                url=url,
            )
        return b"".join(chunks)

    @staticmethod
    def _check_deadline(deadline: float, url: str) -> None:
        if time.monotonic() > deadline:
            raise CollectionError("TIMEOUT", "Request exceeded its total timeout", url=url)

    @staticmethod
    def _check_size(
        raw_size: int, decoded_size: int, max_response_bytes: int, url: str
    ) -> None:
        if raw_size > max_response_bytes or decoded_size > max_response_bytes:
            raise CollectionError(
                "RESPONSE_TOO_LARGE",
                "Response exceeded the configured byte limit",
                url=url,
                details={"limit_bytes": max_response_bytes},
            )


def _content_type(response: httpx.Response) -> str:
    value = response.headers.get("Content-Type", "")
    return value.split(";", 1)[0].strip().lower()


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
        addresses = self.resolver(host, port)
        validate_resolved_addresses(f"https://{host}:{port}/", addresses)
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
            raise last_error
        raise CollectionError(
            "DNS_FAILED",
            "Host did not resolve",
            url=f"https://{host}:{port}/",
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
