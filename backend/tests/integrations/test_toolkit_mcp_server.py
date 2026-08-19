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
from valuz_agent.integrations import _mcp_asgi


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

    token = _mcp_asgi.set_current_mcp_context(session_id="sess-42", user_id="u1")
    try:
        result = asyncio.run(_call_tool(server, "echo", {"text": "hi"}))
    finally:
        _mcp_asgi.reset_current_mcp_context(token)

    content = result.root.content
    assert content[0].text == "echo:hi@sess-42"


def test_call_tool_outside_session_scope_fails() -> None:

    tk.install_toolkit_toolsets(base=(_echo_tool(),), lead=())
    server = tk._build_server("base")
    # The autouse ``_seed_mcp_context`` fixture publishes a context for every
    # test; clear it here so we genuinely exercise the no-session-scope path.
    token = _mcp_asgi._mcp_context.set(None)
    try:
        result = asyncio.run(_call_tool(server, "echo", {}))
    finally:
        _mcp_asgi._mcp_context.reset(token)
    # The lowlevel server converts handler exceptions into an error result.
    assert result.root.isError


def test_tool_result_is_error_projected_as_text_prefix() -> None:

    tk.install_toolkit_toolsets(base=(_failing_tool(),), lead=())
    server = tk._build_server("base")
    token = _mcp_asgi.set_current_mcp_context(session_id="sess-1", user_id="u1")
    try:
        result = asyncio.run(_call_tool(server, "fails", {}))
    finally:
        _mcp_asgi.reset_current_mcp_context(token)
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
    from valuz_agent.adapters.capability_resolver import _mint_internal_mcp_token

    tk.install_toolkit_toolsets(base=(), lead=())
    app = tk.build_toolkit_mcp_asgi("base")

    token = _mint_internal_mcp_token("owner-x")  # valid per-owner token
    status = asyncio.run(_run_asgi(app, _scope({"x-valuz-internal": token})))
    assert status == 400  # verified owner, but missing X-Valuz-Session-Id


def test_asgi_per_owner_verification(monkeypatch) -> None:
    """Built-in MCP auth is per-owner: forged signature and cross-owner session
    are both rejected; a token whose owner matches the session passes (ADR-012)."""
    from types import SimpleNamespace

    from valuz_agent.adapters import data_reader as dr
    from valuz_agent.adapters.capability_resolver import _mint_internal_mcp_token
    from valuz_agent.boot.kernel import mint_data_service_token

    tk.install_toolkit_toolsets(base=(), lead=())
    app = tk.build_toolkit_mcp_asgi("base")

    class _Reader:
        def __init__(self, owner: str) -> None:
            self._owner = owner

        async def list_all_sessions(self, *, ids=None, limit=50, **_):
            return [SimpleNamespace(user_id=self._owner)]

    def _run(tok: str) -> int:
        return asyncio.run(
            _run_asgi(app, _scope({"x-valuz-internal": tok, "x-valuz-session-id": "s"}))
        )

    tok_a = _mint_internal_mcp_token("A")  # creates A's real per-owner secret
    forged = mint_data_service_token("attacker-secret", user_id="A")  # signed with wrong key
    try:
        dr.bind_data_reader(_Reader("A"))
        assert _run(forged) == 403  # bad signature under A's real secret
        dr.bind_data_reader(_Reader("B"))
        assert _run(tok_a) == 403  # A's token, session owned by B → cross-owner reject
    finally:
        dr.bind_data_reader(None)

    # Positive path at the verifier level (the ASGI success case would reach the
    # MCP inner app, which needs a running task group — out of scope here):
    claims = asyncio.run(_mcp_asgi._verify_token_owner(tok_a))
    assert claims is not None and claims.user_id == "A"  # valid token → owner
    assert asyncio.run(_mcp_asgi._verify_token_owner(forged)) is None  # forged → rejected


def test_asgi_awaits_bound_sandbox_credential_verifier() -> None:
    """An overlay can bind an async opaque-credential verifier without changing MCP."""
    from types import SimpleNamespace

    from valuz_agent.adapters import data_reader as dr
    from valuz_agent.ports.sandbox_credential import (
        SandboxCredentialClaims,
        get_sandbox_credential_verifier,
        set_sandbox_credential_verifier,
    )

    calls: list[str | None] = []

    class _Verifier:
        async def verify(self, credential: str | None) -> SandboxCredentialClaims | None:
            await asyncio.sleep(0)
            calls.append(credential)
            return SandboxCredentialClaims(user_id="owner-async")

    class _Reader:
        async def list_all_sessions(self, *, ids=None, limit=50, **_):
            return [SimpleNamespace(user_id="owner-async")]

    async def _inner(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    previous = get_sandbox_credential_verifier()
    try:
        set_sandbox_credential_verifier(_Verifier())
        dr.bind_data_reader(_Reader())
        app = _mcp_asgi.build_internal_mcp_asgi(_inner)
        status = asyncio.run(
            _run_asgi(
                app,
                _scope(
                    {
                        "x-valuz-internal": "opaque-sandbox-credential",
                        "x-valuz-session-id": "session-1",
                    }
                ),
            )
        )
    finally:
        dr.bind_data_reader(None)
        set_sandbox_credential_verifier(previous)

    assert status == 204
    assert calls == ["opaque-sandbox-credential"]


def test_asgi_rejects_session_bound_managed_credential_for_another_session() -> None:
    """A managed MCP credential cannot be replayed through another session."""
    from types import SimpleNamespace

    from valuz_agent.adapters import data_reader as dr
    from valuz_agent.ports.sandbox_credential import (
        SandboxCredentialClaims,
        get_sandbox_credential_verifier,
        set_sandbox_credential_verifier,
    )

    class _Verifier:
        async def verify(self, credential: str | None) -> SandboxCredentialClaims | None:
            assert credential == "managed_for_session_1"
            return SandboxCredentialClaims(user_id="owner-a", session_id="session-1")

    class _Reader:
        async def list_all_sessions(self, *, ids=None, limit=50, **_):
            return [SimpleNamespace(user_id="owner-a")]

    async def _inner(scope, receive, send) -> None:
        raise AssertionError("a mismatched session must not reach MCP")

    previous = get_sandbox_credential_verifier()
    try:
        set_sandbox_credential_verifier(_Verifier())
        dr.bind_data_reader(_Reader())
        app = _mcp_asgi.build_internal_mcp_asgi(_inner)
        status = asyncio.run(
            _run_asgi(
                app,
                _scope(
                    {
                        "x-valuz-internal": "managed_for_session_1",
                        "x-valuz-session-id": "session-2",
                    }
                ),
            )
        )
    finally:
        dr.bind_data_reader(None)
        set_sandbox_credential_verifier(previous)

    assert status == 403


def test_asgi_fails_closed_when_sandbox_credential_verifier_errors() -> None:
    from valuz_agent.ports.sandbox_credential import (
        SandboxCredentialClaims,
        get_sandbox_credential_verifier,
        set_sandbox_credential_verifier,
    )

    class _BrokenVerifier:
        async def verify(self, credential: str | None) -> SandboxCredentialClaims | None:
            raise RuntimeError("identity backend unavailable")

    async def _inner(scope, receive, send) -> None:
        raise AssertionError("unverified requests must not reach MCP")

    previous = get_sandbox_credential_verifier()
    try:
        set_sandbox_credential_verifier(_BrokenVerifier())
        app = _mcp_asgi.build_internal_mcp_asgi(_inner)
        status = asyncio.run(
            _run_asgi(
                app,
                _scope(
                    {
                        "x-valuz-internal": "opaque-sandbox-credential",
                        "x-valuz-session-id": "session-1",
                    }
                ),
            )
        )
    finally:
        set_sandbox_credential_verifier(previous)

    assert status == 403


def test_unknown_toolset_rejected() -> None:
    with pytest.raises(ValueError):
        tk.build_toolkit_mcp_asgi("nope")
    with pytest.raises(ValueError):
        tk.toolkit_mcp_url(base_url="http://x", toolset="nope")


def test_toolkit_mcp_url_uses_new_internal_path() -> None:
    """ADR-013: ``toolkit_mcp_url`` mints the ``/_internal/...`` path."""
    assert tk.toolkit_mcp_url(base_url="http://x", toolset="base") == (
        "http://x/_internal/mcp/toolkit/base/mcp"
    )


# ── always-on injection ────────────────────────────────────────────────


def test_always_on_set_includes_harness_per_toolkit() -> None:
    import asyncio

    from valuz_agent.adapters.capability_resolver import (
        always_on_http_mcp_servers,
        harness_toolkit_for_run_kind,
    )

    base_set = asyncio.run(always_on_http_mcp_servers("sess-1", owner_user_id="u1"))
    by_name = {m.name: m for m in base_set}
    # ADR-013: newly minted URLs use "/_internal/..." — the legacy
    # "/internal/..." mount stays reachable (api/app.py::_mount_internal) for
    # pre-rename session snapshots, but is never generated for new sessions.
    assert by_name["harness"].url.endswith("/_internal/mcp/toolkit/base/mcp")
    assert by_name["harness"].headers["X-Valuz-Session-Id"] == "sess-1"

    lead_set = asyncio.run(
        always_on_http_mcp_servers("sess-1", owner_user_id="u1", toolkit="lead")
    )
    assert {m.name for m in lead_set} == set(by_name)
    assert next(m for m in lead_set if m.name == "harness").url.endswith(
        "/_internal/mcp/toolkit/lead/mcp"
    )

    assert harness_toolkit_for_run_kind("lead") == "lead"
    assert harness_toolkit_for_run_kind("subtask") == "base"
    assert harness_toolkit_for_run_kind(None) == "base"


def test_always_on_set_resolves_one_opaque_credential_for_every_builtin_mcp() -> None:
    import asyncio

    from valuz_agent.adapters.capability_resolver import always_on_http_mcp_servers
    from valuz_agent.ports.sandbox_credential import (
        get_sandbox_credential_verifier,
        set_sandbox_credential_verifier,
    )

    class _CredentialPort:
        def __init__(self) -> None:
            self.owners: list[str] = []

        async def credential_for(self, owner_user_id: str) -> str:
            self.owners.append(owner_user_id)
            return "vzs_owner_credential"

        async def verify(self, credential: str | None):  # type: ignore[no-untyped-def]
            return None

    original = get_sandbox_credential_verifier()
    credential_port = _CredentialPort()
    set_sandbox_credential_verifier(credential_port)
    try:
        servers = asyncio.run(
            always_on_http_mcp_servers("sess-1", owner_user_id="owner-1")
        )
    finally:
        set_sandbox_credential_verifier(original)

    assert credential_port.owners == ["owner-1"]
    assert servers
    assert {
        server.headers["X-Valuz-Internal"] for server in servers
    } == {"vzs_owner_credential"}
