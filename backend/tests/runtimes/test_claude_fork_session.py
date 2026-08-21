"""``ClaudeAgentRuntime.fork_session`` — offline transcript fork (design P2).

The SDK's ``fork_session`` transform is filesystem-only: it slices the
source transcript at the anchor uuid (inclusive), re-mints UUIDs and
returns the new session id synchronously — which is exactly what the
eager ``RuntimePort.fork_session`` contract needs (spawn-time
``resume_session_at`` cannot mint an id before the first query;
probe-verified). The runtime backfills ``session.runtime_session_id``
and the first Send resumes the new id through the normal spawn path.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

import claude_agent_sdk

from src.core.agent_config import AgentConfig
from src.core.types import Session
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


def _runtime(workspace_root: str = "/tmp/ws") -> ClaudeAgentRuntime:
    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = workspace_root
    return rt


def _session() -> Session:
    return Session(
        id="sess-fork",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd="/tmp/cwd",
        user_id="owner",
        runtime_provider="claude_agent",
    )


def _spy(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def fake_fork(session_id, directory=None, up_to_message_id=None, title=None):
        calls.append((session_id, directory, up_to_message_id, title))
        return SimpleNamespace(session_id="sdk-forked")

    monkeypatch.setattr(claude_agent_sdk, "fork_session", fake_fork)
    return calls


def test_message_fork_slices_at_anchor_and_backfills(monkeypatch) -> None:
    calls = _spy(monkeypatch)
    session = _session()

    new_id = asyncio.run(
        _runtime().fork_session(session, source_native_session_id="sdk-src", anchor="uuid-2")
    )

    assert new_id == "sdk-forked"
    assert session.runtime_session_id == "sdk-forked"
    # Anchor is the transcript message uuid, inclusive; directory scopes
    # the transcript lookup to the session workspace; title stays SDK-
    # derived (the host owns display naming via valuz metadata).
    assert calls == [("sdk-src", "/tmp/ws", "uuid-2", None)]


def test_tail_fork_copies_full_transcript(monkeypatch) -> None:
    calls = _spy(monkeypatch)

    asyncio.run(_runtime().fork_session(_session(), source_native_session_id="sdk-src"))

    assert calls[0][2] is None


def test_directory_falls_back_to_session_cwd(monkeypatch) -> None:
    calls = _spy(monkeypatch)

    asyncio.run(
        _runtime(workspace_root="").fork_session(_session(), source_native_session_id="sdk-src")
    )

    assert calls[0][1] == "/tmp/cwd"
