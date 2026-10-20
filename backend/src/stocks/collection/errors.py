from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def sanitize_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return "<invalid-url>"
    if not parsed.scheme or not host:
        return "<invalid-url>"
    host_text = f"[{host}]" if ":" in host else host
    if port is not None and port != 443:
        host_text = f"{host_text}:{port}"
    path = parsed.path or "/"
    if len(path) > 512:
        path = f"{path[:509]}..."
    return f"{parsed.scheme}://{host_text}{path}"


class CollectionError(Exception):
    """A stable, externally safe collection failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.url = sanitize_url(url)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.details)
        result.update(
            {
                "code": self.code,
                "message": self.message,
            }
        )
        if self.url:
            result["url"] = self.url
        return result


class ConfigurationError(CollectionError):
    pass
