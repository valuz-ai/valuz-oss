"""Layer-3 of the project-less-chat automation fix: ``list_members`` must not
be an empty dead-end in a chat.

A chat workspace has no deployed *project* members, but the conversation is
driven by its bound agent. ``_bound_agent_member`` shapes that bound agent
into a ``list_members`` row so the handler can surface it as a fallback — the
slug is then directly usable as an automation's ``agent_slug``.

We unit-test the pure helper (the handler change is a thin
``if not members: members = [bound]`` around it).
"""

from __future__ import annotations

from typing import Any

from valuz_agent.modules.tasks.tools import handlers


class _FakeSession:
    def __init__(self, metadata: dict[str, Any], agent_id: str | None) -> None:
        self.metadata = metadata
        self.agent_id = agent_id


class _FakeAgent:
    def __init__(self, name: str, runtime_provider: str, instructions: str) -> None:
        self.name = name
        self.runtime_provider = runtime_provider
        self.instructions = instructions


async def test_bound_agent_member_shapes_chat_agent(monkeypatch) -> None:
    async def _load_agent(agent_id: str) -> _FakeAgent:
        assert agent_id == "ka-1"
        return _FakeAgent("Default Assistant", "claude_agent", "You help with anything.")

    monkeypatch.setattr(handlers.kernel_store, "load_agent", _load_agent)

    sess = _FakeSession(
        metadata={"valuz": {"agent_slug": "default-assistant"}},
        agent_id="ka-1",
    )
    member = await handlers._bound_agent_member(sess)
    assert member == {
        "slug": "default-assistant",
        "name": "Default Assistant",
        "runtime": "claude_agent",
        "source_agent_slug": "default-assistant",
        "role_summary": "You help with anything.",
    }


async def test_bound_agent_member_none_without_bound_slug() -> None:
    sess = _FakeSession(metadata={"valuz": {}}, agent_id="ka-1")
    assert await handlers._bound_agent_member(sess) is None
    # also: no valuz metadata at all
    assert await handlers._bound_agent_member(_FakeSession(metadata={}, agent_id="ka-1")) is None


async def test_bound_agent_member_degrades_when_kernel_agent_missing(monkeypatch) -> None:
    """An orphaned bound agent still yields a usable row (slug echoes through)."""

    async def _no_agent(_id: str) -> None:
        return None

    monkeypatch.setattr(handlers.kernel_store, "load_agent", _no_agent)

    sess = _FakeSession(metadata={"valuz": {"agent_slug": "ghost"}}, agent_id="ka-x")
    member = await handlers._bound_agent_member(sess)
    assert member == {
        "slug": "ghost",
        "name": "ghost",
        "runtime": "unknown",
        "source_agent_slug": "ghost",
        "role_summary": "",
    }
