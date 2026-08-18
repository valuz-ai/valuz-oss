"""LifecycleService — task AUTHORING: kickoff · draft · commit · abandon.

The other half of the old single class — everything that ENDS a task
(finish_task, update_deliverable, the actor-loop finalize callbacks) — lives
in :mod:`~valuz_agent.modules.tasks.finalization` (``FinalizationService``,
the runner's concrete ``ActorFinalizer``). Split along that protocol seam so
authoring changes cannot disturb the terminal invariants.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any
from uuid import uuid4

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks import launcher
from valuz_agent.modules.tasks.task_worktree import (
    resolve_task_cwd,
    task_worktree_notice,
    task_worktree_snapshot,
)
from valuz_agent.modules.tasks.resolution import (
    task_session_resolver,
)
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.events import (
    block_task,
)
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.provenance import resolve_trigger_provenance

logger = logging.getLogger(__name__)


# Strong references to the detached startup coroutines. asyncio keeps only a
# WEAK one to a running task, so a fire-and-forget ``create_task`` whose handle
# nobody holds can be collected mid-flight — and the flight here is a ~17s
# sandbox provision. The set is that reference; the done callback empties it.
_detached: set[asyncio.Task[None]] = set()


def _run_detached(coro: Coroutine[Any, Any, None], *, name: str) -> None:
    """Run *coro* behind the response, keeping it alive until it finishes."""
    task = asyncio.create_task(coro, name=name)
    _detached.add(task)
    task.add_done_callback(_detached.discard)


class LifecycleService:
    """Task authoring — kickoff / draft / commit / abandon.

    Built once at the composition root with the ActorRunner and
    CoordinationService (terminal writes live in FinalizationService).
    """

    def __init__(
        self,
        *,
        actor_runner: ActorRunner,
        coordination: CoordinationService,
    ) -> None:
        self._actor = actor_runner
        self._coordination = coordination

    # ------------------------------------------------------------------
    # kickoff
    # ------------------------------------------------------------------

    async def kickoff(
        self,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        *,
        refs: list[str] | None = None,
        created_by: str = "user",
        title: str | None = None,
        originating_session_id: str | None = None,
        trigger_type: str | None = None,
        trigger_automation_id: str | None = None,
        worktree: bool = False,
        user_id: str,
    ) -> TaskRow:
        """Register a task, then start its lead session behind the response.

        Returns as soon as the task exists and every host-side check has
        passed; ``_start_lead`` provisions the sandbox, creates the kernel
        session and drives the lead as a persistent actor (``run_actor_loop``:
        it ends a turn and is re-woken by ``member_done`` / ``send`` until
        ``finish_task``). Returns the newly created TaskRow — which is
        addressable immediately, before its lead is up.

        An over-long ``goal`` is no longer rejected: the lead brief is *spilled*
        to a doc and the lead receives a short pointer to read (see
        ``spill_goal_brief_if_too_long``), so a long goal never crashes the
        ``/goal`` payload mid-turn.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)

            # Resolve the project env (row + main cwd + instructions) through
            # the single host-knowledge seam (tasks/resolution.py).
            env = await task_session_resolver.resolve_project_env(
                db, user_id=user_id, project_id=project_id
            )
            if env is None:
                raise ValueError(f"project {project_id!r} not found")
            project_cwd = env.project_cwd

            # Create the task narrative file path (file-as-truth). Always
            # anchored at the MAIN project cwd — even in worktree mode —
            # so coordination files never dirty the task worktree and the
            # clean-teardown check stays meaningful (design §5/R5).
            slug = lead_agent_slug.replace("/", "-")[:32]
            task_id = uuid4().hex
            file_path = str(fs_registry.task_path(project_cwd, task_id, slug))

            # v2.1: the lead runs in the SHARED project cwd (same as members,
            # see _member_run_dir) so it reads/writes project files natively.
            # Task-level worktree (design §5) keeps that shared-cwd shape and
            # just relocates it: ONE worktree per task, lead + every member
            # share its cwd — no per-member isolation on top.
            lead_cwd = str(project_cwd)
            wt_snapshot: dict[str, object] | None = None
            if worktree:
                from valuz_agent.modules.worktrees.service import worktree_service

                handle = await worktree_service.get_or_create(
                    user_id,
                    env.project_row,
                    name=f"task-{task_id[:12]}",
                    origin="task",
                )
                lead_cwd = handle.session_cwd
                wt_snapshot = {
                    "name": handle.name,
                    "branch": handle.branch,
                    "path": handle.path,
                    "git_root": handle.git_root,
                    "base_sha": handle.base_sha,
                    # The projected cwd (worktree + project subdir) every
                    # session of this task runs in — dispatch reads this.
                    "cwd": handle.session_cwd,
                }

            # Classify what spawned this task (user / chat / agent / automation)
            # so the task list can show "由 … 触发" and the reverse "spawned by"
            # query has indexed source ids.
            prov = await resolve_trigger_provenance(
                db,
                originating_session_id=originating_session_id,
                trigger_type=trigger_type,
                trigger_automation_id=trigger_automation_id,
            )

            # Persist TaskRow
            task_title = title or goal[:100]
            task_row = TaskRow(
                id=task_id,
                project_id=project_id,
                file_path=file_path,
                title=task_title,
                goal=goal,
                status="active",
                created_by=created_by,
                lead_agent_slug=lead_agent_slug,
                current_holder=lead_agent_slug,
                trigger_type=prov.trigger_type,
                trigger_task_id=prov.trigger_task_id,
                trigger_agent_slug=prov.trigger_agent_slug,
                trigger_automation_id=prov.trigger_automation_id,
                metadata_={
                    "dispatch_mode": "async",
                    # v3: when a project conversation spawns this task via the
                    # ``create_task`` tool, record the originating session so
                    # the task panel / conversation can cross-reference.
                    **(
                        {"originating_session_id": originating_session_id}
                        if originating_session_id
                        else {}
                    ),
                    # Task worktree snapshot (design §5): dispatch relocates
                    # every member into ``worktree.cwd``; finish_task removes
                    # the worktree iff clean.
                    **({"worktree": wt_snapshot} if wt_snapshot else {}),
                },
            )
            await task_ds.create_task(user_id, task_row)

            # Build the lead brief (caller-specific), then resolve the lead
            # session through the single host-knowledge seam — membership,
            # lead clone, brief spill, session build and the credential
            # pre-flight all live in tasks/resolution.py.
            refs_text = "\n".join(f"- {r}" for r in refs) if refs else ""
            # Goal mode (claude_agent/codex) prepends ``/goal `` to this brief
            # via the kernel's wrap_for_mode, so the directive already reads as
            # "/goal <goal>". A redundant ``## Task Goal`` header would land
            # inside the goal condition — drop it; refs stay as trailing
            # context. (deepagents fallback sends the brief unwrapped, where a
            # bare goal + refs is still clear.)
            lead_brief = goal + (f"\n\n## References\n\n{refs_text}" if refs_text else "")

            resolved = await task_session_resolver.resolve_lead(
                db,
                env=env,
                project_id=project_id,
                task_id=task_id,
                agent_slug=lead_agent_slug,
                cwd=lead_cwd,
                brief=lead_brief,
                user_id=user_id,
                worktree_notice=task_worktree_notice(wt_snapshot),
            )
            if isinstance(resolved, Failure):
                raise ValueError(resolved.reason)
            lead_session = resolved.session
            lead_brief = resolved.brief

            # Fail fast: don't spawn a lead that has no usable credentials —
            # it would only fail mid-turn with a cryptic "Not logged in".
            async def _block_kickoff(reason: str) -> None:
                # ``failed`` is NOT in the task status enum (task_state.py) —
                # task-level failure folds into ``blocked`` (recoverable: the
                # user adds the missing credential, then resume_task rebuilds
                # the lead). The ``kickoff_failed`` event below still records
                # the cause. The old ``"failed"`` write left an out-of-enum,
                # un-resumable status stuck forever.
                await block_task(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    event_type="kickoff_failed",
                    actor=created_by,
                    reason=reason,
                )
                raise ValueError(reason)

            gap = resolved.credential_gap
            if gap is not None:
                await _block_kickoff(gap)

            # Pre-run model-config check over the full roster: the lead can
            # dispatch to ANY member, so an unconfigured member surfaces here
            # as an immediate, actionable kickoff error instead of minutes
            # into the run as a dispatch-time subtask failure.
            member_gaps = await task_session_resolver.preflight_member_providers(
                db, user_id=user_id, project_id=project_id
            )
            if member_gaps:
                await _block_kickoff(
                    "model configuration check failed for project members:\n"
                    + "\n".join(f"- {g}" for g in member_gaps)
                )

        # Everything above is host-side VALIDATION — cheap DB reads, and the
        # source of every 4xx this endpoint can answer with, so it stays in the
        # request. Everything below STARTS the task, and starting it means
        # provisioning the task's sandbox: one task = one scope = a COLD
        # instance every single time (17.2s of a 19.0s kickoff, measured on qa
        # 2026-08-18; reusing a warm scope is ~1s). Holding the response for
        # that left the project composer looking frozen for the whole cold
        # start and then dropped the user into a task that was already
        # mid-turn — so the startup runs BEHIND the response.
        #
        # The caller still gets a real, addressable task: the row above is
        # already committed (datastore writes commit individually —
        # ``async_unit_of_work`` is a session scope, not a transaction), so the
        # UI can navigate to its detail page immediately and watch the lead
        # come up there. Until it does the task is ``active`` with no runs and
        # no events, which every reader already tolerates: the health monitor
        # explicitly stands down on a task whose lead run is absent
        # (``TaskHealthMonitor.sweep_once``), and the detail page polls.
        _run_detached(
            self._start_lead(
                lead_session=lead_session,
                brief=lead_brief,
                task_id=task_id,
                project_id=project_id,
                lead_agent_slug=lead_agent_slug,
                goal=goal,
                lead_cwd=lead_cwd,
                created_by=created_by,
                user_id=user_id,
            ),
            name=f"task-kickoff-{task_id}",
        )

        return task_row

    async def _start_lead(
        self,
        *,
        lead_session: Any,
        brief: str,
        task_id: str,
        project_id: str,
        lead_agent_slug: str,
        goal: str,
        lead_cwd: str,
        created_by: str,
        user_id: str,
    ) -> None:
        """Bring a registered task's lead up — the slow half of ``kickoff``.

        Runs detached, after the response. Creates the kernel session under the
        task's sandbox scope (the cold provision), records the lead run, writes
        the ``kickoff`` event, and finally drives the lead as a persistent
        actor: it ends a turn and is re-woken by ``member_done`` / ``send``
        until ``finish_task`` (the actor loop's finalize callback auto-closes a
        lead that ends without an explicit finish).

        A failure here can no longer become an HTTP error, so it becomes the
        state the user can act on: ``blocked`` + a ``kickoff_failed`` event
        carrying the reason (same landing as the pre-flight gaps above), which
        ``resume_task`` can retry once the cause is fixed. That is strictly
        more recoverable than the old in-request behaviour, where a raise here
        returned a 500 and left the already-committed task ``active`` with no
        lead and nothing to explain it.
        """
        try:
            await launcher.create_task_session(
                user_id,
                lead_session,
                task_id=task_id,
                project_id=project_id,
                kind="task_lead",
            )

            async with async_unit_of_work() as db:
                # The task is visible and actionable during this window now, so
                # the user can stop it before its lead ever exists —
                # ``stop_task`` accepts a task with no lead run and flips it
                # right here. Re-read before committing to the spawn: starting
                # a lead onto a task the user just stopped is the one way this
                # split can produce work nobody asked for.
                task_row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
                if task_row is None or task_row.status != "active":
                    logger.info(
                        "task %s: no longer active (%s) — standing down before the lead spawn",
                        task_id,
                        None if task_row is None else task_row.status,
                    )
                    return

                # Record the lead run in valuz_task_session
                lead_run = TaskSessionRow(
                    project_id=project_id,
                    task_id=task_id,
                    session_id=lead_session.id,
                    agent_slug=lead_agent_slug,
                    sequence=0,
                    kind="lead",
                    status="active",
                    label="Kickoff",
                    goal=goal,
                    project_mode="shared",
                    run_dir=lead_cwd,
                )
                await TaskSessionDatastore(db).create_run(user_id, lead_run)

                # Append kickoff event
                await TaskEventDatastore(db).append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type="kickoff",
                    actor=created_by,
                    session_id=lead_session.id,
                    payload={"goal": goal, "lead_agent_slug": lead_agent_slug},
                )
        except Exception as exc:  # noqa: BLE001 — nothing above us can report it
            logger.exception("task %s: lead startup failed", task_id)
            try:
                async with async_unit_of_work() as db:
                    await block_task(
                        db,
                        user_id=user_id,
                        project_id=project_id,
                        task_id=task_id,
                        event_type="kickoff_failed",
                        actor=created_by,
                        reason=f"lead startup failed: {exc}",
                    )
            except Exception:  # noqa: BLE001 — the log above is the last word
                logger.exception(
                    "task %s: could not record the failed lead startup", task_id
                )
            return

        launcher.spawn_actor(
            self._actor,
            session_id=lead_session.id,
            prompt=brief,
            role="lead",
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Chat-plan-then-execute (VALUZ-CHATPLAN) — Slice 2
    # ------------------------------------------------------------------

    async def draft_task(
        self,
        *,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        originating_session_id: str,
        refs: list[str] | None = None,
        title: str | None = None,
        user_id: str,
    ) -> TaskRow:
        """Create a task in ``draft`` status without starting a lead session.

        The originating chat session is recorded in ``metadata.originating_session_id``
        and becomes the plan-writer holder until ``commit_task`` flips control
        to the lead. ``plan_version`` starts at 0; the chat session is expected
        to follow up with ``plan_task`` (lifting plan_version to 1) before
        committing.

        Raises ``ValueError`` if the project doesn't exist or the agent isn't
        a member of it (same validations as ``kickoff``). An over-long ``goal``
        is accepted as-is: the draft only stores the goal text — when
        ``commit_task`` builds the lead session it spills an over-cap brief to a
        doc and passes a pointer (see ``spill_goal_brief_if_too_long``).
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            env = await task_session_resolver.resolve_project_env(
                db, user_id=user_id, project_id=project_id
            )
            if env is None:
                raise ValueError(f"project {project_id!r} not found")
            if not await task_session_resolver.member_exists(
                db, user_id=user_id, project_id=project_id, agent_slug=lead_agent_slug
            ):
                raise ValueError(
                    f"lead agent {lead_agent_slug!r} is not a member of project {project_id!r}"
                )
            project_cwd = env.project_cwd

            slug = lead_agent_slug.replace("/", "-")[:32]
            task_id = uuid4().hex
            file_path = str(fs_registry.task_path(project_cwd, task_id, slug))
            task_title = title or goal[:100]

            metadata: dict[str, Any] = {
                "originating_session_id": originating_session_id,
                "dispatch_mode": "async",  # commit_task always uses async
            }
            if refs:
                metadata["refs"] = list(refs)

            # Classify the trigger source (chat, or agent when drafted from
            # within another task) so the task list shows "由 … 触发" even for
            # drafts that haven't been committed yet.
            prov = await resolve_trigger_provenance(
                db, originating_session_id=originating_session_id
            )

            task_row = TaskRow(
                id=task_id,
                project_id=project_id,
                file_path=file_path,
                title=task_title,
                goal=goal,
                status="draft",
                created_by="user",
                lead_agent_slug=lead_agent_slug,
                # Draft-period holder = originating chat (logically); we still
                # record the lead agent slug for UI clarity. The actual plan
                # writer gate uses metadata.originating_session_id +
                # project match (see tools/gate.check_plan_writer_gate).
                current_holder=lead_agent_slug,
                trigger_type=prov.trigger_type,
                trigger_task_id=prov.trigger_task_id,
                trigger_agent_slug=prov.trigger_agent_slug,
                trigger_automation_id=prov.trigger_automation_id,
                metadata_=metadata,
                plan_version=0,
                committed_at=None,
            )
            await task_ds.create_task(user_id, task_row)

            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="task_drafted",
                actor=originating_session_id,
                session_id=None,
                payload={
                    "goal": goal,
                    "lead_agent_slug": lead_agent_slug,
                    "refs": list(refs or []),
                },
            )
            return task_row

    async def commit_task(
        self,
        *,
        task_id: str,
        project_id: str,
        caller_session_id: str,
        lead_agent_slug_override: str | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Transition a draft task to active by spawning its lead session.

        NOT atomic: the three DB writes (lead run row → status flip →
        ``committed`` event) each commit on their own — repo-wide datastore
        convention, so ``async_unit_of_work`` is a session scope, not a
        transaction. What is guaranteed is ordering + recovery: the actor
        spawns after the writes, and a failed spawn is NOT rolled back
        (``active → draft`` is illegal) — the health monitor sees a lead with
        no live mailbox and marks the task ``blocked`` for the user to resume.

        The new lead session gets the ``plan_pre_committed=True`` brief
        variant which tells it to skip ``plan_task`` and dispatch directly
        against the already-laid-down plan.

        Returns ``{lead_session_id, status: "active", committed_at}`` on success,
        ``{"error": ...}`` on validation failure.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)

            task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task_row is None:
                return {"error": f"task {task_id!r} not found"}
            if task_row.status != "draft":
                return {
                    "error": (
                        f"commit_task: task is in {task_row.status!r}, only draft tasks "
                        "can be committed"
                    )
                }
            plan = TaskPlan.from_dict(task_row.plan)
            if plan.is_empty:
                return {"error": "commit_task: plan is empty — call plan_task first to lay it down"}
            if plan.all_done():
                return {"error": "commit_task: plan has no work to do (all nodes already done)"}

            lead_slug = lead_agent_slug_override or task_row.lead_agent_slug

            env = await task_session_resolver.resolve_project_env(
                db, user_id=user_id, project_id=project_id
            )
            if env is None:
                return {"error": f"project {project_id!r} not found"}
            # Task-level worktree (design §5): a task carrying a worktree
            # snapshot keeps every session — including this committed lead —
            # in the worktree cwd (heals it first if it was removed).
            lead_cwd = await resolve_task_cwd(task_row, str(env.project_cwd))

            refs = (task_row.metadata_ or {}).get("refs") or []
            refs_text = "\n".join(f"- {r}" for r in refs) if refs else ""
            # The committed brief points at the existing plan; the playbook
            # (COMMITTED_LEAD_PLAYBOOK) tells the lead not to call plan_task.
            plan_summary_lines = "\n".join(f"- {n.key}: {n.title}" for n in plan.nodes)
            lead_brief = (
                f"{task_row.goal}\n\n"
                f"## Plan Summary (already committed; do not re-plan)\n\n"
                f"{plan_summary_lines}\n"
                + (f"\n## References\n\n{refs_text}\n" if refs_text else "")
            )

            resolved = await task_session_resolver.resolve_lead(
                db,
                env=env,
                project_id=project_id,
                task_id=task_id,
                agent_slug=lead_slug,
                cwd=lead_cwd,
                brief=lead_brief,
                user_id=user_id,
                plan_pre_committed=True,  # ← key flag (VALUZ-CHATPLAN D10)
                worktree_notice=task_worktree_notice(task_worktree_snapshot(task_row)),
            )
            if isinstance(resolved, Failure):
                return {"error": resolved.reason}
            lead_session = resolved.session
            lead_brief = resolved.brief

            if resolved.credential_gap is not None:
                return {"error": f"commit_task: {resolved.credential_gap}"}

            # Same roster pre-flight as kickoff (see there): catch an
            # unconfigured member before the committed plan starts running.
            member_gaps = await task_session_resolver.preflight_member_providers(
                db, user_id=user_id, project_id=project_id
            )
            if member_gaps:
                return {
                    "error": "commit_task: model configuration check failed "
                    "for project members:\n" + "\n".join(f"- {g}" for g in member_gaps)
                }

            await launcher.create_task_session(
                user_id,
                lead_session,
                task_id=task_id,
                project_id=project_id,
                kind="task_lead",
            )

            # DB writes: create lead run row + flip task status + append event
            lead_run = TaskSessionRow(
                project_id=project_id,
                task_id=task_id,
                session_id=lead_session.id,
                agent_slug=lead_slug,
                sequence=0,
                kind="lead",
                status="active",
                label="Committed",
                goal=task_row.goal,
                project_mode="shared",
                run_dir=lead_cwd,
            )
            await run_ds.create_run(user_id, lead_run)

            # The draft→active flip IS the commit mutex: the door's UPDATE
            # carries ``status='draft'``, so of two concurrent commits (double
            # click, chat tool + REST) exactly one wins the rowcount. The
            # top-of-function draft guard only covers the sequential case —
            # this closes the window the awaits above (resolve_lead, session
            # creation) opened.
            committed_at = now_ms()
            if not await task_ds.update_task_status(
                user_id, task_id, "active", expect="draft"
            ):
                await run_ds.update_run_by_session(
                    session_id=lead_session.id, status="rejected", ended_at=now_ms()
                )
                return {
                    "error": "commit_task: task was committed or changed concurrently "
                    "— refresh and retry"
                }
            task_row.status = "active"  # mirror the door's write for the merge below
            task_row.committed_at = committed_at
            task_row.current_holder = lead_session.id
            # Stamp the lead session id back into metadata so subsequent
            # tooling (UI, inject) can resolve "the lead" without joining
            # against valuz_task_session.
            md = dict(task_row.metadata_ or {})
            md["lead_session_id"] = lead_session.id
            md["dispatch_mode"] = "async"
            task_row.metadata_ = md
            await task_ds.update_task(task_row)

            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="committed",
                actor=caller_session_id,
                session_id=lead_session.id,
                payload={
                    "lead_session_id": lead_session.id,
                    "plan_version": task_row.plan_version,
                    "plan_summary": plan.counts(),
                },
            )

        # Actor spawn happens AFTER the writes above (which are individually
        # committed — see the docstring; there is no enclosing transaction to
        # roll back). If the spawn fails the task is already ``active``, and
        # the health monitor marks it blocked so the user can resume.

        launcher.spawn_actor(
            self._actor,
            session_id=lead_session.id,
            prompt=lead_brief,
            role="lead",
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
        )

        return {
            "task_id": task_id,
            "lead_session_id": lead_session.id,
            "status": "active",
            "committed_at": committed_at,
        }

    async def abandon_task(
        self,
        *,
        task_id: str,
        project_id: str,
        caller_session_id: str,
        reason: str = "",
        user_id: str,
    ) -> dict[str, Any]:
        """Discard a draft task (status: draft → abandoned).

        Terminal — abandoned tasks cannot be resurrected. Use stop_task
        (intervene) for active tasks; abandon_task is draft-only.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task_row is None:
                return {"error": f"task {task_id!r} not found"}
            if task_row.status != "draft":
                return {
                    "error": (
                        f"abandon_task: task is {task_row.status!r}, only draft tasks "
                        "can be abandoned (use stop_task for active tasks)"
                    )
                }

            # Through the door with expect='draft' — a commit racing this
            # abandon can't interleave into active→abandoned (illegal).
            if not await task_ds.update_task_status(
                user_id, task_id, "abandoned", expect="draft"
            ):
                return {
                    "error": "abandon_task: task changed concurrently — refresh and retry"
                }
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="abandoned",
                actor=caller_session_id,
                session_id=None,
                payload={"reason": reason} if reason else {},
            )
            return {"task_id": task_id, "status": "abandoned"}


__all__ = ["LifecycleService"]
