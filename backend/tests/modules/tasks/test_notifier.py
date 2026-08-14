"""The doorbell's two invariants: it wakes, and it does not accumulate.

Both are easy to lose. An earlier implementation kept one ``asyncio.Event`` per
session in a module-level singleton, which failed on both counts at once: the
map grew for the life of the process, AND an Event outlives the loop it was
first awaited on, so a waiter attached to a dead loop was never woken — a
failure indistinguishable from a lost signal.
"""

from __future__ import annotations

import asyncio

import pytest

from valuz_agent.modules.tasks.notifier import InProcessNotifier


def _retained(n: InProcessNotifier) -> int:
    """How many per-session entries the notifier is still holding, any shape.

    Deliberately does NOT name the attribute. An earlier implementation kept
    its state in ``_events``; asserting on today's ``_waiters`` would pass
    against that one while it leaked an entry per session for the life of the
    process. Sum every dict it owns instead, so the invariant survives a
    rewrite of the thing it constrains.
    """
    return sum(len(v) for v in vars(n).values() if isinstance(v, (dict, set)))


def test_a_ring_wakes_a_waiter_early() -> None:
    n = InProcessNotifier()

    async def _run() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def _ring_soon() -> None:
            await asyncio.sleep(0.02)
            await n.notify("s1")

        task = asyncio.create_task(_ring_soon())
        await n.wait("s1", timeout=5.0)
        await task
        return loop.time() - started

    waited = asyncio.run(_run())
    assert waited < 1.0, f"the ring must cut the wait short, not ride it out ({waited:.2f}s)"


def test_a_wait_that_times_out_returns_rather_than_raising() -> None:
    """The caller cannot tell a ring from a timeout, and must not need to.

    It re-reads durable state either way, so surfacing the difference would
    only invite someone to branch on it.
    """
    n = InProcessNotifier()
    asyncio.run(asyncio.wait_for(n.wait("s1", timeout=0.02), timeout=2.0))


def test_nothing_accumulates_after_a_ring() -> None:
    n = InProcessNotifier()

    async def _run() -> None:
        async def _ring_soon() -> None:
            await asyncio.sleep(0.01)
            await n.notify("s1")

        task = asyncio.create_task(_ring_soon())
        await n.wait("s1", timeout=5.0)
        await task

    asyncio.run(_run())
    assert _retained(n) == 0, (
        "a woken waiter must leave nothing behind — this is keyed by session on "
        "a process that runs for weeks"
    )


def test_nothing_accumulates_after_a_timeout() -> None:
    n = InProcessNotifier()
    asyncio.run(n.wait("s1", timeout=0.01))
    assert _retained(n) == 0, "a timed-out waiter must clean up after itself too"


def test_a_ring_with_nobody_waiting_is_remembered_once() -> None:
    """A ring that lands between a check and a park must not be lost.

    The caller reads its state BEFORE it parks, so there is a gap. A ring
    arriving in it used to vanish and the message waited out a full poll —
    which is the delay a doorbell exists to remove. Remembering it is safe only
    because the signal carries nothing: the cost of one spurious wake is one
    more look at a table.

    Remembered ONCE, though — it is a wake-up, not a queue.
    """
    n = InProcessNotifier()
    asyncio.run(n.notify("s1"))

    async def _two_waits() -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await n.wait("s1", timeout=5.0)
        t1 = loop.time()
        await n.wait("s1", timeout=0.05)
        return t1 - t0, loop.time() - t1

    first, second = asyncio.run(_two_waits())
    assert first < 1.0, "the remembered ring must satisfy the next wait"
    assert second >= 0.04, "and only that one — a doorbell is not a queue"
    assert _retained(n) == 0, "nothing left behind either way"


def test_forget_drops_a_remembered_ring() -> None:
    """Nothing else expires it, so a session rung and never waited on would
    keep an entry for the life of the process — the shape of the leak this
    whole module replaced."""
    n = InProcessNotifier()
    asyncio.run(n.notify("s1"))
    assert _retained(n) == 1
    n.forget("s1")
    assert _retained(n) == 0


def test_concurrent_waiters_on_one_session_are_all_woken_and_cleared() -> None:
    n = InProcessNotifier()

    async def _run() -> None:
        waiters = [asyncio.create_task(n.wait("s1", timeout=5.0)) for _ in range(3)]
        await asyncio.sleep(0.01)
        await n.notify("s1")
        await asyncio.wait_for(asyncio.gather(*waiters), timeout=2.0)

    asyncio.run(_run())
    assert _retained(n) == 0


def test_a_waiter_never_outlives_its_event_loop() -> None:
    """The bug that made the first implementation look like a lost signal.

    Each ``asyncio.run`` is a fresh loop. A primitive created on an earlier one
    and kept in a long-lived object cannot wake a waiter on a later one, and the
    notifier IS long-lived — it is a module singleton.
    """
    n = InProcessNotifier()
    asyncio.run(n.wait("s1", timeout=0.01))  # first loop, times out

    async def _run() -> None:  # a SECOND loop, on the same notifier
        async def _ring_soon() -> None:
            await asyncio.sleep(0.01)
            await n.notify("s1")

        task = asyncio.create_task(_ring_soon())
        await asyncio.wait_for(n.wait("s1", timeout=5.0), timeout=2.0)
        await task

    asyncio.run(_run())  # would hang out its timeout if the waiter were stale


@pytest.mark.asyncio
async def test_a_cancelled_wait_cleans_up() -> None:
    n = InProcessNotifier()
    task = asyncio.create_task(n.wait("s1", timeout=30.0))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _retained(n) == 0, "a cancelled loop must not strand its waiter"


def test_many_sessions_leave_nothing_behind() -> None:
    """The shape the leak actually took: one entry per session, forever.

    A single wait cleaning up is not enough to prove that — the earlier
    implementation cleaned up its Event's *state* on every wait while keeping
    the Event itself keyed by session. Only the total tells you.
    """
    n = InProcessNotifier()
    for i in range(50):
        asyncio.run(n.wait(f"s{i}", timeout=0.001))
    assert _retained(n) == 0, (
        "50 sessions waited and finished; the notifier is still holding state "
        "for some of them"
    )
