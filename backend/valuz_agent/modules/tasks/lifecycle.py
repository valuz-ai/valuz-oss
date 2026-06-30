"""LifecycleService — task lifecycle (ADR-023, Step 3c).

Peeled verbatim out of ``TaskOrchestrator``. Owns the task lifecycle surface:

  * :meth:`kickoff` — create TaskRow + lead session; async-mode spawns the lead
    actor loop, sync-mode runs the lead one turn to idle then auto-finalizes.
  * :meth:`draft_task` — create a ``draft`` task without a lead session.
  * :meth:`commit_task` — flip a draft to ``active`` by spawning its lead.
  * :meth:`abandon_task` — discard a draft task.
  * :meth:`finish_task` — the authoritative terminal: append the terminal
    event, set task status, reset the lead session mode, then broadcast
    shutdown to members + the lead.
  * :meth:`_finalize_actor` — the ``run_actor_loop`` ``finally`` callback;
    lead → auto-finalize, subtask → discard + terminal run write.
  * :meth:`_auto_finalize_lead_task` — host-side terminal fallback for a lead
    that ends without an explicit ``finish_task``.
  * :meth:`_last_assistant_summary` — best-effort summary helper for the above.
  * :meth:`_materialize_lead_agent` — per-task lead-clone builder.

Collaborators are injected at the composition root:

  * ``registry``     — shared :class:`LiveMemberRegistry`
    (``has_live_members`` / ``discard_member`` / ``pop_dispatch_started``).
  * ``actor_runner`` — shared :class:`ActorRunner` (``kickoff`` / ``commit_task``
    spawn ``run_actor_loop``; ``kickoff`` sync-mode runs ``run_session_to_idle``).
  * ``coordination`` — :class:`CoordinationService` (``finish_task`` calls
    ``_broadcast_shutdown``).

``_finalize_actor`` is the ``ActorRunner.run_actor_loop`` ``finally`` callback,
so the runner is bound to a host that resolves ``_finalize_actor`` back onto
this service (via the orchestrator's thin delegator). The orchestrator keeps
delegators for the whole public lifecycle surface so its callers + the
actor-loop seams keep resolving on it.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from valuz_agent.adapters import kernel_client
from valuz_agent.modules.sessions import project_index
from valuz_agent.adapters.agent_resolver import (
    _member_agent_config,
    build_member_session,
    embed_agent_config,
    spill_goal_brief_if_too_long,
)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks._session_build import (
    _credential_gap,
    _provider_resolver_deps,
)
from valuz_agent.modules.tasks.actor_runner import (
    ActorRunner,
    collect_manifest,
    run_session_to_idle,
)
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan
from valuz_agent.modules.tasks.provenance import resolve_trigger_provenance

logger = logging.getLogger(__name__)


def _notify_task_memory(task_id: str, user_id: str | None = None) -> None:
    if user_id is None:
        raise ValueError("user_id is required")

    """Fire the task-finish memory extraction (memory-system-design §7.1): graduate
    a completed task's multi-agent lessons + project progress into project memory.
    Best-effort, non-blocking, fully isolated — never affects task finalization."""
    try:
        from valuz_agent.modules.memory.scheduler import task_finish_scheduler

        task_finish_scheduler.notify_finished(task_id, user_id)
    except Exception:  # noqa: BLE001 — never let the memory hook break finalize
        logger.debug("task-finish memory trigger skipped for %s", task_id, exc_info=True)


class LifecycleService:
    """Task lifecycle — kickoff / draft / commit / abandon / finish + the
    actor-loop finalize callbacks and the lead-clone builder.

    Constructed once at the composition root with the shared registry +
    runtime ActorRunner + CoordinationService (+ the event bus the sync-kickoff
    lead drives its turn on); the orchestrator's lifecycle surface delegates
    straight onto it, and the ActorRunner resolves its finalize callback
    (``_finalize_actor``) through the bound host onto this service.
    """

    def __init__(
        self,
        *,
        registry: LiveMemberRegistry,
        actor_runner: ActorRunner,
        coordination: CoordinationService,
        bus: EventBus,
    ) -> None:
        self._members = registry
        self._actor = actor_runner
        self._coordination = coordination
        self._bus = bus

    # ------------------------------------------------------------------
    # kickoff
    # ------------------------------------------------------------------

    async def kickoff(
        self,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        refs: list[str] | None = None,
        created_by: str = "user",
        title: str | None = None,
        dispatch_mode: Literal["sync", "async"] = "async",
        originating_session_id: str | None = None,
        trigger_type: str | None = None,
        trigger_automation_id: str | None = None,
        user_id: str | None = None,
    ) -> TaskRow:
        """Create a task and start its lead session in the background.

        ``dispatch_mode`` selects the dispatch architecture (M10):
          - ``sync`` (v1): lead drives a single turn; ``dispatch`` blocks until
            each member finishes and returns the manifest as the tool_result.
          - ``async`` (v2): lead is a persistent actor; ``dispatch_async``
            starts member actors and returns immediately, members notify the
            lead via the mailbox, and the lead loops until ``finish_task``.

        Returns the newly created TaskRow.

        An over-long ``goal`` is no longer rejected: the lead brief is *spilled*
        to a doc and the lead receives a short pointer to read (see
        ``spill_goal_brief_if_too_long``), so a long goal never crashes the
        ``/goal`` payload mid-turn.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            member_ds = ProjectMemberDatastore(db)

            # Resolve project cwd
            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(user_id, project_id)
            if ws_row is None:
                raise ValueError(f"project {project_id!r} not found")
            project_cwd = fs_registry.project_cwd(
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )

            # Create the task narrative file path (file-as-truth)
            slug = lead_agent_slug.replace("/", "-")[:32]
            task_id = uuid4().hex
            file_path = str(fs_registry.task_path(project_cwd, task_id, slug))

            # v2.1: the lead runs in the SHARED project cwd (same as members,
            # see _member_run_dir) so it reads/writes project files natively.
            # No per-task isolated subdir — that contradicted the shared-cwd
            # decision (M10 附录 D); only repo-worktree opt-in isolates.
            lead_cwd = str(project_cwd)

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
                    "dispatch_mode": dispatch_mode,
                    # v3: when a project conversation spawns this task via the
                    # ``create_task`` tool, record the originating session so
                    # the task panel / conversation can cross-reference.
                    **(
                        {"originating_session_id": originating_session_id}
                        if originating_session_id
                        else {}
                    ),
                },
            )
            await task_ds.create_task(user_id, task_row)

            # Resolve lead agent and materialize a per-task lead clone that
            # carries the dispatch tools (base agent stays clean — see
            # _materialize_lead_agent). The lead session points at the clone.
            lead_member = await member_ds.get(
                user_id, project_id, lead_agent_slug
            )
            if lead_member is None:
                raise ValueError(
                    f"lead agent {lead_agent_slug!r} is not a member of project {project_id!r}"
                )
            lead_agent = await _member_agent_config(lead_member, member_ds, user_id=user_id)
            lead_clone = None
            if lead_agent is not None:
                lead_clone = await self._materialize_lead_agent(
                    lead_agent, dispatch_mode=dispatch_mode
                )

            # Build the lead kernel Session
            refs_text = "\n".join(f"- {r}" for r in refs) if refs else ""
            # Goal mode (claude_agent/codex) prepends ``/goal `` to this brief
            # via the kernel's wrap_for_mode, so the directive already reads as
            # "/goal <goal>". A redundant ``## Task Goal`` header would land
            # inside the goal condition — drop it; refs stay as trailing
            # context. (deepagents fallback sends the brief unwrapped, where a
            # bare goal + refs is still clear.)
            lead_brief = goal + (f"\n\n## References\n\n{refs_text}" if refs_text else "")
            # Fence the goal-mode payload: if the lead brief is over the ``/goal``
            # cap, spill it to a doc and pass the lead a short pointer instead
            # (used both as the embedded brief and the initial ``/goal`` prompt).
            lead_brief = spill_goal_brief_if_too_long(
                lead_brief,
                run_dir=lead_cwd,
                task_id=task_id,
                label=lead_agent_slug,
                is_lead=True,
            )

            # Fetch project instructions for system prompt
            from valuz_agent.modules.projects.datastore import ProjectDatastore as WsDs

            ws_ds2 = WsDs(db)
            ws_ctx = await ws_ds2.get_context(user_id, project_id)
            project_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            lead_session = await build_member_session(
                project_id=project_id,
                agent_slug=lead_agent_slug,
                members=member_ds,
                is_lead=True,
                task_id=task_id,
                run_dir=lead_cwd,
                brief=lead_brief,
                project_name=ws_row.name,
                project_instructions_md=project_instructions_md,
                dispatch_mode=dispatch_mode,
                # Lead runs the whole task in goal mode: the kernel auto-loops
                # until the task goal is met. ``finish_task`` remains the
                # authoritative terminal (it forces mode back to default).
                goal_mode=True,
                user_id=user_id,
                **_provider_resolver_deps(db),
            )
            if lead_session is None:
                raise ValueError(f"could not build lead session for {lead_agent_slug!r}")

            # Swap the embedded snapshot for the per-task lead clone so the
            # runtime surfaces the dispatch tools (build_member_session
            # embedded the base agent; everything else on the session —
            # instructions / skills / model / provider — already came from it).
            if lead_clone is not None:
                lead_session = embed_agent_config(lead_session, lead_clone)

            # Fail fast: don't spawn a lead that has no usable credentials —
            # it would only fail mid-turn with a cryptic "Not logged in".
            gap = await _credential_gap(lead_session, lead_agent_slug, db=db)
            if gap is not None:
                await task_ds.update_task_status(user_id, task_id, "failed")
                await event_ds.append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type="kickoff_failed",
                    actor=created_by,
                    session_id=None,
                    payload={"error": gap},
                )
                raise ValueError(gap)

            await kernel_client.create_session(user_id, lead_session)
            await project_index.record(
                project_id,
                lead_session.id,
                kind="task_lead",
                origin="task",
                user_id=user_id,
            )

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
            await run_ds.create_run(user_id, lead_run)

            # Append kickoff event
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="kickoff",
                actor=created_by,
                session_id=lead_session.id,
                payload={"goal": goal, "lead_agent_slug": lead_agent_slug},
            )

        # Drive the lead session in the background. Both modes share one lead
        # toolset (non-blocking dispatch + await_members + plan/review/finish)
        # and goal mode; they differ only in the session driver:
        #   async (default): persistent actor loop — the lead ends a turn and is
        #              re-woken by member_done / send until finish_task. Robust
        #              for multi-turn / long-running members.
        #   sync (legacy): one turn to idle, then finalize — no re-drive. Fine
        #              for tasks the lead completes within a single goal turn.
        if dispatch_mode == "async":
            from valuz_agent.modules.tasks.mailbox import mailbox_registry

            mailbox_registry.register(lead_session.id)
            asyncio.create_task(
                self._actor.run_actor_loop(
                    session_id=lead_session.id,
                    initial_prompt=lead_brief,
                    role="lead",
                    task_id=task_id,
                    project_id=project_id,
                )
            )
        else:

            async def _drive_sync_lead() -> None:
                # Sync (v1) path: one lead turn to idle; dispatch blocks in-turn.
                # run_session_to_idle finalizes the kernel SESSION but NOT the
                # task — so a sync lead that answers inline (e.g. user-created
                # "你好") never calls finish_task and the task is orphaned
                # "active". Apply the same terminal fallback the async actor
                # loop gets via _finalize_actor.
                final_status = await run_session_to_idle(
                    session_id=lead_session.id,
                    content=lead_brief,
                    event_bus=self._bus,
                    user_id=user_id,
                )
                try:
                    await self._auto_finalize_lead_task(
                        lead_session_id=lead_session.id,
                        task_id=task_id,
                        project_id=project_id,
                        final_status=final_status,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("sync kickoff: auto-finalize failed for task %s", task_id)

            asyncio.create_task(_drive_sync_lead())

        return task_row

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
        user_id: str | None = None,
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
            member_ds = ProjectMemberDatastore(db)

            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(user_id, project_id)
            if ws_row is None:
                raise ValueError(f"project {project_id!r} not found")
            lead_member = await member_ds.get(
                user_id, project_id, lead_agent_slug
            )
            if lead_member is None:
                raise ValueError(
                    f"lead agent {lead_agent_slug!r} is not a member of project {project_id!r}"
                )

            project_cwd = fs_registry.project_cwd(
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )

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
                # project match (see dispatch_mcp._check_plan_writer_gate).
                current_holder=lead_agent_slug,
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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Transition a draft task to active by spawning its lead session.

        Atomicity (D2 — half-atomic): the DB writes (lead session row + task
        status flip + committed event) commit together. If the actor loop fails
        to spawn afterwards we do NOT roll the task back to draft (the state
        machine forbids ``active → draft``); the task stays ``active`` with no
        running actor — the sweeper picks it up and marks it ``blocked`` so the
        user can resume.

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
            member_ds = ProjectMemberDatastore(db)

            task_row = await task_ds.get_task_by_project(
                user_id, project_id, task_id
            )
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
            lead_member = await member_ds.get(user_id, project_id, lead_slug)
            if lead_member is None:
                return {
                    "error": (f"lead agent {lead_slug!r} is not a member of project {project_id!r}")
                }

            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(user_id, project_id)
            if ws_row is None:
                return {"error": f"project {project_id!r} not found"}
            project_cwd = fs_registry.project_cwd(
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )
            lead_cwd = str(project_cwd)

            lead_agent = await _member_agent_config(lead_member, member_ds, user_id=user_id)
            lead_clone = None
            if lead_agent is not None:
                lead_clone = await self._materialize_lead_agent(lead_agent, dispatch_mode="async")

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
            # Fence the goal-mode payload: spill an over-cap committed brief to a
            # doc and hand the lead a short pointer (used as both the embedded
            # brief and the initial ``/goal`` prompt below).
            lead_brief = spill_goal_brief_if_too_long(
                lead_brief,
                run_dir=lead_cwd,
                task_id=task_id,
                label=lead_slug,
                is_lead=True,
            )

            from valuz_agent.modules.projects.datastore import ProjectDatastore as WsDs

            ws_ds2 = WsDs(db)
            ws_ctx = await ws_ds2.get_context(user_id, project_id)
            project_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            lead_session = await build_member_session(
                project_id=project_id,
                agent_slug=lead_slug,
                members=member_ds,
                is_lead=True,
                task_id=task_id,
                run_dir=lead_cwd,
                brief=lead_brief,
                project_name=ws_row.name,
                project_instructions_md=project_instructions_md,
                dispatch_mode="async",
                goal_mode=True,
                plan_pre_committed=True,  # ← key flag (VALUZ-CHATPLAN D10)
                user_id=user_id,
                **_provider_resolver_deps(db),
            )
            if lead_session is None:
                return {"error": f"could not build lead session for {lead_slug!r}"}
            if lead_clone is not None:
                lead_session = embed_agent_config(lead_session, lead_clone)

            gap = await _credential_gap(lead_session, lead_slug, db=db)
            if gap is not None:
                return {"error": f"commit_task: {gap}"}

            await kernel_client.create_session(user_id, lead_session)
            await project_index.record(
                project_id,
                lead_session.id,
                kind="task_lead",
                origin="task",
                user_id=user_id,
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

            committed_at = now_ms()
            task_row.status = "active"
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

        # Half-atomic D2: actor spawn happens AFTER the DB txn. If spawn
        # fails the DB is already in `active` — the sweeper will mark
        # the task blocked so the user can resume.
        from valuz_agent.modules.tasks.mailbox import mailbox_registry

        mailbox_registry.register(lead_session.id)
        asyncio.create_task(
            self._actor.run_actor_loop(
                session_id=lead_session.id,
                initial_prompt=lead_brief,
                role="lead",
                task_id=task_id,
                project_id=project_id,
            )
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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Discard a draft task (status: draft → abandoned).

        Terminal — abandoned tasks cannot be resurrected. Use stop_task
        (intervene) for active tasks; abandon_task is draft-only.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            task_row = await task_ds.get_task_by_project(
                user_id, project_id, task_id
            )
            if task_row is None:
                return {"error": f"task {task_id!r} not found"}
            if task_row.status != "draft":
                return {
                    "error": (
                        f"abandon_task: task is {task_row.status!r}, only draft tasks "
                        "can be abandoned (use stop_task for active tasks)"
                    )
                }

            task_row.status = "abandoned"
            await task_ds.update_task(task_row)
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

    # ------------------------------------------------------------------
    # auto-finalize — host-side terminal fallback
    # ------------------------------------------------------------------

    @staticmethod
    async def _last_assistant_summary(session_id: str, user_id: str | None = None) -> str:
        """Best-effort last assistant-message text, for an auto-finalize summary."""
        try:
            events = await kernel_client.get_events(
                user_id, session_id, limit=200
            )
            for event in reversed(events):
                payload = event.data if hasattr(event, "data") else {}
                if event.type in ("assistant_message", "text_delta", "content_block"):
                    text = payload.get("text") or payload.get("content") or ""
                    if text:
                        return str(text)[:2000]
        except Exception:  # noqa: BLE001
            logger.debug("auto-finalize: summary extract failed for %s", session_id)
        return ""

    async def _auto_finalize_lead_task(
        self,
        *,
        lead_session_id: str,
        task_id: str,
        project_id: str,
        final_status: str,
        user_id: str | None = None,
    ) -> None:
        """Close a task when its lead actor-loop ends without an explicit
        ``finish_task`` call.

        ``finish_task`` is the authoritative terminal, but a goal-mode lead
        often satisfies a simple goal inline (does the work itself), the goal
        evaluator auto-exits to default, and the loop ends at idle-TTL — never
        calling finish_task. Without this the task is orphaned ``active``
        forever. Restores the original §7.3 intent ("lead session naturally
        ends → task_completed"). Disposition:
          - status != active   → no-op (finish_task / stop / intervene won);
          - members in flight   → no-op (defensive; not the terminal moment);
          - turn errored (final_status terminated/error OR session.stop_reason
            is an error — an errored turn can still leave status "idle") →
            ``failed`` (never "completed"!);
          - plan has unresolved nodes (no error) → ``blocked``;
          - else → ``completed`` (summary = lead's last assistant message).
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)

            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status != "active":
                return  # already closed by finish_task / stop / intervene
            if self._members.has_live_members(task_id):
                return  # members still running — not the lead's terminal moment

            try:
                plan = TaskPlan.from_dict(task.plan)
                unresolved = [
                    n.key
                    for n in plan.nodes
                    if n.status in ("planned", "in_progress", "in_review", "rework")
                ]
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
                sess = await kernel_client.get_session(user_id, lead_session_id)
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
                    # User/host-driven cancellation BEFORE any plan node exists —
                    # there's no in-flight or half-done work to protect, so locking
                    # the task forces an unnecessary ``resume_task`` ceremony for
                    # what is effectively still a fresh kickoff. Real bug report
                    # (2026-05-29): an automation-fired lead entered the Claude
                    # Agent SDK's ``EnterPlanMode`` + a nested ``Agent`` that hung;
                    # the SDK cancelled the turn ~3.5 min later
                    # (``category='user_interrupt'``); plan still empty. Keep the
                    # task ``active`` so the next driver (user message → fresh turn;
                    # or the next automation fire) picks it up cleanly. Logged for
                    # the audit trail; no ``task_blocked`` event (nothing pending).
                    logger.warning(
                        "auto-finalize: task %s lead turn cancelled with empty plan "
                        "(%s) — staying active for next driver",
                        task_id,
                        error_msg,
                    )
                    return

                # Genuine failure (API/network/exec error, or a raised exception),
                # OR a cancellation that left pending work. Per task_state.py:
                # task-level ``failed`` is intentionally not in the enum — this
                # maps to ``blocked`` (recoverable; the plan is intact, the user
                # resumes via the detail page's retry/继续 entry → resume_task
                # rebuilds the lead). The ``reason`` payload tags this as a
                # lead-turn-error to distinguish from the unresolved-subtasks
                # blocked case below.
                await task_ds.update_task_status(user_id, task_id, "blocked")
                await event_ds.append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type="task_blocked",
                    actor=lead_session_id,
                    session_id=lead_session_id,
                    payload={
                        "reason": "lead_turn_error",
                        "error": error_msg,
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
                await task_ds.update_task_status(user_id, task_id, "blocked")
                await event_ds.append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type="task_blocked",
                    actor=lead_session_id,
                    session_id=lead_session_id,
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
            await task_ds.update_task_status(user_id, task_id, "completed")
            await run_ds.update_run_by_session(
                session_id=lead_session_id,
                status="completed",
                ended_at=now_ms(),
            )
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="task_completed",
                actor=lead_session_id,
                session_id=lead_session_id,
                payload={"summary": summary, "artifacts": [], "auto_finalized": True},
            )
            logger.info(
                "auto-finalize: task %s completed (lead natural end, no explicit finish_task)",
                task_id,
            )
            _notify_task_memory(task_id, user_id=user_id)

    # ------------------------------------------------------------------
    # _finalize_actor — the run_actor_loop finally callback
    # ------------------------------------------------------------------

    async def _finalize_actor(
        self,
        *,
        session_id: str,
        last_content: str,
        final_status: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        via_shutdown: bool = False,
        user_id: str | None = None,
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

        try:
            await _finalize_session(session_id, last_content, final_status)
        except KernelUnavailableError:
            # The backend is shutting down — the kernel store is already torn
            # down (the actor loop was cancelled mid-flight and runs this
            # finalize in its ``finally``). Finalize is pointless now: the
            # session is left ``running`` and ``recover_running_sessions`` /
            # ``recover_active_tasks`` reconcile it on the next boot. Skip
            # quietly instead of spamming a "Dependencies not initialized"
            # traceback for every in-flight session at shutdown.
            logger.debug(
                "_finalize_actor: kernel unavailable (shutdown) — deferring "
                "finalize of %s to boot recovery",
                session_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("_finalize_actor: finalize failed for %s", session_id)

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
            )
            return

        # Drop from the live-member set and write the terminal run record.
        self._members.discard_member(task_id, session_id)
        since = self._members.pop_dispatch_started(session_id)
        try:
            async with async_unit_of_work() as db:
                run_ds = TaskSessionDatastore(db)
                event_ds = TaskEventDatastore(db)
                run = await run_ds.get_run(session_id)
                run_dir = Path(run.run_dir) if run and run.run_dir else Path()
                agent_slug = run.agent_slug if run else ""

                # Manifest is best-effort — never let it block the terminal write.
                try:
                    manifest = await collect_manifest(
                        session_id, run_dir, final_status, since_epoch=since
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("_finalize_actor: manifest failed for %s", session_id)
                    manifest = {"session_id": session_id, "status": final_status, "summary": ""}
                manifest["agent"] = agent_slug

                ok = final_status not in ("terminated", "error")
                await run_ds.update_run_by_session(
                    session_id=session_id,
                    status="completed" if ok else "archived",
                    result_manifest=manifest,
                    ended_at=now_ms(),
                )
                # Review model (VALUZ-TASK §6.1): the actor loop ending is NOT
                # subtask completion — the lead decides that via review_subtask. We
                # only surface a terminal *failure* (terminated/error) here. The
                # node goes to ``rework`` (NOT ``failed``): a member run dying —
                # including an API/socket drop that Layer-1 stamps as an
                # ``Error(api_error)`` and ``_resolve_turn_status`` elevates to
                # ``terminated`` — is recoverable. ``rework`` is dispatchable by
                # the gate (planned/rework) and is the bucket both a live lead and
                # a resumed lead re-dispatch, matching ``reconcile()`` /
                # ``stop_member``. ``failed`` would strand it (excluded from
                # ready_keys + non-dispatchable), so a blocked→resume could never
                # relaunch it. The error rides along as ``review_feedback`` so the
                # retry brief explains why; the ``subtask_failed`` event below
                # still records the failed attempt on the timeline.
                if not ok:
                    key = run.subtask_key if run else None
                    if key:
                        task_ds = TaskDatastore(db)
                        task_row = await task_ds.get_task_by_project(
                            user_id, project_id, task_id
                        )
                        if task_row is not None:
                            plan = TaskPlan.from_dict(task_row.plan)
                            if plan.get(key) is not None:
                                plan.update_node(
                                    key,
                                    status="rework",
                                    review_feedback=(
                                        manifest.get("summary") or "上次运行因错误中断,请重试。"
                                    ),
                                )
                                task_row.plan = plan.to_dict()
                                await task_ds.update_task(task_row)
                                await planning.emit_plan_update(
                                    event_ds,
                                    project_id=project_id,
                                    task_id=task_id,
                                    plan=plan,
                                    actor=agent_slug,
                                    session_id=session_id,
                                    user_id=user_id,
                                )
                    await event_ds.append_event(
                        user_id,
                        project_id=project_id,
                        task_id=task_id,
                        type="subtask_failed",
                        actor=agent_slug,
                        session_id=session_id,
                        payload={**manifest, **({"subtask_key": key} if key else {})},
                    )
        except Exception:  # noqa: BLE001
            logger.exception("_finalize_actor: failed to record terminal run for %s", session_id)

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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Close the task — append a terminal event and set the task status.

        ``status`` is ``completed`` (goal achieved) or ``stopped`` (user-
        requested terminate or lead-judged unreachable). Emits
        ``task_completed`` / ``task_stopped`` accordingly.

        Note: task-level ``failed`` is intentionally NOT a valid finish status
        — see ``task_state.py``. Hard lead-turn crashes are surfaced as
        ``blocked`` by ``recover_active_tasks`` / auto-finalize, not via this
        path. Any caller still passing ``status='failed'`` is rejected loudly
        rather than silently aliased — fail fast keeps stale prompts visible.

        Plan-completeness guard (v0.14): a ``completed`` finish is REJECTED
        while the plan still has unresolved nodes (planned / in_progress /
        in_review / rework) — otherwise the lead can silently skip a planned
        subtask (e.g. a final aggregation node) and still mark the task done.
        The lead must dispatch+review those nodes first (or drop them via
        modify_plan, or finish with status='stopped' to terminate the task).
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

        rejected: dict[str, Any] | None = None
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)

            # Guard: don't let a "completed" finish leave planned work behind.
            if final_status == "completed":
                task_row = await task_ds.get_task_by_project(
                    user_id, project_id, task_id
                )
                if task_row is not None:
                    plan = TaskPlan.from_dict(task_row.plan)
                    unresolved = [
                        n.key
                        for n in plan.nodes
                        if n.status in ("planned", "in_progress", "in_review", "rework")
                    ]
                    if unresolved:
                        rejected = {
                            "error": (
                                "finish_task rejected: the plan still has "
                                f"unresolved subtasks {unresolved}. Dispatch and "
                                "review them first (a dependent node like a final "
                                "summary becomes ready once its deps are done), or "
                                "drop them with modify_plan, or call finish_task "
                                "with status='failed' to abandon the task."
                            ),
                            "pending_subtasks": unresolved,
                            "status": "rejected",
                        }

            if rejected is None:
                await task_ds.update_task_status(user_id, task_id, final_status)

                # Mark lead run as completed
                await run_ds.update_run_by_session(
                    session_id=lead_session_id,
                    status="completed",
                    ended_at=now_ms(),
                )

                await event_ds.append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type=event_type,
                    actor=lead_session_id,
                    session_id=lead_session_id,
                    payload={
                        "summary": summary,
                        "artifacts": artifacts or [],
                    },
                )

        if rejected is not None:
            return rejected

        # Graduate the finished task's multi-agent lessons into project memory
        # (only on a real completion, not a user-requested 'stopped').
        if final_status == "completed":
            _notify_task_memory(task_id, user_id=user_id)

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

        # v2: tell any still-running members to finalize, and break the lead's
        # own actor loop after this turn (no-op for sync/v1 — no live mailboxes).
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        self._coordination._broadcast_shutdown(task_id)
        mailbox_registry.put(lead_session_id, InboxMsg(kind="shutdown"))
        return {"ok": True, "status": final_status}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _materialize_lead_agent(
        self, base_agent: Any, dispatch_mode: Literal["sync", "async"] = "sync"
    ) -> Any:  # returns the lead-clone AgentConfig
        """Materialize a per-task **lead clone** of *base_agent* and return its id.

        Tools live only on ``AgentConfig`` (the kernel has no per-session tool
        override), so the lead needs an agent that carries the dispatch tools.
        Rather than mutate the shared base agent in place (which would leak
        dispatch tools into every plain conversation that uses the same agent —
        and race with concurrent sessions), we build a dedicated clone whose
        ``id`` is ``{base}__lead__{mode}``. The clone shares the base's identity
        (model/instructions/skills/provider/permission) and adds exactly the
        dispatch toolset for the mode; orchestration/launcher tools are dropped.

        Stable per (base, mode): re-materializing is idempotent (overwrites the
        same row), so clones don't accumulate per task. The lead session points
        its ``agent_id`` at the returned clone id.
        """
        from dataclasses import replace

        # Tool surfaces ride the session's ``harness`` MCP entry now
        # (``build_member_session(is_lead=True)`` points it at the ``lead``
        # toolset of the host toolkit MCP server) — the clone carries no
        # tool declarations of its own. It survives as an identity stamp:
        # the ``__lead__{mode}`` id marks the embedded snapshot as a lead
        # clone for queries/diagnostics.
        clone_id = f"{base_agent.id}__lead__{dispatch_mode}"
        clone = replace(base_agent, id=clone_id, tools=())
        # The clone exists only as the lead session's embedded snapshot —
        # the kernel has no agents table to materialize it into.
        return clone


__all__ = ["LifecycleService"]
