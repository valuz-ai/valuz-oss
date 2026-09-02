"""Shared HTTP request policy for remote MCP servers."""

from __future__ import annotations

from collections.abc import Mapping

MCP_USER_AGENT = "Valuz/1.0"


def mcp_request_headers(headers: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy with Valuz's User-Agent unless the connector overrides it."""
    result = dict(headers or {})
    if not any(name.lower() == "user-agent" for name in result):
        result["User-Agent"] = MCP_USER_AGENT
    return result


__all__ = ["MCP_USER_AGENT", "mcp_request_headers"]
