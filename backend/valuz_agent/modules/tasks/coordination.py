"""CoordinationService — lead ↔ member coordination (ADR-023, Step 3b).

Peeled verbatim out of ``TaskOrchestrator``. Owns the in-turn / between-turn
coordination surface:

  * :meth:`await_member_results` — in-turn mailbox drain (8s heartbeat slices,
    user_inject preemption).
  * :meth:`_heartbeat_pending` — bad-case#3 backstop (reconcile a member whose
    kernel session went terminal but whose member_done never reached the lead).
  * :meth:`_notify_lead_member_idle` — the role=="subtask" run-actor-loop
    callback: post a ``member_done`` to the lead's inbox after a member turn.
  * :meth:`_lead_idle_with_no_pending` — the role=="lead" run-actor-loop check:
    True when the lead has nothing left to wait for.
  * :meth:`_broadcast_shutdown` — the atomic shutdown primitive (single
    ``drain_members`` pop → per-member shutdown put).

Holds no task state — it receives the shared :class:`LiveMemberRegistry` by
constructor injection (the same instance the composition root wires into every
other task service) for ``has_live_members`` / ``dispatch_started_at`` /
``drain_members``.

ADR folds ``messaging.py`` into coordination: the lead↔member / chat→task text
delivery methods (:meth:`send_to_member` / :meth:`inject_into_task` /
:meth:`notify_lead_goal_revised`) are surfaced here by delegating into the
stateless ``messaging`` module, which stays importable for the dispatch-MCP
handlers + task routes that call it directly.

CRITICAL invariant (``_broadcast_shutdown``): the drain + per-member shutdown
``put`` loop must stay SYNCHRONOUS and contiguous — no ``await`` may separate the
single atomic ``registry.drain_members`` pop from the shutdown puts, or a member
spawned concurrently by ``dispatch_async`` could be dropped.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from valuz_agent.adapters import kernel_store
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import messaging, planning
from valuz_agent.modules.tasks.actor_runner import collect_manifest
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan

logger = logging.getLogger(__name__)

# Heartbeat slice for await_member_results: how often the lead reconciles
# in-flight members against their kernel session while waiting (VALUZ-RESUME §5.4).
_HEARTBEAT_S = 8.0


class CoordinationService:
    """Lead ↔ member coordination + chat→task / lead→member text delivery.

    Constructed once at the composition root with the shared registry; the
    orchestrator's coordination surface delegates straight onto it, and the
    ActorRunner resolves its role callbacks (``_notify_lead_member_idle`` /
    ``_lead_idle_with_no_pending``) through the bound host onto this service.
    """

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
        from valuz_agent.modules.tasks.mailbox import mailbox_registry

        # Ensure the lead inbox exists so ``get`` blocks for member_done
        # instead of raising KeyError (which would return empty instantly and
        # make the lead think members are stuck). ``dispatch`` already
        # registers it; this is belt-and-suspenders. Idempotent.
        mailbox_registry.register(lead_session_id)

        # Resolve the target set from the plan when keys are not given.
        if keys:
            target: set[str] = {k for k in keys if k}
        else:
            async with async_unit_of_work(commit=False) as db:
                row = await TaskDatastore(db).get_task_by_project(project_id, task_id)
                plan = TaskPlan.from_dict(row.plan) if row else TaskPlan()
            target = {n.key for n in plan.nodes if n.status in ("in_progress", "in_review")}

        loop = asyncio.get_running_loop()
        # Default cap so a member that dies without a member_done can't hang
        # the lead indefinitely (the actor loop posts member_done even on
        # terminal status, so this is a backstop, not the common path).
        effective_timeout = timeout_s if timeout_s is not None else 600.0
        deadline = loop.time() + effective_timeout
        collected: dict[str, dict[str, Any]] = {}
        # VALUZ-CHATPLAN S5: if a user-injected ``message`` arrives in the
        # lead mailbox while we wait, BREAK OUT immediately with whatever has
        # been collected so far + the injection — the lead needs to react
        # (often by ``modify_plan``/``dispatch``-ing extra work) before
        # continuing to wait. Was previously silently dropped (``continue``),
        # which delayed inject by up to ``timeout_s``.
        user_inject: dict[str, Any] | None = None

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
                pending_now = (target - set(collected.keys())) if target else set()
                collected.update(
                    await self._heartbeat_pending(
                        task_id=task_id,
                        project_id=project_id,
                        pending_keys=pending_now,
                    )
                )
                continue
            except KeyError:
                break
            if msg.kind == "shutdown":
                break
            if msg.kind in ("message", "revise_goal"):
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
            # Member idle ≠ done: flip the node to in_review for the lead's
            # review_subtask (the actor-loop fallback does the same).
            if run and run.subtask_key:
                await planning.mark_in_review(
                    task_id=task_id,
                    project_id=project_id,
                    member_session_id=from_sid,
                )
            m = msg.payload or {}
            collected[sk] = {
                "subtask_key": run.subtask_key if (run and run.subtask_key) else None,
                "session_id": from_sid,
                "agent": m.get("agent", ""),
                "status": m.get("status", ""),
                "summary": m.get("summary", ""),
                "artifacts": m.get("artifacts", []),
            }

        pending = sorted(target - set(collected.keys())) if target else []
        out: dict[str, Any] = {
            "results": list(collected.values()),
            "pending": pending,
            "collected": len(collected),
            "timed_out": bool(pending) and mode == "all",
        }
        if user_inject is not None:
            # Surface the inject to the lead so it can decide how to respond
            # (typically: modify_plan + dispatch extra, or send to an in-flight
            # member, or stop a misdirected subtask). The user-instruction
            # wrap ``<user-instruction source="chat">`` already provides
            # framing inside ``text`` for the LLM.
            out["user_inject"] = user_inject
            out["preempted_by_inject"] = True
        return out

    async def _heartbeat_pending(
        self,
        *,
        task_id: str,
        project_id: str,
        pending_keys: set[str],
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
        from valuz_agent.modules.tasks.recovery import classify_member

        out: dict[str, dict[str, Any]] = {}
        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            runs_by_key = {
                r.subtask_key: r
                for r in await run_ds.list_runs(task_id)
                if r.kind == "subtask" and r.subtask_key and r.status == "active"
            }
            if not any(k in runs_by_key for k in pending_keys):
                return {}  # nothing in-flight for these keys — don't touch the plan
            task = await task_ds.get_task_by_project(project_id, task_id)
            plan = TaskPlan.from_dict(task.plan) if task is not None else None
            plan_dirty = False
            for key in pending_keys:
                run = runs_by_key.get(key)
                if run is None:
                    continue
                ks = await kernel_store.load_session(run.session_id)
                if getattr(ks, "status", None) == "running":
                    continue  # genuinely in flight — keep waiting
                disp = classify_member(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                )
                node = plan.get(key) if plan is not None else None
                if disp == "completed":
                    try:
                        manifest = collect_manifest(
                            run.session_id,
                            Path(run.run_dir) if run.run_dir else Path(),
                            "idle",
                        )
                    except Exception:  # noqa: BLE001
                        manifest = {
                            "session_id": run.session_id,
                            "status": "completed",
                            "summary": "",
                        }
                    manifest["agent"] = run.agent_slug
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status="completed", result_manifest=manifest
                    )
                    if node is not None and node.status in ("in_progress", "rework"):
                        plan.update_node(key, status="in_review")  # type: ignore[union-attr]
                        plan_dirty = True
                    out[key] = {
                        "subtask_key": key,
                        "session_id": run.session_id,
                        "agent": run.agent_slug,
                        "status": manifest.get("status", "completed"),
                        "summary": manifest.get("summary", ""),
                        "artifacts": manifest.get("artifacts", []),
                    }
                elif disp == "failed":
                    await run_ds.update_run_by_session(session_id=run.session_id, status="archived")
                    if node is not None:
                        plan.update_node(  # type: ignore[union-attr]
                            key,
                            status="rework",
                            review_feedback="member session errored (heartbeat)",
                        )
                        plan_dirty = True
                    out[key] = {
                        "subtask_key": key,
                        "session_id": run.session_id,
                        "agent": run.agent_slug,
                        "status": "failed",
                        "summary": "member session errored",
                        "artifacts": [],
                    }
            if plan_dirty and plan is not None and task is not None:
                task.plan = plan.to_dict()
                await task_ds.update_task(task)
                await planning.emit_plan_update(
                    event_ds,
                    project_id=project_id,
                    task_id=task_id,
                    plan=plan,
                    actor="system",
                    session_id=None,
                )
        return out

    # ------------------------------------------------------------------
    # actor-loop role callbacks (driven by ActorRunner via the bound host)
    # ------------------------------------------------------------------

    async def _notify_lead_member_idle(self, session_id: str, status: str) -> None:
        """After a member turn, push a member_done message to its lead's inbox.

        Also appends a ``subtask_message`` task event so the timeline shows the
        member→lead notification. Best-effort — a missing lead inbox (lead
        already finished) just means the message is dropped.
        """
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            run = await run_ds.get_run(session_id)
            if run is None:
                return
            lead_session_id = run.dispatched_by or ""
            run_dir = Path(run.run_dir) if run.run_dir else Path()
            since = self._members.dispatch_started_at(session_id)
            manifest = collect_manifest(session_id, run_dir, status, since_epoch=since)
            manifest["agent"] = run.agent_slug
            await event_ds.append_event(
                project_id=run.project_id,
                task_id=run.task_id or "",
                type="subtask_message",
                actor=run.agent_slug,
                session_id=session_id,
                payload={
                    "direction": "member->lead",
                    "summary": manifest.get("summary", ""),
                    "status": status,
                },
            )

        # Mailbox delivery on the event loop (asyncio.Queue is not thread-safe).
        if lead_session_id:
            mailbox_registry.put(
                lead_session_id,
                InboxMsg(
                    kind="member_done",
                    from_session=session_id,
                    payload=manifest,
                ),
            )

    async def _lead_idle_with_no_pending(self, task_id: str, project_id: str) -> bool:
        """True when a lead has nothing left to wait for after a turn.

        The actor loop normally parks on the mailbox for LEAD_IDLE_TTL_S between
        turns to catch ``member_done`` / follow-ups. But a lead only has a reason
        to wait if it has a member in flight OR an unresolved plan node still to
        drive. When neither holds, the lead is done — break now so
        ``_finalize_actor`` closes the task immediately instead of after 30min.
        """
        if self._members.has_live_members(task_id):
            return False  # a member is still running — keep waiting for its result
        async with async_unit_of_work(commit=False) as db:
            task = await TaskDatastore(db).get_task_by_project(project_id, task_id)
            if task is None or task.status != "active":
                return True  # already closed (finish_task/stop) — let the loop end
            try:
                plan = TaskPlan.from_dict(task.plan)
            except PlanError:
                return True
            unresolved = any(
                n.status in ("planned", "in_progress", "in_review", "rework") for n in plan.nodes
            )
            return not unresolved

    # ------------------------------------------------------------------
    # shutdown broadcast — the atomic shutdown primitive
    # ------------------------------------------------------------------

    def _broadcast_shutdown(self, task_id: str) -> None:
        """Tell every still-running member of a task to finalize after its turn."""
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        for member_sid in self._members.drain_members(task_id):
            mailbox_registry.put(member_sid, InboxMsg(kind="shutdown"))

    # ------------------------------------------------------------------
    # text delivery (folds messaging.py — delegates to the stateless module)
    # ------------------------------------------------------------------

    async def send_to_member(
        self,
        *,
        from_session_id: str,
        to_session_id: str,
        text: str,
        project_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """Deliver a free-text follow-up from the lead to a running member.

        Delegates to the stateless ``messaging`` module (kept importable for the
        dispatch-MCP handlers + task routes that call it directly).
        """
        return await messaging.send_to_member(
            from_session_id=from_session_id,
            to_session_id=to_session_id,
            text=text,
            project_id=project_id,
            task_id=task_id,
        )

    async def inject_into_task(
        self,
        *,
        task_id: str,
        project_id: str,
        text: str,
        from_session_id: str,
    ) -> dict[str, Any]:
        """Inject a free-text instruction from a chat session into a running task's lead.

        Delegates to the stateless ``messaging`` module (kept importable for the
        dispatch-MCP handlers + task routes that call it directly).
        """
        return await messaging.inject_into_task(
            task_id=task_id,
            project_id=project_id,
            text=text,
            from_session_id=from_session_id,
        )

    async def notify_lead_goal_revised(
        self,
        *,
        task_id: str,
        project_id: str,
        new_goal: str,
    ) -> dict[str, Any]:
        """Wake a running task's lead after the user revised ``task.goal``.

        Delegates to the stateless ``messaging`` module (kept importable for the
        dispatch-MCP handlers + task routes that call it directly).
        """
        return await messaging.notify_lead_goal_revised(
            task_id=task_id,
            project_id=project_id,
            new_goal=new_goal,
        )


__all__ = ["CoordinationService"]
