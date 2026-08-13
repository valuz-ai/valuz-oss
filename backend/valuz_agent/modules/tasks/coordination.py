"""CoordinationService — lead ↔ member coordination.

await_member_results (in-turn mailbox drain, heartbeat-sliced) · the
role callbacks the ActorRunner binds as its ``ActorCoordinator``
(notify_lead_member_idle / lead_idle_with_no_pending / session_still_working)
· broadcast_shutdown, the atomic halt primitive.

Text DELIVERY (send_to_member / inject_into_task / goal revision) lives in
``messaging`` — callers import it directly; there is no wrapper here.

CRITICAL invariant: ``broadcast_shutdown`` must stay a plain ``def`` — the
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
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.manifest import collect_manifest_safe
from valuz_agent.modules.tasks.task_state import NON_REVIEWABLE_DONE
from valuz_agent.modules.tasks.events import record_subtask_failed
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks import mailbox_store
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.tasks.member_state import classify_member
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


def _member_result(
    subtask_key: str | None,
    session_id: str,
    agent: str | None,
    *,
    status: str,
    summary: str = "",
    artifacts: list[Any] | None = None,
) -> dict[str, Any]:
    """The member-result entry shape await_members / heartbeat hand the lead —
    one spelling, three producers."""
    return {
        "subtask_key": subtask_key,
        "session_id": session_id,
        "agent": agent or "",
        "status": status,
        "summary": summary,
        "artifacts": artifacts or [],
    }


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
        mailbox_registry.register(lead_session_id)

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
            try:
                msg = await mailbox_registry.get(lead_session_id, timeout=slice_timeout)
            except TimeoutError:
                # Nothing in THIS process's queue — but a user instruction, a
                # goal revision or a member's report written by another process
                # lives in the durable inbox, and this wait is exactly where it
                # matters: the lead is inside a turn, parked on members that may
                # run for minutes, and the whole point of the preempt below is
                # to let it react now instead of then.
                #
                # Feeding them back through the registry rather than handling
                # them here is deliberate: the branches below (shutdown, inject,
                # revision, member_done) then apply unchanged, so this path
                # cannot drift from the in-process one.
                durable = await self._drain_durable_inbox(lead_session_id)
                if durable:
                    for extra in durable:
                        mailbox_registry.put(lead_session_id, extra)
                    continue
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
                        await self._heartbeat_pending(
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
                    probe = await self._probe_pending_members(
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
                continue
            except KeyError:
                break
            if msg.kind == "shutdown":
                # Put it BACK before leaving. ``await_member_results`` runs
                # inside the lead's turn and drains the same inbox the actor
                # loop reads between turns, so consuming a shutdown here would
                # swallow the only signal that tells the loop to stop — the
                # lead would finish this turn and keep looping on a task that
                # ``stop_task`` / ``finish_task`` already halted. (It survived
                # this long only because ``stop_task`` ALSO interrupts the
                # kernel turn, which happens to end the loop by another route;
                # ``finish_task``'s own broadcast had no such backstop.)
                mailbox_registry.put(lead_session_id, msg)
                break
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
                pending_probe = await self._probe_pending_members(
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

    async def reconcile_finished_members(
        self, *, task_id: str, project_id: str, user_id: str
    ) -> list[InboxMsg]:
        """Members that finished but whose ``member_done`` never arrived here.

        Same backstop as ``_heartbeat_pending``, exposed for the BETWEEN-turns
        wait. Inside a turn the lead already re-reads durable state every few
        seconds; parked on its mailbox it used to read nothing at all, so a
        result posted into another process's queue was simply never seen.

        Returns ready-to-consume ``member_done`` messages, so the loop keeps one
        formatting path whether a result arrived by mailbox or by reconcile.
        """
        async with async_unit_of_work(commit=False) as db:
            row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
        if row is None:
            return []
        pending = {n.key for n in TaskPlan.from_dict(row.plan).nodes if n.status == "in_progress"}
        if not pending:
            return []
        collected = await self._heartbeat_pending(
            task_id=task_id, project_id=project_id, pending_keys=pending, user_id=user_id
        )
        return [
            InboxMsg(
                kind="member_done",
                from_session=str(entry.get("session_id") or ""),
                origin="reconcile",
                payload=entry,
            )
            for entry in collected.values()
        ]

    @staticmethod
    async def _drain_durable_inbox(session_id: str) -> list[InboxMsg]:
        """Cross-process messages for this actor. Never raises.

        A failed read must not end the wait: the lead is mid-turn with members
        in flight, and turning a transient DB error into "no results" would
        have it conclude the members produced nothing.
        """
        try:
            return await mailbox_store.drain(session_id)
        except Exception:  # noqa: BLE001
            logger.debug("durable inbox read failed for %s, still waiting", session_id)
            return []

    async def _heartbeat_pending(
        self,
        *,
        task_id: str,
        project_id: str,
        pending_keys: set[str],
        user_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Backstop for bad-case #3 (VALUZ-RESUME §5.4): a member whose kernel
        session went terminal but whose ``member_done`` never reached the lead's
        mailbox (delivery window / crash before finalize).

        For each still-pending subtask key, check the kernel session; if terminal
        (end_turn → completed, error → failed) persist the run/node disposition
        and return a synthesized collection entry so the lead's wait completes.
        ``running``/resumable members are left pending (resume is a restart
        concern, not an online-wait one).
        """
        if not pending_keys:
            return {}
        out: dict[str, dict[str, Any]] = {}
        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            runs_by_key = {
                r.subtask_key: r
                for r in await run_ds.list_runs(user_id, task_id)
                if r.kind == "subtask" and r.subtask_key and r.status == "active"
            }
            if not any(k in runs_by_key for k in pending_keys):
                return {}  # nothing in-flight for these keys — don't touch the plan
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            # Node mutations are recorded as (key, fields, only_from) and
            # applied inside persist_plan's CAS closure against the fresh plan.
            mutations: list[tuple[str, dict[str, Any], tuple[str, ...] | None]] = []
            for key in pending_keys:
                run = runs_by_key.get(key)
                if run is None:
                    continue
                ks = await data_reader().get_session(user_id, run.session_id)
                if getattr(ks, "status", None) == "running":
                    continue  # genuinely in flight — keep waiting
                disp = classify_member(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                )
                if disp == "completed":
                    manifest = await collect_manifest_safe(
                        run.session_id,
                        Path(run.run_dir) if run.run_dir else Path(),
                        "idle",
                        agent_slug=run.agent_slug or "",
                        user_id=user_id,
                    )
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status="completed", result_manifest=manifest
                    )
                    mutations.append((key, {"status": "in_review"}, ("in_progress", "rework")))
                    out[key] = _member_result(
                        key,
                        run.session_id,
                        run.agent_slug,
                        status=manifest.get("status", "completed"),
                        summary=manifest.get("summary", ""),
                        artifacts=manifest.get("artifacts", []),
                    )
                elif disp == "failed":
                    await run_ds.update_run_by_session(session_id=run.session_id, status="archived")
                    mutations.append(
                        (
                            key,
                            {
                                "status": "rework",
                                "review_feedback": "member session errored (heartbeat)",
                            },
                            ("in_progress", "in_review", "rework", "paused"),
                        )
                    )
                    # Same emitter as every other failure path — without it a
                    # heartbeat-detected failure reworked the node invisibly.
                    agent_name = await resolve_agent_display_name(
                        project_id, run.agent_slug or "", user_id
                    )
                    await record_subtask_failed(
                        event_ds,
                        user_id=user_id,
                        project_id=project_id,
                        task_id=task_id,
                        session_id=run.session_id,
                        agent_slug=run.agent_slug or "",
                        agent_name=agent_name,
                        subtask_key=key,
                        summary="member session errored",
                        reason="heartbeat_detected",
                    )
                    out[key] = _member_result(
                        key,
                        run.session_id,
                        run.agent_slug,
                        status="failed",
                        summary="member session errored",
                    )
            if mutations and task is not None:

                def _apply(p: TaskPlan) -> bool:
                    changed = False
                    for key, fields, only_from in mutations:
                        n = p.get(key)
                        if n is None or (only_from and n.status not in only_from):
                            continue
                        p.update_node(key, **fields)
                        changed = True
                    return changed

                await planning.persist_plan_best_effort(
                    task_ds,
                    event_ds,
                    task,
                    mutate=_apply,
                    actor="system",
                    session_id=None,
                    user_id=user_id,
                    diverges="probed member outcomes not reflected on their nodes "
                    f"({', '.join(k for k, _, _ in mutations)})",
                )
        return out

    async def _probe_pending_members(
        self,
        *,
        task_id: str,
        pending_keys: set[str],
        user_id: str,
    ) -> list[dict[str, Any]]:
        """READ-ONLY live status of still-pending members: ``awaiting_user``
        (parked on an AskUserQuestion — nothing moves until the user answers),
        ``running``, or the kernel status as-is. Never touches plan or runs —
        that is ``_heartbeat_pending``'s job.
        """
        if not pending_keys:
            return []
        try:
            async with async_unit_of_work(commit=False) as db:
                runs_by_key = {
                    r.subtask_key: r
                    for r in await TaskSessionDatastore(db).list_runs(user_id, task_id)
                    if r.kind == "subtask" and r.subtask_key and r.status == "active"
                }
        except Exception:  # noqa: BLE001
            logger.debug("probe_pending_members: run listing failed", exc_info=True)
            return []
        asks = await self._pending_asks_by_session(user_id)
        out: list[dict[str, Any]] = []
        for key in sorted(pending_keys):
            run = runs_by_key.get(key)
            if run is None:
                continue
            kernel_status: str | None = None
            try:
                ks = await data_reader().get_session(user_id, run.session_id)
                kernel_status = getattr(ks, "status", None) if ks is not None else None
            except Exception:  # noqa: BLE001
                logger.debug(
                    "probe_pending_members: session read failed for %s",
                    run.session_id,
                    exc_info=True,
                )
            question = asks.get(run.session_id)
            entry: dict[str, Any] = {
                "subtask_key": key,
                "session_id": run.session_id,
                "agent": getattr(run, "agent_slug", "") or "",
                "state": "awaiting_user" if question is not None else (kernel_status or "unknown"),
            }
            if question:
                entry["question"] = question
            out.append(entry)
        return out

    @staticmethod
    async def _pending_asks_by_session(user_id: str | None) -> dict[str, str]:
        """Map session_id → first pending clarifying-question text, from the
        decision inbox. Best-effort: an unwired aggregator (tests, early boot)
        just means no ask detection, never a failed await."""
        try:
            # Lazy import — decisions is a sibling MODULE (its service API, not
            # its datastore), so this is a sanctioned cross-module call; the
            # import stays lazy only to keep the boot import graph flat.
            # Best-effort by design: an unwired aggregator raises, and this
            # method must degrade to "no ask detected", never fail the await.
            from valuz_agent.modules.decisions.aggregator import get_decision_aggregator

            entries = await get_decision_aggregator().snapshot(user_id or "")
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for e in entries:
            if e.session_id in out:
                continue
            questions = (e.question_payload or {}).get("questions") or []
            first = questions[0] if questions else {}
            text = str(first.get("question") or "").strip() if isinstance(first, dict) else ""
            out[e.session_id] = text[:200] or "(question pending)"
        return out

    # ------------------------------------------------------------------
    # actor-loop role callbacks (driven by ActorRunner via the bound host)
    # ------------------------------------------------------------------

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
        # ``reconcile_finished_members`` retire (see R5 in
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

    def broadcast_shutdown(self, task_id: str) -> None:
        """Tell every still-running member of a task to finalize after its turn.

        Public on purpose — finalization and recovery are its callers, and a
        load-bearing cross-service contract must not hide behind a private
        name. MUST stay a plain ``def``: the single ``drain_members`` pop and
        the per-member puts may not be separated by an ``await``, or a
        concurrently spawned member is dropped (compiler-enforced; pinned by
        test_spawn_atomicity).
        """

        for member_sid in self._members.drain_members(task_id):
            mailbox_registry.put(member_sid, InboxMsg(kind="shutdown"))


__all__ = ["CoordinationService"]
