"""MCP tools on the Valuz hook bus — every MCP call path dispatches ``tool.call``.

- the kernel's HTTP proxy (what Codex and DSH reach while a handler listens),
  against a real stdio MCP server;
- the Claude in-process source proxy, DeepAgents' interceptor and PTC's
  upstream pool share ``dispatch_mcp_call``;
- Codex / DSH swap their MCP configs for proxy entries only while a
  ``tool.call`` handler is registered.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, TextContent
from src.core.agent_config import AgentConfig
from src.core.hooks import TOOL_CALL, SessionRef, ToolOutcome, hook_registry
from src.core.hooks.registry import SessionHooks
from src.core.types import McpHttpServerConfig, McpStdioServerConfig, Session
from src.runtimes.mcp_proxy import (
    dispatch_mcp_call,
    register_session_proxy,
    unregister_session_proxy,
)

OWNER = "test.mcp-proxy-hook-bus"
UPSTREAM = McpStdioServerConfig(
    name="upstream",
    command=sys.executable,
    args=(str(Path(__file__).parent / "fixtures" / "hook_bus_mcp_server.py"),),
)
SESSION = SessionRef(session_id="s-proxy", runtime_provider="codex", permission_mode="full_access")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    hook_registry.unregister_owner(OWNER)


def _hooks() -> SessionHooks:
    return SessionHooks(hook_registry, SESSION)


# -- the shared dispatch helper ----------------------------------------------------


async def test_unchanged_result_comes_back_untouched() -> None:
    raw = CallToolResult(
        content=[TextContent(type="text", text="x")],
        structuredContent={"k": 1},
    )

    async def watch(ctx, event, next_):  # noqa: ANN001
        assert event.get("tool.name") == "mcp__srv__search"
        assert event.get("tool.server") == "srv"
        return await next_()

    hook_registry.register(TOOL_CALL, watch, owner=OWNER)

    async def call(args: dict[str, Any]) -> CallToolResult:
        return raw

    assert await dispatch_mcp_call(_hooks(), "srv", "search", {"q": 1}, call) is raw


async def test_rewrite_args_and_result_and_take_over() -> None:
    calls: list[dict[str, Any]] = []

    async def rewrite(ctx, event, next_):  # noqa: ANN001
        if event.get("input.q") == "blocked":
            return ToolOutcome(content="not allowed", is_error=True, executed=False)
        result = await next_(event.with_data(input={"q": "rewritten"}))
        return ToolOutcome(content=[*result.content, {"type": "text", "text": "+hook"}])

    hook_registry.register(TOOL_CALL, rewrite, owner=OWNER, matcher={"tool.server": "srv"})

    async def call(args: dict[str, Any]) -> CallToolResult:
        calls.append(args)
        return CallToolResult(content=[TextContent(type="text", text=f"got {args['q']}")])

    result = await dispatch_mcp_call(_hooks(), "srv", "search", {"q": "orig"}, call)
    assert calls == [{"q": "rewritten"}]
    assert [block.text for block in result.content] == ["got rewritten", "+hook"]
    refused = await dispatch_mcp_call(_hooks(), "srv", "search", {"q": "blocked"}, call)
    assert refused.isError and refused.content[0].text == "not allowed"
    assert len(calls) == 1


# -- the kernel HTTP proxy against a real stdio MCP server -------------------------


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def proxy_base(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """The proxy served on the test's own event loop — as in the kernel, where
    registration, serving and teardown share one loop."""
    import app.mcp_proxy_router as router
    from app.mcp_proxy_router import (
        MCP_PROXY_MOUNT_PATH,
        mcp_proxy_asgi,
        mcp_proxy_lifespan,
    )
    from contextlib import asynccontextmanager

    router.reset_for_tests()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ANN202
        async with mcp_proxy_lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount(MCP_PROXY_MOUNT_PATH, mcp_proxy_asgi)  # type: ignore[arg-type]
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("CODEX_TOOLKIT_BASE_URL", base)
    try:
        yield base
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        router.reset_for_tests()


async def _client_call(config: McpHttpServerConfig, fn) -> Any:  # noqa: ANN001
    async with create_mcp_http_client(headers=dict(config.headers)) as http_client:
        async with streamable_http_client(config.url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


async def test_http_proxy_serves_tools_resources_and_runs_the_hook(proxy_base: str) -> None:
    seen: list[str] = []

    async def stamp(ctx, event, next_):  # noqa: ANN001
        seen.append(event.get("tool.name"))
        result = await next_()
        return ToolOutcome(content=[*result.content, {"type": "text", "text": "[hooked]"}])

    hook_registry.register(TOOL_CALL, stamp, owner=OWNER)
    [proxied] = register_session_proxy("s-proxy", [UPSTREAM], _hooks())
    assert proxied.url.startswith(f"{proxy_base}/mcp/proxy/s-proxy/upstream")
    assert proxied.headers["Authorization"].startswith("Bearer ")
    try:

        async def scenario(session: ClientSession) -> Any:
            tools = await session.list_tools()
            result = await session.call_tool("echo", {"text": "hi"})
            resources = await session.list_resources()
            read = await session.read_resource(resources.resources[0].uri)
            return tools, result, read

        tools, result, read = await _client_call(proxied, scenario)
        assert [tool.name for tool in tools.tools] == ["echo"]
        assert [block.text for block in result.content] == ["upstream:hi", "[hooked]"]
        assert seen == ["mcp__upstream__echo"]
        assert read.contents[0].text == "hello from the upstream"
    finally:
        await unregister_session_proxy("s-proxy")


async def test_http_proxy_rejects_a_wrong_token(proxy_base: str) -> None:
    [proxied] = register_session_proxy("s-auth", [UPSTREAM], _hooks())
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                proxied.url,
                headers={"Authorization": "Bearer nope", "accept": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert response.status_code == 401
    finally:
        await unregister_session_proxy("s-auth")


# -- Codex / DSH route through the proxy only while a handler listens ---------------


def _session() -> Session:
    return Session(
        id="s-route",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        mcp_servers=(
            McpHttpServerConfig(
                name="remote", url="https://example.com/mcp", headers={"X-Key": "secret"}
            ),
            UPSTREAM,
        ),
    )


async def test_codex_routes_mcp_through_the_proxy_only_with_a_handler() -> None:
    from src.runtimes.codex.runtime import CodexRuntime, _build_config_overrides

    runtime = CodexRuntime.__new__(CodexRuntime)
    runtime._mcp_proxy_session_id = None
    session = _session()

    # A bare completion has no handler at all: codex connects directly.
    runtime._hook_session_ref = SessionRef(
        session_id="s-route", runtime_provider="codex", bare=True
    )
    assert await runtime._route_mcp_through_proxy(session) is session

    # Every other codex session has one — the citation projection
    # (core/hooks/builtin/citation_projection.py). It only needs the remote
    # servers (Valuz's source-metadata connectors); stdio ones stay direct.
    runtime._hook_session_ref = SessionRef(session_id="s-route", runtime_provider="codex")
    routed = await runtime._route_mcp_through_proxy(session)
    try:
        joined = "\n".join(
            _build_config_overrides(routed, None, "gpt", expose_toolkit=False, egress_base_url=None)
        )
        assert "/mcp/proxy/s-route/remote/" in joined and "example.com" not in joined
        assert "mcp_servers.upstream.command" in joined
    finally:
        await runtime._release_mcp_proxy()

    # Any other tool.call handler sees every MCP call: everything is proxied.
    async def watch(ctx, event, next_):  # noqa: ANN001
        return await next_()

    hook_registry.register(TOOL_CALL, watch, owner=OWNER)
    routed = await runtime._route_mcp_through_proxy(session)
    try:
        joined = "\n".join(
            _build_config_overrides(routed, None, "gpt", expose_toolkit=False, egress_base_url=None)
        )
        assert "/mcp/proxy/s-route/remote/" in joined and "/mcp/proxy/s-route/upstream/" in joined
        assert "example.com" not in joined and "secret" not in joined
        assert "mcp_servers.upstream.command" not in joined
    finally:
        await runtime._release_mcp_proxy()


async def test_dsh_patch_arms_the_bridge_and_proxies_only_with_a_handler() -> None:
    from src.runtimes.deepseek_harness.composition import build_session_patch
    from src.runtimes.deepseek_harness.runtime import DeepSeekHarnessRuntime

    runtime = DeepSeekHarnessRuntime.__new__(DeepSeekHarnessRuntime)
    runtime._hook_session_ref = None
    runtime._hook_bridge_token = None
    runtime._mcp_proxy_session_id = None
    session = _session()

    # Only the MCP-only citation projection listens: MCP goes through the
    # proxy, but the bridge for dsh's own tools stays off (no round trip per
    # bash / read).
    patch_session, bridge = await runtime._prepare_hook_bus(session)
    try:
        assert patch_session is not session and bridge is None
        assert not any(
            row.get("id") == "valuz-hook-bridge" for row in build_session_patch(patch_session)
        )
    finally:
        await runtime._release_hook_bus()

    async def watch(ctx, event, next_):  # noqa: ANN001
        return await next_()

    hook_registry.register(TOOL_CALL, watch, owner=OWNER)
    patch_session, bridge = await runtime._prepare_hook_bus(session)
    try:
        assert bridge is not None and bridge["events"] == ["tool.call"]
        assert "/hook-bridge/" in bridge["endpoint"]
        patch = build_session_patch(patch_session, hook_bridge=bridge)
        assert {"id": "valuz-hook-bridge", "config": bridge} in patch
        rows = next(row["insert"] for row in patch if "insert" in row)
        urls = [row["config"].get("url") for row in rows]
        assert all("/mcp/proxy/s-route/" in url for url in urls)
    finally:
        await runtime._release_hook_bus()
