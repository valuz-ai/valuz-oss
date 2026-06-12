"""Tests for the host toolkit MCP server (``integrations/toolkit_mcp_server``).

Covers the seams P0.3 introduced:

- toolset installation + per-toolset MCP server construction (declarations
  dropped, schemas passed through verbatim);
- the call path: session-id header → ContextVar → ``ExecContext`` rebuild
  → handler invocation → ``ToolResult``/``is_error`` projection;
- ASGI gate: internal-token check + missing-session-id rejection;
- the ``harness`` entry in the always-on MCP set (base vs lead toolsets,
  run-kind mapping).

Tests drive the MCP ``Server`` request handlers directly (same approach as
the kernel's mcp_bridge tests would) — no HTTP stack needed except for the
ASGI-gate cases, which call the wrapper with a synthetic scope.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.tools import ExecContext, ToolDef, ToolResult

from valuz_agent.integrations import toolkit_mcp_server as tk


@pytest.fixture(autouse=True)
def _fresh_toolsets():
    """Each test installs its own toolsets; restore emptiness afterwards."""
    yield
    tk._TOOLSETS.clear()
    tk._SERVERS.clear()
    tk._MANAGERS.clear()


def _echo_tool(name: str = "echo") -> ToolDef:
    async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        return ToolResult(content=f"{name}:{args.get('text', '')}@{ctx.session_id}")

    return ToolDef(
        name=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=_handler,
    )


def _failing_tool() -> ToolDef:
    async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        return ToolResult(content="boom", is_error=True)

    return ToolDef(name="fails", description="always errors", parameters={}, handler=_handler)


def _declaration_only() -> ToolDef:
    return ToolDef(name="decl", description="declaration", parameters={}, handler=None)


def test_build_server_drops_declarations_and_keeps_schemas() -> None:
    tk.install_toolkit_toolsets(
        base=(_echo_tool(), _declaration_only()), lead=(_echo_tool("lead_echo"),)
    )
    base_server = tk._build_server("base")
    tools = asyncio.run(_list_tools(base_server))
    names = {t.name for t in tools}
    assert names == {"echo"}  # the declaration (handler=None) is dropped
    echo = next(t for t in tools if t.name == "echo")
    assert echo.inputSchema == {"type": "object", "properties": {"text": {"type": "string"}}}


async def _list_tools(server: Any) -> list[Any]:
    from mcp.types import ListToolsRequest

    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    return list(result.root.tools)


async def _call_tool(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    from mcp.types import CallToolRequest, CallToolRequestParams

    handler = server.request_handlers[CallToolRequest]
    request = CallToolRequest(
        method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments)
    )
    return await handler(request)


def test_call_tool_rebuilds_exec_context_from_session_header() -> None:
    tk.install_toolkit_toolsets(base=(_echo_tool(),), lead=())
    server = tk._build_server("base")

    token = tk._session_var.set("sess-42")
    try:
        result = asyncio.run(_call_tool(server, "echo", {"text": "hi"}))
    finally:
        tk._session_var.reset(token)

    content = result.root.content
    assert content[0].text == "echo:hi@sess-42"


def test_call_tool_outside_session_scope_fails() -> None:
    tk.install_toolkit_toolsets(base=(_echo_tool(),), lead=())
    server = tk._build_server("base")
    result = asyncio.run(_call_tool(server, "echo", {}))
    # The lowlevel server converts handler exceptions into an error result.
    assert result.root.isError


def test_tool_result_is_error_projected_as_text_prefix() -> None:
    tk.install_toolkit_toolsets(base=(_failing_tool(),), lead=())
    server = tk._build_server("base")
    token = tk._session_var.set("sess-1")
    try:
        result = asyncio.run(_call_tool(server, "fails", {}))
    finally:
        tk._session_var.reset(token)
    # Not a wire-level failure — surfaced as ERROR-prefixed text.
    assert not result.root.isError
    assert result.root.content[0].text == "ERROR: boom"


def test_toolsets_are_isolated() -> None:
    tk.install_toolkit_toolsets(base=(_echo_tool("base_only"),), lead=(_echo_tool("lead_only"),))
    base_names = {t.name for t in asyncio.run(_list_tools(tk._build_server("base")))}
    lead_names = {t.name for t in asyncio.run(_list_tools(tk._build_server("lead")))}
    assert base_names == {"base_only"}
    assert lead_names == {"lead_only"}


# ── ASGI gate ──────────────────────────────────────────────────────────


def _scope(headers: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }


async def _run_asgi(app: Any, scope: dict[str, Any]) -> int:
    status: list[int] = []

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def _send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])

    await app(scope, _receive, _send)
    return status[0]


def test_asgi_rejects_bad_token(monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "internal_mcp_token_override", "GOOD")
    tk.install_toolkit_toolsets(base=(), lead=())
    app = tk.build_toolkit_mcp_asgi("base")

    status = asyncio.run(_run_asgi(app, _scope({"x-valuz-internal": "BAD"})))
    assert status == 403


def test_asgi_requires_session_id(monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "internal_mcp_token_override", "GOOD")
    tk.install_toolkit_toolsets(base=(), lead=())
    app = tk.build_toolkit_mcp_asgi("base")

    status = asyncio.run(_run_asgi(app, _scope({"x-valuz-internal": "GOOD"})))
    assert status == 400


def test_unknown_toolset_rejected() -> None:
    with pytest.raises(ValueError):
        tk.build_toolkit_mcp_asgi("nope")
    with pytest.raises(ValueError):
        tk.toolkit_mcp_url(base_url="http://x", toolset="nope")


# ── always-on injection ────────────────────────────────────────────────


def test_always_on_set_includes_harness_per_toolkit() -> None:
    from valuz_agent.adapters.capability_resolver import (
        always_on_http_mcp_servers,
        harness_toolkit_for_run_kind,
    )

    base_set = always_on_http_mcp_servers("sess-1")
    by_name = {m.name: m for m in base_set}
    assert by_name["harness"].url.endswith("/internal/mcp/toolkit/base")
    assert by_name["harness"].headers["X-Valuz-Session-Id"] == "sess-1"

    lead_set = always_on_http_mcp_servers("sess-1", toolkit="lead")
    assert {m.name for m in lead_set} == set(by_name)
    assert next(m for m in lead_set if m.name == "harness").url.endswith(
        "/internal/mcp/toolkit/lead"
    )

    assert harness_toolkit_for_run_kind("lead") == "lead"
    assert harness_toolkit_for_run_kind("subtask") == "base"
    assert harness_toolkit_for_run_kind(None) == "base"
