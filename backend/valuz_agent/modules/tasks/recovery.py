"""RecoveryService — startup recovery + user-initiated stop / resume.

The durable truth for a member's liveness is its kernel session state + the
host run/plan rows, never the in-memory mailbox (which dies with the process).
The pure state→disposition rules live in ``member_state``; this service
applies their side effects: Layer-1 startup sweep (``recover_active_tasks``),
Layer-2 ``stop_task`` / ``resume_task`` / ``stop_member``, and the shared
``_recover_one_task`` reconcile-and-respawn machine.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.agent_resolver import spill_goal_brief_if_too_long
from valuz_agent.i18n import t
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.manifest import MemberManifest, collect_manifest_safe
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.adapters.agent_resolver import resolve_agent_display_name
from valuz_agent.modules.tasks import launcher
from valuz_agent.modules.tasks.events import block_task, finalize_task, record_subtask_stopped  # noqa: I001
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
    pick_lead_run,
)
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks.lease import (
    ActorLease,
    acquire_actor_lease,
    load_actor_lease_states,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks import mailbox_store
from valuz_agent.modules.tasks.member_state import (
    reconcile,
)
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RecoveryService (ADR-023 Step 3d)
# ---------------------------------------------------------------------------


class RecoveryService:
    """Startup sweep + user stop/resume.

    Registry keystone in ``_recover_one_task``: each resumable member is
    re-seeded via ``registry.add_member`` (no dispatch epoch on the recovery
    branch) BEFORE its actor loop respawns — mirroring ``dispatch_async``.
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
            # Cross-owner boot sweep: capture each task's owner so per-task
            # recovery runs under that owner (downstream reads are owner-scoped
            # by explicit user_id parameters).
            active = [
                (t.id, t.project_id, t.user_id) for t in await TaskDatastore(db).list_active()
            ]
        recovered = 0
        for task_id, project_id, user_id in active:
            try:
                # This sweep is cross-owner and unconditional, so with several
                # host processes every one of them used to re-drive every active
                # task — N lead loops, N× model spend, competing plan CAS
                # writes. ``_recover_one_task`` takes the lead session's lease
                # before respawning anything, so a task someone else is running
                # is left alone there.
                #
                # There used to be an advisory pre-check here as well, keyed by
                # task id. The lease is keyed by session now, and resolving the
                # lead session is exactly the first thing the authoritative
                # path does — so the pre-check bought one skipped query at the
                # cost of a second answer to the same question.
                if await self._recover_one_task(task_id, project_id, user_id=user_id):
                    recovered += 1
            except Exception:  # noqa: BLE001
                logger.exception("recover_active_tasks: failed for task %s", task_id)
        if recovered:
            logger.warning(
                "recover_active_tasks: reconciled + re-drove %d active task(s)", recovered
            )
        return recovered

    async def _recover_one_task(
        self,
        task_id: str,
        project_id: str,
        user_id: str,
        *,
        lead_instruction: str | None = None,
    ) -> bool:
        """Reconcile one active task's members + re-drive its lead.

        Used by both Layer 1 (startup) and Layer 2 (user 'resume'). Returns False
        if the task isn't recoverable (gone / no lead run).

        ``lead_instruction`` (Layer 2 only): a free-text user instruction that
        rides along with the resume — appended to the lead's recovery brief in
        the same ``<user-instruction>`` envelope ``inject_into_task`` uses, so
        "回复并恢复" is one atomic step instead of resume-then-hope-the-mailbox
        -delivery-races-the-respawn.
        """

        member_done: list[tuple[str, MemberManifest]] = []
        # (session_id, brief, run_dir, agent_slug, subtask_key) — run_dir + slug
        # + key let us spill an over-cap resume brief to a doc before re-injecting
        # it into the member's goal-mode session.
        resume_members: list[tuple[str, str, str, str, str]] = []
        summary: list[str] = []
        lead_session_id: str | None = None

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status not in ("active", "paused"):
                return False
            runs = await run_ds.list_runs(user_id, task_id)
            lead_run = pick_lead_run(runs)
            if lead_run is None:
                return False
            lead_session_id = lead_run.session_id

            plan = TaskPlan.from_dict(task.plan)
            # (key, fields, bump_attempts) — applied inside persist_plan's CAS
            # closure; ``plan`` above only feeds the reconcile classification.
            mutations: list[tuple[str, dict[str, Any], bool]] = []
            for run in runs:
                if run.kind != "subtask" or run.status not in ("active", "paused"):
                    continue
                ks = await kernel_client.get_session(user_id, run.session_id)
                node = plan.get(run.subtask_key) if run.subtask_key else None
                rec = reconcile(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                    node_attempts=(node.attempts if node else 0),
                )
                manifest: MemberManifest | None = None
                if rec.disposition == "completed":
                    manifest = await collect_manifest_safe(
                        run.session_id,
                        Path(run.run_dir) if run.run_dir else Path(),
                        "idle",
                        agent_slug=run.agent_slug or "",
                        user_id=user_id,
                    )
                if rec.run_status:
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status=rec.run_status, result_manifest=manifest
                    )
                if node is not None and rec.node_status:
                    fields: dict[str, Any] = {"status": rec.node_status}
                    if rec.reason and rec.node_status == "rework":
                        fields["review_feedback"] = rec.reason
                    # ``node`` was looked up BY ``run.subtask_key``, so a
                    # non-None node means the key is a real str.
                    mutations.append((node.key, fields, rec.resume))
                if rec.deliver_member_done and manifest is not None:
                    member_done.append((run.session_id, manifest))
                if rec.resume:
                    resume_members.append(
                        (
                            run.session_id,
                            run.goal or "",
                            run.run_dir or "",
                            run.agent_slug or "",
                            run.subtask_key or "",
                        )
                    )
                summary.append(f"- {run.subtask_key}({run.agent_slug}): {rec.disposition}")

            if mutations:

                def _apply(p: TaskPlan) -> bool:
                    changed = False
                    for key, fields, bump_attempts in mutations:
                        n = p.get(key)
                        if n is None:
                            continue
                        if bump_attempts:
                            fields = {**fields, "attempts": n.attempts + 1}
                        try:
                            p.update_node(key, **fields)
                        except PlanError:
                            logger.warning(
                                "reconcile: skipping illegal node write %s %s", key, fields
                            )
                            continue
                        changed = True
                    return changed

                await planning.persist_plan_best_effort(
                    task_ds,
                    event_ds,
                    task,
                    mutate=_apply,
                    actor="system",
                    session_id=lead_session_id,
                    user_id=user_id,
                    diverges="reconcile could not write the node statuses it "
                    f"derived from the run rows ({', '.join(k for k, _, _ in mutations)})",
                )

        # Evict any stale kernel runtime BEFORE respawning. Load-bearing for
        # pause→resume: the pause interrupt leaves a cancelled SDK client in
        # the kernel's runtime cache, and reusing it makes the resumed turn
        # cancel instantly → auto-finalize blocks the task. Doing it here is
        # race-free (old loop exited, new one not yet built).
        async def _evict_runtime(sid: str) -> None:
            try:
                await kernel_client.cleanup_runtime(sid)
            except Exception:  # noqa: BLE001
                pass

        # Everything above is read + reconcile and safely repeatable. Everything
        # below RESPAWNS: kernel evictions, member actor loops driving real
        # turns, then the lead. Take the task's lease first, and give up if a
        # peer holds it.
        #
        # The advisory check in ``recover_active_tasks`` cannot cover this: at a
        # cold boot NOBODY holds a lease yet, so every worker passes it, and
        # without an authoritative acquisition here each of them would respawn
        # the same members — duplicate turns, duplicate model spend — before the
        # lead loops raced for ownership. The lease travels into the lead loop
        # so it does not re-acquire and fence the recovery that spawned it.
        lease = await acquire_actor_lease(session_id=lead_session_id, task_id=task_id)
        if lease is None:
            logger.info(
                "recover: task %s is already being driven — leaving it alone", task_id
            )
            return False
        try:
            return await self._respawn(
                task_id=task_id,
                project_id=project_id,
                user_id=user_id,
                lead_session_id=lead_session_id,
                lease=lease,
                member_done=member_done,
                resume_members=resume_members,
                summary=summary,
                lead_instruction=lead_instruction,
                evict=_evict_runtime,
            )
        except BaseException:
            # We hold the task but are not going to drive it — hand it back now
            # rather than making the next driver wait out the TTL.
            await lease.release()
            raise

    async def _respawn(
        self,
        *,
        task_id: str,
        project_id: str,
        user_id: str,
        lead_session_id: str,
        lease: ActorLease,
        member_done: list[tuple[str, MemberManifest]],
        resume_members: list[tuple[str, str, str, str, str]],
        summary: list[str],
        lead_instruction: str | None,
        evict: Any,
    ) -> bool:
        """Side-effectful half of recovery, run under the task's lease.

        Split out so the lease can wrap it as one unit: register the lead
        mailbox, deliver any completed members' results, respawn resumable
        members (kernel ``run_turn`` on the persisted session), then respawn the
        lead with a reconcile brief.
        """
        _evict_runtime = evict
        for member_sid, manifest in member_done:
            # Durable: a restart re-seeds results for a lead whose loop may
            # come up in a different process than this one.
            async with async_unit_of_work() as db:
                await mailbox_store.enqueue(
                    db,
                    session_id=lead_session_id,
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                    kind="member_done",
                    from_session=member_sid,
                    origin="recovery-reseed",
                    payload=dict(manifest),
                )
        for member_sid, brief, m_run_dir, m_slug, m_key in resume_members:
            await _evict_runtime(member_sid)
            resume_prompt = brief or t("task.brief.memberResumeDefault")
            # Fence the goal-mode re-injection: an over-cap subtask goal would
            # blow the ``/goal`` payload again on resume — spill it to a doc and
            # re-inject a short pointer instead (same fence as first dispatch).
            if brief and m_run_dir:
                resume_prompt = spill_goal_brief_if_too_long(
                    brief,
                    run_dir=m_run_dir,
                    task_id=task_id,
                    label=f"{m_slug}-{m_key}",
                    is_lead=False,
                )
            # No dispatch_epoch on the recovery branch: a resumed member's
            # artifacts predate the respawn, so attribution restarts from zero.
            launcher.spawn_actor(
                self._actor,
                session_id=member_sid,
                prompt=resume_prompt,
                role="subtask",
                task_id=task_id,
                project_id=project_id,
                user_id=user_id,
                registry=self._members,
            )
        await _evict_runtime(lead_session_id)
        # Localized: this is the lead's USER-turn prompt on resume, and a model
        # answers in the language it was prompted in — a hardcoded one flips an
        # otherwise-English task transcript after any restart.
        lead_brief = (
            "<system-recovery>\n"
            + t(
                "task.brief.recoveryLead",
                params={
                    "summary": "\n".join(summary)
                    if summary
                    else t("task.brief.recoveryNoMembers")
                },
            )
            + "\n</system-recovery>"
        )
        if lead_instruction and lead_instruction.strip():
            lead_brief += (
                '\n<user-instruction source="resume">\n'
                + lead_instruction.strip()
                + "\n</user-instruction>\n"
                + t("task.brief.recoveryUserInstruction")
            )
        launcher.spawn_actor(
            self._actor,
            session_id=lead_session_id,
            prompt=lead_brief,
            role="lead",
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            lease=lease,
        )
        return True

    # ------------------------------------------------------------------
    # Layer 2 (VALUZ-RESUME §5.5): user-initiated stop / resume
    # ------------------------------------------------------------------

    async def _interrupt_kernel_session(self, session_id: str, user_id: str) -> None:
        """Best-effort: ask the kernel runtime to stop an in-flight turn.

        Returns silently whether or not a runtime was active — a member parked
        between turns has no live runtime (``interrupt`` returns False), and the
        ``shutdown`` mailbox message is what stops its actor loop instead.
        """
        try:
            await kernel_client.interrupt(user_id, session_id)
        except Exception:  # noqa: BLE001
            logger.warning("interrupt failed for session %s", session_id, exc_info=True)

    async def stop_task(
        self,
        task_id: str,
        project_id: str,
        *,
        target_status: str = "paused",
        user_id: str,
    ) -> bool:
        """Cascade halt → ``paused`` (from active) or ``stopped`` (from
        active/paused; soft-terminal but revivable via resume_task).

        Interrupts lead + members, broadcasts shutdown, parks in-flight runs
        and their ``in_progress`` plan nodes ``→paused``, then flips the task.
        Members are parked identically for both targets. Returns False when
        the task is gone or the transition is illegal.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None:
                return False
            # pause: only an active task. stop: an active OR already-paused task.
            allowed_from = ("active",) if target_status == "paused" else ("active", "paused")
            if task.status not in allowed_from:
                return False
            runs = await run_ds.list_runs(user_id, task_id)
            lead_pick = pick_lead_run(runs)
            lead_session_id: str | None = lead_pick.session_id if lead_pick else None
            member_sids = [
                r.session_id for r in runs if r.kind == "subtask" and r.status == "active"
            ]
            for sid in member_sids:
                await run_ds.update_run_by_session(session_id=sid, status="paused")
            # The lead's own run too. Its loop leaves through the
            # externally-managed exit, which skips finalize by design — the
            # terminal state belongs to whoever stopped it — so nothing else
            # ever settles this row. Observed on qa: a task ``stopped`` for
            # twelve minutes, its lease correctly released, and its lead run
            # still reading ``active``. Anything judging liveness from the run
            # index rather than the lease saw a runner that had long since
            # gone. ``paused`` is the same word the members get, and it is the
            # resumable one.
            if lead_session_id and lead_pick is not None and lead_pick.status == "active":
                await run_ds.update_run_by_session(
                    session_id=lead_session_id, status="paused"
                )
            # Park only the running member's node (``in_progress`` = a live
            # member session, the one we're halting) → ``paused`` so the panel
            # stops spinning it. Leave ``in_review`` (member finished, awaiting
            # the lead's review — parking would lose that) and ``rework``
            # (awaiting re-dispatch) alone. On resume, recovery reconcile flips
            # a parked node back to ``in_progress`` if its run survived;
            # otherwise it stays ``paused`` and is re-dispatchable (ready_keys +
            # resolve_dispatch_node both accept ``paused``).
            def _park_running(p: TaskPlan) -> bool:
                parked = 0
                for node in p.nodes:
                    if node.status == "in_progress":
                        p.update_node(node.key, status="paused")
                        parked += 1
                return parked > 0

            await planning.persist_plan_best_effort(
                task_ds,
                event_ds,
                task,
                mutate=_park_running,
                actor="user",
                session_id=lead_session_id,
                user_id=user_id,
                diverges="running nodes stay in_progress on a halted task, so the "
                "panel keeps spinning them until resume reconciles",
            )
            if target_status == "stopped":
                # Terminal write — goes through finalize_task so the status
                # flip rides the task_state guard AND ``task.finalized`` is
                # announced (the sandbox-TTL clamp listens on it; the old
                # direct ``update_task`` write here skipped both). The event
                # type stays the raw "stopped" — it drives UI status + timer.
                await finalize_task(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    status="stopped",
                    event_type="stopped",
                    actor="user",
                    payload={"members_paused": len(member_sids)},
                )
            else:
                if not await task_ds.update_task_status(user_id, task_id, "paused"):
                    # Lost the flip (lead finalize landed between our status
                    # check and the write) — the winner owns the terminal;
                    # appending "paused" here would put a lying event on a
                    # completed/blocked task's timeline.
                    logger.warning("stop_task: pause flip lost a race for %s", task_id)
                    return False
                await event_ds.append_event(
                    user_id,
                    project_id,
                    task_id,
                    "paused",  # drives UI status + timer
                    actor="user",
                    payload={"members_paused": len(member_sids)},
                )

        # Cascade interrupt + shutdown (outside the DB txn).
        for sid in member_sids:
            await self._interrupt_kernel_session(sid, user_id=user_id)
        if lead_session_id is not None:
            await self._interrupt_kernel_session(lead_session_id, user_id=user_id)
        # The lead reads the paused task on its own poll, from whichever
        # process runs it — which the queued ``shutdown`` this replaced could
        # only manage when that happened to be this one.
        self._coordination.stop_tracking_members(task_id)
        return True

    async def resume_task(
        self,
        task_id: str,
        project_id: str,
        *,
        actor: str = "user",
        user_id: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """Resume a ``paused`` / ``blocked`` / ``stopped`` / ``completed`` task
        (``completed`` = deliberate reopen to supplement its subtasks; only
        ``abandoned`` is hard-terminal, and ``draft`` launches via commit_task).

        Flips the task ``active``, reactivates the lead run row when a prior
        finish marked it completed, then reconciles + respawns through the
        shared ``_recover_one_task`` machine. ``instruction`` rides along into
        the respawned lead's recovery brief and is recorded as ``user_inject``.

        Returns ``{ok, prior_status, resumed|error}`` — a dict rather than a
        bool because the MCP tool needs a human-readable rejection reason.
        """
        from valuz_agent.modules.tasks.task_state import assert_transition

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None:
                return {"ok": False, "error": f"task {task_id!r} not found", "prior_status": None}
            prior_status = task.status
            # ``failed`` is a LEGACY status (pre-dates folding task failure
            # into ``blocked``); old rows still carry it and were stranded —
            # no action bar, resume rejected. Treat it exactly like blocked.
            if prior_status not in ("paused", "blocked", "stopped", "completed", "failed"):
                return {
                    "ok": False,
                    "error": (
                        f"resume_task rejected: task is {prior_status!r}, only "
                        "'paused', 'blocked', 'stopped', or 'completed' tasks "
                        "(or legacy 'failed' rows) can be resumed. 'abandoned' "
                        "is hard-terminal (draft discarded, nothing to revive) "
                        "and 'draft' must be launched with commit_task. "
                        "Reopening a 'completed' task is for supplementing/"
                        "adjusting its subtasks; a genuinely new goal should "
                        "be a fresh follow-up task."
                    ),
                    "prior_status": prior_status,
                }
            # Belt-and-suspenders: confirm the transition the state machine
            # accepts. paused/blocked/stopped/completed → active are all legal.
            # Legacy ``failed`` is outside the enum — ``update_task_status``
            # tolerates unknown *source* statuses precisely for this case, so
            # skip the formal check there.
            if prior_status != "failed":
                assert_transition(prior_status, "active")
            if not await task_ds.update_task_status(user_id, task_id, "active"):
                # Lost the flip (e.g. a concurrent stop moved the task under
                # us) — do NOT append "resumed", normalise runs, or clear the
                # failure notification off a state we did not create.
                return {
                    "ok": False,
                    "error": "resume_task: task changed concurrently — refresh and retry",
                    "prior_status": prior_status,
                }
            # When reviving a stopped OR completed task: finish_task previously
            # marked the lead run as "completed" and broadcast shutdown to
            # members. _recover_one_task respawns the lead unconditionally, but
            # the run row still showing "completed" would lie about reality —
            # fix it so listings + UI reflect the live state. Legacy ``failed``
            # rows may carry any run status — normalise them the same way.
            if prior_status in ("stopped", "completed", "failed"):
                runs = await run_ds.list_runs(user_id, task_id)
                lead_run = pick_lead_run(runs)
                if lead_run is not None and lead_run.status != "active":
                    await run_ds.update_run_by_session(
                        session_id=lead_run.session_id,
                        status="active",
                        ended_at=None,
                    )
            await event_ds.append_event(
                user_id,
                project_id,
                task_id,
                "resumed",
                actor=actor,
                payload={"from": prior_status},
            )
            if instruction and instruction.strip():
                # Timeline record of what the user asked for alongside the
                # resume — same event type the chat-inject path appends, so
                # the detail page renders both uniformly.
                await event_ds.append_event(
                    user_id,
                    project_id,
                    task_id,
                    "user_inject",
                    actor=actor,
                    payload={"text": instruction.strip(), "via": "resume"},
                )
        # Clear any open "task failed" notification — the user is dealing with
        # it now, so it mustn't keep the badge lit (docs/design/notifications.md).
        try:
            from valuz_agent.modules.notifications.service import notification_service

            await notification_service.resolve_task(user_id or "", task_id)
        except Exception:  # noqa: BLE001
            logger.warning("resume_task: failed to clear failure notification", exc_info=True)

        ok = await self._recover_one_task(
            task_id, project_id, user_id=user_id, lead_instruction=instruction
        )
        return {"ok": ok, "prior_status": prior_status, "resumed": ok}

    async def inject_or_revive(
        self,
        *,
        task_id: str,
        project_id: str,
        text: str,
        from_session_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Deliver a user instruction to the lead — reviving a halted task.

        The ONE spelling of the "talking to a halted task is resume intent"
        policy (the :intervene contract promises chat/inject can revive).
        Both transports (REST :inject, MCP inject_into_task) call this; the
        policy used to be duplicated in each with drifting result shaping.
        Returns the messaging result dict, reshaped on the revive path to
        ``{"delivered", "lead_session_id", "reason"}``.
        """
        from valuz_agent.modules.tasks import messaging

        result = await messaging.inject_into_task(
            task_id=task_id,
            project_id=project_id,
            text=text,
            from_session_id=from_session_id,
            user_id=user_id,
        )
        if result.get("reason") == "TASK_HALTED":
            revived = await self.resume_task(
                task_id, project_id, user_id=user_id, instruction=text
            )
            ok = bool(revived.get("ok"))
            return {
                "delivered": ok,
                "lead_session_id": None,
                "reason": "TASK_RESUMED" if ok else "RESUME_FAILED",
            }
        return result

    async def stop_member(self, session_id: str, user_id: str) -> bool:
        """User-initiated single-member stop (task stays ``active``).

        Interrupts one subtask session, notifies the lead with a
        ``member_done(status=cancelled)`` so it doesn't wait forever, flips the
        run ``→rejected`` and the plan node ``→rework``. The lead decides next
        (redispatch / modify_plan / finish) on its next ``get_plan``.
        """

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
                task = await task_ds.get_task_by_project(user_id, project_id, task_id)
                if task is not None:

                    def _park(p: TaskPlan, *, _key: str = subtask_key or "") -> bool:
                        n = p.get(_key)
                        if n is None or n.status not in (
                            "in_progress", "in_review", "rework", "paused"
                        ):
                            return False
                        p.update_node(
                            _key, status="rework", review_feedback=t("task.reworkUserStopped")
                        )
                        return True

                    await planning.persist_plan_best_effort(
                        task_ds,
                        event_ds,
                        task,
                        mutate=_park,
                        actor="user",
                        session_id=lead_session_id or None,
                        user_id=user_id,
                        diverges=f"node {subtask_key!r} not parked to rework after the "
                        "user stopped its member",
                    )
            await record_subtask_stopped(
                event_ds,
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                agent_slug=agent_slug,
                agent_name=await resolve_agent_display_name(project_id, agent_slug, user_id),
                subtask_key=subtask_key,
            )

        # The interrupt only reaches a member MID-TURN. An idle one, parked
        # between turns, learns it was cancelled from its own run row on the
        # next poll — bounded by the inbox poll rather than by its 10-minute
        # idle TTL, and without depending on which process it runs in.
        await self._interrupt_kernel_session(session_id, user_id=user_id)
        self._members.discard_member(task_id, session_id)
        if lead_session_id:
            async with async_unit_of_work() as db:
                # Anything still queued for the member it just stopped would be
                # read by nobody; cancel it in the same breath as telling the
                # lead, so the two cannot disagree.
                await mailbox_store.cancel_pending(db, session_id=session_id)
                await mailbox_store.enqueue(
                    db,
                    session_id=lead_session_id,
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                    kind="member_done",
                    from_session=session_id,
                    origin="member-stopped",
                    payload={
                        "agent": agent_slug,
                        "status": "cancelled",
                        "summary": t("task.summaryUserStopped"),
                        "artifacts": [],
                    },
                )
        return True


# ---------------------------------------------------------------------------
# Live watchdog (task attention & reliability, P2).
#
# Boot recovery only reconciles at process START; a lead that dies while the
# process stays up (uncaught loop crash, failed finalize) would sit ``active``
# forever with nothing dispatched. This periodic sweep is the same concern as
# the rest of this module — notice a dead lead, make the task resumable — on a
# timer instead of at boot.
#
# Liveness signal: the task's LEASE (``tasks/lease.py``) — shared state, so it
# answers for every host process. It used to be the lead's mailbox
# registration, which is process-local: with more than one process this sweep
# read every sibling's healthy lead as dead and blocked live tasks mid-run.
# A task with no lease row is UNKNOWN, never dead. On top of that a task must
# look dead for ``confirm_sweeps`` consecutive sweeps before we act, and the
# intervention is deliberately minimal: ``blocked`` + notification, never a
# respawn.
# ---------------------------------------------------------------------------


def _parse_duration_env(name: str, default: timedelta) -> timedelta:
    """``"30"`` → 30s; ``"5m"`` / ``"90s"`` / ``"1h"`` → that duration.
    Bad input warns and returns the default. Mirrors the automations monitor."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    suffixes: dict[str, int] = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    try:
        if raw[-1] in suffixes:
            return timedelta(seconds=int(raw[:-1]) * suffixes[raw[-1]])
        return timedelta(seconds=int(raw))
    except (ValueError, IndexError):
        logger.warning("task health monitor: bad duration %s=%r, using default", name, raw)
        return default


@dataclass(frozen=True)
class TaskHealthConfig:
    interval: timedelta = timedelta(seconds=60)
    startup_delay: timedelta = timedelta(seconds=90)
    # A task must look dead for this many consecutive sweeps before we act —
    # absorbs the brief spawn/resume window where the loop hasn't registered
    # its mailbox yet.
    confirm_sweeps: int = 2

    @property
    def enabled(self) -> bool:
        return self.interval.total_seconds() > 0

    @classmethod
    def from_env(cls) -> TaskHealthConfig:
        return cls(
            interval=_parse_duration_env(
                "VALUZ_TASK_HEALTH_MONITOR_INTERVAL", cls.interval
            ),
            startup_delay=_parse_duration_env(
                "VALUZ_TASK_HEALTH_MONITOR_STARTUP_DELAY", cls.startup_delay
            ),
        )


class TaskHealthMonitor:
    def __init__(self, config: TaskHealthConfig | None = None) -> None:
        self._config = config or TaskHealthConfig.from_env()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # task_id → consecutive dead-looking sweep count.
        self._suspect: dict[str, int] = {}

    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info("task health monitor: disabled (interval<=0)")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        logger.info(
            "task health monitor: started (interval=%s, startup_delay=%s, confirm_sweeps=%d)",
            self._config.interval,
            self._config.startup_delay,
            self._config.confirm_sweeps,
        )

    async def shutdown(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._suspect.clear()
        logger.info("task health monitor: stopped")

    async def _tick_loop(self) -> None:
        if self._config.startup_delay.total_seconds() > 0:
            try:
                await asyncio.sleep(self._config.startup_delay.total_seconds())
            except asyncio.CancelledError:
                return
        interval_s = self._config.interval.total_seconds()
        while self._running:
            await self._safe_sweep()
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                break

    async def _safe_sweep(self) -> None:
        try:
            await self.sweep_once()
        except Exception:  # noqa: BLE001
            logger.exception("task health monitor: sweep failed")

    async def sweep_once(self) -> list[str]:
        """One pass over active tasks. Returns the task_ids marked blocked this
        sweep (for tests / observability). Never raises to the caller loop."""
        if is_draining():
            return []
        async with async_unit_of_work(commit=False) as db:
            # ONE query for (task_id, user_id, project_id, lead_session_id) —
            # this sweep runs every 60s forever, and the previous shape was a
            # full-row scan plus one list_runs per active task.
            candidates = await TaskDatastore(db).list_active_lead_bindings()

        # Liveness is the LEASE, and only the lease. It used to be read
        # alongside ``mailbox_registry.is_owned``, which is process-local and
        # therefore reported every sibling process's healthy lead as dead —
        # this sweep then blocked it mid-run. Now every actor loop holds a
        # lease on its own session, so one shared answer serves all processes
        # and the second, lying opinion is gone.
        leases = await load_actor_lease_states([sid for *_, sid in candidates if sid])
        now = now_ms()

        acted: list[str] = []
        live_task_ids: set[str] = set()
        for task_id, user_id, project_id, lead_session_id in candidates:
            live_task_ids.add(task_id)
            if lead_session_id is None:
                # No lead run at all — nothing this monitor can safely do; leave
                # it to boot recovery / user action.
                self._suspect.pop(task_id, None)
                continue
            lease = leases.get(lead_session_id)
            if lease is None:
                # No lease row: nobody has claimed this task under the lease
                # scheme. Either it predates the table or its driver is still on
                # an older build mid-rollout. UNKNOWN is not dead — refusing to
                # act is what makes this change safe to roll out gradually.
                self._suspect.pop(task_id, None)
                continue
            if lease.is_live(now):
                # Someone, here or elsewhere, is running the lead.
                self._suspect.pop(task_id, None)
                continue
            # Dead-looking: the loop has exited but the task is still active.
            n = self._suspect.get(task_id, 0) + 1
            self._suspect[task_id] = n
            if n < self._config.confirm_sweeps:
                logger.debug(
                    "task health monitor: task %s lead loop absent (%d/%d) — waiting to confirm",
                    task_id,
                    n,
                    self._config.confirm_sweeps,
                )
                continue
            # Confirmed zombie across ``confirm_sweeps`` — mark blocked so it
            # surfaces + becomes user-resumable.
            self._suspect.pop(task_id, None)
            marked = await self._mark_blocked(task_id, user_id, project_id, lead_session_id)
            if marked:
                acted.append(task_id)

        # Drop suspicion for tasks that are no longer active (finished / paused
        # between sweeps) so the map doesn't grow unbounded.
        for stale in [tid for tid in self._suspect if tid not in live_task_ids]:
            self._suspect.pop(stale, None)
        return acted

    async def _mark_blocked(
        self, task_id: str, user_id: str, project_id: str, lead_session_id: str
    ) -> bool:
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            # Re-read under the write UoW — the task may have moved off
            # ``active`` since the read snapshot (a late finalize won the race).
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status != "active":
                return False
            # Double-check liveness right before writing — a driver may have
            # taken the task over (a resume landed, or a peer picked it up) in
            # the sweep gap. Both oracles, for the same reason as in the sweep.
            lease = (await load_actor_lease_states([lead_session_id])).get(lead_session_id)
            if lease is None or lease.is_live(now_ms()):
                return False
            reason = (
                "The lead stopped without finishing the task (the process "
                "stayed up but its loop exited). Resume to rebuild the lead "
                "and continue."
            )
            await block_task(
                db,
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                event_type="task_blocked",
                actor=lead_session_id,
                session_id=lead_session_id,
                reason=reason,
                payload={"reason": "lead_dead"},
            )
        logger.warning(
            "task health monitor: task %s -> blocked (lead loop dead, session %s)",
            task_id,
            lead_session_id,
        )
        return True


# Process-singleton, mirrors ``automation_failure_monitor``.
task_health_monitor = TaskHealthMonitor()

