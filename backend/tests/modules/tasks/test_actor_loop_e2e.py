"""The lead↔member actor loop, end to end, through the REAL machinery.

Every other test in this package exercises one link and stubs the loop:
dispatch alone, member_done alone, review alone, finalize alone. But the
invariants that actually broke in production live BETWEEN the links, in the
loop itself — a member's post-turn notify reaching the lead's inbox, the lead
waking on it, `lead_idle_with_no_pending` deciding when there is nothing left
to wait for, the shutdown broadcast landing before a member's next turn, and
mailbox ownership surviving a teardown that overlaps a respawn. A suite of
per-link tests can all pass while the chain is broken.

So this drives the real `run_actor_loop`, the real `MailboxRegistry`, the real
services and a real (tmp-SQLite) database. ONLY the kernel boundary is faked:

  * ``ActorRunner.run_turn`` — a scripted turn. Each entry may run REAL service
    calls (dispatch / review / finish), exactly as the agent's tool calls
    would, and returns the turn's final status.
  * session creation / finalize / manifest / member resolution — the adapter
    seam, which has its own contract tests.

What that leaves under test is precisely the wiring: who wakes whom, in what
order, and who writes the terminal state.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from valuz_agent.modules.tasks import messaging, planning
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.lease import load_actor_lease_states
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator
from valuz_agent.modules.tasks.plan import TaskPlan

# Several tests below park a lead until a member result arrives. Delivery is
# durable now, so it lands on the loop's inbox poll (ACTOR_INBOX_POLL_S, 5s)
# rather than instantly through an in-process queue. Their budgets reflect that
# cadence; R4's notifier is what removes the wait, not a smaller number here.

OWNER = "local-test-owner"
LEAD = "lead-sess"
PROJECT = "w1"
TASK = "t1"


# ---------------------------------------------------------------------------
# Harness — the kernel seam, faked once
# ---------------------------------------------------------------------------


@pytest.fixture
def loop_env(db_factory, tmp_path, monkeypatch):
    """A task with a lead run, plus every kernel-boundary call stubbed.

    Returns a small control object: ``script`` maps session_id → list of turn
    callables, and each is invoked (awaited) in place of a real agent turn.
    """
    from valuz_agent.modules.sessions import run_orchestrator as run_orch
    from valuz_agent.modules.tasks import dispatcher as dispatcher_mod
    from valuz_agent.modules.tasks import launcher as launcher_mod
    from valuz_agent.modules.tasks import manifest as manifest_mod
    from valuz_agent.modules.tasks import resolution as resolution_mod
    from valuz_agent.modules.tasks.resolution import ResolvedTaskSession, TaskProjectEnv

    db = db_factory()
    try:
        db.add(
            TaskRow(
                id=TASK,
                user_id=OWNER,
                project_id=PROJECT,
                file_path=str(tmp_path / "t1.md"),
                title="T",
                goal="do it",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        db.add(
            TaskSessionRow(
                id="run-lead",
                user_id=OWNER,
                project_id=PROJECT,
                task_id=TASK,
                session_id=LEAD,
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
                run_dir=str(tmp_path),
            )
        )
        db.commit()
    finally:
        db.close()

    class _Resolver:
        """Hands dispatch a ready-to-create member session."""

        async def resolve_project_env(self, _db: Any, **_kw: Any) -> TaskProjectEnv:
            return TaskProjectEnv(
                project_row=SimpleNamespace(id=PROJECT),
                project_cwd=tmp_path,
                instructions_md=None,
            )

        async def resolve_member(
            self, _db: Any, *, agent_slug: str, brief: str, **_kw: Any
        ) -> ResolvedTaskSession:
            seq = len(state.members) + 1
            sid = f"mem-{seq}"
            state.members.append(sid)
            return ResolvedTaskSession(
                session=SimpleNamespace(id=sid),
                agent_slug=agent_slug,
                brief=brief,
                credential_gap=None,
                agent_name=agent_slug.title(),
            )

    async def _noop(*_a: Any, **_k: Any) -> None: ...

    async def _manifest(session_id: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        return {"session_id": session_id, "status": "idle", "summary": f"{session_id} done"}

    # ``from … import task_session_resolver`` binds the name in each consumer,
    # so patch the source AND the binding dispatch actually reads.
    fake_resolver = _Resolver()
    monkeypatch.setattr(resolution_mod, "task_session_resolver", fake_resolver)
    monkeypatch.setattr(dispatcher_mod, "task_session_resolver", fake_resolver)
    monkeypatch.setattr(launcher_mod.kernel_client, "create_session", _noop)
    monkeypatch.setattr(launcher_mod.project_index, "record", _noop)
    monkeypatch.setattr(run_orch, "_finalize_session", _noop)
    monkeypatch.setattr(manifest_mod, "collect_manifest", _manifest)

    orch = TaskOrchestrator()

    class _State:
        def __init__(self) -> None:
            self.script: dict[str, list[Any]] = {}
            self.members: list[str] = []
            self.turns: list[str] = []

    state = _State()

    async def _scripted_turn(_self: Any, session_id: str, content: str, user_id: str) -> str:
        state.turns.append(session_id)
        queue = state.script.get(session_id) or []
        step = queue.pop(0) if queue else "idle"
        if callable(step):
            result = step(content)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result or "idle")
        return str(step)

    monkeypatch.setattr(type(orch.actor), "run_turn", _scripted_turn)
    yield SimpleNamespace(orch=orch, state=state, tmp_path=tmp_path, db_factory=db_factory)

    for sid in [LEAD, *state.members]:
        mailbox_registry.unregister(sid)


def _plan(db_factory) -> TaskPlan:
    db = db_factory()
    try:
        return TaskPlan.from_dict(db.get(TaskRow, TASK).plan)
    finally:
        db.close()


def _task_status(db_factory) -> str:
    db = db_factory()
    try:
        return db.get(TaskRow, TASK).status
    finally:
        db.close()


def _events(db_factory) -> list[str]:
    db = db_factory()
    try:
        return [
            e.type
            for e in db.execute(select(TaskEventRow).order_by(TaskEventRow.sequence)).scalars()
        ]
    finally:
        db.close()


def _runs(db_factory) -> dict[str, str]:
    db = db_factory()
    try:
        return {r.session_id: r.status for r in db.execute(select(TaskSessionRow)).scalars()}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The happy path, whole
# ---------------------------------------------------------------------------


def test_dispatch_report_review_finish_through_the_real_loop(loop_env) -> None:
    """plan → dispatch → member works → member_done wakes the lead → review
    approves → finish closes the task. Every hop through the real loop.

    This is the chain no other test covers: the member's post-turn
    ``notify_lead_member_idle`` has to land in the lead's inbox, the lead's
    park has to wake on it, ``mark_in_review`` has to fire from the loop (not
    from a test calling it directly), and the terminal write has to happen in
    ``finalize_actor`` after the loop exits.
    """
    orch, state = loop_env.orch, loop_env.state

    asyncio.run(
        planning.plan_task(
            task_id=TASK,
            project_id=PROJECT,
            user_id=OWNER,
            lead_session_id=LEAD,
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )

    async def _lead_turn_1(_prompt: str) -> str:
        # The lead's first turn dispatches the only subtask, then ends. The
        # loop must now PARK (a member is live) rather than finalize.
        res = await orch.dispatcher.dispatch_async(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            subtask_key="a",
            user_id=OWNER,
        )
        assert res.get("status") == "dispatched", res
        return "idle"

    async def _lead_turn_2(prompt: str) -> str:
        # Woken by member_done — the prompt the loop built carries the result.
        assert "mem-1" in prompt or "done" in prompt, prompt
        approved = await planning.review_subtask(
            task_id=TASK,
            project_id=PROJECT,
            user_id=OWNER,
            lead_session_id=LEAD,
            decision="approve",
            subtask_key="a",
        )
        assert approved.get("decision") == "approve", approved
        finished = await orch.finalization.finish_task(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            summary="all done",
            user_id=OWNER,
        )
        assert finished.get("ok") is True, finished
        return "idle"

    state.script[LEAD] = [_lead_turn_1, _lead_turn_2]

    async def _run() -> None:
        await asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="drive the task",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=2.0,
                user_id=OWNER,
            ),
            timeout=25,
        )
        # The member loop was spawned by dispatch as a sibling task; give it
        # room to finish so its own finalize lands before we assert.
        for _ in range(50):
            if _runs(loop_env.db_factory).get("mem-1") in ("completed", "archived"):
                break
            await asyncio.sleep(0.02)

    asyncio.run(_run())

    assert state.turns[0] == LEAD
    assert "mem-1" in state.turns, "the member's own loop must have run a turn"

    node = _plan(loop_env.db_factory).get("a")
    assert node is not None and node.status == "done", "approve must land through the loop"
    assert _task_status(loop_env.db_factory) == "completed"

    events = _events(loop_env.db_factory)
    for expected in (
        "task_planned",
        "subtask_spawned",
        "subtask_reported",  # the member's post-turn report — the wake-up
        "subtask_reviewed",
        "subtask_completed",
        "task_completed",
    ):
        assert expected in events, f"{expected} missing from {events}"

    runs = _runs(loop_env.db_factory)
    assert runs["mem-1"] == "completed"
    assert runs[LEAD] == "completed"
    # Boxes outlive their loops now — nothing drops one on the way out, which
    # is what made a stale teardown able to pop the box a resumed loop was
    # reading. What must be true is that both loops left cleanly: nothing
    # queued, and neither still holds the right to run its session.
    assert not mailbox_registry.has_pending("mem-1")
    assert not mailbox_registry.has_pending(LEAD)
    # Only the LEAD's lease is asserted: this test awaits the lead loop, and
    # the member's teardown finishes on its own schedule — asserting its
    # release here would be a race, not a guarantee.
    held = asyncio.run(load_actor_lease_states([LEAD]))
    assert held[LEAD].state == "released", "the lead must hand its session back"


def test_lead_with_nothing_outstanding_exits_without_waiting(loop_env) -> None:
    """A lead that satisfies the goal inline (no dispatch) must NOT park for
    the idle TTL: ``lead_idle_with_no_pending`` breaks the loop and
    auto-finalize closes the task. Regression for the 30-minute orphan."""
    orch, state = loop_env.orch, loop_env.state
    state.script[LEAD] = ["idle"]

    async def _run() -> None:
        # A 60s TTL would hang the test if the early-exit predicate regressed.
        await asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="just answer",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=60.0,
                user_id=OWNER,
            ),
            timeout=5,
        )

    asyncio.run(_run())

    assert _task_status(loop_env.db_factory) == "completed"
    assert "task_completed" in _events(loop_env.db_factory)


def test_shutdown_broadcast_ends_the_member_loop_and_leaves_its_run_parked(
    loop_env,
) -> None:
    """stop_task's broadcast must reach a member parked between turns, and the
    member's own loop exit must NOT overwrite the run stop_task parked.

    Two invariants in one: the shutdown reaches an idle member (its mailbox
    park wakes on it), and the loop's terminal write respects a run that is no
    longer ``active`` (settle_run_if_active) — the pair that used to make a
    paused member invisible to recovery.
    """
    orch, state = loop_env.orch, loop_env.state

    asyncio.run(
        planning.plan_task(
            task_id=TASK,
            project_id=PROJECT,
            user_id=OWNER,
            lead_session_id=LEAD,
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )

    async def _run() -> None:
        res = await orch.dispatcher.dispatch_async(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            subtask_key="a",
            user_id=OWNER,
        )
        assert res.get("status") == "dispatched", res
        # Let the member run its first turn and park on its mailbox.
        for _ in range(50):
            if "mem-1" in state.turns:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)

        async def _no_interrupt(_sid: str, user_id: str | None = None) -> None: ...

        orch.recovery._interrupt_kernel_session = _no_interrupt  # type: ignore[method-assign]
        assert await orch.recovery.stop_task(TASK, PROJECT, user_id=OWNER) is True

        # The parked member must exit promptly on the broadcast — no TTL wait.
        # "Exited" is the lease being handed back: the mailbox is no longer an
        # ownership record, so its box outlives the loop that read it.
        for _ in range(100):
            states = await load_actor_lease_states(["mem-1"])
            if "mem-1" not in states or states["mem-1"].state == "released":
                break
            await asyncio.sleep(0.02)

    asyncio.run(_run())

    assert _task_status(loop_env.db_factory) == "paused"
    final = asyncio.run(load_actor_lease_states(["mem-1"]))
    assert "mem-1" not in final or final["mem-1"].state == "released", (
        "the shutdown broadcast must wake a member parked between turns"
    )
    assert _runs(loop_env.db_factory)["mem-1"] == "paused", (
        "the member's loop exit must not overwrite the run stop_task parked — "
        "recovery only resumes active/paused runs"
    )


def test_member_result_reaches_a_lead_that_is_mid_park(loop_env) -> None:
    """The wake-up itself: a member finishing while the lead is parked on its
    mailbox must resume the lead's loop with the report as its prompt."""
    orch, state = loop_env.orch, loop_env.state
    woken: list[str] = []

    asyncio.run(
        planning.plan_task(
            task_id=TASK,
            project_id=PROJECT,
            user_id=OWNER,
            lead_session_id=LEAD,
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )

    async def _lead_turn_1(_prompt: str) -> str:
        await orch.dispatcher.dispatch_async(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            subtask_key="a",
            user_id=OWNER,
        )
        return "idle"

    async def _lead_turn_2(prompt: str) -> str:
        woken.append(prompt)
        await orch.finalization.finish_task(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            summary="done",
            status="stopped",  # skip the unresolved-node guard; the wake is the point
            user_id=OWNER,
        )
        return "idle"

    state.script[LEAD] = [_lead_turn_1, _lead_turn_2]

    async def _run() -> None:
        await asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="go",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=12.0,
                user_id=OWNER,
            ),
            timeout=25,
        )

    asyncio.run(_run())

    assert woken, "the lead never woke on member_done — it parked until the TTL"
    assert "mem-1 done" in woken[0], woken[0]
    # The node moved to in_review from the LOOP's mark_in_review, not a test call.
    node = _plan(loop_env.db_factory).get("a")
    assert node is not None and node.status in ("in_review", "done"), node.status


def test_lead_wake_up_restates_the_task_goal(loop_env) -> None:
    """A lead wake-up must carry the task goal, not just the member result.

    The kernel wraps EVERY non-slash message of a goal-mode session as
    ``/goal <text>`` (wrap_for_mode: "each turn enters its native mode for
    that turn"), and task leads run in goal mode. So whatever we send on a
    wake-up becomes that turn's goal: a bare member result would re-goal the
    lead to "review this result", and the runtime's goal-auto-exit fires as
    soon as that trivial goal is met — the lead stops driving the real task.
    """
    orch, state = loop_env.orch, loop_env.state
    prompts: list[str] = []

    asyncio.run(
        planning.plan_task(
            task_id=TASK,
            project_id=PROJECT,
            user_id=OWNER,
            lead_session_id=LEAD,
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )

    async def _turn_1(_prompt: str) -> str:
        await orch.dispatcher.dispatch_async(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            subtask_key="a",
            user_id=OWNER,
        )
        return "idle"

    async def _turn_2(prompt: str) -> str:
        prompts.append(prompt)
        await orch.finalization.finish_task(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            summary="done",
            status="stopped",
            user_id=OWNER,
        )
        return "idle"

    state.script[LEAD] = [_turn_1, _turn_2]

    async def _run() -> None:
        await asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="go",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=12.0,
                user_id=OWNER,
            ),
            timeout=25,
        )

    asyncio.run(_run())

    assert prompts, "the lead never woke on member_done"
    assert "<task-goal>do it</task-goal>" in prompts[0], (
        "the wake-up prompt must restate the task goal — without it the "
        f"turn's goal becomes the member result (got: {prompts[0][:200]})"
    )
    assert "mem-1 done" in prompts[0], "the member result must still be carried"


def test_last_assistant_text_reads_the_tail_not_the_head(monkeypatch) -> None:
    """The summary must come from the session's LAST turn.

    ``get_events(limit=200)`` is ``get_events_after(after_seq=0, limit=200)``
    — "row id strictly greater than 0, ordered ASCENDING" — so it returns the
    FIRST 200 events. Walking those backwards found the newest assistant
    message among the OLDEST 200: on any session past that mark the member
    reported a summary frozen near its start, and the lead reviewed stale
    work. This pins the tail read.
    """
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import manifest as manifest_mod

    called: dict[str, Any] = {}

    async def _fake_window(user_id: str, session_id: str, **kw: Any) -> Any:
        called.update(kw)
        return SimpleNamespace(
            items=[
                SimpleNamespace(type="assistant_message", data={"text": "newest"}),
            ],
            has_more=True,
        )

    async def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not use the head-first get_events read")

    monkeypatch.setattr(manifest_mod.kernel_client, "get_events_window", _fake_window)
    monkeypatch.setattr(manifest_mod.kernel_client, "get_events", _explode)

    out = asyncio.run(manifest_mod.last_assistant_text(OWNER, "s1"))
    assert out == "newest"
    assert called.get("turn_limit"), "the tail window must be turn-bounded"


def test_every_turn_logs_where_its_prompt_came_from(loop_env, caplog) -> None:
    """Each turn must name its own trigger in the log.

    A turn is otherwise anonymous: the log shows work happening but never why
    it started. That gap has a cost on record — a regression that re-ran a
    finished task from its original goal took hours to pin down, because
    proving WHICH producer woke the lead meant correlating DB turn rows with
    log timestamps by hand. Several distinct producers emit ``member_done``
    (a live member going idle, the durable reconcile backstop, a cancelled
    member, recovery re-seeding after a restart), so the kind alone cannot
    answer it; the label has to name the producer.
    """
    orch, state = loop_env.orch, loop_env.state

    asyncio.run(
        planning.plan_task(
            task_id=TASK,
            project_id=PROJECT,
            user_id=OWNER,
            lead_session_id=LEAD,
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )

    async def _turn_1(_prompt: str) -> str:
        await orch.dispatcher.dispatch_async(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            subtask_key="a",
            user_id=OWNER,
        )
        return "idle"

    async def _turn_2(_prompt: str) -> str:
        await orch.finalization.finish_task(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            summary="done",
            status="stopped",
            user_id=OWNER,
        )
        return "idle"

    state.script[LEAD] = [_turn_1, _turn_2]

    async def _run() -> None:
        await asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="go",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=12.0,
                user_id=OWNER,
            ),
            timeout=25,
        )

    with caplog.at_level("INFO", logger="valuz_agent.modules.tasks.actor_runner"):
        asyncio.run(_run())

    # Scoped to the LEAD session: the dispatched member runs its own loop and
    # logs its own turns, which is itself part of the point — every actor is
    # attributable, so the lines have to be separable per session.
    origins = [
        line.split("←", 1)[1].strip()
        for line in caplog.messages
        if line.startswith(f"actor loop {LEAD} ") and "←" in line
    ]
    assert len(origins) == 2, f"expected one log line per lead turn, got {origins}"
    assert origins[0].startswith("initial"), (
        f"turn 1 is the kickoff prompt, not a mailbox wake-up (got {origins[0]})"
    )
    # The producer, not just the kind: a live member going idle must not read
    # the same as the reconcile backstop firing for an already-finished member.
    assert origins[1].startswith("member_done/member-idle<"), (
        "turn 2 must name the producer that woke the lead, so an unexpected "
        f"turn can be traced to its source (got {origins[1]})"
    )


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        (
            InboxMsg(kind="text", origin="user-inject", from_session="abcdef123456"),
            "text/user-inject<abcdef12>",
        ),
        (
            InboxMsg(kind="member_done", origin="reconcile", from_session="ff00"),
            "member_done/reconcile<ff00>",
        ),
        (InboxMsg(kind="revise_goal", origin="goal-revised"), "revise_goal/goal-revised"),
        # An untagged producer degrades to the bare kind rather than inventing one.
        (InboxMsg(kind="text", from_session="deadbeefcafe"), "text<deadbeef>"),
    ],
)
def test_origin_label_names_the_producer(msg, expected) -> None:
    assert ActorRunner._origin_label(msg) == expected


def test_a_lead_runs_an_instruction_written_by_another_process(loop_env) -> None:
    """The whole delivery path, through the real loop.

    ``inject_into_task`` is called without touching the mailbox registry — the
    state of every host process but the one driving the lead. It has to become
    a turn anyway, with its envelope intact.
    """
    orch, state = loop_env.orch, loop_env.state
    prompts: list[str] = []

    async def _turn_1(_prompt: str) -> str:
        await messaging.inject_into_task(
            task_id=TASK,
            project_id=PROJECT,
            text="add the STAR market hot sectors",
            from_session_id="chat-sess",
            user_id=OWNER,
        )
        return "idle"

    async def _turn_2(prompt: str) -> str:
        prompts.append(prompt)
        await orch.finalization.finish_task(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            summary="done",
            status="stopped",
            user_id=OWNER,
        )
        return "idle"

    state.script[LEAD] = [_turn_1, _turn_2]

    asyncio.run(
        asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="go",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=30.0,
                user_id=OWNER,
            ),
            timeout=20,
        )
    )

    assert prompts, "the lead never woke on the injected instruction"
    assert "add the STAR market hot sectors" in prompts[0]
    assert '<user-instruction source="chat">' in prompts[0], (
        "the envelope must survive the durable round trip — it is what marks "
        "the text as authoritative user intent"
    )


def test_a_lead_does_not_finalize_while_a_message_is_unread(loop_env) -> None:
    """The early exit has to consult the durable inbox, not just the queue.

    A lead with nothing outstanding finalizes at once rather than parking for
    its 30-minute TTL. That check read the in-process queue, which cannot see
    a message another process wrote, so the task would complete with the
    user's instruction unread.
    """
    orch, state = loop_env.orch, loop_env.state
    seen: list[str] = []

    async def _turn_1(_prompt: str) -> str:
        # Nothing dispatched: idle with no pending by every in-process measure.
        await messaging.inject_into_task(
            task_id=TASK,
            project_id=PROJECT,
            text="one more thing",
            from_session_id="chat-sess",
            user_id=OWNER,
        )
        return "idle"

    async def _turn_2(prompt: str) -> str:
        seen.append(prompt)
        await orch.finalization.finish_task(
            task_id=TASK,
            project_id=PROJECT,
            lead_session_id=LEAD,
            summary="done",
            status="stopped",
            user_id=OWNER,
        )
        return "idle"

    state.script[LEAD] = [_turn_1, _turn_2]

    asyncio.run(
        asyncio.wait_for(
            orch.actor.run_actor_loop(
                session_id=LEAD,
                initial_prompt="go",
                role="lead",
                task_id=TASK,
                project_id=PROJECT,
                idle_ttl=30.0,
                user_id=OWNER,
            ),
            timeout=20,
        )
    )

    assert seen, (
        "the lead finalized with an unread message — the early exit only asked its own process"
    )
    assert "one more thing" in seen[0]
