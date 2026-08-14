"""FinalizationService — everything that ENDS a task or settles a run.

The other half of what used to be a single 1400-line LifecycleService: the
split follows the seam the type system already named — this class is the
runner's concrete :class:`~valuz_agent.modules.tasks.actor_runner.ActorFinalizer`,
bound at the composition root. Authoring (kickoff / draft / commit / abandon)
stays in ``lifecycle``; the terminal invariants ("the terminal write is the
last and most important step", "only settle a still-active run") live here,
where an authoring change can no longer disturb them.

finish_task · update_deliverable · finalize_actor (the ``run_actor_loop``
``finally``) · _finalize_interrupted_member · _auto_finalize_lead_task ·
_complete_task (the shared completed-terminal pair).
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.adapters.agent_resolver import resolve_agent_display_name
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.i18n import t
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.events import (
    block_task,
    finalize_task,
    record_subtask_failed,
    record_subtask_stopped,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.manifest import collect_manifest_safe, last_assistant_text
from valuz_agent.modules.tasks import mailbox_store
from valuz_agent.modules.tasks.models import TaskRow
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan

logger = logging.getLogger(__name__)


class FinalizationService:
    """Terminal writes + actor-loop finalize callbacks (the ``ActorFinalizer``).

    Built once at the composition root with the shared registry, ActorRunner
    and CoordinationService — same collaborators as LifecycleService, other
    half of the old class.
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

    @staticmethod
    async def _complete_task(
        db: Any,
        *,
        user_id: str,
        project_id: str,
        task_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str],
        auto_finalized: bool = False,
    ) -> None:
        """The ``completed`` terminal pair: settle the lead run + finalize.

        The two spellings (explicit ``finish_task``, host-side auto-finalize)
        must stay identical — they differ only in who decided and whether the
        ``auto_finalized`` marker rides the payload.
        """
        await TaskSessionDatastore(db).update_run_by_session(
            session_id=lead_session_id,
            status="completed",
            ended_at=now_ms(),
        )
        await finalize_task(
            db,
            user_id=user_id,
            project_id=project_id,
            task_id=task_id,
            status="completed",
            event_type="task_completed",
            actor=lead_session_id,
            session_id=lead_session_id,
            payload={
                "summary": summary,
                "artifacts": artifacts,
                **({"auto_finalized": True} if auto_finalized else {}),
            },
        )

    # ------------------------------------------------------------------
    # auto-finalize — host-side terminal fallback
    # ------------------------------------------------------------------

    @staticmethod
    async def _last_assistant_summary(session_id: str, user_id: str) -> str:
        """Best-effort last assistant-message text, for an auto-finalize summary."""
        return await last_assistant_text(user_id, session_id)

    async def _auto_finalize_lead_task(
        self,
        *,
        lead_session_id: str,
        task_id: str,
        project_id: str,
        final_status: str,
        user_id: str,
    ) -> None:
        """Close a task whose lead loop ended without calling ``finish_task``
        (common: a goal-mode lead satisfies a simple goal inline) — otherwise
        the task is orphaned ``active`` forever. Disposition:
          - status != active / members in flight → no-op;
          - turn errored (status OR stop_reason — an errored turn can still
            read "idle") → ``blocked``, never ``completed``;
          - unresolved plan nodes → ``blocked``;
          - else → ``completed`` (summary = lead's last assistant message).
        """
        # Fail loud: a missing owner silently misses the owner-scoped task
        # lookup below (``get_task_by_project`` filters ``user_id``), which would
        # no-op this finalize and orphan the task ``active`` forever — the exact
        # bug this fallback exists to prevent. Every caller must thread the owner.
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)

            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status != "active":
                return  # already closed by finish_task / stop / intervene
            if self._members.has_live_members(task_id):
                return  # members still running — not the lead's terminal moment

            try:
                # Shared predicate (TaskPlan.unresolved_keys) — ``paused`` counts
                # as outstanding, so a task halted mid-flight whose parked node
                # was never re-dispatched can no longer be closed ``completed``.
                unresolved = TaskPlan.from_dict(task.plan).unresolved_keys()
            except PlanError:
                unresolved = []

            # A turn can ERROR yet still leave session.status == "idle" — the
            # failure lives in stop_reason (e.g. a skill-materialization crash,
            # or an API transport error like ECONNRESET / a dropped socket that
            # the SDK surfaces as ResultMessage(is_error=True) — both report
            # stop_reason.type=="error" with status idle and ~0 assistant
            # output). Always consult stop_reason (it carries the ``category``
            # needed below) and fall back to final_status; never mark an errored
            # turn "completed".
            error_msg: str | None = None
            error_category: str | None = None
            try:
                sess = await data_reader().get_session(user_id, lead_session_id)
                sr = getattr(sess, "stop_reason", None) if sess is not None else None
                if sr:
                    typ = sr.get("type") if isinstance(sr, dict) else getattr(sr, "type", None)
                    if typ == "error" or (isinstance(typ, str) and "error" in typ):
                        msg = (
                            sr.get("message")
                            if isinstance(sr, dict)
                            else getattr(sr, "message", None)
                        )
                        error_msg = str(msg or "lead turn errored")
                        error_category = (
                            sr.get("category")
                            if isinstance(sr, dict)
                            else getattr(sr, "category", None)
                        )
            except Exception:  # noqa: BLE001
                logger.debug("auto-finalize: stop_reason check failed for %s", lead_session_id)
            if error_msg is None and final_status in ("terminated", "error"):
                # Driver flagged a failure with no stop_reason to read (e.g. a
                # raised exception). Treat as a genuine failure (category stays
                # None → not a user cancellation).
                error_msg = f"lead turn ended with status={final_status}"

            if error_msg:
                if error_category in ("user_interrupt", "interrupted") and not unresolved:
                    # Cancellation BEFORE any plan node exists: nothing
                    # half-done to protect, so ``blocked`` (with its failure
                    # notification) would be a lie. But "stay active" was a
                    # lie too: an active task with no lead loop is a dead
                    # zone — inject on an ACTIVE task with an unregistered
                    # lead returns LEAD_OFFLINE and DROPS the message (revive
                    # only triggers on halted states), and the health
                    # watchdog then "corrected" the state to blocked after
                    # two sweeps with a misleading "lead stopped" alert.
                    # ``paused`` is the honest state: inject/resume revive it
                    # immediately and the watchdog ignores it.
                    if await TaskDatastore(db).update_task_status(
                        user_id, task_id, "paused"
                    ):
                        await TaskEventDatastore(db).append_event(
                            user_id,
                            project_id,
                            task_id,
                            "paused",
                            actor=lead_session_id,
                            payload={"reason": "kickoff_cancelled"},
                        )
                    logger.warning(
                        "auto-finalize: task %s lead turn cancelled with empty plan "
                        "(%s) — parking paused for the next driver",
                        task_id,
                        error_msg,
                    )
                    return

                # Genuine failure, or a cancellation that left pending work.
                # Task-level ``failed`` is not in the enum (task_state.py) —
                # this maps to ``blocked``, recoverable via resume_task.
                await block_task(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    event_type="task_blocked",
                    actor=lead_session_id,
                    session_id=lead_session_id,
                    reason=error_msg,
                    payload={
                        "reason": "lead_turn_error",
                        "category": error_category,
                        "pending_subtasks": unresolved,
                    },
                )
                logger.warning(
                    "auto-finalize: task %s -> blocked (lead turn error: %s, category=%s)",
                    task_id,
                    error_msg,
                    error_category,
                )
                return
            if unresolved:
                # Lead stopped with planned work undispatched — surface as blocked
                # (not a hard error, but not done either).
                await block_task(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    event_type="task_blocked",
                    actor=lead_session_id,
                    session_id=lead_session_id,
                    reason=t("task.blockedLeadEndedIncomplete"),
                    payload={"reason": "unresolved_subtasks", "pending_subtasks": unresolved},
                )
                logger.warning(
                    "auto-finalize: task %s -> blocked (unresolved=%s); lead ended without "
                    "finish_task",
                    task_id,
                    unresolved,
                )
                return

            summary = await self._last_assistant_summary(lead_session_id, user_id=user_id) or (
                "(auto-finalized) Lead ended its turn with no pending subtasks; "
                "task closed automatically."
            )
            await self._complete_task(
                db,
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                lead_session_id=lead_session_id,
                summary=summary,
                artifacts=[],
                auto_finalized=True,
            )
            logger.info(
                "auto-finalize: task %s completed (lead natural end, no explicit finish_task)",
                task_id,
            )

    # ------------------------------------------------------------------
    # finalize_actor — the run_actor_loop finally callback
    # ------------------------------------------------------------------

    async def finalize_actor(
        self,
        *,
        session_id: str,
        last_content: str,
        final_status: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        via_shutdown: bool = False,
        user_id: str,
    ) -> None:
        """Finalize a session once its actor loop ends; record member result.

        Each step is independent and best-effort: a slow/failed kernel finalize
        or manifest scan must never prevent the terminal run record from being
        written, otherwise a member is left stuck "active". The terminal write
        is the last and most important step. Concurrent member completions
        (from a finish_task shutdown burst) re-sequence safely inside
        ``append_event`` (retry on the sequence unique-constraint collision).
        """
        from valuz_agent.modules.sessions.run_orchestrator import _finalize_session

        from valuz_agent.adapters.kernel_client import KernelUnavailableError

        # ``interrupted`` is a loop-local status (user cancelled the turn) —
        # not a persistable kernel status. The session is idle and resumable;
        # the kernel already stamped the cancellation stop_reason itself.
        kernel_status = "idle" if final_status == "interrupted" else final_status
        try:
            await _finalize_session(session_id, last_content, kernel_status)
        except KernelUnavailableError:
            # The backend is shutting down — the kernel store is already torn
            # down (the actor loop was cancelled mid-flight and runs this
            # finalize in its ``finally``). Finalize is pointless now: the
            # session is left ``running`` and ``recover_running_sessions`` /
            # ``recover_active_tasks`` reconcile it on the next boot. Skip
            # quietly instead of spamming a "Dependencies not initialized"
            # traceback for every in-flight session at shutdown.
            logger.debug(
                "finalize_actor: kernel unavailable (shutdown) — deferring "
                "finalize of %s to boot recovery",
                session_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("finalize_actor: finalize failed for %s", session_id)

        if role == "lead":
            # A ``shutdown``-triggered exit (pause / stop / finish_task
            # broadcast) is externally managed: stop_task already set the task
            # paused/stopped, finish_task set it terminal. Running auto-finalize
            # here would race a concurrent resume (a rapid pause→resume flips the
            # task back to ``active`` before this old loop's finalize runs, and
            # auto-finalize then wrongly ``blocked``s the freshly-resumed task).
            # Only NATURAL exits (idle-TTL / end_turn / terminal status) should
            # auto-close the task.
            if via_shutdown:
                return
            # Host-side terminal fallback: a lead loop can end (goal auto-exit
            # to default, idle-TTL, normal end_turn) WITHOUT the model calling
            # finish_task — common when a goal-mode lead satisfies a simple goal
            # inline. finish_task is the only thing that closes the task, so
            # without this the task is orphaned "active" forever (see the live
            # 美湖/news-reporter case). Close it here based on the plan state.
            await self._auto_finalize_lead_task(
                lead_session_id=session_id,
                task_id=task_id,
                project_id=project_id,
                final_status=final_status,
                user_id=user_id,
            )
            return

        # Drop from the live-member set and write the terminal run record.
        self._members.discard_member(task_id, session_id)
        since = self._members.pop_dispatch_started(session_id)

        # User-cancelled turn (conversation-page stop / kernel interrupt) — the
        # user-stop path, NOT the failure path. Converges with ``stop_member``:
        # run → rejected, node → rework (a "user stopped" note, still
        # re-dispatchable), a ``subtask_stopped`` timeline event, and exactly
        # one ``member_done(cancelled)`` so the lead never hangs in
        # ``await_members``. When the run is no longer ``active`` the outcome
        # was already recorded by ``stop_member`` (→rejected, lead notified) or
        # ``stop_task`` (→paused, task halted) — leave it untouched so we don't
        # overwrite a parked run with a terminal state.
        if final_status == "interrupted":
            try:
                await self._finalize_interrupted_member(
                    session_id=session_id,
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "finalize_actor: interrupted-member finalize failed for %s", session_id
                )
            return

        try:
            async with async_unit_of_work() as db:
                run_ds = TaskSessionDatastore(db)
                event_ds = TaskEventDatastore(db)
                run = await run_ds.get_run(session_id)
                if run is not None and run.status != "active":
                    # stop_member (→rejected) or stop_task (→paused) already
                    # recorded this run's outcome; the loop exit must not
                    # overwrite it — same rule as _finalize_interrupted_member.
                    logger.debug(
                        "finalize_actor: run %s already settled (%s) — skipping",
                        session_id,
                        run.status,
                    )
                    return
                run_dir = Path(run.run_dir) if run and run.run_dir else Path()
                agent_slug = run.agent_slug if run else ""

                # Manifest is best-effort — never let it block the terminal write.
                manifest = await collect_manifest_safe(
                    session_id,
                    run_dir,
                    final_status,
                    agent_slug=agent_slug,
                    since_epoch=since,
                    user_id=user_id,
                )

                ok = final_status not in ("terminated", "error")
                settled = await run_ds.settle_run_if_active(
                    session_id,
                    status="completed" if ok else "archived",
                    result_manifest=manifest,
                    ended_at=now_ms(),
                )
                if run is not None and not settled:
                    # Parked between our read and the write — the same rule as
                    # the early return, enforced at the row this time.
                    return
                # The loop ending is NOT subtask completion — the lead decides
                # that via review_subtask; only terminal FAILURE is surfaced
                # here. The node goes to ``rework``, never ``failed``: a dead
                # member run is recoverable, and ``failed`` would strand it
                # (non-dispatchable), so a blocked→resume could never relaunch
                # it. The error rides along as ``review_feedback``.
                if not ok:
                    key = run.subtask_key if run else None
                    if key:
                        task_ds = TaskDatastore(db)
                        task_row = await task_ds.get_task_by_project(
                            user_id, project_id, task_id
                        )
                        if task_row is not None:
                            feedback = manifest.get("summary") or t("task.reworkRunErrored")

                            def _park(p: TaskPlan, *, _key: str = key or "") -> bool:
                                n = p.get(_key)
                                if n is None or n.status not in (
                                    "in_progress", "in_review", "rework", "paused"
                                ):
                                    return False
                                p.update_node(_key, status="rework", review_feedback=feedback)
                                return True

                            await planning.persist_plan_best_effort(
                                task_ds,
                                event_ds,
                                task_row,
                                mutate=_park,
                                actor=agent_slug,
                                session_id=session_id,
                                user_id=user_id,
                                diverges=f"node {key!r} not parked to rework after "
                                "its member run errored",
                            )
                    agent_name = await resolve_agent_display_name(
                        project_id, agent_slug, user_id
                    )
                    await record_subtask_failed(
                        event_ds,
                        user_id=user_id,
                        project_id=project_id,
                        task_id=task_id,
                        session_id=session_id,
                        agent_slug=agent_slug,
                        agent_name=agent_name,
                        subtask_key=key,
                        summary=str(manifest.get("summary") or ""),
                        reason="run_error",
                        artifacts=list(manifest.get("artifacts") or []),
                    )
        except Exception:  # noqa: BLE001
            logger.exception("finalize_actor: failed to record terminal run for %s", session_id)

    async def _finalize_interrupted_member(
        self,
        *,
        session_id: str,
        task_id: str,
        project_id: str,
        user_id: str,
    ) -> None:
        """Record a user-interrupted member run — converges with ``stop_member``.

        Only acts on a still-``active`` run: ``stop_member`` (run→rejected +
        lead already notified) and ``stop_task`` (run→paused, resumable) have
        both recorded their outcome before this loop-exit callback fires, and
        overwriting theirs would either double-notify the lead or destroy a
        parked run's resumability.
        """

        lead_session_id = ""
        key: str | None = None
        agent_slug = ""
        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task_ds = TaskDatastore(db)
            run = await run_ds.get_run(session_id)
            if run is None or run.status != "active":
                return
            lead_session_id = run.dispatched_by or ""
            key = run.subtask_key
            agent_slug = run.agent_slug
            await run_ds.update_run_by_session(
                session_id=session_id, status="rejected", ended_at=now_ms()
            )
            if key:
                task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
                if task_row is not None:

                    def _park(p: TaskPlan, *, _key: str = key or "") -> bool:
                        n = p.get(_key)
                        if n is None or n.status not in (
                            "in_progress", "in_review", "rework", "paused"
                        ):
                            return False
                        p.update_node(
                            _key,
                            status="rework",
                            review_feedback=t("task.reworkUserInterrupted"),
                        )
                        return True

                    await planning.persist_plan_best_effort(
                        task_ds,
                        event_ds,
                        task_row,
                        mutate=_park,
                        actor="user",
                        session_id=session_id,
                        user_id=user_id,
                        diverges=f"node {key!r} not parked to rework after the user "
                        "interrupted its member",
                    )
            agent_name = await resolve_agent_display_name(project_id, agent_slug, user_id)
            await record_subtask_stopped(
                event_ds,
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                agent_slug=agent_slug,
                agent_name=agent_name,
                subtask_key=key,
            )
            # Inside the same transaction as parking the node and recording the
            # stop: a lead told its member was cancelled while the plan still
            # says otherwise (or the reverse) is a task that cannot be reasoned
            # about from the outside.
            await mailbox_store.cancel_pending(db, session_id=session_id)
            if lead_session_id:
                await mailbox_store.enqueue(
                    db,
                    session_id=lead_session_id,
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                    kind="member_done",
                    from_session=session_id,
                    origin="member-interrupted",
                    payload={
                        "agent": agent_slug,
                        "status": "cancelled",
                        "summary": t("task.reworkUserInterrupted"),
                        "artifacts": [],
                    },
                )

    # ------------------------------------------------------------------
    # finish_task
    # ------------------------------------------------------------------

    async def finish_task(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str] | None = None,
        status: str = "completed",
        force: bool = False,
        user_id: str,
    ) -> dict[str, Any]:
        """Close the task: terminal event + status + lead-mode reset + shutdown.

        ``status`` is ``completed`` or ``stopped``; ``failed`` is rejected
        loudly (task-level failure is ``blocked``, written by recovery /
        auto-finalize — see task_state.py).

        Two guards:
        * completed → REJECTED while ``TaskPlan.unresolved_keys()`` is
          non-empty (paused included), or the lead could skip planned work and
          still mark the task done.
        * stopped → REJECTED while members are live unless ``force=True`` — a
          member deep in a long build looks exactly like a hang from the
          lead's side, and killing the task is almost never right.
        """
        if status not in ("completed", "stopped"):
            return {
                "ok": False,
                "error": (
                    f"finish_task: invalid status={status!r}. Allowed: "
                    "'completed' (goal achieved) or 'stopped' (user-requested "
                    "terminate / goal unreachable). Task-level 'failed' is no "
                    "longer accepted — use 'stopped' instead."
                ),
                "status": "rejected",
            }
        final_status = "stopped" if status == "stopped" else "completed"
        event_type = "task_stopped" if final_status == "stopped" else "task_completed"

        # Live-member guard: don't let a lead kill the task while members are
        # mid-flight (the observed failure mode: a member deep in a long build
        # is indistinguishable from a hang, the lead "tries a few times" and
        # stops the whole task). Name the live subtasks so the lead can check
        # or stop them individually; ``force=True`` overrides deliberately.
        if final_status == "stopped" and not force and self._members.has_live_members(task_id):
            async with async_unit_of_work(commit=False) as db:
                live_keys = sorted(
                    r.subtask_key
                    for r in await TaskSessionDatastore(db).list_runs(user_id, task_id)
                    if r.kind == "subtask" and r.subtask_key and r.status == "active"
                )
            return {
                "ok": False,
                "error": (
                    "finish_task(stopped) rejected: members are still running "
                    f"(subtasks {live_keys or '<unknown>'}). A silent member is "
                    "usually still working (e.g. a long build), not dead — call "
                    "await_members to see each pending member's live status, or "
                    "stop_subtask the ones you no longer need. If you have "
                    "deliberately decided to terminate the task anyway, call "
                    "finish_task again with force=true."
                ),
                "live_subtasks": live_keys,
                "status": "rejected",
            }

        rejected: dict[str, Any] | None = None
        finished_task_row: TaskRow | None = None
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)

            # Fetched for the plan guard below AND for the task-worktree
            # teardown after the terminal writes commit.
            finished_task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)

            # No row = nothing to close. This used to be checked only INSIDE
            # the completeness guard below, so a wrong/stale task_id skipped
            # the guard entirely and still wrote a ``task_completed`` event for
            # a task that does not exist.
            if finished_task_row is None:
                return {
                    "ok": False,
                    "error": f"finish_task: task {task_id!r} not found",
                    "status": "rejected",
                }

            # Guard: don't let a "completed" finish leave planned work behind.
            if final_status == "completed":
                # Shared predicate (TaskPlan.unresolved_keys) — includes
                # ``paused``, so a node parked by a pause/stop that was never
                # re-dispatched still blocks a ``completed`` close.
                unresolved = TaskPlan.from_dict(finished_task_row.plan).unresolved_keys()
                if unresolved:
                    rejected = {
                        "error": (
                            "finish_task rejected: the plan still has "
                            f"unresolved subtasks {unresolved}. Dispatch and "
                            "review them first (a dependent node like a final "
                            "summary becomes ready once its deps are done), or "
                            "re-scope them with modify_plan(update=[...]), or call finish_task "
                            "with status='stopped' to terminate the task."
                        ),
                        "pending_subtasks": unresolved,
                        "status": "rejected",
                    }

            if rejected is None:
                # Mark lead run as completed
                if final_status == "completed":
                    await self._complete_task(
                        db,
                        user_id=user_id,
                        project_id=project_id,
                        task_id=task_id,
                        lead_session_id=lead_session_id,
                        summary=summary,
                        artifacts=artifacts or [],
                    )
                else:  # stopped — same settle, different terminal event
                    await TaskSessionDatastore(db).update_run_by_session(
                        session_id=lead_session_id,
                        status="completed",
                        ended_at=now_ms(),
                    )
                    await finalize_task(
                        db,
                        user_id=user_id,
                        project_id=project_id,
                        task_id=task_id,
                        status=final_status,
                        event_type=event_type,
                        actor=lead_session_id,
                        session_id=lead_session_id,
                        payload={"summary": summary, "artifacts": artifacts or []},
                    )

        if rejected is not None:
            return rejected

        # Session-modes reconciliation (task-goal-mode.md §Key decisions):
        # ``finish_task`` is the authoritative terminal. Force the lead
        # session's mode back to ``default`` so the kernel's goal evaluator
        # cannot keep (or re-enter) the auto-loop after the task is closed,
        # and so a re-opened conversation on this session isn't stuck in
        # goal mode. Best-effort — a missing session is not fatal here.
        try:
            lead_sess = await kernel_client.get_session(user_id, lead_session_id)
            if lead_sess is not None and getattr(lead_sess, "mode", "default") != "default":
                await kernel_client.set_mode(user_id, lead_session_id, "default")
        except Exception:  # noqa: BLE001 — terminal bookkeeping, never block close
            logger.warning(
                "finish_task: could not reset lead session %s mode to default",
                lead_session_id,
                exc_info=True,
            )

        # Park any member still marked active. A member reads its OWN run row
        # to know it was stopped, and ``finish_task`` settles only the lead's —
        # so without this, ``finish_task(stopped, force=True)``, the one path
        # that ends a task while members are deliberately still running, would
        # leave them running until their idle TTL.
        #
        # It used to be a ``shutdown`` queued per member, which reached only the
        # ones sharing this process. Writing the state reaches all of them.
        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            for run in await run_ds.list_runs(user_id, task_id):
                if run.kind == "subtask" and run.status == "active":
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status="paused", ended_at=now_ms()
                    )
                    await mailbox_store.cancel_pending(db, session_id=run.session_id)

        # The task is terminal now, which is what the lead's own loop reads to
        # know it is done. This only drops the lead's in-process tracking.
        self._coordination.stop_tracking_members(task_id)

        # Task-worktree teardown (design §5): drop the task's worktree iff
        # it holds no work worth keeping (fail-closed inside). Work left
        # behind surfaces in the project's Worktrees panel instead.
        if finished_task_row is not None:
            from valuz_agent.modules.tasks.task_worktree import (
                cleanup_task_worktree_if_clean,
            )

            await cleanup_task_worktree_if_clean(finished_task_row)

        return {"ok": True, "status": final_status}

    # ------------------------------------------------------------------
    # update_deliverable — refresh the deliverable card after the task is
    # completed (post-completion follow-up chat). Append-only: emits a
    # ``deliverable_updated`` event the detail page reads as the latest
    # deliverable, without mutating the original ``task_completed`` event.
    # Does NOT touch task status / plan / runs — the task stays completed.
    # ------------------------------------------------------------------

    async def update_deliverable(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str] | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Refresh the deliverable card on a completed task (follow-up chat).

        Append-only: emits a ``deliverable_updated`` event carrying the latest
        ``summary`` / ``artifacts`` without mutating the original
        ``task_completed`` event or the task's status / plan / runs — the task
        stays ``completed``. The caller is the lead session. Returns a result
        dict (``status`` ``"updated"`` or ``"rejected"``) rather than raising,
        mirroring the sibling ``finish_task`` tool-facing method.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task_row is None:
                return {
                    "ok": False,
                    "error": f"update_deliverable: task {task_id!r} not found",
                    "status": "rejected",
                }
            if task_row.status != "completed":
                return {
                    "ok": False,
                    "error": (
                        "update_deliverable: task is "
                        f"{task_row.status!r}; only a 'completed' task can "
                        "refresh its deliverable card."
                    ),
                    "status": "rejected",
                }

            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="deliverable_updated",
                actor=lead_session_id,
                session_id=lead_session_id,
                payload={"summary": summary, "artifacts": artifacts or []},
            )

        return {"ok": True, "status": "updated"}


__all__ = ["FinalizationService"]
