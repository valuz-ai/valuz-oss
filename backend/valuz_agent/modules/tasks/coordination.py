"""CoordinationService — lead ↔ member coordination.

await_member_results (in-turn mailbox drain, heartbeat-sliced) · the
role callbacks the ActorRunner binds as its ``ActorCoordinator``
(notify_lead_member_idle / lead_idle_with_no_pending / session_still_working)
· stop_tracking_members, which drops a task's members atomically.

Text DELIVERY (send_to_member / inject_into_task / goal revision) lives in
``messaging`` — callers import it directly; there is no wrapper here.

CRITICAL invariant: ``stop_tracking_members`` must stay a plain ``def`` — the
single ``drain_members`` pop and the per-member puts may not be separated by
an ``await``, or a concurrently spawned member is dropped. ``await`` inside a
sync function is a SyntaxError, so the compiler enforces it (as it does for
``DispatcherService._spawn_member``, the other half of the race).
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.agent_resolver import resolve_agent_display_name
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.manifest import collect_manifest_safe
from valuz_agent.modules.tasks.task_state import NON_REVIEWABLE_DONE
from valuz_agent.modules.tasks.member_probe import _member_result
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
    pick_lead_run,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks import mailbox_store, member_probe, notifier
from valuz_agent.modules.tasks.mailbox import InboxMsg
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan

logger = logging.getLogger(__name__)

# Heartbeat slice for await_member_results: how often the lead reconciles
# in-flight members against their kernel session while waiting (VALUZ-RESUME §5.4).
_HEARTBEAT_S = 8.0

# Run the parked-member probe every Nth heartbeat slice rather than every one.
# The probe asks the decision inbox "is EVERY pending member waiting on the
# user?" — a state that can only change when a HUMAN acts, so 8-second
# resolution buys nothing and costs a full run listing + one kernel-session
# read per pending member + an inbox snapshot on every slice of a wait that can
# run ten minutes. The heartbeat itself is NOT throttled: it is the backstop
# for a member that died without delivering ``member_done``, and every slice it
# skips is time the lead hangs on a result that will never arrive.
_PROBE_EVERY_N_SLICES = 4

# Max seconds a SINGLE await_members call parks. await is designed to be LOOPED
# (the still_running hint + inbox-notice drive prompt re-await), so one call
# never needs to wait longer — and it MUST stay under the MCP client's tool-call
# ceiling (codex aborts a tool call at its ``tool_timeout_sec``) so a healthy
# wait is never mis-reported as a transport failure. The harness MCP servers set
# that ceiling to this value + a margin (see
# capability_resolver._INTERNAL_MCP_TOOL_TIMEOUT_SEC). A model-supplied
# ``timeout_s`` above this is clamped.
_MAX_AWAIT_WINDOW_S = 600.0


class CoordinationService:
    """Lead ↔ member coordination; the ActorRunner's typed ``ActorCoordinator``."""

    def __init__(self, *, registry: LiveMemberRegistry) -> None:
        self._members = registry

    # ------------------------------------------------------------------
    # await_members (v0.14) — turn-内阻塞收集并行 member 结果
    # ------------------------------------------------------------------

    async def await_member_results(
        self,
        *,
        lead_session_id: str,
        project_id: str,
        task_id: str,
        keys: list[str] | None = None,
        mode: str = "all",
        timeout_s: float | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Block (inside the lead's turn) until dispatched members finish.

        v0.14 real-time dispatch (see decision doc §14): the lead calls this
        right after ``dispatch``-ing one or more subtasks. Drains the lead's
        mailbox for ``member_done`` messages (the same channel the actor-loop
        fallback uses *between* turns — but here we consume it *within* the
        turn, so the lead reviews results without a between-turn round-trip).

        ``keys``: subtask keys to wait for; ``None`` = all currently
        outstanding nodes (plan status in_progress/in_review). ``mode``:
        ``all`` waits for every target key, ``any`` returns on the first.
        ``timeout_s``: on expiry, return whatever was collected plus
        ``pending`` (so a stuck member can't hang the lead forever).
        """

        # Ensure the lead inbox exists so ``get`` blocks for member_done
        # instead of raising KeyError (which would return empty instantly and
        # make the lead think members are stuck). ``dispatch`` already
        # registers it; this is belt-and-suspenders. Idempotent.

        # Load the plan + the set of subtask keys that currently have a
        # dispatched, in-flight member (an "active" subtask run). We need both to
        # (a) resolve the target set when ``keys`` is omitted and (b) guard the
        # wait below. ``dispatch_async`` records the run as ``active``
        # synchronously (create_run) before it returns, so this DB view is
        # authoritative the moment a real dispatch has happened.
        async with async_unit_of_work(commit=False) as db:
            row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
            live_keys = {
                r.subtask_key
                for r in await TaskSessionDatastore(db).list_runs(user_id, task_id)
                if r.kind == "subtask" and r.subtask_key and r.status == "active"
            }
        plan = TaskPlan.from_dict(row.plan) if row else TaskPlan()

        # Resolve the target set from the plan when keys are not given.
        if keys:
            target: set[str] = {k for k in keys if k}
        else:
            target = {n.key for n in plan.nodes if n.status in ("in_progress", "in_review")}

        # Precondition (VALUZ: "planned-but-never-dispatched, then await" trap):
        # at least one target key must have a live member to wait on. Awaiting a
        # key with no dispatched member can only ever burn the full timeout
        # waiting for a ``member_done`` that can never arrive — which is exactly
        # what strands a lead that re-planned but forgot to ``dispatch``. Return
        # immediately with actionable guidance instead of blocking.
        awaitable = (target & live_keys) if target else live_keys
        if not awaitable:
            requested = sorted(target)
            return {
                "error": "no_dispatched_members",
                "message": (
                    "await_members: nothing to wait for — no dispatched member is in "
                    "flight"
                    + (f" for keys {requested}" if requested else "")
                    + ". A member exists only after you dispatch its subtask."
                ),
                "hint": (
                    "Call dispatch(subtask_key=...) for a ready subtask BEFORE "
                    "await_members. Use get_plan to inspect statuses."
                ),
                "ready_keys": plan.ready_keys(),
                "results": [],
                "pending": requested,
                "collected": 0,
                "timed_out": False,
            }

        loop = asyncio.get_running_loop()
        # Default cap so a member that dies without a member_done can't hang
        # the lead indefinitely (the actor loop posts member_done even on
        # terminal status, so this is a backstop, not the common path).
        # Clamp the per-call wait to one window unit (_MAX_AWAIT_WINDOW_S). A
        # larger model-supplied timeout_s doesn't buy anything — await loops — and
        # would risk exceeding the codex tool-call ceiling, turning a healthy wait
        # into a "timed out awaiting tools/call" transport failure.
        # NB: distinct from the ``requested`` key list above — this one is the
        # caller's wait window. They shared a name until 2026-07, which only
        # worked because the key-list branch returns before reaching here.
        requested_window = timeout_s if timeout_s is not None else _MAX_AWAIT_WINDOW_S
        effective_timeout = min(requested_window, _MAX_AWAIT_WINDOW_S)
        deadline = loop.time() + effective_timeout
        collected: dict[str, dict[str, Any]] = {}
        # VALUZ-CHATPLAN S5: if a user-injected ``message`` arrives in the
        # lead mailbox while we wait, BREAK OUT immediately with whatever has
        # been collected so far + the injection — the lead needs to react
        # (often by ``modify_plan``/``dispatch``-ing extra work) before
        # continuing to wait. Was previously silently dropped (``continue``),
        # which delayed inject by up to ``timeout_s``.
        user_inject: dict[str, Any] | None = None
        # Set when the wait broke early because EVERY pending member is parked
        # on a question for the user (requires_action) — waiting the full
        # timeout is pure waste when nothing can move without the user.
        awaiting_user_break = False
        pending_probe: list[dict[str, Any]] = []
        slices_waited = 0

        while True:
            if mode == "all" and target and target.issubset(collected.keys()):
                break
            if mode == "any" and collected:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            # Chop the wait into ~8s heartbeat slices (VALUZ-RESUME §5.4): on each
            # slice expiry, reconcile in-flight members whose kernel session went
            # terminal but whose member_done never reached the mailbox (bad-case
            # #3 online window). Synthesize their result so the lead doesn't hang.
            slice_timeout = min(_HEARTBEAT_S, remaining)
            # A user instruction, a goal revision or a member's report — from
            # whichever process wrote it. This wait is where it matters most:
            # the lead is inside a turn, parked on members that may run for
            # minutes, and the preempt below exists so it can react now.
            if not await self.actor_still_wanted(
                session_id=lead_session_id,
                role="lead",
                task_id=task_id,
                project_id=project_id,
                user_id=user_id,
            ):
                # The task was paused or finished while we waited. Stop
                # collecting and let the turn end; the loop reads the same
                # state and leaves.
                #
                # This used to require seeing a ``shutdown`` message and
                # putting it BACK, because the actor loop reads the same
                # inbox between turns and would otherwise never see it. A
                # fact can be read twice.
                break
            # Same rule as the between-turns wait: never claim what a
            # shutdown will discard. The drain marks rows ``consumed``, and
            # a turn being torn down cannot act on what it took.
            durable = (
                [] if is_draining() else await self._drain_durable_inbox(lead_session_id)
            )
            # Exactly one, and handled below. Draining a batch meant parking
            # the rest somewhere between iterations, and that somewhere was a
            # module-level dict keyed by session that nothing ever emptied.
            msg = durable[0] if durable else None
            if msg is None:
                slices_waited += 1
                # Let the REAL delivery win the first slice. The backstop reads
                # durable state, so it sees a member as finished the instant its
                # kernel session goes terminal — while that member is still
                # inside ``notify_lead_member_idle`` collecting its manifest,
                # which scans the run directory and is not fast. Synthesizing
                # there does not just duplicate work: the lead reviews the
                # synthesized result and moves on, and the real ``member_done``
                # then lands in a mailbox nobody is draining. It sits there until
                # the turn ends and wakes the lead for a member it already
                # handled — an extra model turn per member, on a task that is
                # often already complete. Measured on qa: 2 members, 2 wasted
                # turns, both after ``task_completed``.
                #
                # One slice of grace covers manifest collection in the normal
                # case. A member that genuinely died without delivering is still
                # caught, just one slice later — which is what a backstop is for.
                if slices_waited > 1:
                    pending_now = (target - set(collected.keys())) if target else set()
                    collected.update(
                        await member_probe.heartbeat_pending(
                            task_id=task_id,
                            project_id=project_id,
                            pending_keys=pending_now,
                            user_id=user_id,
                        )
                    )
                # Parked-member probe: a member sitting on an AskUserQuestion
                # keeps its kernel session ``running``, so from here it is
                # indistinguishable from a long tool call — unless we ask the
                # decision inbox. When EVERY still-pending member is parked on
                # user input, break out now with that state instead of burning
                # the rest of the timeout (nothing moves until the user
                # answers; the lead gets to react — do other work or end the
                # turn and be woken by the eventual member_done).
                still_pending = (target - set(collected.keys())) if target else set()
                if still_pending and slices_waited % _PROBE_EVERY_N_SLICES == 0:
                    probe = await member_probe.probe_pending_members(
                        task_id=task_id, pending_keys=still_pending, user_id=user_id
                    )
                    if (
                        probe
                        and len(probe) == len(still_pending)
                        and all(p.get("state") == "awaiting_user" for p in probe)
                    ):
                        pending_probe = probe
                        awaiting_user_break = True
                        break
                # Nothing to act on. Park on the doorbell for what used to be
                # the blocking read's timeout — same cadence, but a ring now
                # cuts it short instead of being ignored.
                await notifier.wait_for_ring(lead_session_id, slice_timeout)
                continue
            if msg.kind in ("text", "revise_goal"):
                # VALUZ-CHATPLAN S5: user inject via chat, OR a goal revision
                # (both are authoritative user intent). Capture + break so the
                # lead can react in this turn instead of waiting for a member_done
                # that may not arrive for minutes.
                user_inject = {
                    "text": msg.text,
                    "from_session": msg.from_session,
                }
                break
            if msg.kind != "member_done":
                continue
            from_sid = msg.from_session
            async with async_unit_of_work(commit=False) as db:
                run = await TaskSessionDatastore(db).get_run(from_sid)
            sk = run.subtask_key if (run and run.subtask_key) else from_sid
            m = msg.payload or {}
            # Member idle ≠ done: flip the node to in_review for the lead's
            # review_subtask (the actor-loop fallback does the same). ONLY for
            # a delivering member — a failed/cancelled member_done has no work
            # to review; its node is already parked in ``rework`` and flipping
            # it back would present a dead run as a pending deliverable.
            if run and run.subtask_key and str(m.get("status") or "") not in NON_REVIEWABLE_DONE:
                await planning.mark_in_review(
                    task_id=task_id,
                    project_id=project_id,
                    member_session_id=from_sid,
                    user_id=user_id,
                )
            collected[sk] = _member_result(
                run.subtask_key if (run and run.subtask_key) else None,
                from_sid,
                m.get("agent", ""),
                status=m.get("status", ""),
                summary=m.get("summary", ""),
                artifacts=m.get("artifacts", []),
            )

        pending = sorted(target - set(collected.keys())) if target else []
        out: dict[str, Any] = {
            "results": list(collected.values()),
            "pending": pending,
            "collected": len(collected),
            "timed_out": bool(pending) and mode == "all" and not awaiting_user_break,
        }
        if pending:
            # Tell the lead what the pending members are actually DOING — a
            # bare key list left it unable to distinguish "still building" from
            # "dead", which is how leads end up stopping healthy tasks.
            if not pending_probe:
                pending_probe = await member_probe.probe_pending_members(
                    task_id=task_id, pending_keys=set(pending), user_id=user_id
                )
            out["pending_status"] = pending_probe
            if awaiting_user_break:
                out["awaiting_user"] = True
                out["hint"] = (
                    "Every pending member is paused on a question for the USER "
                    "(it appears in the user's decision inbox). Do NOT re-call "
                    "await_members right away — it will return this same state. "
                    "Either work on other ready subtasks (get_plan → dispatch), "
                    "or end your turn: you will be woken with a member_done once "
                    "the member gets its answer and finishes."
                )
            elif any(p.get("state") == "running" for p in pending_probe):
                # Members still running is the COMMON early-return case: mode
                # "any" returns the instant nothing is collected, so ``timed_out``
                # stays False (it requires mode "all"). The old guard gated this
                # hint on ``timed_out`` and therefore NEVER fired for the default
                # mode — which is exactly how a lead, told only a bare
                # ``pending:[k] state:running``, went silent for minutes instead
                # of re-awaiting (the queued member_done then sat unread until the
                # next await). Fire whenever a pending member is alive, regardless
                # of mode / timed_out.
                out["still_running"] = True
                out["hint"] = (
                    "Pending members with state 'running' are ALIVE and still "
                    "working — a long tool call (research, build, tests) easily "
                    "exceeds this wait. Do NOT treat them as dead and do NOT stop "
                    "the task. Call await_members again right away (the wait is a "
                    "fixed window — just loop it, a bigger timeout_s won't help): "
                    "any member that finishes meanwhile is already queued in your "
                    "inbox and returns to you instantly. Do not pause to reason in "
                    "between."
                )
        if user_inject is not None:
            # Surface the inject to the lead so it can decide how to respond
            # (typically: modify_plan + dispatch extra, or send to an in-flight
            # member, or stop a misdirected subtask). The user-instruction
            # wrap ``<user-instruction source="chat">`` already provides
            # framing inside ``text`` for the LLM.
            out["user_inject"] = user_inject
            out["preempted_by_inject"] = True
        return out

    @staticmethod
    async def actor_still_wanted(
        *, session_id: str, role: str, task_id: str, project_id: str, user_id: str
    ) -> bool:
        """Does this actor still have work it is supposed to be doing?

        Stopping an actor is a STATE TRANSITION, not a message. ``stop_task``,
        ``finish_task`` and ``stop_member`` all write durably before anything
        else — a terminal task status, a parked run row — so the loop can just
        look, and looking works from any process.

        It used to be told instead, by a ``shutdown`` queued in its inbox. Two
        different consumers read that inbox (the loop between turns, and
        ``await_member_results`` inside one), so the message had to be put BACK
        by whichever one saw it first or the stop was swallowed. And the box is
        shared across incarnations, so a stop meant for one loop could reach
        the loop that replaced it. Neither problem exists for a fact you read.

        Errs toward KEEP RUNNING: a failed read is not a stop order, and
        halting a healthy actor because the database hiccuped would be a worse
        outcome than one extra turn.
        """
        try:
            async with async_unit_of_work(commit=False) as db:
                if role == "lead":
                    task = await TaskDatastore(db).get_task_by_project(
                        user_id, project_id, task_id
                    )
                    # Absent is not halted: a task read that comes back empty
                    # mid-flight is far more likely to be a scoping mistake
                    # than a deletion, and the idle TTL bounds us anyway.
                    return task is None or task.status == "active"
                run = await TaskSessionDatastore(db).get_run(session_id)
                return run is None or run.status == "active"
        except Exception:  # noqa: BLE001
            logger.debug("could not read stop state for %s — assuming it stands", session_id)
            return True

    async def member_already_settled(
        self, *, task_id: str, project_id: str, member_session_id: str, user_id: str
    ) -> bool:
        """Is there still work here for the lead, or has this member been dealt with?

        Answers from DURABLE state, not from what the mailbox happens to hold:
        a ``member_done`` can be produced by several paths (the member's own
        notify, recovery re-seeding, stop_member) and any of them can repeat.
        The only question worth asking is whether the lead still owes this
        member a turn.

        Settled means either the task itself has ended, or the member's plan
        node has moved past the point where a wake-up would change anything.
        ``in_review`` counts as NOT settled: the lead has yet to review it.
        """
        async with async_unit_of_work(commit=False) as db:
            row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
            if row is None:
                return True  # nothing left to drive
            if row.status != "active":
                return True  # finished / stopped / blocked — a turn changes nothing
            run = (
                await TaskSessionDatastore(db).get_run(member_session_id)
                if member_session_id
                else None
            )
        if run is None or not run.subtask_key:
            return False  # unknown member — let the lead see it rather than swallow it
        node = TaskPlan.from_dict(row.plan).get(run.subtask_key)
        if node is None:
            return False
        return node.status in ("done", "failed")

    async def recover_crashed_members(
        self, *, task_id: str, project_id: str, user_id: str
    ) -> int:
        """Members whose process died before they could record finishing.

        **This is crash recovery, not a delivery backstop — do not delete it.**

        It was written when the mailbox was in-process and a result could be
        posted into a queue nobody read; the durable mailbox
        (``mailbox_store``) took that job over, and it is tempting to conclude
        this became redundant. It did not, and an earlier design note of ours
        said otherwise and was wrong.

        What it covers is a case no delivery mechanism can: the member's loop
        never ran its ``finalize_actor`` at all — pod evicted, process killed,
        unhandled exception — so it left NOTHING behind. There is no fact to
        have enqueued atomically with, because the fact was never written. The
        only witness is the kernel session, which lives outside these tables;
        ``_heartbeat_pending`` reads it, writes the run row and plan node the
        dead member owed, and reports the result the lead is waiting on.

        Without it, a lead waits out its full idle TTL on a member that will
        never speak again.

        Results are ENQUEUED, not returned. Recovery reconstructs facts, and a
        fact goes in the mailbox like any other — so the loop keeps one
        formatting path, one delivery path, and no way to be handed a batch it
        must find somewhere to park. It also brings this in line with the rule
        the rest of the module follows: the enqueue rides the same transaction
        as the run row and plan node it settles, so there is no window where a
        member has been marked finished but nobody has been told.

        Returns how many it recovered, for the log.
        """
        async with async_unit_of_work(commit=False) as db:
            row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
        if row is None:
            return []
        pending = {n.key for n in TaskPlan.from_dict(row.plan).nodes if n.status == "in_progress"}
        if not pending:
            return []
        collected = await member_probe.heartbeat_pending(
            task_id=task_id, project_id=project_id, pending_keys=pending, user_id=user_id
        )
        if not collected:
            return 0
        lead = pick_lead_run(await self._runs(user_id, task_id))
        if lead is None:
            return 0
        async with async_unit_of_work() as db:
            for entry in collected.values():
                await mailbox_store.enqueue(
                    db,
                    session_id=lead.session_id,
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                    kind="member_done",
                    from_session=str(entry.get("session_id") or ""),
                    origin="reconcile",
                    payload=dict(entry),
                )
        await mailbox_store.ring_for(lead.session_id)
        return len(collected)

    @staticmethod
    async def _runs(user_id: str, task_id: str):
        async with async_unit_of_work(commit=False) as db:
            return await TaskSessionDatastore(db).list_runs(user_id, task_id)

    @staticmethod
    async def _drain_durable_inbox(session_id: str) -> list[InboxMsg]:
        """Cross-process messages for this actor. Never raises.

        A failed read must not end the wait: the lead is mid-turn with members
        in flight, and turning a transient DB error into "no results" would
        have it conclude the members produced nothing.
        """
        try:
            # ONE. The caller uses ``durable[0]`` and has nowhere to put the
            # rest — claiming a batch would mark them consumed and then drop
            # them on the floor. (It did, briefly: two member results arrived,
            # one was collected.)
            return await mailbox_store.drain(session_id, limit=1)
        except Exception:  # noqa: BLE001
            logger.debug("durable inbox read failed for %s, still waiting", session_id)
            return []

    async def notify_lead_member_idle(self, session_id: str, status: str, user_id: str) -> None:
        """After a member turn: ``member_done`` to the lead's inbox + a
        ``subtask_reported`` timeline event. Best-effort — a missing lead inbox
        (lead already finished) drops the mailbox message.

        (Pre-2026-07 rows carry this as ``subtask_message`` with a
        ``payload.direction``; the append-only log is never rewritten.)
        """

        async with async_unit_of_work(commit=False) as db:
            run = await TaskSessionDatastore(db).get_run(session_id)
            if run is None:
                return
            lead_session_id = run.dispatched_by or ""
            project_id = run.project_id
            task_id = run.task_id or ""
            agent_slug = run.agent_slug or ""
            run_dir = Path(run.run_dir) if run.run_dir else Path()

        since = self._members.dispatch_started_at(session_id)
        manifest = await collect_manifest_safe(
            session_id,
            run_dir,
            status,
            agent_slug=agent_slug,
            since_epoch=since,
            user_id=user_id,
        )

        # DELIVER FIRST — this is the load-bearing leg, and the timeline write
        # below used to run ahead of it inside one unit of work. When that write
        # raised (WAL contention exhausting the commit retry, a display-name
        # lookup failing) the loop unwound into its ``finally``, which settles
        # the run row to ``completed`` — and both lead-side backstops
        # (``_heartbeat_pending`` / ``_probe_pending_members``) filter on
        # ``status == "active"``, so the member read as never-dispatched. The
        # lead then burned its whole await window and eventually parked the task.
        # It gets its OWN unit of work rather than riding the timeline write
        # below for exactly that reason. It is not yet joined to the write that
        # settles this run either — that settle happens in the loop's
        # ``finally``, and pairing the two is what finally lets
        # ``recover_crashed_members`` retire (see R5 in
        # docs/design/task-delivery-and-control.md). Until then this leg is
        # durable but not atomic with the fact it reports.
        if lead_session_id:
            async with async_unit_of_work() as db:
                await mailbox_store.enqueue(
                    db,
                    session_id=lead_session_id,
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                    kind="member_done",
                    from_session=session_id,
                    origin="member-idle",
                    payload=dict(manifest),
                )
            await mailbox_store.ring_for(lead_session_id)

        # Timeline bookkeeping — on its own unit of work so a failure here costs
        # a row in the log, never the delivery above.
        try:
            async with async_unit_of_work() as db:
                # Stamp the display name at emit time (established rule): the
                # frontend renders ``payload.agent_name`` directly instead of
                # joining the slug against an async members list, which races
                # the load and misses agents removed since.
                agent_name = await resolve_agent_display_name(project_id, agent_slug, user_id)
                await TaskEventDatastore(db).append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type="subtask_reported",
                    actor=agent_slug,
                    session_id=session_id,
                    payload={
                        "agent_name": agent_name,
                        "summary": manifest.get("summary", ""),
                        "status": status,
                    },
                )
        except Exception:  # noqa: BLE001 — never let the timeline cost the report
            logger.exception(
                "subtask_reported timeline write failed for %s (member_done was delivered)",
                session_id,
            )

    async def lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str, lead_session_id: str = ""
    ) -> bool:
        """True when a lead has nothing left to wait for after a turn.

        The actor loop normally parks on the mailbox for LEAD_IDLE_TTL_S between
        turns to catch ``member_done`` / follow-ups. But a lead only has a reason
        to wait if it has a member in flight, BACKGROUND WORK of its own still
        running, or an unresolved plan node still to drive
        (``TaskPlan.unresolved_keys`` — the shared predicate, ``paused``
        included). When none holds, the lead is done — break now so
        ``finalize_actor`` closes the task immediately instead of after 30min.
        """
        if self._members.has_live_members(task_id):
            return False  # a member is still running — keep waiting for its result
        if lead_session_id and await self._session_has_background_work(lead_session_id):
            # The lead spawned a ``run_in_background`` subagent. Its own turn
            # genuinely ended, but the work has NOT — and when the task finishes
            # the CLI wakes the session with the result. Finalising here would
            # close the task out from under work that is still running.
            return False
        async with async_unit_of_work(commit=False) as db:
            task = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status != "active":
                return True  # already closed (finish_task/stop) — let the loop end
            try:
                plan = TaskPlan.from_dict(task.plan)
            except PlanError:
                return True
            return not plan.unresolved_keys()

    async def session_still_working(self, session_id: str) -> bool:
        """ActorCoordinator: is this session doing work the loop cannot see?

        Today that means a live ``run_in_background`` task. The actor loop asks
        this before treating an idle-TTL expiry as "this actor is done".
        """
        return await self._session_has_background_work(session_id)

    @staticmethod
    async def _session_has_background_work(session_id: str) -> bool:
        """Is this session running a ``run_in_background`` task right now?

        A background task outlives the turn that launched it and the CLI
        drives the follow-up turns — the loop sees none of it. Same signal as
        the conversation header (``bg_busy_session_ids``), so surfaces agree.
        Best-effort: a failed lookup reports False rather than pin the loop.
        """
        try:
            return session_id in set(await kernel_client.bg_busy_session_ids())
        except Exception:  # noqa: BLE001
            logger.debug("bg-busy probe failed for %s", session_id, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # shutdown broadcast — the atomic shutdown primitive
    # ------------------------------------------------------------------

    def stop_tracking_members(self, task_id: str) -> None:
        """Forget a task's members; each one reads its own stop from the store.

        This was ``stop_tracking_members``, and it queued a ``shutdown`` for every
        member. It could only reach members whose loops happened to live in
        this process, which is a minority of them once the host runs more than
        one — so the signal that ends a member was the one thing least able to
        cross a process boundary.

        Its callers (``stop_task``, ``finish_task``) already write what each
        member needs to know: the task goes terminal, the run rows are parked.
        Members read that on their own poll, from wherever they run. All that
        is left here is dropping the lead's in-process tracking of them.

        MUST stay a plain ``def``: ``drain_members`` pops the set, and letting
        an ``await`` in would drop a concurrently spawned member (pinned by
        test_spawn_atomicity).
        """
        self._members.drain_members(task_id)


__all__ = ["CoordinationService"]
