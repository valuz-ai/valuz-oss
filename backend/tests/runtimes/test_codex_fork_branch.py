"""``RuntimePort.fork_session`` — the third thread-birth verb beside
start/resume (docs/design/session-fork.md §6.5).

Codex implements it as ``thread/fork(lastTurnId)`` after the standard
app-server spawn, backfills ``session.runtime_session_id`` and leaves the
runtime warm on the new thread. ``_ensure_thread`` keeps its plain
start/resume shape — fork is never inferred from session state. The other
runtimes' implementations are pinned in test_claude_fork_session.py and
test_deepagents_fork_session.py.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Session
from src.runtimes.codex.runtime import CodexRuntime


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


class _FakeClient:
    def __init__(self) -> None:
        self.fork_calls: list[tuple[str, object]] = []
        self.start_calls: list[object] = []
        self.resume_calls: list[tuple[str, object]] = []

    async def thread_fork(self, thread_id: str, params: object) -> SimpleNamespace:
        self.fork_calls.append((thread_id, params))
        return SimpleNamespace(thread=SimpleNamespace(id="th-forked"))

    async def thread_start(self, params: object) -> SimpleNamespace:
        self.start_calls.append(params)
        return SimpleNamespace(thread=SimpleNamespace(id="th-fresh"))

    async def thread_resume(self, thread_id: str, params: object) -> SimpleNamespace:
        self.resume_calls.append((thread_id, params))
        return SimpleNamespace(thread=SimpleNamespace(id=thread_id))


def _runtime() -> tuple[CodexRuntime, _FakeClient]:
    rt = CodexRuntime(
        config=AgentConfig(id="a", name="a"),
        model="gpt-5.5",
        event_sink=_RecordingSink(),
        workspace_root="/tmp/ws",
    )
    client = _FakeClient()
    # ``_ensure_codex`` early-returns when the client is already present,
    # so the spawn path is bypassed and ``fork_session`` goes straight to
    # the ``thread/fork`` RPC.
    rt._codex = SimpleNamespace(_client=client)
    return rt, client


def _session(**overrides: object) -> Session:
    defaults: dict = {
        "id": "sess-fork",
        "agent_config": AgentConfig(id="agent-1", name="tester"),
        "cwd": "/tmp/ws",
        "user_id": "owner",
        "runtime_provider": "codex",
    }
    defaults.update(overrides)
    return Session(**defaults)


def test_fork_session_calls_thread_fork_and_backfills_id() -> None:
    rt, client = _runtime()
    session = _session()

    new_id = asyncio.run(
        rt.fork_session(session, source_native_session_id="th-src", anchor="turn-2")
    )

    assert new_id == "th-forked"
    assert len(client.fork_calls) == 1
    thread_id, params = client.fork_calls[0]
    assert thread_id == "th-src"
    assert params.thread_id == "th-src"
    assert params.last_turn_id == "turn-2"
    # Config surface rides along like thread_start (cwd, model, tri-axis).
    assert params.cwd == "/tmp/ws"
    assert params.model == "gpt-5.5"
    assert session.runtime_session_id == "th-forked"
    # Warm on the new thread — the first Send resumes without a cold start.
    assert rt._thread is not None and rt._thread.id == "th-forked"
    assert not client.start_calls and not client.resume_calls


def test_fork_session_at_tail_sends_no_anchor() -> None:
    rt, client = _runtime()
    session = _session()

    asyncio.run(rt.fork_session(session, source_native_session_id="th-src"))

    _thread_id, params = client.fork_calls[0]
    assert params.last_turn_id is None
    assert session.runtime_session_id == "th-forked"


def test_ensure_thread_still_resumes_and_starts_only() -> None:
    rt, client = _runtime()
    session = _session(runtime_session_id="th-existing")
    asyncio.run(rt._ensure_thread(session))
    assert client.resume_calls and not client.fork_calls

    rt2, client2 = _runtime()
    fresh = _session()
    asyncio.run(rt2._ensure_thread(fresh))
    assert client2.start_calls and not client2.fork_calls
    assert fresh.runtime_session_id == "th-fresh"
