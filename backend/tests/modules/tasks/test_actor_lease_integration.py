"""How the actor loop behaves around its task lease.

The lease module's own semantics are covered in ``test_task_lease.py``; this
file is about the three places the LOOP has to get the interaction right, each
of which was wrong in an earlier draft of this change:

1. the lease is released only AFTER finalize (finalize writes terminal state,
   so it must run while we still own the task),
2. failing to acquire hands the inbox back through the claim guard instead of
   dropping it,
3. a superseded loop does not post its ``shutdown`` into an inbox a newer loop
   now owns.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra import execution_lease as lease_mod
from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.tasks import mailbox_store
from .conftest import deliver
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.lease import acquire_actor_lease, load_actor_lease_states
from valuz_agent.modules.tasks.mailbox import InboxMsg

OWNER = "local-test-owner"


@pytest.fixture(autouse=True)
def _multi_process_world(monkeypatch):
    """These tests are about CROSS-process behaviour, so pin the world to it.

    ``_exclusive_by_construction`` is ambient: it answers True whenever the
    single-writer lock happens to be held in this interpreter, which another
    test in the same session can arrange. Leases then need no renewal and the
    fencing these tests exercise cannot happen at all — one of them span
    forever waiting for a renewal that could never fail.
    """
    monkeypatch.setattr(
        "valuz_agent.infra.execution_lease._exclusive_by_construction", lambda: False
    )


@pytest.fixture(autouse=True)
def _reset_mailbox():
    yield


class _Collaborators:
    """Fake finalizer + coordinator recording the order seams fire in."""

    def __init__(self, calls: list[str], on_finalize=None, *, stops_after=None) -> None:
        self._calls = calls
        self._on_finalize = on_finalize
        # After this many checks the actor is no longer wanted. That is how a
        # loop ends now — a state it reads, not a ``shutdown`` queued for it.
        self._stops_after = stops_after
        self._wanted_checks = 0

    async def actor_still_wanted(self, **_kw) -> bool:
        self._wanted_checks += 1
        if self._stops_after is None:
            return True
        return self._wanted_checks <= self._stops_after

    async def finalize_actor(self, **kwargs: Any) -> None:
        self._calls.append("finalize")
        if self._on_finalize is not None:
            await self._on_finalize()

    async def notify_lead_member_idle(self, session_id, status, user_id) -> None:
        return None

    async def lead_idle_with_no_pending(
        self, task_id, project_id, user_id, lead_session_id=""
    ) -> bool:
        return True

    async def recover_crashed_members(self, *, task_id, project_id, user_id) -> list:
        return []

    async def session_still_working(self, session_id) -> bool:
        return False


def _runner(calls: list[str], turn_status: str = "terminated", on_finalize=None):
    fake = _Collaborators(calls, on_finalize)
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        calls.append("turn")
        return turn_status

    runner.run_turn = _turn  # type: ignore[method-assign]
    return runner


def _drive(runner, *, session_id="lead-s", task_id="t1"):
    return runner.run_actor_loop(
        session_id=session_id,
        initial_prompt="go",
        role="lead",
        task_id=task_id,
        project_id="p1",
        user_id=OWNER,
    )


def test_lease_is_still_held_while_finalize_runs(db_factory) -> None:
    """Regression: releasing before finalize let a peer take over mid-finalize.

    ``finalize_actor`` writes the authoritative terminal state — for a natural
    lead exit that includes flipping the task to completed/blocked. If the
    lease is already released when it runs, another process can acquire and
    start driving, and this loop's finalize then overwrites the new driver's
    state.
    """
    seen: list[str | None] = []

    async def _peek_holder_during_finalize() -> None:
        states = await lease_mod.load_lease_states("actor", ["lead-s"])
        state = states.get("lead-s")
        seen.append(state.state if state else None)

    calls: list[str] = []
    asyncio.run(_drive(_runner(calls, on_finalize=_peek_holder_during_finalize)))

    assert calls == ["turn", "finalize"]
    assert seen == ["held"], "the lease must still be held while finalize writes"
    # ...and handed back once finalize is done, so a peer can pick it up now.
    after = asyncio.run(lease_mod.load_lease_states("actor", ["lead-s"]))
    assert after["lead-s"].state == "released"


def test_bailing_out_leaves_the_peers_lease_untouched(db_factory) -> None:
    """A loop that may not run must change nothing about the one that may.

    This used to be a test about a leaked mailbox claim: ``spawn_actor``
    claimed eagerly, and a loop that then found the lease taken had to hand the
    claim back, or ``is_owned`` would report a reader this process did not have
    for the rest of its life — and the watchdog trusted that as liveness.

    There is no claim any more; the lease answers the same question and it is
    shared, so a loop that never acquired it leaves nothing behind by
    construction. What is still worth pinning is the consequence: we do not
    drive, and the holder's grip is exactly as it was.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
    peer = asyncio.run(acquire_actor_lease(session_id="lead-s", task_id="t1"))
    assert peer is not None
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")

    calls: list[str] = []
    asyncio.run(_drive(_runner(calls)))

    assert calls == [], "we must not run a session a peer holds"
    state = asyncio.run(load_actor_lease_states(["lead-s"]))["lead-s"]
    assert state.holder_id == "peer-proc"
    assert state.fence_token == peer.fence_token, (
        "a loop that never ran must not bump the fence — that would revoke the "
        "holder that IS running"
    )
    monkey.undo()


def test_a_superseded_loop_puts_nothing_in_the_shared_inbox(db_factory) -> None:
    """A revoked runner must leave silently — no message, anywhere.

    Both loops share one inbox for the session, so a fenced loop that posts its
    own stop signal delivers it to whichever loop reads the box now — the
    replacement — leaving the task with no runner at all. That happened, and
    was patched with a process-local claim token gating the post.

    The post is gone entirely: losing the lease raises a fence the loop
    observes on its own. Nothing is queued, so there is nothing to mis-deliver
    and nothing to gate.
    """
    stale = asyncio.run(acquire_actor_lease(session_id="lead-s", task_id="t1"))
    assert stale is not None

    # A newer loop takes the lease — a rapid resume, or a peer process.
    newer = asyncio.run(acquire_actor_lease(session_id="lead-s", task_id="t1"))
    assert newer is not None
    assert newer.fence_token > stale.fence_token, "acquiring must revoke the predecessor"

    fenced = asyncio.Event()

    async def _run_renewer() -> None:
        monkey = pytest.MonkeyPatch()
        monkey.setattr("valuz_agent.modules.tasks.actor_runner.ACTOR_LEASE_RENEW_INTERVAL_S", 0.0)
        await ActorRunner._renew_lease(stale, "lead-s", fenced)
        monkey.undo()

    asyncio.run(_run_renewer())

    assert fenced.is_set(), "the superseded loop must learn it lost the lease"
    assert not asyncio.run(mailbox_store.has_pending("lead-s")), (
        "a revoked runner must not queue anything — the replacement reads this box"
    )
    assert asyncio.run(newer.renew()) is True, "the live runner is untouched"


def test_recovery_refuses_to_respawn_a_task_a_peer_holds(db_factory) -> None:
    """Regression: at a COLD boot the advisory pre-check cannot help.

    Every worker boots at once, so nobody holds a lease yet and all of them
    pass ``is_running_elsewhere``. Without an authoritative acquisition inside
    ``_recover_one_task`` — before the respawn half — each worker would evict
    the kernel runtime and respawn the same member loops, which then drive real
    turns: duplicated work and duplicated model spend on one task.
    """
    from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow
    from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

    db = db_factory()
    try:
        db.add(
            TaskRow(
                id="t1",
                user_id=OWNER,
                project_id="p1",
                file_path="tasks/t1.md",
                title="T",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={"subtasks": []},
            )
        )
        db.add(
            TaskSessionRow(
                user_id=OWNER,
                project_id="p1",
                task_id="t1",
                session_id="lead-s",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
    assert (
        asyncio.run(acquire_actor_lease(session_id="lead-s", task_id="t1"))
        is not None
    )
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")

    orch = TaskOrchestrator()
    spawned: list[str] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append(session_id)

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> bool:
        result = await orch.recovery._recover_one_task("t1", "p1", user_id=OWNER)
        await asyncio.sleep(0.05)  # let any create_task'd loop run
        return result

    assert asyncio.run(_run()) is False
    assert spawned == [], "a peer owns this task — nothing here may respawn"
    monkey.undo()


def test_a_parked_loop_leaves_on_its_own_when_the_fence_goes_up(db_factory) -> None:
    """The other half: a revoked loop must actually stop, not park forever.

    Cross-process takeover is the common case, and there is no local
    replacement to nudge it. A ``shutdown`` message used to be the only thing
    that unparked a loop sitting on a long inbox wait — which is exactly why
    the stop travelled as a message and could be mis-delivered.

    The wait now checks the fence on every slice, so a revoked loop ends its
    own wait. It surfaces as ``KeyError``, the signal the loop already uses for
    "this session is no longer yours" — leave without finalizing.
    """
    fenced = asyncio.Event()
    fenced.set()
    runner = _runner([])

    async def _wait() -> None:
        await runner._await_wakeup(
            session_id="lead-s",
            role="lead",
            ttl=30.0,
            task_id="t1",
            project_id="p1",
            user_id=OWNER,
            coordinator=runner._coordinator,
            fenced=fenced,
        )

    with pytest.raises(KeyError):
        asyncio.run(asyncio.wait_for(_wait(), timeout=15))

    assert not asyncio.run(mailbox_store.has_pending("lead-s")), (
        "leaving must not require — or leave behind — a message"
    )





class _ReconcilingCoordinator(_Collaborators):
    """Coordinator whose mailbox never delivers, but whose store knows the truth."""

    def __init__(self, calls: list[str], recovered: list[InboxMsg]) -> None:
        super().__init__(calls)
        self._recovered = recovered
        self.reconciles = 0

    async def recover_crashed_members(self, *, task_id, project_id, user_id) -> list:
        self.reconciles += 1
        out, self._recovered = self._recovered, []
        return out


def _member_done(session_id: str, summary: str) -> InboxMsg:
    return InboxMsg(
        kind="member_done",
        from_session=session_id,
        payload={"session_id": session_id, "status": "completed", "summary": summary},
    )


def test_lead_recovers_a_member_result_its_mailbox_never_received(db_factory) -> None:
    """Regression: the false `unresolved_subtasks` block.

    `dispatch` is an HTTP tool call, so it lands on whichever host process the
    load balancer picked and the member it spawns can post `member_done` into a
    DIFFERENT process's queue — `put` returns False, unchecked. The lead's
    mailbox here is deliberately left EMPTY to model exactly that. Before the
    durable backstop the lead slept out its whole idle TTL and auto-finalize
    blocked a task whose members had all finished.
    """
    fake = _ReconcilingCoordinator([], [_member_done("m1", "did the thing")])
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _drive():
        return await runner._await_wakeup(
            session_id="lead-s",
            role="lead",
            ttl=60.0,
            task_id="t1",
            project_id="p1",
            user_id=OWNER,
            coordinator=fake,
            fenced=asyncio.Event(),
        )

    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.LEAD_RECONCILE_SLICE_S", 0.01)
    msg = asyncio.run(_drive())
    monkey.undo()

    assert msg.kind == "member_done"
    assert msg.payload["summary"] == "did the thing"
    assert fake.reconciles >= 1


def test_extra_recovered_results_are_queued_not_dropped(db_factory) -> None:
    """Two members finished while nobody was listening — both must reach the lead."""
    fake = _ReconcilingCoordinator([], [_member_done("m1", "first"), _member_done("m2", "second")])
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.LEAD_RECONCILE_SLICE_S", 0.01)

    async def _drive():
        first = await runner._await_wakeup(
            session_id="lead-s", role="lead", ttl=60.0, task_id="t1",
            project_id="p1", user_id=OWNER, coordinator=fake,
            fenced=asyncio.Event(),
        )
        # The second one must already be waiting — consumed through the same
        # path as a real arrival, not invented by a second reconcile.
        second = await runner._await_wakeup(
            session_id="lead-s", role="lead", ttl=60.0, task_id="t1",
            project_id="p1", user_id=OWNER, coordinator=fake,
            fenced=asyncio.Event(),
        )
        return first, second

    first, second = asyncio.run(_drive())
    monkey.undo()
    assert [first.payload["summary"], second.payload["summary"]] == ["first", "second"]
    assert fake.reconciles == 1, "the queued one must not need a second reconcile"


def test_members_do_not_reconcile(db_factory) -> None:
    """A subtask actor has no siblings to reconcile — it keeps the plain wait."""
    fake = _ReconcilingCoordinator([], [_member_done("m1", "nope")])
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _drive():
        with pytest.raises(TimeoutError):
            await runner._await_wakeup(
                session_id="member-s", role="subtask", ttl=0.05, task_id="t1",
                project_id="p1", user_id=OWNER, coordinator=fake,
                fenced=asyncio.Event(),
            )

    asyncio.run(_drive())
    assert fake.reconciles == 0


def test_a_failing_reconcile_does_not_end_the_wait(db_factory) -> None:
    """The backstop is best-effort: a broken read must not look like a timeout."""

    class _Broken(_Collaborators):
        async def recover_crashed_members(self, *, task_id, project_id, user_id) -> list:
            raise RuntimeError("store unreachable")

    fake = _Broken([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.LEAD_RECONCILE_SLICE_S", 0.01)

    async def _drive():
        # A real message still gets through while the backstop is failing.
        # It travels the real path — a durable enqueue plus a ring — because
        # nothing puts into the in-process queue any more: that is a local
        # buffer for extras the loop itself parked, not a channel.
        async def _late_delivery():
            await asyncio.sleep(0.05)
            async with async_unit_of_work() as db:
                await mailbox_store.enqueue(
                    db,
                    session_id="lead-s",
                    task_id="t1",
                    project_id="p1",
                    user_id=OWNER,
                    kind="member_done",
                    from_session="m9",
                    payload={"summary": "arrived by mailbox"},
                )
            await mailbox_store.ring_for("lead-s")

        task = asyncio.create_task(_late_delivery())
        msg = await runner._await_wakeup(
            session_id="lead-s", role="lead", ttl=5.0, task_id="t1",
            project_id="p1", user_id=OWNER, coordinator=fake,
            fenced=asyncio.Event(),
        )
        await task
        return msg

    msg = asyncio.run(_drive())
    monkey.undo()
    assert msg.payload["summary"] == "arrived by mailbox"


def test_no_durable_writes_while_draining(db_factory) -> None:
    """The backstop must go quiet at teardown.

    ``recover_crashed_members`` WRITES — it settles run rows and flips plan
    nodes to ``in_review``. The loop already skips its whole finalize while
    draining, for the reason the drain comment gives: a terminal write here
    fights the boot recovery that is meant to resume the task. A parked lead
    reconciling every slice through shutdown would reintroduce exactly that.
    """
    from valuz_agent.infra import lifecycle

    fake = _ReconcilingCoordinator([], [_member_done("m1", "should not be consulted")])
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.LEAD_RECONCILE_SLICE_S", 0.01)
    lifecycle.set_draining()

    async def _drive():
        with pytest.raises(TimeoutError):
            await runner._await_wakeup(
                session_id="lead-s", role="lead", ttl=0.08, task_id="t1",
                project_id="p1", user_id=OWNER, coordinator=fake,
                fenced=asyncio.Event(),
            )

    try:
        asyncio.run(_drive())
    finally:
        lifecycle.reset_draining()
        monkey.undo()

    assert fake.reconciles == 0, "no durable write may be attempted during teardown"


def test_the_renewer_keeps_going_when_a_renewal_raises(db_factory) -> None:
    """A transient failure must not kill the renewer.

    If it dies, nothing renews the lease and nothing raises the fence: the
    holder keeps running while its lease quietly expires, and the watchdog
    eventually declares a live runner dead. So an unexpected error has to be
    logged and retried, not propagated.

    (This used to inject the failure through ``is_claim_current``, the guard on
    the shutdown message the renewer posted. Both are gone — losing the lease
    now only raises a fence — so the injection point is the renewal itself.)
    """
    calls = {"n": 0}

    class _FlakyLease:
        """Raises twice, then reports the lease as lost."""

        needs_renewal = True
        key = "lead-s"

        async def renew(self) -> bool:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("database blip")
            return False

    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.ACTOR_LEASE_RENEW_INTERVAL_S", 0.01)
    fenced = asyncio.Event()

    async def _run() -> bool:
        task = asyncio.create_task(ActorRunner._renew_lease(_FlakyLease(), "lead-s", fenced))
        for _ in range(80):
            await asyncio.sleep(0.02)
            if task.done():
                break
        done_cleanly = task.done() and task.exception() is None
        task.cancel()
        return done_cleanly

    ended_cleanly = asyncio.run(_run())
    monkey.undo()

    assert calls["n"] >= 3, "it must retry past the failures rather than die on the first"
    assert fenced.is_set(), "and it must still report the lost lease once it can"
    assert ended_cleanly, "the renewer must not propagate the transient error"


class _DupThenStopped(_Collaborators):
    """Reports every member_done as already settled, then stops being wanted.

    The stop used to be a queued ``shutdown``; it is a state read now, so the
    fake answers the question the loop actually asks.
    """

    def __init__(self, calls: list[str]) -> None:
        super().__init__(calls)
        self.settled_asked = 0

    async def member_already_settled(self, *, task_id, project_id, member_session_id, user_id) -> bool:
        self.settled_asked += 1
        return True

    async def actor_still_wanted(self, **_kw) -> bool:
        # Wanted until the duplicate has been examined; stopped after, which is
        # how the loop ends now.
        return self.settled_asked == 0

    async def lead_idle_with_no_pending(self, task_id, project_id, user_id, lead_session_id="") -> bool:
        return False  # keep the loop parked on its mailbox

    async def recover_crashed_members(self, *, task_id, project_id, user_id) -> list:
        return []


def test_dropping_a_duplicate_does_not_re_run_the_previous_prompt(db_factory) -> None:
    """Production regression: the user's own message was sent a second time.

    Dropping a duplicate `member_done` used to `continue` the OUTER loop, whose
    very next statement is `run_turn(session_id, prompt)` — and `prompt` still
    held the PREVIOUS turn's text. So "ignore this message" actually meant
    "re-run the last prompt", and a finished task was executed a second time
    from its original goal.

    Here the loop must run the kickoff turn, silently drop the duplicate, and
    then leave because the task no longer wants it — one turn, not two.
    """
    fake = _DupThenStopped([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    prompts: list[str] = []

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        prompts.append(content)
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]

    deliver("lead-s", _member_done("m1", "already reviewed"))

    asyncio.run(_drive(runner))

    assert fake.settled_asked == 1, "the duplicate must have been examined"
    assert prompts == ["go"], (
        "exactly ONE turn, on the kickoff prompt — dropping a duplicate must "
        f"never re-run it. Got: {prompts}"
    )


def test_an_actionable_member_done_still_runs_a_turn(db_factory) -> None:
    """The drop must not swallow work that IS outstanding."""

    class _NotSettled(_Collaborators):
        async def member_already_settled(self, *, task_id, project_id, member_session_id, user_id) -> bool:
            return False

        async def lead_idle_with_no_pending(self, task_id, project_id, user_id, lead_session_id="") -> bool:
            return False

        async def recover_crashed_members(self, *, task_id, project_id, user_id) -> list:
            return []

    fake = _NotSettled([], stops_after=1)
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    prompts: list[str] = []

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        prompts.append(content)
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]

    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        "valuz_agent.modules.tasks.planning.mark_in_review",
        lambda **kw: asyncio.sleep(0),
    )
    deliver("lead-s", _member_done("m1", "please review me"))

    asyncio.run(_drive(runner))
    monkey.undo()

    assert len(prompts) == 2, "kickoff + the member result"
    assert "please review me" in prompts[1]
    assert prompts[1] != prompts[0], "the second turn must not repeat the first"


def test_a_failed_acquire_leaves_no_lease_behind(db_factory, monkeypatch) -> None:
    """Undriven AND invisible is worse than undriven.

    An exception escaping the acquire used to strand the eagerly-taken mailbox
    claim, and the watchdog read that claim as liveness — so the task stayed
    ``active`` with nothing running it and nothing able to notice.

    The claim is gone, and liveness is the lease. What has to hold now is that
    a failed acquire records no holder at all, so the watchdog sees the session
    for what it is: unheld, and therefore its business.
    """

    async def _boom(**kw):
        raise RuntimeError("database blip")

    monkeypatch.setattr("valuz_agent.modules.tasks.actor_runner.acquire_actor_lease", _boom)
    fake = _Collaborators([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    with pytest.raises(RuntimeError):
        asyncio.run(_drive(runner))

    assert asyncio.run(load_actor_lease_states(["lead-s"])) == {}, (
        "a loop that could not take the lease must not look like a holder"
    )


def test_an_unreadable_goal_is_retried_on_the_next_wake_up(db_factory, monkeypatch) -> None:
    """A goal read that fails must not silence the restatement for the whole loop.

    ``_with_goal_restated`` is a no-op on an empty goal, and without it the
    kernel re-goals the lead to whatever the wake-up says — goal-auto-exit then
    fires the moment that trivial goal is met and the lead stops driving the
    real task. So one failed read used to be enough to strand a task.
    """
    class _Wake(_Collaborators):
        async def member_already_settled(self, **kw) -> bool:
            return False

        async def lead_idle_with_no_pending(self, task_id, project_id, user_id, lead_session_id="") -> bool:
            return False

        async def recover_crashed_members(self, *, task_id, project_id, user_id) -> list:
            return []

    reads: list[int] = []

    async def _goal(task_id, project_id, user_id):
        reads.append(1)
        return "" if len(reads) == 1 else "THE REAL GOAL"

    monkeypatch.setattr(ActorRunner, "_task_goal", staticmethod(_goal))
    monkeypatch.setattr(
        "valuz_agent.modules.tasks.planning.mark_in_review", lambda **kw: asyncio.sleep(0)
    )

    fake = _Wake([], stops_after=1)
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    prompts: list[str] = []

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        prompts.append(content)
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]
    deliver("lead-s", _member_done("m1", "result"))

    asyncio.run(_drive(runner))

    assert len(reads) == 2, "the empty first read must be retried at wake-up"
    assert "<task-goal>THE REAL GOAL</task-goal>" in prompts[1], (
        "the recovered goal must be restated on the wake-up"
    )


def test_a_fenced_loop_does_not_finalize_the_new_drivers_session(db_factory) -> None:
    """`finalize_actor` flips the KERNEL session before it looks at `via_shutdown`.

    So a loop that lost its task still reached in and finalized the session the
    NEW driver may be mid-turn on: `via_shutdown` only ever guarded the
    task-level auto-finalize, never the kernel write. A takeover only happens
    after a 90s expiry, but the evicted loop can be minutes into a turn and only
    reaches its `finally` afterwards — long after the replacement started.
    """
    calls: list[str] = []
    fake = _Collaborators(calls)
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        calls.append("turn")
        # A peer takes the task over while this turn is running: expire our
        # lease, then let another holder acquire it.
        db = db_factory()
        try:
            db.execute(
                ExecutionLeaseRow.__table__.update()
                .where(ExecutionLeaseRow.scope == "actor", ExecutionLeaseRow.key == "lead-s")
                .values(lease_expires_at=1)
            )
            db.commit()
        finally:
            db.close()
        monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
        await acquire_actor_lease(session_id="lead-s", task_id="t1")
        monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")
        return "terminated"  # end the loop so the finally runs now

    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")
    runner.run_turn = _turn  # type: ignore[method-assign]

    asyncio.run(_drive(runner))
    monkey.undo()

    assert calls == ["turn"], (
        "the turn ran, but finalize must NOT — the task belongs to someone else now. "
        f"Got: {calls}"
    )
    # And the peer's lease is untouched: still held, on the newer token.
    row = asyncio.run(lease_mod.load_lease_states("actor", ["lead-s"]))["lead-s"]
    assert row.state == "held" and row.holder_id == "peer-proc"


def test_an_unproven_lease_check_still_finalizes(db_factory, monkeypatch) -> None:
    """Only a PROVEN loss skips finalize.

    If the confirmation itself fails we cannot prove anything, so the
    pre-existing behaviour has to stand — otherwise a transient database error
    would start leaving every normal exit unfinalized, handing healthy tasks to
    the watchdog.
    """
    calls: list[str] = []
    fake = _Collaborators(calls)
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        calls.append("turn")
        return "terminated"

    runner.run_turn = _turn  # type: ignore[method-assign]

    async def _cannot_tell(self):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(lease_mod.ExecutionLease, "renew", _cannot_tell)
    asyncio.run(_drive(runner))

    assert calls == ["turn", "finalize"], (
        "an unprovable check must not change behaviour"
    )


def test_a_draining_loop_does_not_claim_what_it_cannot_deliver(db_factory) -> None:
    """Claiming during shutdown loses the message outright.

    The drain flips rows to ``consumed``, and the outer loop breaks on
    ``is_draining`` before it can run a turn — so anything taken here is taken
    and thrown away. Nothing re-creates it: crash recovery only re-synthesises
    MEMBER results from run rows, so a user instruction claimed this way is
    gone for good. And it fires precisely during deploys.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.is_draining", lambda: True)

    async def _run() -> None:
        async with async_unit_of_work() as db:
            await mailbox_store.enqueue(
                db,
                session_id="lead-s",
                task_id="t1",
                project_id="p1",
                user_id=OWNER,
                kind="text",
                text="do not eat me",
                origin="user-inject",
            )
        fake = _Collaborators([])
        runner = ActorRunner(finalizer=fake, coordinator=fake)
        with pytest.raises(TimeoutError):
            await runner._await_wakeup(
                session_id="lead-s", role="lead", ttl=0.3, task_id="t1",
                project_id="p1", user_id=OWNER, coordinator=fake,
                fenced=asyncio.Event(),
            )

    asyncio.run(_run())
    monkey.undo()

    assert asyncio.run(mailbox_store.has_pending("lead-s")), (
        "the instruction must still be pending for whoever comes up after the "
        "deploy — a claimed one is unrecoverable"
    )


def test_a_finished_loop_reclaims_its_buffer(db_factory) -> None:
    """Nothing dropped a box, so a long-lived process grew one per session.

    Reclaiming is safe now only because the box is a local buffer with a single
    owner — but it is still gated on the lease, because the box is shared by
    every incarnation of a session and an ungated drop pulls it out from under
    a replacement. That is the race the claim token used to guard.
    """
    calls: list[str] = []
    asyncio.run(_drive(_runner(calls)))

    assert calls == ["turn", "finalize"], "precondition: a normal, owned exit"
    assert not asyncio.run(mailbox_store.has_pending("lead-s"))
    assert "lead-s" not in mailbox_registry._boxes, (
        "a loop that owned its session to the end must take its buffer with it"
    )


def test_a_superseded_loop_leaves_the_replacements_buffer_alone(db_factory) -> None:
    """The gate, stated as a consequence.

    A loop that lost its lease is not the owner any more. Its buffer belongs to
    whoever replaced it, and dropping it on the way out would strand messages
    the replacement had already parked there.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
    assert asyncio.run(acquire_actor_lease(session_id="lead-s", task_id="t1")) is not None
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")

    # The replacement's buffer, with something parked in it.
    deliver("lead-s", _member_done("m1", "for the replacement"))

    calls: list[str] = []
    asyncio.run(_drive(_runner(calls)))
    monkey.undo()

    assert calls == [], "precondition: we never ran — a peer holds the session"
    assert asyncio.run(mailbox_store.has_pending("lead-s")), (
        "a loop that never owned the session must not take the buffer with it"
    )
