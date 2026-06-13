"""Task stop/resume reconciliation core (VALUZ-RESUME, S1).

The durable source of truth for a subtask's liveness/completion is the kernel
session state (status + stop_reason) + the host run/plan rows — NOT the
in-memory mailbox (which dies on app restart). These pure functions map a
member's real kernel-session state to a host-side disposition; the
:class:`RecoveryService` (S2/S4) applies the side effects (DB writes, respawn
actor loop, mailbox).

The :class:`RecoveryService` (ADR-023 Step 3d) was peeled verbatim out of
``TaskOrchestrator``: startup Layer-1 sweep (``recover_active_tasks`` /
``_recover_one_task``) plus the user-initiated Layer-2 stop/resume surface
(``stop_task`` / ``resume_task`` / ``stop_member`` + ``_interrupt_kernel_session``).
It re-populates the shared :class:`LiveMemberRegistry` (no dispatch epoch on the
recovery branch — the Step-1 invariant) before respawning each resumable
member's actor loop, mirroring ``dispatch_async``'s add-member-before-spawn
ordering. The pure ``reconcile`` / ``classify_member`` functions below stay
module-level domain code, imported by both the service and
``CoordinationService._heartbeat_pending``.

See docs/exec-plans/completed/task-stop-resume.md.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from valuz_agent.infra.auth_context import (
    require_current_user_id,
    reset_current_user_id,
    set_current_user_id,
)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import ActorRunner, collect_manifest
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.plan import TaskPlan

logger = logging.getLogger(__name__)

# resume     — re-run the member (kernel run_turn on the persisted session);
#              covers "created but never ran" and "interrupted by host_restart".
# completed  — member reached a normal terminal turn (end_turn); collect + review.
# failed     — member errored terminally (non-restart), or resume retry exhausted.
# in_flight  — member is genuinely still running; leave it to the heartbeat/mailbox.
Disposition = Literal["resume", "completed", "failed", "in_flight"]

# Resume retry cap (VALUZ-RESUME §5.0): a member node may be resumed at most this
# many times before we give up and hand it back to the lead as rework.
RESUME_RETRY_CAP = 3


def _stop_reason_dict(stop_reason: Any) -> dict[str, Any]:
    """Normalise a kernel ``stop_reason`` (dict or Error-like object) to a dict."""
    if not stop_reason:
        return {}
    if isinstance(stop_reason, dict):
        return stop_reason
    return {
        "type": getattr(stop_reason, "type", None),
        "category": getattr(stop_reason, "category", None),
        "message": getattr(stop_reason, "message", None),
    }


def classify_member(status: str | None, stop_reason: Any) -> Disposition:
    """Classify a member subtask from its kernel session state.

    ``status``/``stop_reason`` are the kernel ``Session`` fields (status is None
    when the session row is missing entirely).
    """
    if status is None or status == "created":
        return "resume"  # built but never ran (app stopped before the first turn)
    if status == "running":
        return "in_flight"  # genuinely active — don't touch
    sr = _stop_reason_dict(stop_reason)
    typ = sr.get("type")
    if typ == "end_turn":
        return "completed"  # normal terminal turn
    if typ == "error":
        # host_restart = interrupted mid-flight by a crash → resumable;
        # any other error = a real execution failure.
        return "resume" if sr.get("category") == "host_restart" else "failed"
    # idle with no / unknown stop_reason → conservatively resumable.
    return "resume"


@dataclass(frozen=True)
class MemberReconcile:
    """The host-side plan for one member run, derived purely from its state.

    The orchestrator applies it: write ``run_status`` to the
    ``valuz_task_session`` row and ``node_status`` to the plan node; if
    ``resume`` respawn the member actor loop (kernel run_turn); if
    ``deliver_member_done`` put a member_done into the lead's mailbox.
    """

    disposition: Disposition
    run_status: str | None  # new valuz_task_session.status (None = leave as-is)
    node_status: str | None  # new plan-node status (None = leave as-is)
    resume: bool  # caller should respawn the member actor loop
    deliver_member_done: bool  # caller should notify the lead via mailbox
    reason: str = ""


def reconcile(
    status: str | None,
    stop_reason: Any,
    *,
    node_attempts: int,
    retry_cap: int = RESUME_RETRY_CAP,
) -> MemberReconcile:
    """Map a member's kernel state + retry count to a concrete disposition.

    Pure — no I/O. ``node_attempts`` is the plan node's ``attempts`` (resume
    count); once it reaches ``retry_cap`` a would-be resume becomes a failure
    so a broken member can't be respawned forever.
    """
    disp = classify_member(status, stop_reason)
    if disp == "in_flight":
        return MemberReconcile("in_flight", None, None, False, False)
    if disp == "completed":
        return MemberReconcile("completed", "completed", "in_review", False, True)
    if disp == "failed":
        msg = _stop_reason_dict(stop_reason).get("message") or "member session errored"
        return MemberReconcile("failed", "archived", "rework", False, False, reason=str(msg))
    # disp == "resume"
    if node_attempts >= retry_cap:
        return MemberReconcile(
            "failed",
            "archived",
            "rework",
            False,
            False,
            reason=f"resume retry cap ({retry_cap}) exhausted",
        )
    return MemberReconcile("resume", "active", "in_progress", True, False)


# ---------------------------------------------------------------------------
# RecoveryService (ADR-023 Step 3d)
# ---------------------------------------------------------------------------


class RecoveryService:
    """Startup recovery + user-initiated stop/resume — peeled verbatim out of
    ``TaskOrchestrator``.

    Owns:

      * :meth:`recover_active_tasks` — Layer-1 startup sweep over
        ``TaskDatastore.list_active``.
      * :meth:`_recover_one_task` — reconcile one task's members + re-drive its
        lead (shared by Layer-1 startup and Layer-2 ``resume_task``). The
        registry re-population keystone: each resumable member is seeded via
        ``registry.add_member`` (NO dispatch epoch — recovery branch) BEFORE
        its actor loop respawns, mirroring ``dispatch_async``'s invariant.
      * :meth:`stop_task` — Layer-2 cascade interrupt + shutdown broadcast →
        ``paused``.
      * :meth:`resume_task` — Layer-2 flip back to ``active`` then
        ``_recover_one_task``.
      * :meth:`stop_member` — Layer-2 single-member stop.
      * :meth:`_interrupt_kernel_session` — best-effort kernel turn interrupt.

    Constructed once at the composition root with the shared registry +
    runtime ActorRunner + CoordinationService; the orchestrator's recovery
    surface delegates straight onto it.
    """

    def __init__(
        self,
        *,
        registry: LiveMemberRegistry,
        actor_runner: ActorRunner,
        coordination: CoordinationService,
    ) -> None:
        self._members = registry
        self._actor = actor_runner
        self._coordination = coordination

    # ------------------------------------------------------------------
    # Layer 1 (VALUZ-RESUME §5.3): startup recovery
    # ------------------------------------------------------------------

    async def recover_active_tasks(self) -> int:
        """Layer 1 (VALUZ-RESUME §5.3): on host startup, reconcile + resume every
        ``active`` task whose actor loops died with the previous process.

        Only ``active`` tasks are touched — ``paused``/``stopped`` are intentional
        user stops (resume on explicit request), terminal states are done.
        Best-effort + idempotent (re-running converges on current run/node state).
        """
        async with async_unit_of_work(commit=False) as db:
            # Cross-owner boot sweep: capture each task's owner so the per-task
            # recovery below runs under that owner's identity (the downstream
            # datastore reads are owner-scoped via require_current_user_id()).
            active = [
                (t.id, t.project_id, t.user_id) for t in await TaskDatastore(db).list_active()
            ]
        recovered = 0
        for task_id, project_id, user_id in active:
            token = set_current_user_id(user_id)
            try:
                if await self._recover_one_task(task_id, project_id):
                    recovered += 1
            except Exception:  # noqa: BLE001
                logger.exception("recover_active_tasks: failed for task %s", task_id)
            finally:
                reset_current_user_id(token)
        if recovered:
            logger.warning(
                "recover_active_tasks: reconciled + re-drove %d active task(s)", recovered
            )
        return recovered

    async def _recover_one_task(self, task_id: str, project_id: str) -> bool:
        """Reconcile one active task's members + re-drive its lead.

        Used by both Layer 1 (startup) and Layer 2 (user 'resume'). Returns False
        if the task isn't recoverable (gone / no lead run).
        """
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        member_done: list[tuple[str, dict[str, Any]]] = []
        resume_members: list[tuple[str, str]] = []  # (session_id, brief)
        summary: list[str] = []
        lead_session_id: str | None = None

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(require_current_user_id(), project_id, task_id)
            if task is None or task.status not in ("active", "paused"):
                return False
            runs = await run_ds.list_runs(require_current_user_id(), task_id)
            lead_run = next((r for r in runs if r.kind == "lead"), None)
            if lead_run is None:
                return False
            lead_session_id = lead_run.session_id

            plan = TaskPlan.from_dict(task.plan)
            plan_dirty = False
            for run in runs:
                if run.kind != "subtask" or run.status not in ("active", "paused"):
                    continue
                ks = await kernel_client.get_session(require_current_user_id(), run.session_id)
                node = plan.get(run.subtask_key) if run.subtask_key else None
                rec = reconcile(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                    node_attempts=(node.attempts if node else 0),
                )
                manifest: dict[str, Any] | None = None
                if rec.disposition == "completed":
                    try:
                        manifest = await collect_manifest(
                            run.session_id, Path(run.run_dir) if run.run_dir else Path(), "idle"
                        )
                    except Exception:  # noqa: BLE001
                        manifest = {
                            "session_id": run.session_id,
                            "status": "completed",
                            "summary": "",
                        }
                    manifest["agent"] = run.agent_slug
                if rec.run_status:
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status=rec.run_status, result_manifest=manifest
                    )
                if node is not None and rec.node_status:
                    fields: dict[str, Any] = {"status": rec.node_status}
                    if rec.resume:
                        fields["attempts"] = node.attempts + 1
                    if rec.reason and rec.node_status == "rework":
                        fields["review_feedback"] = rec.reason
                    plan.update_node(run.subtask_key, **fields)
                    plan_dirty = True
                if rec.deliver_member_done and manifest is not None:
                    member_done.append((run.session_id, manifest))
                if rec.resume:
                    resume_members.append((run.session_id, run.goal or ""))
                summary.append(f"- {run.subtask_key}({run.agent_slug}): {rec.disposition}")

            if plan_dirty:
                task.plan = plan.to_dict()
                await task_ds.update_task(task)
                await planning.emit_plan_update(
                    event_ds,
                    project_id=project_id,
                    task_id=task_id,
                    plan=plan,
                    actor="system",
                    session_id=lead_session_id,
                )

        # Re-drive (outside the DB txn): register the lead mailbox, deliver any
        # completed members' results, respawn resumable members (kernel run_turn
        # on the persisted session), then respawn the lead with a reconcile brief.
        mailbox_registry.register(lead_session_id)
        for member_sid, manifest in member_done:
            mailbox_registry.put(
                lead_session_id,
                InboxMsg(kind="member_done", from_session=member_sid, payload=manifest),
            )
        for member_sid, brief in resume_members:
            self._members.add_member(task_id, member_sid)
            asyncio.create_task(
                self._actor.run_actor_loop(
                    session_id=member_sid,
                    initial_prompt=brief or "继续完成你的子任务,完成后会汇报给 lead。",
                    role="subtask",
                    task_id=task_id,
                    project_id=project_id,
                )
            )
        lead_brief = (
            "<system-recovery>\n本任务已被恢复(系统重启或用户恢复)。子任务对账结果:\n"
            + ("\n".join(summary) if summary else "(无在途子任务)")
            + "\n\n请先调用 get_plan 对齐当前状态,然后继续编排:派发未决子任务、"
            "审核 in_review、重试 rework;全部完成后调用 finish_task。\n</system-recovery>"
        )
        asyncio.create_task(
            self._actor.run_actor_loop(
                session_id=lead_session_id,
                initial_prompt=lead_brief,
                role="lead",
                task_id=task_id,
                project_id=project_id,
            )
        )
        return True

    # ------------------------------------------------------------------
    # Layer 2 (VALUZ-RESUME §5.5): user-initiated stop / resume
    # ------------------------------------------------------------------

    async def _interrupt_kernel_session(self, session_id: str) -> None:
        """Best-effort: ask the kernel runtime to stop an in-flight turn.

        Returns silently whether or not a runtime was active — a member parked
        between turns has no live runtime (``interrupt`` returns False), and the
        ``shutdown`` mailbox message is what stops its actor loop instead.
        """
        try:
            from valuz_agent.adapters import kernel_client

            await kernel_client.interrupt(require_current_user_id(), session_id)
        except Exception:  # noqa: BLE001
            logger.warning("interrupt failed for session %s", session_id, exc_info=True)

    async def stop_task(self, task_id: str, project_id: str) -> bool:
        """User-initiated cascade stop → ``paused`` (recoverable).

        Interrupts the lead + every in-flight member, broadcasts ``shutdown`` to
        their actor loops, flips in-flight member runs ``active→paused`` and the
        task ``→paused``. ``paused`` is deliberate: Layer-1 app-restart recovery
        skips it; the user resumes explicitly via ``resume_task``. Only acts on an
        ``active`` task. Returns False if the task is gone / not active.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(require_current_user_id(), project_id, task_id)
            if task is None or task.status != "active":
                return False
            runs = await run_ds.list_runs(require_current_user_id(), task_id)
            lead_session_id: str | None = next(
                (r.session_id for r in runs if r.kind == "lead"), None
            )
            member_sids = [
                r.session_id for r in runs if r.kind == "subtask" and r.status == "active"
            ]
            for sid in member_sids:
                await run_ds.update_run_by_session(session_id=sid, status="paused")
            await task_ds.update_task_status(require_current_user_id(), task_id, "paused")
            await event_ds.append_event(
                require_current_user_id(),
                project_id,
                task_id,
                "stopped",
                actor="user",
                payload={"members_paused": len(member_sids)},
            )

        # Cascade interrupt + shutdown (outside the DB txn).
        for sid in member_sids:
            await self._interrupt_kernel_session(sid)
        if lead_session_id is not None:
            await self._interrupt_kernel_session(lead_session_id)
        self._coordination._broadcast_shutdown(task_id)
        if lead_session_id is not None:
            from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

            mailbox_registry.put(lead_session_id, InboxMsg(kind="shutdown"))
        return True

    async def resume_task(
        self,
        task_id: str,
        project_id: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """User-initiated resume of a ``paused`` / ``blocked`` / ``stopped`` /
        ``completed`` task.

        Flips the task back to ``active`` then reconciles + respawns members
        and re-drives the lead via the shared ``_recover_one_task`` machine
        (same path as Layer-1 startup recovery; only the trigger differs).

        Allowed source states (per ``task_state.ALLOWED_TRANSITIONS``):

        - ``paused`` — user-intervened pause (REST /intervene action=pause)
        - ``blocked`` — auto-finalize couldn't close (unresolved nodes, OR a
          mid-turn lead crash surfaced as ``task_blocked``). Recoverable.
        - ``stopped`` — user-driven termination (typically via inject "停止此
          任务" → lead calling finish_task(status="stopped")). The lead run
          was marked "completed" by finish_task; we flip it back to "active"
          here so ``_recover_one_task`` rebuilds a fresh lead actor.
        - ``completed`` — a finished task REOPENED to supplement or adjust
          subtasks from a second chat-plan (chat-plan "区分场景"). Like
          ``stopped``, the lead run was marked "completed" by finish_task; we
          reactivate it the same way. The caller is expected to immediately
          modify_plan / inject the new subtasks. A genuinely new goal should
          be a fresh follow-up task, not a reopen.

        Only ``abandoned`` is hard-terminal (draft discarded, nothing to
        revive). ``draft`` is also rejected — call ``commit_task`` to launch
        the lead for the first time.

        Returns a dict explaining what happened:

        - ``{ok: True, prior_status, resumed: True}`` — flip + recovery kicked
        - ``{ok: False, error, prior_status}`` — illegal source state or
          task not found; caller surfaces ``error`` to the user.

        Why a dict, not a bool: the chat-facing ``resume_task`` MCP tool needs
        a human-readable reason to feed back when resume fails (legacy
        ``True/False`` collapses "wrong status" with "task not found").
        """
        from valuz_agent.modules.tasks.task_state import assert_transition

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            task = await task_ds.get_task_by_project(require_current_user_id(), project_id, task_id)
            if task is None:
                return {"ok": False, "error": f"task {task_id!r} not found", "prior_status": None}
            prior_status = task.status
            if prior_status not in ("paused", "blocked", "stopped", "completed"):
                return {
                    "ok": False,
                    "error": (
                        f"resume_task rejected: task is {prior_status!r}, only "
                        "'paused', 'blocked', 'stopped', or 'completed' tasks "
                        "can be resumed. 'abandoned' is hard-terminal (draft "
                        "discarded, nothing to revive) and 'draft' must be "
                        "launched with commit_task. Reopening a 'completed' "
                        "task is for supplementing/adjusting its subtasks; a "
                        "genuinely new goal should be a fresh follow-up task."
                    ),
                    "prior_status": prior_status,
                }
            # Belt-and-suspenders: confirm the transition the state machine
            # accepts. paused/blocked/stopped/completed → active are all legal.
            assert_transition(prior_status, "active")
            await task_ds.update_task_status(require_current_user_id(), task_id, "active")
            # When reviving a stopped OR completed task: finish_task previously
            # marked the lead run as "completed" and broadcast shutdown to
            # members. _recover_one_task respawns the lead unconditionally, but
            # the run row still showing "completed" would lie about reality —
            # fix it so listings + UI reflect the live state.
            if prior_status in ("stopped", "completed"):
                runs = await run_ds.list_runs(require_current_user_id(), task_id)
                lead_run = next((r for r in runs if r.kind == "lead"), None)
                if lead_run is not None and lead_run.status != "active":
                    await run_ds.update_run_by_session(
                        session_id=lead_run.session_id,
                        status="active",
                        ended_at=None,
                    )
            await event_ds.append_event(
                require_current_user_id(),
                project_id,
                task_id,
                "resumed",
                actor=actor,
                payload={"from": prior_status},
            )
        ok = await self._recover_one_task(task_id, project_id)
        return {"ok": ok, "prior_status": prior_status, "resumed": ok}

    async def stop_member(self, session_id: str) -> bool:
        """User-initiated single-member stop (task stays ``active``).

        Interrupts one subtask session, notifies the lead with a
        ``member_done(status=cancelled)`` so it doesn't wait forever, flips the
        run ``→rejected`` and the plan node ``→rework``. The lead decides next
        (redispatch / modify_plan / finish) on its next ``get_plan``.
        """
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run = await run_ds.get_run(session_id)
            if run is None or run.kind != "subtask":
                return False
            task_id = run.task_id or ""
            project_id = run.project_id
            lead_session_id = run.dispatched_by or ""
            subtask_key = run.subtask_key
            agent_slug = run.agent_slug
            await run_ds.update_run_by_session(session_id=session_id, status="rejected")
            if subtask_key:
                task = await task_ds.get_task_by_project(
                    require_current_user_id(), project_id, task_id
                )
                if task is not None:
                    plan = TaskPlan.from_dict(task.plan)
                    if plan.get(subtask_key) is not None:
                        plan.update_node(
                            subtask_key,
                            status="rework",
                            review_feedback="用户手动停止了该子任务",
                        )
                        task.plan = plan.to_dict()
                        await task_ds.update_task(task)
                        await planning.emit_plan_update(
                            event_ds,
                            project_id=project_id,
                            task_id=task_id,
                            plan=plan,
                            actor="user",
                            session_id=lead_session_id or None,
                        )
            await event_ds.append_event(
                require_current_user_id(),
                project_id,
                task_id,
                "subtask_stopped",
                actor="user",
                session_id=session_id,
                payload={"subtask_key": subtask_key},
            )

        await self._interrupt_kernel_session(session_id)
        self._members.discard_member(task_id, session_id)
        if lead_session_id:
            mailbox_registry.put(
                lead_session_id,
                InboxMsg(
                    kind="member_done",
                    from_session=session_id,
                    payload={
                        "agent": agent_slug,
                        "status": "cancelled",
                        "summary": "用户停止了该子任务",
                        "artifacts": [],
                    },
                ),
            )
        return True
