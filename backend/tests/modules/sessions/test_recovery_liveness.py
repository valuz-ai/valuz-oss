"""Liveness-aware boot recovery — only genuinely-stranded sessions are reset.

``recover_running_sessions`` must skip any ``running`` session whose sandbox
scope still holds a LIVE remote sandbox (multiple host replicas + per-scope
sandboxes share one durable — a booting replica must not clobber an in-flight
turn on another sandbox), and reset the rest via the kernel's per-session
``reset_stranded_session`` (idle + resumable ``host_restart``).
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
import valuz_agent.modules.sessions.recovery as rec
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.sandbox_allocator import SandboxLease, SandboxScope
from valuz_agent.ports.sandbox_provider import SandboxEndpoint


def _session(sid: str, status: str = "running", user_id: str = "u1") -> SimpleNamespace:
    return SimpleNamespace(id=sid, user_id=user_id, status=status)


class _Alloc:
    """peek-only allocator: live scopes get a real endpoint, others None."""

    def __init__(self, live_scopes: set[str]) -> None:
        self._live = live_scopes
        self.peeked: list[SandboxScope | None] = []

    async def peek(
        self, *, owner_user_id: str, scope: SandboxScope | None = None
    ) -> SandboxLease | None:
        self.peeked.append(scope)
        if scope is not None and scope.key in self._live:
            return SandboxLease(
                endpoint=SandboxEndpoint(
                    sandbox_id=scope.key, base_url=f"https://{scope.key}.pool", token="t"
                )
            )
        return None


@pytest.fixture
def wired(monkeypatch):  # noqa: ANN001, ANN201
    """Stub the reader, task-scope lookup and the kernel reset call."""
    sessions: list[SimpleNamespace] = []
    resets: list[tuple[str, str]] = []

    class _Reader:
        async def list_all_sessions(self, *, limit):  # noqa: ANN001
            return sessions

    monkeypatch.setattr(rec, "data_reader", lambda: _Reader())

    async def _reset(user_id: str, session_id: str) -> bool:
        resets.append((user_id, session_id))
        return True

    monkeypatch.setattr(rec.kernel_client, "reset_stranded_session", _reset)

    # Non-task sessions by default; tests override for task members.
    import valuz_agent.modules.tasks.sandbox_scope as ts

    async def _no_task(user_id: str, session_id: str):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(ts, "resolve_sandbox_scope", _no_task)
    return sessions, resets, monkeypatch


async def test_should_skip_session_whose_sandbox_is_alive(wired) -> None:
    sessions, resets, monkeypatch = wired
    sessions.append(_session("s-live"))
    alloc = _Alloc(live_scopes={"session:s-live"})
    monkeypatch.setattr(ext, "sandbox_allocator", alloc, raising=False)

    recovered = await rec.recover_running_sessions()

    assert recovered == 0
    assert resets == []  # live sandbox → untouched
    assert [s.key for s in alloc.peeked if s] == ["session:s-live"]


async def test_should_reset_session_whose_sandbox_is_gone(wired) -> None:
    sessions, resets, monkeypatch = wired
    sessions.append(_session("s-dead"))
    monkeypatch.setattr(ext, "sandbox_allocator", _Alloc(live_scopes=set()), raising=False)

    recovered = await rec.recover_running_sessions()

    assert recovered == 1
    assert resets == [("u1", "s-dead")]


async def test_should_reset_everything_without_an_allocator(wired) -> None:
    # Single-process deployment (no allocator): this freshly-booted process
    # runs everything, so every running row is stranded — pre-liveness behaviour.
    sessions, resets, monkeypatch = wired
    sessions.append(_session("s1"))
    sessions.append(_session("s2", status="idle"))  # never touched
    monkeypatch.setattr(ext, "sandbox_allocator", None, raising=False)

    recovered = await rec.recover_running_sessions()

    assert recovered == 1
    assert resets == [("u1", "s1")]


async def test_should_route_task_member_through_task_scope(wired) -> None:
    # A task member's sandbox lives under task:{id} — liveness must be checked
    # there, not under session:{id}.
    sessions, resets, monkeypatch = wired
    sessions.append(_session("member-1"))
    import valuz_agent.modules.tasks.sandbox_scope as ts

    async def _task_scope(user_id: str, session_id: str):  # noqa: ANN001, ANN202
        return SandboxScope(kind="task", id="t9")

    monkeypatch.setattr(ts, "resolve_sandbox_scope", _task_scope)
    alloc = _Alloc(live_scopes={"task:t9"})
    monkeypatch.setattr(ext, "sandbox_allocator", alloc, raising=False)

    recovered = await rec.recover_running_sessions()

    assert recovered == 0 and resets == []  # task sandbox alive → member untouched
    assert [s.key for s in alloc.peeked if s] == ["task:t9"]


async def test_should_skip_reset_when_liveness_probe_fails(wired) -> None:
    # Fail-closed: an erroring probe must not lead to resetting a possibly-live
    # session; recovery waits for the next boot instead.
    sessions, resets, monkeypatch = wired
    sessions.append(_session("s1"))

    class _Boom:
        async def peek(self, **kw):  # noqa: ANN003
            raise RuntimeError("allocator down")

    monkeypatch.setattr(ext, "sandbox_allocator", _Boom(), raising=False)

    recovered = await rec.recover_running_sessions()

    assert recovered == 0 and resets == []
