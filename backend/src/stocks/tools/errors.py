from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """A stable, externally safe Tool or MCP failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.details,
            "code": self.code,
            "message": self.message,
        }


class ToolConfigurationError(ToolError):
    pass


class ToolAuthorizationError(ToolError):
    pass


class McpTransportError(ToolError):
    pass


class McpProtocolError(ToolError):
    pass


class ToolBudgetError(ToolError):
    pass
