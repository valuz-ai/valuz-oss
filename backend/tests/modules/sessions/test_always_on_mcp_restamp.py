"""Regression: always-on in-process MCP token must self-heal across restarts.

``settings.internal_mcp_token`` is per-process and baked into each session's
``mcp_servers`` headers at create-time. A session resumed after a backend
restart carries a stale ``X-Valuz-Internal`` → the in-process MCP gate 403s →
Claude Code parks the server in ``needsAuth`` (only OAuth stubs, real
``automation``/``doc_search``/``create_mcp`` tools hidden). ``send_message``
re-stamps the trio every turn so the stale token self-heals.
"""

from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.infra.config import settings
from valuz_agent.modules.sessions import capabilities


def _make_session(*, mcp_servers):
    from src.core.types import Session  # type: ignore[import-not-found]

    return Session(
        id="sess-1",
        project_id="proj-1",
        agent_id="agent-1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        model_provider=None,
        model_settings=None,
        instructions="",
        skills=(),
        mcp_servers=mcp_servers,
        permission_mode="full_access",
        status="idle",
        stop_reason=None,
        created_at=0,
        metadata={},
    )


def _stale_trio(token: str):
    from src.core.types import McpHttpServerConfig  # type: ignore[import-not-found]

    base = "http://127.0.0.1:8000/internal/mcp"
    return tuple(
        McpHttpServerConfig(
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


def test_restamps_stale_token_and_preserves_external(monkeypatch):
    """A stale always-on token is rewritten to the current one; external MCP kept."""
    from src.core.types import McpHttpServerConfig  # type: ignore[import-not-found]

    monkeypatch.setattr(settings, "internal_mcp_token_override", "NEWTOKEN")

    external = McpHttpServerConfig(
        name="valuz-search",
        url="https://mcp.reportify.cn/search/mcp",
        transport="http",
        headers={"Authorization": "Bearer xyz"},
    )
    session = _make_session(mcp_servers=(external, *_stale_trio("OLDTOKEN")))

    from valuz_agent.adapters import kernel_sync

    saved: list = []
    monkeypatch.setattr(kernel_sync, "load_session_sync", lambda _sid: session)
    monkeypatch.setattr(kernel_sync, "save_session_sync", lambda s: saved.append(s))

    changed = capabilities.refresh_always_on_mcp_for_session("sess-1")

    assert changed is True
    assert len(saved) == 1
    by_name = {m.name: m for m in saved[0].mcp_servers}
    # All three always-on entries now carry the live token.
    for name in ("valuz_docs", "valuz_automations", "valuz_connectors"):
        assert by_name[name].headers["X-Valuz-Internal"] == "NEWTOKEN"
    # The user-attached external connector is untouched.
    assert by_name["valuz-search"].headers == {"Authorization": "Bearer xyz"}


def test_noop_when_token_already_current(monkeypatch):
    """No save (prompt cache stays warm) when the token already matches."""
    monkeypatch.setattr(settings, "internal_mcp_token_override", "CURRENT")
    session = _make_session(mcp_servers=_stale_trio("CURRENT"))

    from valuz_agent.adapters import kernel_sync

    saved: list = []
    monkeypatch.setattr(kernel_sync, "load_session_sync", lambda _sid: session)
    monkeypatch.setattr(kernel_sync, "save_session_sync", lambda s: saved.append(s))

    changed = capabilities.refresh_always_on_mcp_for_session("sess-1")

    assert changed is False
    assert saved == []
