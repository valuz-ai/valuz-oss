"""Regression: always-on in-process MCP token must self-heal across restarts.

``settings.internal_mcp_token`` is per-process and baked into each session's
``mcp_servers`` headers at create-time. A session resumed after a backend
restart carries a stale ``X-Valuz-Internal`` → the in-process MCP gate 403s →
Claude Code parks the server in ``needsAuth`` (only OAuth stubs, real
``automation``/``doc_search``/``create_mcp`` tools hidden). ``send_message``
re-stamps the trio every turn so the stale token self-heals.
"""

from __future__ import annotations

from app.schemas import (  # type: ignore[import-not-found]
    AgentConfigSchema,
    McpHttpServerConfigSchema,
    SessionData,
)

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.infra.config import settings
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
        metadata={},
    )


def _stale_trio(token: str):
    base = "http://127.0.0.1:8000/internal/mcp"
    return tuple(
        McpHttpServerConfigSchema(
            name=name,
            url=f"{base}/{slug}/mcp",
            transport="http",
            headers={"X-Valuz-Internal": token, "X-Valuz-Session-Id": "sess-1"},
        )
        for name, slug in (
            ("valuz_docs", "docs"),
            ("valuz_automations", "automations"),
            ("valuz_connectors", "connectors"),
        )
    )


def _patch_client(monkeypatch, session):
    from valuz_agent.adapters import kernel_client

    updates: list = []

    async def _get(_sid):
        return session

    async def _update(sid, req):
        updates.append((sid, req))
        return session

    monkeypatch.setattr(kernel_client, "get_session", _get)
    monkeypatch.setattr(kernel_client, "update_session", _update)
    return updates


async def test_restamps_stale_token_and_preserves_external(monkeypatch):
    """A stale always-on token is rewritten to the current one; external MCP kept."""
    monkeypatch.setattr(settings, "internal_mcp_token_override", "NEWTOKEN")

    external = McpHttpServerConfigSchema(
        name="valuz-search",
        url="https://mcp.reportify.cn/search/mcp",
        transport="http",
        headers={"Authorization": "Bearer xyz"},
    )
    session = _make_session(mcp_servers=(external, *_stale_trio("OLDTOKEN")))
    updates = _patch_client(monkeypatch, session)

    changed = await capabilities.refresh_always_on_mcp_for_session("sess-1")

    assert changed is True
    assert len(updates) == 1
    _sid, req = updates[0]
    by_name = {m.name: m for m in req.mcp_servers}
    # All three always-on entries now carry the live token.
    for name in ("valuz_docs", "valuz_automations", "valuz_connectors"):
        assert by_name[name].headers["X-Valuz-Internal"] == "NEWTOKEN"
    # The user-attached external connector is untouched.
    assert by_name["valuz-search"].headers == {"Authorization": "Bearer xyz"}


async def test_noop_when_token_already_current(monkeypatch):
    """No PATCH (prompt cache stays warm) when the token already matches."""
    monkeypatch.setattr(settings, "internal_mcp_token_override", "CURRENT")
    session = _make_session(mcp_servers=_stale_trio("CURRENT"))
    updates = _patch_client(monkeypatch, session)

    changed = await capabilities.refresh_always_on_mcp_for_session("sess-1")

    assert changed is False
    assert updates == []
