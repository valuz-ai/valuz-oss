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

from valuz_agent.infra import execution_lease as lease_mod
from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.lease import acquire_task_lease
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

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
    mailbox_registry._boxes.clear()
    mailbox_registry._claims.clear()
    yield
    mailbox_registry._boxes.clear()
    mailbox_registry._claims.clear()


class _Collaborators:
    """Fake finalizer + coordinator recording the order seams fire in."""

    def __init__(self, calls: list[str], on_finalize=None) -> None:
        self._calls = calls
        self._on_finalize = on_finalize

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

    async def reconcile_finished_members(self, *, task_id, project_id, user_id) -> list:
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
        states = await lease_mod.load_lease_states("task", ["t1"])
        state = states.get("t1")
        seen.append(state.state if state else None)

    calls: list[str] = []
    asyncio.run(_drive(_runner(calls, on_finalize=_peek_holder_during_finalize)))

    assert calls == ["turn", "finalize"]
    assert seen == ["held"], "the lease must still be held while finalize writes"
    # ...and handed back once finalize is done, so a peer can pick it up now.
    after = asyncio.run(lease_mod.load_lease_states("task", ["t1"]))
    assert after["t1"].state == "released"


def test_bailing_out_leaves_no_phantom_liveness_behind(db_factory) -> None:
    """Regression: the eager claim must not outlive a loop that never drove.

    ``spawn_actor`` claims the inbox eagerly, before the loop can know whether
    it may drive. When the lease says a peer owns the task, the loop exits —
    and if that claim stayed behind, ``is_owned`` would report a reader this
    process does not have for the rest of its life. The watchdog reads
    ``is_owned`` as a second liveness opinion, so the leak would make a task
    permanently unblockable here even after its real driver died with its
    process.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
    assert (
        asyncio.run(acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s"))
        is not None
    )
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")

    mailbox_registry.claim("lead-s")  # what spawn_actor does, eagerly

    calls: list[str] = []
    asyncio.run(_drive(_runner(calls)))

    assert calls == [], "we must not drive a task a peer holds"
    assert not mailbox_registry.is_owned("lead-s"), (
        "a loop that never drove must not leave a claim behind"
    )
    monkey.undo()


def test_superseded_loop_does_not_shut_down_its_replacement(db_factory) -> None:
    """Regression: the stale loop's ``shutdown`` killed the live loop.

    Both loops share ONE inbox for the session, so a fenced loop posting its
    own stop signal unconditionally delivers it to whichever loop owns the box
    now — the replacement — leaving the task with no driver at all.
    """
    stale = asyncio.run(acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s"))
    assert stale is not None
    stale_token = mailbox_registry.claim("lead-s")

    # A newer loop takes both the lease and the inbox claim (a rapid resume).
    newer = asyncio.run(acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s"))
    assert newer is not None
    newer_token = mailbox_registry.claim("lead-s")
    assert newer_token != stale_token

    # The stale loop's renewer now notices it was fenced.
    fenced = asyncio.Event()

    async def _run_renewer() -> None:
        monkey = pytest.MonkeyPatch()
        monkey.setattr("valuz_agent.modules.tasks.actor_runner.TASK_LEASE_RENEW_INTERVAL_S", 0.0)
        await ActorRunner._renew_lease(stale, "lead-s", stale_token, fenced)
        monkey.undo()

    asyncio.run(_run_renewer())

    assert fenced.is_set(), "the stale loop must learn it lost the lease"
    assert not mailbox_registry.has_pending("lead-s"), (
        "no shutdown may be posted into the replacement's inbox"
    )
    # The live loop is untouched and still owns everything.
    assert mailbox_registry.is_claim_current("lead-s", newer_token)
    assert asyncio.run(newer.renew()) is True


def test_recovery_refuses_to_respawn_a_task_a_peer_holds(db_factory) -> None:
    """Regression: at a COLD boot the advisory pre-check cannot help.

    Every worker boots at once, so nobody holds a lease yet and all of them
    pass ``is_driven_elsewhere``. Without an authoritative acquisition inside
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
        asyncio.run(acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s"))
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


def test_fenced_loop_still_signals_when_it_owns_the_inbox(db_factory) -> None:
    """The flip side: a fenced loop that DOES own the box must be woken.

    Cross-process takeover is the common case — there is no local replacement,
    so the shutdown is the only thing that unparks a loop sitting on a 30-minute
    mailbox wait.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")
    ours = asyncio.run(acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s"))
    assert ours is not None
    token = mailbox_registry.claim("lead-s")

    # A different process takes the task over.
    monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
    db = db_factory()
    try:
        db.execute(
            ExecutionLeaseRow.__table__.update()
            .where(ExecutionLeaseRow.scope == "task", ExecutionLeaseRow.key == "t1")
            .values(holder_id="peer-proc", fence_token=ours.fence_token + 1)
        )
        db.commit()
    finally:
        db.close()

    fenced = asyncio.Event()

    async def _run_renewer() -> None:
        m = pytest.MonkeyPatch()
        m.setattr("valuz_agent.modules.tasks.actor_runner.TASK_LEASE_RENEW_INTERVAL_S", 0.0)
        await ActorRunner._renew_lease(ours, "lead-s", token, fenced)
        m.undo()

    asyncio.run(_run_renewer())

    assert fenced.is_set()
    assert mailbox_registry.has_pending("lead-s"), (
        "a parked loop must be woken so it can notice it was fenced"
    )
    msg: InboxMsg = mailbox_registry._boxes["lead-s"].get_nowait()
    assert msg.kind == "shutdown"
    monkey.undo()


# ---------------------------------------------------------------------------
# Between-turns durable backstop: a lead must not depend on cross-process
# mailbox delivery to learn that its members finished.
# ---------------------------------------------------------------------------


class _ReconcilingCoordinator(_Collaborators):
    """Coordinator whose mailbox never delivers, but whose store knows the truth."""

    def __init__(self, calls: list[str], recovered: list[InboxMsg]) -> None:
        super().__init__(calls)
        self._recovered = recovered
        self.reconciles = 0

    async def reconcile_finished_members(self, *, task_id, project_id, user_id) -> list:
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
        )

    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.LEAD_RECONCILE_SLICE_S", 0.01)
    mailbox_registry.register("lead-s")
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
    mailbox_registry.register("lead-s")

    async def _drive():
        first = await runner._await_wakeup(
            session_id="lead-s", role="lead", ttl=60.0, task_id="t1",
            project_id="p1", user_id=OWNER, coordinator=fake,
        )
        # The second one must already be waiting — consumed through the same
        # path as a real arrival, not invented by a second reconcile.
        second = await runner._await_wakeup(
            session_id="lead-s", role="lead", ttl=60.0, task_id="t1",
            project_id="p1", user_id=OWNER, coordinator=fake,
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
    mailbox_registry.register("member-s")

    async def _drive():
        with pytest.raises(TimeoutError):
            await runner._await_wakeup(
                session_id="member-s", role="subtask", ttl=0.05, task_id="t1",
                project_id="p1", user_id=OWNER, coordinator=fake,
            )

    asyncio.run(_drive())
    assert fake.reconciles == 0


def test_a_failing_reconcile_does_not_end_the_wait(db_factory) -> None:
    """The backstop is best-effort: a broken read must not look like a timeout."""

    class _Broken(_Collaborators):
        async def reconcile_finished_members(self, *, task_id, project_id, user_id) -> list:
            raise RuntimeError("store unreachable")

    fake = _Broken([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.LEAD_RECONCILE_SLICE_S", 0.01)
    mailbox_registry.register("lead-s")

    async def _drive():
        # A real message still gets through while the backstop is failing.
        async def _late_put():
            await asyncio.sleep(0.05)
            mailbox_registry.put("lead-s", _member_done("m9", "arrived by mailbox"))

        task = asyncio.create_task(_late_put())
        msg = await runner._await_wakeup(
            session_id="lead-s", role="lead", ttl=5.0, task_id="t1",
            project_id="p1", user_id=OWNER, coordinator=fake,
        )
        await task
        return msg

    msg = asyncio.run(_drive())
    monkey.undo()
    assert msg.payload["summary"] == "arrived by mailbox"


def test_no_durable_writes_while_draining(db_factory) -> None:
    """The backstop must go quiet at teardown.

    ``reconcile_finished_members`` WRITES — it settles run rows and flips plan
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
    mailbox_registry.register("lead-s")
    lifecycle.set_draining()

    async def _drive():
        with pytest.raises(TimeoutError):
            await runner._await_wakeup(
                session_id="lead-s", role="lead", ttl=0.08, task_id="t1",
                project_id="p1", user_id=OWNER, coordinator=fake,
            )

    try:
        asyncio.run(_drive())
    finally:
        lifecycle.reset_draining()
        monkey.undo()

    assert fake.reconciles == 0, "no durable write may be attempted during teardown"


def test_renewer_survives_a_failure_after_the_renew_call(db_factory) -> None:
    """The renewer must not die silently — that is the worst outcome here.

    Only the ``renew()`` call used to be guarded, so anything raising after it
    (posting the shutdown, the claim check) killed the task. Nothing observes
    it, so the lease then expires under a driver that is perfectly healthy and
    a peer's watchdog blocks a live task: the original bug, re-entered through
    a different door.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")
    ours = asyncio.run(
        acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s")
    )
    assert ours is not None
    token = mailbox_registry.claim("lead-s")

    # Fence it, so the renewer takes the "lost the lease" branch. A peer can
    # only take over an expired lease, so age it out first.
    db = db_factory()
    try:
        db.execute(
            ExecutionLeaseRow.__table__.update()
            .where(ExecutionLeaseRow.scope == "task", ExecutionLeaseRow.key == "t1")
            .values(lease_expires_at=1)
        )
        db.commit()
    finally:
        db.close()
    monkey.setattr(lease_mod, "_HOLDER_ID", "peer-proc")
    assert (
        asyncio.run(acquire_task_lease(user_id=OWNER, task_id="t1", lead_session_id="lead-s"))
        is not None
    )
    monkey.setattr(lease_mod, "_HOLDER_ID", "our-proc")

    # ...and make that branch blow up where it was previously unguarded.
    calls = {"n": 0}

    def _boom(session_id, tok):
        calls["n"] += 1
        raise RuntimeError("registry exploded")

    monkey.setattr(mailbox_registry, "is_claim_current", _boom)
    monkey.setattr("valuz_agent.modules.tasks.actor_runner.TASK_LEASE_RENEW_INTERVAL_S", 0.01)

    fenced = asyncio.Event()

    async def _run():
        task = asyncio.create_task(
            ActorRunner._renew_lease(ours, "lead-s", token, fenced)
        )
        await asyncio.sleep(0.15)
        alive = not task.done()
        task.cancel()
        return alive

    still_alive = asyncio.run(_run())
    monkey.undo()

    assert calls["n"] >= 2, "it must keep retrying rather than dying on the first raise"
    assert still_alive, "the renewer must not exit on an unexpected failure"
    assert fenced.is_set(), "and it must still report the lost lease"


class _DupThenShutdown(_Collaborators):
    """Coordinator that reports every member_done as already settled."""

    def __init__(self, calls: list[str]) -> None:
        super().__init__(calls)
        self.settled_asked = 0

    async def member_already_settled(self, *, task_id, project_id, member_session_id, user_id) -> bool:
        self.settled_asked += 1
        return True

    async def lead_idle_with_no_pending(self, task_id, project_id, user_id, lead_session_id="") -> bool:
        return False  # keep the loop parked on its mailbox

    async def reconcile_finished_members(self, *, task_id, project_id, user_id) -> list:
        return []


def test_dropping_a_duplicate_does_not_re_run_the_previous_prompt(db_factory) -> None:
    """Production regression: the user's own message was sent a second time.

    Dropping a duplicate `member_done` used to `continue` the OUTER loop, whose
    very next statement is `run_turn(session_id, prompt)` — and `prompt` still
    held the PREVIOUS turn's text. So "ignore this message" actually meant
    "re-run the last prompt", and a finished task was executed a second time
    from its original goal.

    Here the loop must run the kickoff turn, silently drop the duplicate, and
    then exit on the shutdown — one turn, not two.
    """
    fake = _DupThenShutdown([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    prompts: list[str] = []

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        prompts.append(content)
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]

    mailbox_registry.register("lead-s")
    mailbox_registry.put("lead-s", _member_done("m1", "already reviewed"))
    mailbox_registry.put("lead-s", InboxMsg(kind="shutdown"))

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

        async def reconcile_finished_members(self, *, task_id, project_id, user_id) -> list:
            return []

    fake = _NotSettled([])
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
    mailbox_registry.register("lead-s")
    mailbox_registry.put("lead-s", _member_done("m1", "please review me"))
    mailbox_registry.put("lead-s", InboxMsg(kind="shutdown"))

    asyncio.run(_drive(runner))
    monkey.undo()

    assert len(prompts) == 2, "kickoff + the member result"
    assert "please review me" in prompts[1]
    assert prompts[1] != prompts[0], "the second turn must not repeat the first"


def test_a_failed_acquire_hands_the_inbox_back(db_factory, monkeypatch) -> None:
    """Undriven AND invisible is worse than undriven.

    The inbox is already claimed when the lease is acquired, so an exception
    escaping the acquire used to leave that claim behind with no loop to answer
    for it. ``is_owned`` is the watchdog's second liveness opinion, so the task
    would read as healthy forever and could never be blocked — stuck ``active``
    with nothing driving it and nothing able to notice.
    """
    async def _boom(**kw):
        raise RuntimeError("database blip")

    monkeypatch.setattr(
        "valuz_agent.modules.tasks.actor_runner.acquire_task_lease", _boom
    )
    fake = _Collaborators([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    mailbox_registry.claim("lead-s")  # what spawn_actor does

    with pytest.raises(RuntimeError):
        asyncio.run(_drive(runner))

    assert not mailbox_registry.is_owned("lead-s"), (
        "a loop that could not take the lease must not leave a claim behind"
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

        async def reconcile_finished_members(self, *, task_id, project_id, user_id) -> list:
            return []

    reads: list[int] = []

    async def _goal(task_id, project_id, user_id):
        reads.append(1)
        return "" if len(reads) == 1 else "THE REAL GOAL"

    monkeypatch.setattr(ActorRunner, "_task_goal", staticmethod(_goal))
    monkeypatch.setattr(
        "valuz_agent.modules.tasks.planning.mark_in_review", lambda **kw: asyncio.sleep(0)
    )

    fake = _Wake([])
    runner = ActorRunner(finalizer=fake, coordinator=fake)
    prompts: list[str] = []

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        prompts.append(content)
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]
    mailbox_registry.register("lead-s")
    mailbox_registry.put("lead-s", _member_done("m1", "result"))
    mailbox_registry.put("lead-s", InboxMsg(kind="shutdown"))

    asyncio.run(_drive(runner))

    assert len(reads) == 2, "the empty first read must be retried at wake-up"
    assert "<task-goal>THE REAL GOAL</task-goal>" in prompts[1], (
        "the recovered goal must be restated on the wake-up"
    )
