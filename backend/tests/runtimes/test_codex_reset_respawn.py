"""Codex runtime drops a dead subprocess so the next turn can respawn.

Regression for the "task keeps failing with ``[Errno 32] Broken pipe`` on
retry": the codex process is long-lived per session (``_ensure_codex``
early-returns when ``self._codex`` is set). When it died mid-session — it
"closed stdout" / the pipe broke — the cached client became a corpse and every
retry wrote into a dead pipe, so the session could never recover. ``run``'s
unexpected-error path now calls ``_reset_codex_session`` so the retry spawns a
fresh process (and resumes the server-side thread by id).
"""

# ruff: noqa: I001 — ``valuz_agent.boot.kernel`` must import BEFORE ``src.*``
# (it injects the kernel onto sys.path); isort would reorder and break that.
from __future__ import annotations

import asyncio
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for ``src.*``
from src.runtimes.codex.runtime import (  # type: ignore[import-not-found]
    CodexRuntime,
)


class _StubCodex:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _bare_runtime() -> Any:
    # Bypass ``__init__`` — it needs an AgentConfig / EventSink / the codex
    # SDK. The reset path only touches the few attributes set below.
    rt = object.__new__(CodexRuntime)
    rt._codex = None
    rt._thread = None
    rt._active_turn = None
    rt._registered_session_id = None
    return rt


def test_reset_drops_dead_codex_and_thread() -> None:
    rt = _bare_runtime()
    stub = _StubCodex()
    rt._codex = stub
    rt._thread = object()
    rt._active_turn = object()

    asyncio.run(rt._reset_codex_session())

    # All cached process/thread/turn handles cleared → ``_ensure_codex`` /
    # ``_ensure_thread`` will rebuild on the next turn instead of reusing the
    # corpse.
    assert rt._codex is None
    assert rt._thread is None
    assert rt._active_turn is None
    assert stub.closed is True


def test_reset_is_safe_when_close_raises() -> None:
    # Closing an already-dead client commonly raises (that's how we got here) —
    # it must be swallowed so the reset still nulls the handles.
    class _BoomCodex:
        async def close(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    rt = _bare_runtime()
    rt._codex = _BoomCodex()
    rt._thread = object()

    asyncio.run(rt._reset_codex_session())  # must not raise

    assert rt._codex is None
    assert rt._thread is None


def test_ensure_codex_guard_short_circuits_only_while_alive() -> None:
    # The bug was ``_ensure_codex``'s ``if self._codex is not None: return``
    # reusing a dead client. After a reset the guard no longer short-circuits.
    rt = _bare_runtime()
    rt._codex = _StubCodex()
    assert rt._codex is not None  # alive → guard would early-return

    asyncio.run(rt._reset_codex_session())
    assert rt._codex is None  # dead → guard falls through → respawn
