"""Interrupt cancellation hygiene: a swallowed interrupt must not poison the
turn task, and the idle-drainer teardown must not misread a stale count.

Root cause of the "run.failed / CancelledError right after a completed answer"
report: ``interrupt()`` cancels the turn task; ``run()`` swallows the injected
``CancelledError`` (stamping ``user_interrupt``) but never ``uncancel()``ed, so
the task kept ``cancelling() > 0`` for its whole life. One asyncio task drives
several turns (queue drain after a steer, the actor loop), and the Claude
runtime's ``_stop_idle_drainer`` used ``current_task().cancelling()`` to tell
"my own cancellation" from "the drainer's" — on such a task it re-raised the
drainer's CancelledError out of the task-coverage client rebuild, which the
host then minted as ``session_error{category: CancelledError}``.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

import kernel  # noqa: F401
import pytest

from src.core.types import UserMessage
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.interruption import absorb_interrupt_cancellations


# -- shared helper -----------------------------------------------------------


async def _swallow_one_cancel() -> None:
    me = asyncio.current_task()
    assert me is not None
    asyncio.get_running_loop().call_soon(me.cancel)
    try:
        await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


async def test_absorb_balances_the_swallowed_interrupt_cancel() -> None:
    async def turn() -> int:
        me = asyncio.current_task()
        assert me is not None
        await _swallow_one_cancel()
        assert me.cancelling() == 1
        absorb_interrupt_cancellations(1)
        return me.cancelling()

    assert await asyncio.create_task(turn()) == 0


async def test_absorb_never_erases_more_than_it_is_told() -> None:
    async def turn() -> int:
        me = asyncio.current_task()
        assert me is not None
        await _swallow_one_cancel()
        # A genuine, unrelated cancel request also pending: count 2, but the
        # runtime only issued one interrupt cancel — the other must survive.
        me.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        assert me.cancelling() == 2
        absorb_interrupt_cancellations(1)
        return me.cancelling()

    assert await asyncio.create_task(turn()) == 1


async def test_absorb_is_bounded_by_the_live_count() -> None:
    async def turn() -> int:
        me = asyncio.current_task()
        assert me is not None
        absorb_interrupt_cancellations(5)  # nothing pending — must not underflow/raise
        return me.cancelling()

    assert await asyncio.create_task(turn()) == 0


# -- Claude idle drainer teardown ---------------------------------------------


class _HangingClient:
    """An open SDK stream with nothing to say (the drainer parks on it)."""

    async def receive_messages(self):
        await asyncio.Event().wait()
        yield None  # pragma: no cover


def _drainer_runtime() -> ClaudeAgentRuntime:
    rt = object.__new__(ClaudeAgentRuntime)
    rt.model = "claude-sonnet-4-6"

    async def _emit(event) -> None:
        pass

    rt.event_sink = SimpleNamespace(emit=_emit)
    rt._cancelled = False
    rt._bracket_open = False
    rt._open_bracket_is_wakeup = False
    rt._pending_wakeups = 0
    rt._idle_drainer = None
    rt._live_bg_tasks = {}
    rt._client = _HangingClient()
    return rt


def _idle_session() -> SimpleNamespace:
    return SimpleNamespace(status="idle", stop_reason=None, runtime_session_id=None)


async def test_stop_idle_drainer_ignores_stale_cancel_count() -> None:
    """The exact reproduction: a task that swallowed an earlier interrupt
    (queue-drain / steer path) later tears down the idle drainer inside the
    task-coverage client rebuild. The drainer's own cancellation must land as a
    plain return, not escape as this turn's CancelledError."""
    rt = _drainer_runtime()

    async def turn() -> str:
        await _swallow_one_cancel()  # turn N interrupted; run() swallowed it
        me = asyncio.current_task()
        assert me is not None and me.cancelling() == 1  # the historical poison
        rt._start_idle_drainer(_idle_session())  # turn N+1 finished cleanly
        drainer = rt._idle_drainer
        await asyncio.sleep(0)
        try:
            await rt._stop_idle_drainer()  # run_task_coverage → _destroy_client
        except asyncio.CancelledError:
            return "escaped"
        assert drainer is not None and drainer.cancelled()
        return "ok"

    assert await asyncio.create_task(turn()) == "ok"


async def test_stop_idle_drainer_still_propagates_a_genuine_cancellation() -> None:
    """A real cancel of the turn task while it waits on the drainer must still
    surface — ``asyncio.wait`` propagates the CURRENT task's cancellation."""
    rt = _drainer_runtime()

    class _SlowExitClient:
        # The drainer ignores its first cancel long enough for the turn task
        # to be cancelled while parked in ``_stop_idle_drainer``.
        async def receive_messages(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                raise
            yield None  # pragma: no cover

    rt._client = _SlowExitClient()

    async def turn() -> str:
        rt._start_idle_drainer(_idle_session())
        await asyncio.sleep(0)
        me = asyncio.current_task()
        assert me is not None
        asyncio.get_running_loop().call_later(0.01, me.cancel)
        try:
            await rt._stop_idle_drainer()
        except asyncio.CancelledError:
            return "cancelled"
        return "swallowed"

    assert await asyncio.create_task(turn()) == "cancelled"


# -- Claude run() + interrupt() end to end -----------------------------------


class _InterruptibleClient:
    """Duck-typed ``ClaudeSDKClient``: ``query`` returns at once, the stream
    parks until ``interrupt()`` cancels the turn task."""

    def __init__(self) -> None:
        self.interrupted = False
        self.exited = False

    async def query(self, prompt: str) -> None:
        pass

    async def receive_messages(self):
        await asyncio.Event().wait()
        yield None  # pragma: no cover

    async def interrupt(self) -> None:
        self.interrupted = True

    async def __aexit__(self, *exc) -> bool:
        self.exited = True
        return False


def _run_runtime(
    client: _InterruptibleClient, monkeypatch: pytest.MonkeyPatch
) -> ClaudeAgentRuntime:
    rt = object.__new__(ClaudeAgentRuntime)
    rt.model = "claude-sonnet-4-6"
    rt.workspace_root = None
    rt.config = SimpleNamespace(hooks=None)

    async def _emit(event) -> None:
        pass

    rt.event_sink = SimpleNamespace(emit=_emit)
    rt._client = client
    rt._active_client = None
    rt._active_task = None
    rt._interrupt_cancels = 0
    rt._idle_drainer = None
    rt._cancelled = False
    rt._bracket_open = False
    rt._open_bracket_is_wakeup = False
    rt._pending_wakeups = 0
    rt._live_bg_tasks = {}
    rt._pending_futures = {}
    rt._stderr_buffer = deque()
    rt._egress_enabled_for_spawn = False
    rt._turn_anchor = None

    async def _noop_levers(session) -> None:
        pass

    async def _noop_phase(phase: str, **fields) -> None:
        pass

    monkeypatch.setattr(rt, "_reconcile_session_levers", _noop_levers)
    monkeypatch.setattr(rt, "_emit_turn_phase", _noop_phase)
    return rt


async def test_claude_run_swallows_interrupt_and_leaves_no_stale_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _InterruptibleClient()
    rt = _run_runtime(client, monkeypatch)
    session = SimpleNamespace(
        status="running",
        stop_reason=None,
        runtime_session_id=None,
        mode="default",
        permission_mode="default",
        model_settings=None,
        id="s1",
    )

    async def turn() -> tuple[object, int]:
        await rt.run(session, UserMessage(text="hi"))
        me = asyncio.current_task()
        assert me is not None
        return session.stop_reason, me.cancelling()

    task = asyncio.create_task(turn())
    # Let run() reach the parked stream, then interrupt like the host does.
    for _ in range(20):
        await asyncio.sleep(0)
        if rt._active_task is not None:
            break
    assert rt._active_task is not None
    await rt.interrupt()

    stop_reason, cancelling = await task
    assert client.interrupted is True
    assert getattr(stop_reason, "category", None) == "user_interrupt"
    assert session.status == "idle"
    # The load-bearing assertion: the swallowed interrupt no longer leaves the
    # task with a pending cancel request for the next turn it drives.
    assert cancelling == 0
    assert rt._interrupt_cancels == 0
