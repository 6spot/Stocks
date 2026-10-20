from __future__ import annotations

import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit, urlunsplit

from stocks.collection.errors import CollectionError


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_private
        and not address.is_reserved
    )


def validate_https_url(url: str) -> SplitResult:
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        raise CollectionError("INVALID_URL", "URL must be a non-empty HTTPS URL", url=url)
    if any(ord(char) < 32 for char in url):
        raise CollectionError("INVALID_URL", "URL contains control characters", url=url)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise CollectionError("INVALID_URL", "URL is malformed", url=url) from exc
    if parsed.scheme.lower() != "https":
        raise CollectionError("URL_NOT_HTTPS", "Only HTTPS URLs are allowed", url=url)
    if not parsed.hostname:
        raise CollectionError("INVALID_URL", "URL must include a host", url=url)
    if parsed.username is not None or parsed.password is not None:
        raise CollectionError("URL_USERINFO", "URL user information is not allowed", url=url)
    if port is not None and port != 443:
        raise CollectionError("URL_PORT", "Only the default HTTPS port is allowed", url=url)
    if "\\" in parsed.netloc:
        raise CollectionError("INVALID_URL", "URL contains an invalid host", url=url)
    address = _literal_address(parsed.hostname)
    if address is None:
        return parsed
    if not _is_public_address(address):
        raise CollectionError(
            "UNSAFE_ADDRESS",
            "URL resolves to a non-public network address",
            url=url,
        )
    return parsed


def _literal_address(
    host: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if ":" in host or not all(
        char in "0123456789abcdefABCDEFxX." for char in host
    ):
        return None
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def validate_resolved_addresses(url: str, addresses: list[str]) -> None:
    if not addresses:
        raise CollectionError("DNS_FAILED", "Host did not resolve", url=url)
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise CollectionError("DNS_FAILED", "Host returned an invalid address", url=url) from exc
        if not _is_public_address(address):
            raise CollectionError(
                "UNSAFE_ADDRESS",
                "Host resolves to a non-public network address",
                url=url,
            )


def canonical_url(url: str) -> str:
    parsed = validate_https_url(url)
    host = parsed.hostname or ""
    host_text = f"[{host}]" if ":" in host else host
    return urlunsplit(
        (
            "https",
            host_text,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
