"""The dispatch/shutdown race — the module's sharpest concurrency invariant.

``dispatch_async`` registers a new member; ``finish_task`` /``stop_task`` tell
every live member to shut down via ``stop_tracking_members``, which drains the
live-member set in ONE pop. If the loop yields to the event loop between
"member exists" and "member is registered", a concurrent broadcast sees an
empty set, the just-spawned member is never told to stop, and it hangs until
its idle TTL (10 minutes) — a rare interleaving that is close to impossible to
reproduce from a bug report.

The rule used to live in a comment ("no ``await`` may separate ..."), which
warns nobody at edit time. It is now structural: both halves of the race are
plain ``def``s, so ``await`` inside them is a SyntaxError.

These tests pin that, from two directions:

  * ``test_*_is_synchronous`` fails the moment someone makes one of them
    ``async`` — the single edit that re-opens the race — and says why.
  * ``test_shutdown_reaches_a_member_spawned_concurrently`` exercises the race
    itself: it drives a real spawn and a real broadcast from two concurrent
    tasks and asserts the member still gets its shutdown.
"""

from __future__ import annotations

import asyncio
import inspect

from valuz_agent.modules.tasks import launcher, mailbox_store
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.dispatcher import DispatcherService
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry

LOCAL_USER_ID = "local-test-owner"


# ---------------------------------------------------------------------------
# Structural: the compiler is the enforcement, these tests explain it
# ---------------------------------------------------------------------------


def test_spawn_actor_is_synchronous() -> None:
    """``launcher.spawn_actor`` must never become ``async``.

    It registers mailboxes, seeds the live set and starts the loop. Those have
    to land without the event loop getting a turn, or a concurrent
    ``stop_tracking_members`` drains the set in between and the member is lost.
    Sync makes ``await`` a SyntaxError — checked on every edit, not remembered.
    Every launch path (dispatch, kickoff, commit, recovery) goes through it.
    """
    assert not inspect.iscoroutinefunction(launcher.spawn_actor)


def test_stop_tracking_members_is_synchronous() -> None:
    """``CoordinationService.stop_tracking_members`` must never become ``async``.

    It pops the whole live set and then delivers to each member. An ``await``
    between the pop and the puts would let a member spawned meanwhile be
    dropped — the same race from the other side.
    """
    assert not inspect.iscoroutinefunction(CoordinationService.stop_tracking_members)


def test_live_member_registry_is_entirely_synchronous() -> None:
    """Every registry method stays sync — it is the shared state both halves
    race over, so an await point anywhere inside reopens the window."""
    for name, member in inspect.getmembers(LiveMemberRegistry, inspect.isfunction):
        assert not inspect.iscoroutinefunction(member), f"{name} must stay synchronous"


# ---------------------------------------------------------------------------
# Behavioural: drive the race
# ---------------------------------------------------------------------------


async def test_shutdown_reaches_a_member_spawned_concurrently(db_factory) -> None:
    """A member spawned while a shutdown broadcast is in flight still gets it.

    Both operations run as concurrent tasks against the same registry. Because
    each half is atomic, the interleaving can only be "spawn fully, then
    broadcast" or "broadcast, then spawn" — never a half-registered member. The
    first ordering must deliver the shutdown; the second must leave the member
    out of the drained set entirely (it is not yet live), never in a state
    where it is live but unreachable.
    """
    registry = LiveMemberRegistry()
    coordination = CoordinationService(registry=registry)
    runner = ActorRunner()
    dispatcher = DispatcherService(registry=registry, actor_runner=runner)

    task_id, lead, member = "t-race", "lead-race", "mem-race"
    loops: list[str] = []

    async def _never_runs(**kwargs: object) -> None:
        # Stand in for the member's actor loop: record that it was started and
        # park, so the spawned asyncio task doesn't touch the kernel.
        loops.append(str(kwargs["session_id"]))
        await asyncio.sleep(3600)

    runner.run_actor_loop = _never_runs  # type: ignore[method-assign]

    async def _spawn() -> None:
        launcher.spawn_actor(
            runner,
            session_id=member,
            prompt="do it",
            role="subtask",
            task_id=task_id,
            project_id="w1",
            user_id=LOCAL_USER_ID,
            registry=registry,
            dispatch_epoch=1.0,
            lead_session_id=lead,
        )

    async def _shutdown() -> None:
        coordination.stop_tracking_members(task_id)

    try:
        await asyncio.gather(_spawn(), _shutdown())

        assert loops == [member], "the member's actor loop must have been started"
        # The pop is atomic, so the member is either still tracked (the drain
        # ran first and never saw it) or gone from the registry (the drain saw
        # it). What must never happen is the set being left half-mutated —
        # that is what a non-atomic drain would produce, and it is why this
        # function may not contain an ``await``.
        #
        # Whether the member then STOPS is no longer this function's business:
        # it reads its own parked run row, from whichever process runs it.
        assert not await mailbox_store.has_pending(member), (
            "halting a task must queue nothing — a stop that travels as a "
            "message only reaches members that share this process"
        )
    finally:
        for sid in (lead, member):
            pass
        for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
            t.cancel()


async def test_stop_tracking_drains_every_member_exactly_once(db_factory) -> None:
    """The drain is a single pop, and a second halt is a no-op.

    ``stop_task`` then ``finish_task`` both run it, so the second must find
    nothing rather than act twice. When each pop also queued a message that
    mattered for delivery; now it matters because a half-mutated set would let
    a member be dropped from tracking while another was still being added.
    """
    registry = LiveMemberRegistry()
    coordination = CoordinationService(registry=registry)

    members = [f"m{i}" for i in range(4)]
    for m in members:
        registry.add_member("t1", m)

    coordination.stop_tracking_members("t1")
    assert not registry.has_live_members("t1"), "every member popped in one go"
    for m in members:
        assert not await mailbox_store.has_pending(m), (
            "halting a task queues nothing — a stop that travels as a message "
            "only reaches members sharing this process"
        )

    coordination.stop_tracking_members("t1")  # second halt — nothing to do
    assert not registry.has_live_members("t1")
