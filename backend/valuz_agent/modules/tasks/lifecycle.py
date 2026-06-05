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

from valuz_agent.adapters import kernel_store, kernel_sync
from valuz_agent.adapters.agent_resolver import build_member_session
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

logger = logging.getLogger(__name__)


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
        workspace_id: str,
        goal: str,
        lead_agent_slug: str,
        refs: list[str] | None = None,
        created_by: str = "user",
        title: str | None = None,
        dispatch_mode: Literal["sync", "async"] = "async",
        originating_session_id: str | None = None,
    ) -> TaskRow:
        """Create a task and start its lead session in the background.

        ``dispatch_mode`` selects the dispatch architecture (M10):
          - ``sync`` (v1): lead drives a single turn; ``dispatch`` blocks until
            each member finishes and returns the manifest as the tool_result.
          - ``async`` (v2): lead is a persistent actor; ``dispatch_async``
            starts member actors and returns immediately, members notify the
            lead via the mailbox, and the lead loops until ``finish_task``.

        Returns the newly created TaskRow.

        Raises ``BriefTooLongError`` (subclass of ValueError) when ``goal``
        exceeds the goal-mode payload cap — surfaced here BEFORE any DB write
        so the user gets a clean error in chat instead of a mid-turn crash.
        """
        # Goal-mode brief cap. ``build_member_session`` enforces this too as
        # defense-in-depth, but checking here means failures surface before
        # we write any rows / spin up sessions.
        from valuz_agent.adapters.agent_resolver import assert_goal_brief_length

        assert_goal_brief_length(goal)

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            member_ds = ProjectMemberDatastore(db)

            # Resolve workspace cwd
            from valuz_agent.modules.projects.datastore import WorkspaceDatastore

            ws_ds = WorkspaceDatastore(db)
            ws_row = await ws_ds.get_by_id(workspace_id)
            if ws_row is None:
                raise ValueError(f"workspace {workspace_id!r} not found")
            project_cwd = fs_registry.workspace_cwd(
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

            # Persist TaskRow
            task_title = title or goal[:100]
            task_row = TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                file_path=file_path,
                title=task_title,
                goal=goal,
                status="active",
                created_by=created_by,
                lead_agent_slug=lead_agent_slug,
                current_holder=lead_agent_slug,
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
            await task_ds.create_task(task_row)

            # Resolve lead agent and materialize a per-task lead clone that
            # carries the dispatch tools (base agent stays clean — see
            # _materialize_lead_agent). The lead session points at the clone.
            lead_member = await member_ds.get(workspace_id, lead_agent_slug)
            if lead_member is None:
                raise ValueError(
                    f"lead agent {lead_agent_slug!r} is not a member of workspace {workspace_id!r}"
                )
            lead_agent = await kernel_store.load_agent(lead_member.kernel_agent_id)
            lead_clone_id: str | None = None
            if lead_agent is not None:
                lead_clone_id = self._materialize_lead_agent(
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

            # Fetch workspace instructions for system prompt
            from valuz_agent.modules.projects.datastore import WorkspaceDatastore as WsDs

            ws_ds2 = WsDs(db)
            ws_ctx = await ws_ds2.get_context(workspace_id)
            workspace_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            lead_session = await build_member_session(
                workspace_id=workspace_id,
                agent_slug=lead_agent_slug,
                members=member_ds,
                is_lead=True,
                task_id=task_id,
                run_dir=lead_cwd,
                brief=lead_brief,
                workspace_name=ws_row.name,
                workspace_instructions_md=workspace_instructions_md,
                dispatch_mode=dispatch_mode,
                # Lead runs the whole task in goal mode: the kernel auto-loops
                # until the task goal is met. ``finish_task`` remains the
                # authoritative terminal (it forces mode back to default).
                goal_mode=True,
                **_provider_resolver_deps(db),
            )
            if lead_session is None:
                raise ValueError(f"could not build lead session for {lead_agent_slug!r}")

            # Point the lead session at the per-task lead clone so the runtime
            # surfaces the dispatch tools (build_member_session set agent_id to
            # the base agent; everything else on the session — instructions /
            # skills / model / provider — already came from the base).
            if lead_clone_id is not None:
                from dataclasses import replace as _replace

                lead_session = _replace(lead_session, agent_id=lead_clone_id)

            # Fail fast: don't spawn a lead that has no usable credentials —
            # it would only fail mid-turn with a cryptic "Not logged in".
            gap = await _credential_gap(lead_session, lead_agent_slug, db=db)
            if gap is not None:
                await task_ds.update_task_status(task_id, "failed")
                await event_ds.append_event(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    type="kickoff_failed",
                    actor=created_by,
                    session_id=None,
                    payload={"error": gap},
                )
                raise ValueError(gap)

            await kernel_store.save_session(lead_session)

            # Record the lead run in valuz_task_session
            lead_run = TaskSessionRow(
                workspace_id=workspace_id,
                task_id=task_id,
                session_id=lead_session.id,
                agent_slug=lead_agent_slug,
                sequence=0,
                kind="lead",
                status="active",
                label="Kickoff",
                goal=goal,
                workspace_mode="shared",
                run_dir=lead_cwd,
            )
            await run_ds.create_run(lead_run)

            # Append kickoff event
            await event_ds.append_event(
                workspace_id=workspace_id,
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
                    workspace_id=workspace_id,
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
                )
                try:
                    await self._auto_finalize_lead_task(
                        lead_session_id=lead_session.id,
                        task_id=task_id,
                        workspace_id=workspace_id,
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
        workspace_id: str,
        goal: str,
        lead_agent_slug: str,
        originating_session_id: str,
        refs: list[str] | None = None,
        title: str | None = None,
    ) -> TaskRow:
        """Create a task in ``draft`` status without starting a lead session.

        The originating chat session is recorded in ``metadata.originating_session_id``
        and becomes the plan-writer holder until ``commit_task`` flips control
        to the lead. ``plan_version`` starts at 0; the chat session is expected
        to follow up with ``plan_task`` (lifting plan_version to 1) before
        committing.

        Raises ``ValueError`` if the workspace doesn't exist or the agent isn't
        a member of it (same validations as ``kickoff``). Raises
        ``BriefTooLongError`` (subclass of ValueError) when ``goal`` exceeds
        the goal-mode payload cap — fails before any DB write so the chat
        user sees a clean error before the draft row is created.
        """
        # Same goal-mode brief cap as ``kickoff``. Catching it here means a
        # draft with an over-long goal never enters the DB at all.
        from valuz_agent.adapters.agent_resolver import assert_goal_brief_length

        assert_goal_brief_length(goal)

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            member_ds = ProjectMemberDatastore(db)

            from valuz_agent.modules.projects.datastore import WorkspaceDatastore

            ws_ds = WorkspaceDatastore(db)
            ws_row = await ws_ds.get_by_id(workspace_id)
            if ws_row is None:
                raise ValueError(f"workspace {workspace_id!r} not found")
            lead_member = await member_ds.get(workspace_id, lead_agent_slug)
            if lead_member is None:
                raise ValueError(
                    f"lead agent {lead_agent_slug!r} is not a member of workspace {workspace_id!r}"
                )

            project_cwd = fs_registry.workspace_cwd(
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
                workspace_id=workspace_id,
                file_path=file_path,
                title=task_title,
                goal=goal,
                status="draft",
                created_by="user",
                lead_agent_slug=lead_agent_slug,
                # Draft-period holder = originating chat (logically); we still
                # record the lead agent slug for UI clarity. The actual plan
                # writer gate uses metadata.originating_session_id +
                # workspace match (see dispatch_mcp._check_plan_writer_gate).
                current_holder=lead_agent_slug,
                metadata_=metadata,
                plan_version=0,
                committed_at=None,
            )
            await task_ds.create_task(task_row)

            await event_ds.append_event(
                workspace_id=workspace_id,
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
        workspace_id: str,
        caller_session_id: str,
        lead_agent_slug_override: str | None = None,
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

            task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
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
            lead_member = await member_ds.get(workspace_id, lead_slug)
            if lead_member is None:
                return {
                    "error": (
                        f"lead agent {lead_slug!r} is not a member of workspace {workspace_id!r}"
                    )
                }

            from valuz_agent.modules.projects.datastore import WorkspaceDatastore

            ws_ds = WorkspaceDatastore(db)
            ws_row = await ws_ds.get_by_id(workspace_id)
            if ws_row is None:
                return {"error": f"workspace {workspace_id!r} not found"}
            project_cwd = fs_registry.workspace_cwd(
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )
            lead_cwd = str(project_cwd)

            lead_agent = await kernel_store.load_agent(lead_member.kernel_agent_id)
            lead_clone_id: str | None = None
            if lead_agent is not None:
                lead_clone_id = self._materialize_lead_agent(lead_agent, dispatch_mode="async")

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

            from valuz_agent.modules.projects.datastore import WorkspaceDatastore as WsDs

            ws_ds2 = WsDs(db)
            ws_ctx = await ws_ds2.get_context(workspace_id)
            workspace_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            lead_session = await build_member_session(
                workspace_id=workspace_id,
                agent_slug=lead_slug,
                members=member_ds,
                is_lead=True,
                task_id=task_id,
                run_dir=lead_cwd,
                brief=lead_brief,
                workspace_name=ws_row.name,
                workspace_instructions_md=workspace_instructions_md,
                dispatch_mode="async",
                goal_mode=True,
                plan_pre_committed=True,  # ← key flag (VALUZ-CHATPLAN D10)
                **_provider_resolver_deps(db),
            )
            if lead_session is None:
                return {"error": f"could not build lead session for {lead_slug!r}"}
            if lead_clone_id is not None:
                from dataclasses import replace as _replace

                lead_session = _replace(lead_session, agent_id=lead_clone_id)

            gap = await _credential_gap(lead_session, lead_slug, db=db)
            if gap is not None:
                return {"error": f"commit_task: {gap}"}

            await kernel_store.save_session(lead_session)

            # DB writes: create lead run row + flip task status + append event
            lead_run = TaskSessionRow(
                workspace_id=workspace_id,
                task_id=task_id,
                session_id=lead_session.id,
                agent_slug=lead_slug,
                sequence=0,
                kind="lead",
                status="active",
                label="Committed",
                goal=task_row.goal,
                workspace_mode="shared",
                run_dir=lead_cwd,
            )
            await run_ds.create_run(lead_run)

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
                workspace_id=workspace_id,
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
                workspace_id=workspace_id,
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
        workspace_id: str,
        caller_session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Discard a draft task (status: draft → abandoned).

        Terminal — abandoned tasks cannot be resurrected. Use stop_task
        (intervene) for active tasks; abandon_task is draft-only.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
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
                workspace_id=workspace_id,
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
    def _last_assistant_summary(session_id: str) -> str:
        """Best-effort last assistant-message text, for an auto-finalize summary."""
        try:
            events = kernel_sync.get_events_sync(session_id, limit=200)
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
        workspace_id: str,
        final_status: str,
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

            task = await task_ds.get_task_by_workspace(workspace_id, task_id)
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
            # failure lives in stop_reason (e.g. a skill-materialization crash
            # reports stop_reason.type=="error" with status idle and 0 assistant
            # output). So check the driver's final_status AND the session's
            # stop_reason; never mark an errored turn "completed".
            error_msg: str | None = None
            if final_status in ("terminated", "error"):
                error_msg = f"lead turn ended with status={final_status}"
            else:
                try:
                    sess = await kernel_store.load_session(lead_session_id)
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
                except Exception:  # noqa: BLE001
                    logger.debug("auto-finalize: stop_reason check failed for %s", lead_session_id)

            if error_msg and not unresolved:
                # Lead turn errored BEFORE producing any plan nodes — there's
                # no in-flight or half-done work the ``blocked`` state would
                # protect, so locking the task here forces an
                # unnecessary ``resume_task`` ceremony for what is effectively
                # still a fresh kickoff. Common trigger from a real bug
                # report (2026-05-29): an automation-fired lead session
                # entered Claude Agent SDK's ``EnterPlanMode`` + spawned a
                # nested ``Agent`` subagent that hung; the SDK cancelled the
                # turn ~3.5 minutes later (``stop_reason.type='error',
                # category='user_interrupt', message='cancelled'``); plan was
                # still empty. With the old logic this immediately blocked
                # the task and the lead's next turn (driven by the user
                # opening the conversation to ask "why didn't you plan?")
                # hit ``plan is read-only`` on the very first plan_task call.
                #
                # New behaviour: keep task ``active`` so the next driver
                # (user message → kernel send_message → fresh turn; OR the
                # next automation fire → fresh lead session) picks it up
                # cleanly. The turn error is still surfaced via logger so
                # backend.log retains the audit trail; no ``task_blocked``
                # event is emitted because the task isn't actually blocked
                # (no work pending, status unchanged).
                logger.warning(
                    "auto-finalize: task %s lead turn errored with empty plan "
                    "(%s) — staying active for next driver",
                    task_id,
                    error_msg,
                )
                return

            if error_msg:
                # Lead turn errored out mid-task. Per task_state.py: task-level
                # ``failed`` is intentionally not in the enum — this scenario
                # maps to ``blocked`` (recoverable; the lead crashed but the
                # plan is intact, user can resume by re-engaging chat or
                # creating a new lead session). The ``reason`` payload tags
                # this as a lead-turn-error to distinguish from the
                # unresolved-subtasks-no-error blocked case below.
                await task_ds.update_task_status(task_id, "blocked")
                await event_ds.append_event(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    type="task_blocked",
                    actor=lead_session_id,
                    session_id=lead_session_id,
                    payload={
                        "reason": "lead_turn_error",
                        "error": error_msg,
                        "pending_subtasks": unresolved,
                    },
                )
                logger.warning(
                    "auto-finalize: task %s -> blocked (lead turn error: %s)", task_id, error_msg
                )
                return
            if unresolved:
                # Lead stopped with planned work undispatched — surface as blocked
                # (not a hard error, but not done either).
                await task_ds.update_task_status(task_id, "blocked")
                await event_ds.append_event(
                    workspace_id=workspace_id,
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

            summary = self._last_assistant_summary(lead_session_id) or (
                "(auto-finalized) Lead ended its turn with no pending subtasks; "
                "task closed automatically."
            )
            await task_ds.update_task_status(task_id, "completed")
            await run_ds.update_run_by_session(
                session_id=lead_session_id,
                status="completed",
                ended_at=now_ms(),
            )
            await event_ds.append_event(
                workspace_id=workspace_id,
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
        workspace_id: str,
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

        try:
            await _finalize_session(session_id, last_content, final_status)
        except Exception:  # noqa: BLE001
            logger.exception("_finalize_actor: finalize failed for %s", session_id)

        try:
            from valuz_agent.adapters.broadcast_sink import cleanup_session

            await cleanup_session(session_id)
        except Exception:  # noqa: BLE001
            pass

        if role == "lead":
            # Host-side terminal fallback: a lead loop can end (goal auto-exit
            # to default, idle-TTL, normal end_turn) WITHOUT the model calling
            # finish_task — common when a goal-mode lead satisfies a simple goal
            # inline. finish_task is the only thing that closes the task, so
            # without this the task is orphaned "active" forever (see the live
            # 美湖/news-reporter case). Close it here based on the plan state.
            await self._auto_finalize_lead_task(
                lead_session_id=session_id,
                task_id=task_id,
                workspace_id=workspace_id,
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
                    manifest = collect_manifest(
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
                # only surface a terminal *failure* (terminated/error) here, and
                # mark the plan node failed so the lead/panel sees it. A clean loop
                # exit (idle-TTL / finish_task shutdown) emits no completion event;
                # the node's status was already set by review / _mark_in_review.
                if not ok:
                    key = run.subtask_key if run else None
                    if key:
                        task_ds = TaskDatastore(db)
                        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
                        if task_row is not None:
                            plan = TaskPlan.from_dict(task_row.plan)
                            if plan.get(key) is not None:
                                plan.update_node(key, status="failed")
                                task_row.plan = plan.to_dict()
                                await task_ds.update_task(task_row)
                                await planning.emit_plan_update(
                                    event_ds,
                                    workspace_id=workspace_id,
                                    task_id=task_id,
                                    plan=plan,
                                    actor=agent_slug,
                                    session_id=session_id,
                                )
                    await event_ds.append_event(
                        workspace_id=workspace_id,
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
        workspace_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str] | None = None,
        status: str = "completed",
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
                task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
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
                await task_ds.update_task_status(task_id, final_status)

                # Mark lead run as completed
                await run_ds.update_run_by_session(
                    session_id=lead_session_id,
                    status="completed",
                    ended_at=now_ms(),
                )

                await event_ds.append_event(
                    workspace_id=workspace_id,
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

        # Session-modes reconciliation (task-goal-mode.md §Key decisions):
        # ``finish_task`` is the authoritative terminal. Force the lead
        # session's mode back to ``default`` so the kernel's goal evaluator
        # cannot keep (or re-enter) the auto-loop after the task is closed,
        # and so a re-opened conversation on this session isn't stuck in
        # goal mode. Best-effort — a missing session is not fatal here.
        try:
            lead_sess = await kernel_store.load_session(lead_session_id)
            if lead_sess is not None and getattr(lead_sess, "mode", "default") != "default":
                lead_sess.mode = "default"
                await kernel_store.save_session(lead_sess)
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

    def _materialize_lead_agent(
        self, base_agent: Any, dispatch_mode: Literal["sync", "async"] = "sync"
    ) -> str:  # returns the lead-clone AgentConfig id
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

        from valuz_agent.modules.tasks.dispatch_mcp import (
            DISPATCH_TOOL_DECLARATIONS,
            LEAD_ONLY_TOOL_NAMES,
            ORCHESTRATION_TOOL_NAMES,
        )

        # sync/async share one lead toolset (non-blocking dispatch +
        # await_members + plan/review/finish); the modes differ only in the
        # session driver (one-shot run_session_to_idle vs persistent actor loop).
        declarations = DISPATCH_TOOL_DECLARATIONS
        # Start from the base tools minus any dispatch/launcher tools, then add
        # exactly this mode's dispatch declarations — so the clone never carries
        # stale dispatch tools or the conversation-only launcher/observability
        # tools (create_task / list_tasks / get_task / draft_task / commit_task /
        # abandon_task).
        #
        # VALUZ-CHATPLAN S2: plan_task / modify_plan / get_plan are now also
        # advertised on chat agents (via ORCHESTRATION_TOOL_DECLARATIONS) so a
        # base chat agent might carry them. They are NOT in ``drop`` (they
        # legitimately belong on the lead), so we'd duplicate them when we add
        # DISPATCH_TOOL_DECLARATIONS. Also dedupe against the upcoming
        # declarations' names so the clone advertises each tool exactly once.
        decl_names = {getattr(d, "name", None) for d in declarations}
        drop = LEAD_ONLY_TOOL_NAMES | ORCHESTRATION_TOOL_NAMES | decl_names
        base_tools = tuple(
            t for t in (base_agent.tools or ()) if getattr(t, "name", None) not in drop
        )
        # Lead sessions must also carry the always-on in-process baseline tools
        # (memory + submit_skill). The base member agent normally already has
        # them via _prepare_conversation_tools, but a base created before they
        # landed would be missing them — ensure them on the clone too.
        from valuz_agent.modules.agents.service import _ensure_global_tools_declared

        clone_id = f"{base_agent.id}__lead__{dispatch_mode}"
        clone = _ensure_global_tools_declared(
            replace(base_agent, id=clone_id, tools=base_tools + tuple(declarations))
        )
        kernel_sync.save_agent_sync(clone)
        return clone_id


__all__ = ["LifecycleService"]
