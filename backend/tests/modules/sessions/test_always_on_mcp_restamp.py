"""Regression: always-on in-process MCP token must self-heal across restarts.

``settings.internal_mcp_token`` is per-process and baked into each session's
``mcp_servers`` headers at create-time. A session resumed after a backend
restart carries a stale ``X-Valuz-Internal`` → the in-process MCP gate 403s →
Claude Code parks the server in ``needsAuth`` (only OAuth stubs, real
``automation``/``playbook``/``doc_search``/``create_mcp`` tools hidden).
``send_message`` re-stamps the always-on set every turn so the stale token
self-heals.
"""

from __future__ import annotations

from app.schemas import (  # type: ignore[import-not-found]
    AgentConfigSchema,
    McpHttpServerConfigSchema,
    SessionData,
)

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.modules.sessions import capabilities


def _make_session(*, mcp_servers):
    return SessionData(
        id="sess-1",
        agent_config=AgentConfigSchema(id="agent-1", name="a"),
        cwd="/tmp/restamp-test",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        instructions="",
        skills=[],
        mcp_servers=list(mcp_servers),
        permission_mode="full_access",
        status="idle",
        created_at=0,
        user_id="local-test-owner",
        metadata={},
    )


def _always_on_set(token: str, *, base: str, tool_timeout_sec: float | None = None):
    servers = tuple(
        McpHttpServerConfigSchema(
            name=name,
            url=f"{base}/{slug}/mcp",
            transport="http",
            headers={"X-Valuz-Internal": token, "X-Valuz-Session-Id": "sess-1"},
            tool_timeout_sec=tool_timeout_sec,
        )
        for name, slug in (
            ("valuz_docs", "docs"),
            ("valuz_automations", "automations"),
            ("valuz_playbooks", "playbooks"),
            ("valuz_connectors", "connectors"),
        )
    )
    harness = McpHttpServerConfigSchema(
        name="harness",
        url=f"{base}/toolkit/base/mcp",
        transport="http",
        headers={"X-Valuz-Internal": token, "X-Valuz-Session-Id": "sess-1"},
        tool_timeout_sec=tool_timeout_sec,
    )
    return (*servers, harness)


def _stale_trio(token: str):
    """A pre-ADR-013 session snapshot — the legacy ``/internal/mcp`` path
    (models a session created before the rename; ``refresh_always_on_mcp_for_session``
    must self-heal it onto the current ``/_internal/mcp`` path, not just the
    token — see ``test_restamps_stale_token_and_preserves_external``)."""
    return _always_on_set(token, base="http://127.0.0.1:8000/internal/mcp")


def _current_trio(token: str):
    """The trio a FRESHLY-minted session carries today — same base
    ``always_on_http_mcp_servers`` actually generates (ADR-013:
    ``/_internal/mcp``). Used where the test's premise is "already
    up-to-date" (see ``test_noop_when_token_already_current``); using the
    legacy base here would make every field EXCEPT the token differ, forcing
    a PATCH the test asserts must NOT happen."""
    from valuz_agent.adapters.capability_resolver import (
        _INTERNAL_MCP_TOOL_TIMEOUT_SEC,
    )

    return _always_on_set(
        token,
        base="http://127.0.0.1:8000/_internal/mcp",
        tool_timeout_sec=_INTERNAL_MCP_TOOL_TIMEOUT_SEC,
    )


def _patch_client(monkeypatch, session):
    from valuz_agent.adapters import kernel_client

    updates: list = []

    async def _get(_user_id, _sid):
        return session

    async def _update(_user_id, sid, req):
        updates.append((sid, req))
        return session

    monkeypatch.setattr(kernel_client, "get_session", _get)
    monkeypatch.setattr(kernel_client, "update_session", _update)
    return updates


async def test_restamps_stale_token_and_preserves_external(monkeypatch):
    """A stale always-on token is rewritten to the current one; external MCP kept."""
    from valuz_agent.adapters import capability_resolver as cr

    monkeypatch.setattr(cr, "_mcp_token_cache", {})
    current = cr._mint_internal_mcp_token("local-test-owner")  # per-owner signed token

    external = McpHttpServerConfigSchema(
        name="valuz-search",
        url="https://data.valuz.cn/mcp/search",
        transport="http",
        headers={"Authorization": "Bearer xyz"},
    )
    session = _make_session(mcp_servers=(external, *_stale_trio("OLDTOKEN")))
    updates = _patch_client(monkeypatch, session)

    changed = await capabilities.refresh_always_on_mcp_for_session("sess-1", "local-test-owner")

    assert changed is True
    assert len(updates) == 1
    _sid, req = updates[0]
    by_name = {m.name: m for m in req.mcp_servers}
    # Every always-on entry (incl. the harness toolkit) carries the live per-owner token.
    for name in (
        "valuz_docs",
        "valuz_automations",
        "valuz_playbooks",
        "valuz_connectors",
        "harness",
    ):
        assert by_name[name].headers["X-Valuz-Internal"] == current
    # The user-attached external connector is untouched.
    assert by_name["valuz-search"].headers == {"Authorization": "Bearer xyz"}


async def test_noop_when_token_already_current(monkeypatch):
    """No PATCH (prompt cache stays warm) when the token already matches."""
    from valuz_agent.adapters import capability_resolver as cr

    monkeypatch.setattr(cr, "_mcp_token_cache", {})
    current = cr._mint_internal_mcp_token("local-test-owner")  # stable per-owner (cached)
    session = _make_session(mcp_servers=_current_trio(current))
    updates = _patch_client(monkeypatch, session)

    changed = await capabilities.refresh_always_on_mcp_for_session("sess-1", "local-test-owner")

    assert changed is False
    assert updates == []


async def test_external_oauth_connector_headers_refresh_on_restamp(monkeypatch):
    """A user-attached connector entry gets re-resolved with CURRENT
    credentials each turn — a re-auth (or expiry refresh) must reach existing
    sessions, not only brand-new ones (Reportify tokens live ~1h)."""
    stale_external = McpHttpServerConfigSchema(
        name="valuz-search",
        url="https://data.valuz.cn/mcp/search",
        transport="http",
        headers={"Authorization": "Bearer STALE-JWT"},
    )
    session = _make_session(mcp_servers=(stale_external, *_current_trio("CURRENT")))

    fresh_external = McpHttpServerConfigSchema(
        name="valuz-search",
        url="https://data.valuz.cn/mcp/search",
        transport="http",
        headers={"Authorization": "Bearer FRESH-JWT"},
    )

    async def fake_resolve(*, enabled_slugs, connectors=None, user_id=None):
        assert enabled_slugs == ["valuz-search"]
        return [fresh_external]

    import valuz_agent.adapters.mcp_resolver as mcp_resolver

    monkeypatch.setattr(mcp_resolver, "resolve_mcp_servers", fake_resolve)

    saved = {}

    async def fake_get_session(user_id, session_id):
        return session

    async def fake_update_session(user_id, session_id, req):
        saved["mcp"] = list(req.mcp_servers)

    monkeypatch.setattr(capabilities.kernel_client, "get_session", fake_get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", fake_update_session)

    changed = await capabilities.refresh_always_on_mcp_for_session("sess-1", "local-test-owner")
    assert changed is True
    external = [m for m in saved["mcp"] if m.name == "valuz-search"]
    assert external and external[0].headers["Authorization"] == "Bearer FRESH-JWT"


async def test_external_entry_kept_when_reresolve_yields_nothing(monkeypatch):
    """A connector that no longer resolves (deleted / disabled / credentials
    gone) keeps its existing snapshot — same failure surface as before."""
    stale_external = McpHttpServerConfigSchema(
        name="gone-connector",
        url="https://example.com/mcp",
        transport="http",
        headers={"Authorization": "Bearer OLD"},
    )
    session = _make_session(mcp_servers=(stale_external, *_current_trio("CURRENT")))

    async def fake_resolve(*, enabled_slugs, connectors=None, user_id=None):
        return []

    import valuz_agent.adapters.mcp_resolver as mcp_resolver

    monkeypatch.setattr(mcp_resolver, "resolve_mcp_servers", fake_resolve)

    async def fake_get_session(user_id, session_id):
        return session

    saved = {}

    async def fake_update_session(user_id, session_id, req):
        saved["mcp"] = list(req.mcp_servers)

    monkeypatch.setattr(capabilities.kernel_client, "get_session", fake_get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", fake_update_session)

    await capabilities.refresh_always_on_mcp_for_session("sess-1", "local-test-owner")
    kept = [m for m in (saved.get("mcp") or session.mcp_servers) if m.name == "gone-connector"]
    assert kept and kept[0].headers["Authorization"] == "Bearer OLD"
