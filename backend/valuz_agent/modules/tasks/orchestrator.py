"""TaskOrchestrator — drives task lifecycle and subtask dispatch.

Architecture (lead-dispatch-mvp §S5, §1.3):

  kickoff()       → creates TaskRow + lead Session (is_lead=True, cwd=shared project cwd)
                    → appends kickoff event
                    → drives lead session via asyncio.create_task (background)

  dispatch()      → builds member Session (is_lead=False, cwd=subrun_dir)
                    → saves session to kernel store
                    → spawns member as sibling asyncio task (NOT recursive)
                    → awaits it (synchronous manifest return inside lead's tool call)
                    → appends subtask_spawned / subtask_completed / subtask_failed events
                    → returns manifest dict as the tool_result payload

  dispatch_batch()→ dispatches multiple items with asyncio.Semaphore(max_concurrent=4)
                    → groups by agent skill-set, parallel within group / sequential
                      across groups (§7.2.2 safety valve)

  finish_task()   → appends task_completed event, updates task status

  (Read-side queries — list_members / list_tasks / get_task — live in
   ``tasks/queries.py``; they hold no orchestrator state. T1.1 split.)

run_session_to_idle()  → extracted from _run_agent_background; used by both the
                         existing send_message path (unchanged) and dispatch.
                         Attaches BroadcastEventSink, drives run_turn, finalises,
                         returns final_status string.

collect_manifest() → gathers final_status + last assistant message (summary)
                     + scans run_dir for artifact file paths.

Lead gate enforcement lives in dispatch_mcp.py handlers, not here.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.adapters import kernel_store, kernel_sync
from valuz_agent.adapters.agent_resolver import build_member_session
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import EventBus, event_bus as _global_bus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.tasks._session_build import (
    _credential_gap,
    _provider_resolver_deps,
)
from valuz_agent.modules.tasks.actor_runner import (
    ActorRunner,
    collect_manifest,
    run_session_to_idle,
    _member_run_dir,  # noqa: F401 — re-exported for tests + back-compat
)
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.dispatcher import DispatcherService
from valuz_agent.modules.tasks.lifecycle import LifecycleService
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan
from valuz_agent.modules.tasks.recovery import RecoveryService


logger = logging.getLogger(__name__)

# ``run_session_to_idle`` / ``collect_manifest`` / ``_member_run_dir`` and the
# actor-loop tuning constants now live in the runtime layer
# (``tasks/actor_runner.py``, ADR-023). They are imported above and re-exported
# from this module so existing call sites + tests keep importing them here.

# ``await_member_results`` / ``_heartbeat_pending`` / the member-idle notify +
# lead-idle-no-pending callbacks / ``_broadcast_shutdown`` and the lead↔member
# text delivery (send_to_member / inject_into_task / notify_lead_goal_revised)
# now live in :class:`CoordinationService` (``tasks/coordination.py``, ADR-023
# Step 3b). The orchestrator keeps thin delegators so its public coordination
# surface + the actor-loop role callbacks keep resolving on ``self``.

# ``_credential_gap`` / ``_provider_resolver_deps`` now live in the shared
# build-session helper (``tasks/_session_build.py``, ADR-023). They are imported
# above and re-exported here so existing call sites + tests keep importing them
# from this module.


# ---------------------------------------------------------------------------
# TaskOrchestrator
# ---------------------------------------------------------------------------


class TaskOrchestrator:
    """Drives the full task lifecycle — kickoff, dispatch, finish.

    Instantiated once at startup (like schedule_runner); registered in
    app.py and passed to register_dispatch_tools().
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        max_concurrent: int = 4,
        registry: LiveMemberRegistry | None = None,
        actor_runner: ActorRunner | None = None,
    ) -> None:
        self._bus = bus or _global_bus
        self.max_concurrent = max_concurrent
        # Live member tracking: task_id → live member session ids (so
        # finish_task can broadcast shutdown to every still-running member) and
        # session_id → dispatch-start epoch (manifest attribution under the
        # shared project cwd). See LiveMemberRegistry for the sync invariant.
        self._members = registry or LiveMemberRegistry()
        # The shared runtime turn/actor engine (ADR-023). Bind ``self`` as the
        # host so the loop's seams (_run_turn_with_sink / _finalize_actor /
        # _notify_lead_member_idle / _lead_idle_with_no_pending) resolve back to
        # this orchestrator at call time — preserving the existing behaviour
        # where the loop drives those methods (and lets tests stub them).
        self._actor = actor_runner or ActorRunner()
        self._actor.bind(self)
        # Subtask dispatch (sync / batch / async) lives in DispatcherService
        # (ADR-023 Step 3a). It shares this orchestrator's registry + runtime
        # ActorRunner (same instances) plus the event bus + fan-out cap; the
        # orchestrator's public dispatch methods delegate straight onto it.
        self._dispatcher = DispatcherService(
            registry=self._members,
            actor_runner=self._actor,
            bus=self._bus,
            max_concurrent=self.max_concurrent,
        )
        # Lead ↔ member coordination (await_members / heartbeat / shutdown
        # broadcast / member-idle notify / text delivery) lives in
        # CoordinationService (ADR-023 Step 3b). It shares this orchestrator's
        # registry (same instance) for has_live_members / dispatch_started_at /
        # drain_members; the orchestrator's coordination surface delegates
        # straight onto it, and the ActorRunner resolves its role callbacks
        # (_notify_lead_member_idle / _lead_idle_with_no_pending) through the
        # bound host onto this service.
        self._coordination = CoordinationService(registry=self._members)
        # Task lifecycle (kickoff / draft / commit / abandon / finish + the
        # actor-loop finalize callbacks + the lead-clone builder) lives in
        # LifecycleService (ADR-023 Step 3c). It shares this orchestrator's
        # registry (same instance) + runtime ActorRunner + CoordinationService
        # + event bus; the orchestrator's lifecycle surface delegates straight
        # onto it, and the ActorRunner resolves its finalize callback
        # (_finalize_actor) through the bound host onto this service.
        self._lifecycle = LifecycleService(
            registry=self._members,
            actor_runner=self._actor,
            coordination=self._coordination,
            bus=self._bus,
        )
        # Startup recovery + user-initiated stop/resume lives in RecoveryService
        # (ADR-023 Step 3d). It shares this orchestrator's registry (same
        # instance — re-populated WITHOUT a dispatch epoch on the recovery
        # branch, the Step-1 invariant) + runtime ActorRunner + CoordinationService;
        # the orchestrator's recovery surface delegates straight onto it.
        self._recovery = RecoveryService(
            registry=self._members,
            actor_runner=self._actor,
            coordination=self._coordination,
        )

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

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        Kept on the orchestrator so its existing callers (REST routes,
        in-process automation runner, the ``create_task`` MCP handler) keep
        invoking ``task_orchestrator.kickoff``.
        """
        return await self._lifecycle.kickoff(
            project_id=project_id,
            goal=goal,
            lead_agent_slug=lead_agent_slug,
            refs=refs,
            created_by=created_by,
            title=title,
            dispatch_mode=dispatch_mode,
            originating_session_id=originating_session_id,
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
    ) -> TaskRow:
        """Create a task in ``draft`` status without starting a lead session.

        The originating chat session is recorded in ``metadata.originating_session_id``
        and becomes the plan-writer holder until ``commit_task`` flips control
        to the lead. ``plan_version`` starts at 0; the chat session is expected
        to follow up with ``plan_task`` (lifting plan_version to 1) before
        committing.

        Raises ``ValueError`` if the project doesn't exist or the agent isn't
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

            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(project_id)
            if ws_row is None:
                raise ValueError(f"project {project_id!r} not found")
            lead_member = await member_ds.get(project_id, lead_agent_slug)
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
            await task_ds.create_task(task_row)

            await event_ds.append_event(
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

            task_row = await task_ds.get_task_by_project(project_id, task_id)
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
            lead_member = await member_ds.get(project_id, lead_slug)
            if lead_member is None:
                return {
                    "error": (
                        f"lead agent {lead_slug!r} is not a member of project {project_id!r}"
                    )
                }

            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(project_id)
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

            from valuz_agent.modules.projects.datastore import ProjectDatastore as WsDs

            ws_ds2 = WsDs(db)
            ws_ctx = await ws_ds2.get_context(project_id)
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
            self.run_actor_loop(
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
    ) -> dict[str, Any]:
        """Discard a draft task (status: draft → abandoned).

        Terminal — abandoned tasks cannot be resurrected. Use stop_task
        (intervene) for active tasks; abandon_task is draft-only.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            task_row = await task_ds.get_task_by_project(project_id, task_id)
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
                project_id=project_id,
                task_id=task_id,
                type="abandoned",
                actor=caller_session_id,
                session_id=None,
                payload={"reason": reason} if reason else {},
            )
            return {"task_id": task_id, "status": "abandoned"}

    # ------------------------------------------------------------------
    # dispatch (single subtask)
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        subtask_key: str,
        agent: str | None = None,
        goal: str | None = None,
        refs: list[str] | None = None,
        project_mode: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch one planned subtask (by key) and await its completion.

        Plan-first (VALUZ-TASK D2/D4): the subtask must exist in the plan and be
        dispatchable (status planned/rework, deps done). agent/goal default to
        the plan node. Called from the dispatch MCP handler while the lead's
        run_turn is in progress. Returns the manifest dict as the tool_result.
        On member idle the node goes to ``in_review`` (the lead then reviews) —
        completion is decided by ``review_subtask``, not by this method.
        """
        return await self._dispatcher.dispatch(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            subtask_key=subtask_key,
            agent=agent,
            goal=goal,
            refs=refs,
            project_mode=project_mode,
        )

    # ------------------------------------------------------------------
    # dispatch_batch
    # ------------------------------------------------------------------

    async def dispatch_batch(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        keys: list[str],
    ) -> list[dict[str, Any]]:
        """Dispatch multiple planned subtasks (by key) with concurrency control.

        Groups by the node's agent skill-set fingerprint — keys sharing the same
        skill set run in parallel (within the semaphore); groups with different
        skill sets run sequentially to avoid skill-dir conflicts (§7.2.2). Each
        key goes through the same plan-first gate as ``dispatch``.

        Returns a list of manifests in the same order as *keys*.
        """
        return await self._dispatcher.dispatch_batch(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            keys=keys,
        )

    # ==================================================================
    # v2 actor dispatch (M10 附录 B) — persistent lead + member actors
    # ==================================================================

    async def _run_turn_with_sink(self, session_id: str, content: str) -> str:
        """Run ONE turn on a persistent session and return its final status.

        Thin delegator onto the shared :class:`ActorRunner` runtime engine
        (ADR-023). Kept as a method so the actor loop drives it via ``self``
        (and tests can stub ``orch._run_turn_with_sink``).
        """
        return await self._actor._run_turn_with_sink(session_id, content)

    async def run_actor_loop(
        self,
        *,
        session_id: str,
        initial_prompt: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        idle_ttl: float | None = None,
    ) -> None:
        """Persistent actor loop: run turn → idle → await mailbox → repeat.

        Delegates to the shared :class:`ActorRunner`; the runner resolves the
        loop's seams (_run_turn_with_sink / _finalize_actor /
        _notify_lead_member_idle / _lead_idle_with_no_pending) back through this
        orchestrator (bound as its host), preserving the prior behaviour.
        """
        await self._actor.run_actor_loop(
            session_id=session_id,
            initial_prompt=initial_prompt,
            role=role,
            task_id=task_id,
            project_id=project_id,
            idle_ttl=idle_ttl,
        )

    @staticmethod
    def _format_member_done(msg: Any) -> str:
        """Render a member_done mailbox message as the lead's next turn prompt."""
        return ActorRunner._format_member_done(msg)

    async def _notify_lead_member_idle(self, session_id: str, status: str) -> None:
        """After a member turn, push a member_done message to its lead's inbox.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so the actor loop drives it via the bound host (and
        tests can stub ``orch._notify_lead_member_idle``).
        """
        await self._coordination._notify_lead_member_idle(session_id, status)

    async def _lead_idle_with_no_pending(self, task_id: str, project_id: str) -> bool:
        """True when a lead has nothing left to wait for after a turn.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so the actor loop drives it via the bound host (and
        tests can stub ``orch._lead_idle_with_no_pending``).
        """
        return await self._coordination._lead_idle_with_no_pending(task_id, project_id)

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
        project_id: str,
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

            task = await task_ds.get_task_by_project(project_id, task_id)
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
                    project_id=project_id,
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

    # ------------------------------------------------------------------
    # Stop / resume (VALUZ-RESUME)
    # ------------------------------------------------------------------

    async def recover_active_tasks(self) -> int:
        """Layer 1 (VALUZ-RESUME §5.3): on host startup, reconcile + resume every
        ``active`` task whose actor loops died with the previous process.

        Only ``active`` tasks are touched — ``paused``/``stopped`` are intentional
        user stops (resume on explicit request), terminal states are done.
        Best-effort + idempotent (re-running converges on current run/node state).
        """
        async with async_unit_of_work(commit=False) as db:
            task_ids = [(t.id, t.project_id) for t in await TaskDatastore(db).list_active()]
        recovered = 0
        for task_id, project_id in task_ids:
            try:
                if await self._recover_one_task(task_id, project_id):
                    recovered += 1
            except Exception:  # noqa: BLE001
                logger.exception("recover_active_tasks: failed for task %s", task_id)
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
        from valuz_agent.modules.tasks.recovery import reconcile

        member_done: list[tuple[str, dict[str, Any]]] = []
        resume_members: list[tuple[str, str]] = []  # (session_id, brief)
        summary: list[str] = []
        lead_session_id: str | None = None

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(project_id, task_id)
            if task is None or task.status not in ("active", "paused"):
                return False
            runs = await run_ds.list_runs(task_id)
            lead_run = next((r for r in runs if r.kind == "lead"), None)
            if lead_run is None:
                return False
            lead_session_id = lead_run.session_id

            plan = TaskPlan.from_dict(task.plan)
            plan_dirty = False
            for run in runs:
                if run.kind != "subtask" or run.status not in ("active", "paused"):
                    continue
                ks = await kernel_store.load_session(run.session_id)
                node = plan.get(run.subtask_key) if run.subtask_key else None
                rec = reconcile(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                    node_attempts=(node.attempts if node else 0),
                )
                manifest: dict[str, Any] | None = None
                if rec.disposition == "completed":
                    try:
                        manifest = collect_manifest(
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

        # Evict any stale kernel runtime BEFORE respawning so each resumed turn
        # builds a FRESH one. Load-bearing for pause→resume: the pause
        # ``interrupt`` cancels the in-flight turn and leaves the runtime's SDK
        # client in a broken/cancelled state cached in the kernel orchestrator's
        # ``_runtimes``; ``_ensure_runtime`` would reuse it and the resumed turn
        # immediately cancels (9s, null output) → the lead loop ends with an
        # errored ``stop_reason`` → ``_auto_finalize`` blocks the task. Doing it
        # HERE — right before respawn, not in the old loop's async
        # ``_finalize_actor`` — is race-free: the old loop has already exited and
        # the new one hasn't built its runtime yet. On Layer-1 startup recovery
        # the cache is empty so it's a harmless no-op.
        async def _evict_runtime(sid: str) -> None:
            try:
                from app.dependencies import (  # type: ignore[import-not-found]
                    get_orchestrator,
                )

                await get_orchestrator().cleanup(sid)
            except Exception:  # noqa: BLE001
                pass

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
            await _evict_runtime(member_sid)
            self._members.add_member(task_id, member_sid)
            asyncio.create_task(
                self.run_actor_loop(
                    session_id=member_sid,
                    initial_prompt=brief or "继续完成你的子任务,完成后会汇报给 lead。",
                    role="subtask",
                    task_id=task_id,
                    project_id=project_id,
                )
            )
        await _evict_runtime(lead_session_id)
        lead_brief = (
            "<system-recovery>\n本任务已被恢复(系统重启或用户恢复)。子任务对账结果:\n"
            + ("\n".join(summary) if summary else "(无在途子任务)")
            + "\n\n请先调用 get_plan 对齐当前状态,然后继续编排:派发未决子任务、"
            "审核 in_review、重试 rework;全部完成后调用 finish_task。\n</system-recovery>"
        )
        asyncio.create_task(
            self.run_actor_loop(
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
            from app.dependencies import get_orchestrator  # type: ignore[import-not-found]

            await get_orchestrator().interrupt(session_id)
        except Exception:  # noqa: BLE001
            logger.warning("interrupt failed for session %s", session_id, exc_info=True)

    async def stop_task(
        self, task_id: str, project_id: str, *, target_status: str = "paused"
    ) -> bool:
        """User-initiated cascade halt → ``paused`` (pause) or ``stopped`` (stop).

        Interrupts the lead + every in-flight member, broadcasts ``shutdown`` to
        their actor loops, parks in-flight member runs ``→paused`` AND their
        running plan nodes (``in_progress`` → ``paused``) so the panel stops
        rendering them as actively running, then flips the task to
        ``target_status``:

          * ``paused`` — recoverable pause. Layer-1 app-restart recovery skips it;
            the user resumes explicitly via ``resume_task``. Only from ``active``.
          * ``stopped`` — user-driven stop. Soft-terminal in the UI (no resume
            button) but still revivable via chat/inject (``resume_task`` accepts
            it). From ``active`` or an already-``paused`` task.

        Members + plan nodes are parked identically for both (``stopped`` stays
        revivable by design); only the task status + the emitted event differ.
        Returns False if the task is gone or the transition is illegal.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(project_id, task_id)
            if task is None:
                return False
            # pause: only an active task. stop: an active OR already-paused task.
            allowed_from = ("active",) if target_status == "paused" else ("active", "paused")
            if task.status not in allowed_from:
                return False
            runs = await run_ds.list_runs(task_id)
            lead_session_id: str | None = next(
                (r.session_id for r in runs if r.kind == "lead"), None
            )
            member_sids = [
                r.session_id for r in runs if r.kind == "subtask" and r.status == "active"
            ]
            for sid in member_sids:
                await run_ds.update_run_by_session(session_id=sid, status="paused")
            # Park only the running member's node (``in_progress`` = a live
            # member session, the one we're halting) → ``paused`` so the panel
            # stops spinning it. Leave ``in_review`` (member finished, awaiting
            # the lead's review — parking would lose that) and ``rework``
            # (awaiting re-dispatch) alone. On resume, recovery reconcile flips
            # a parked node back to ``in_progress`` if its run survived;
            # otherwise it stays ``paused`` and is re-dispatchable (ready_keys +
            # resolve_dispatch_node both accept ``paused``).
            plan = TaskPlan.from_dict(task.plan)
            parked = 0
            for node in plan.nodes:
                if node.status == "in_progress":
                    plan.update_node(node.key, status="paused")
                    parked += 1
            if parked:
                task.plan = plan.to_dict()
            task.status = target_status
            await task_ds.update_task(task)
            if parked:
                await planning.emit_plan_update(
                    event_ds,
                    project_id=project_id,
                    task_id=task_id,
                    plan=plan,
                    actor="user",
                    session_id=lead_session_id,
                )
            await event_ds.append_event(
                project_id,
                task_id,
                target_status,  # "paused" | "stopped" — drives UI status + timer
                actor="user",
                payload={"members_paused": len(member_sids)},
            )

        # Cascade interrupt + shutdown (outside the DB txn).
        for sid in member_sids:
            await self._interrupt_kernel_session(sid)
        if lead_session_id is not None:
            await self._interrupt_kernel_session(lead_session_id)
        self._broadcast_shutdown(task_id)
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
            task = await task_ds.get_task_by_project(project_id, task_id)
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
            await task_ds.update_task_status(task_id, "active")
            # When reviving a stopped OR completed task: finish_task previously
            # marked the lead run as "completed" and broadcast shutdown to
            # members. _recover_one_task respawns the lead unconditionally, but
            # the run row still showing "completed" would lie about reality —
            # fix it so listings + UI reflect the live state.
            if prior_status in ("stopped", "completed"):
                runs = await run_ds.list_runs(task_id)
                lead_run = next((r for r in runs if r.kind == "lead"), None)
                if lead_run is not None and lead_run.status != "active":
                    await run_ds.update_run_by_session(
                        session_id=lead_run.session_id,
                        status="active",
                        ended_at=None,
                    )
            await event_ds.append_event(
                project_id, task_id, "resumed", actor=actor, payload={"from": prior_status}
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
                task = await task_ds.get_task_by_project(project_id, task_id)
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
                        task_row = await task_ds.get_task_by_project(project_id, task_id)
                        if task_row is not None:
                            plan = TaskPlan.from_dict(task_row.plan)
                            if plan.get(key) is not None:
                                plan.update_node(key, status="failed")
                                task_row.plan = plan.to_dict()
                                await task_ds.update_task(task_row)
                                await planning.emit_plan_update(
                                    event_ds,
                                    project_id=project_id,
                                    task_id=task_id,
                                    plan=plan,
                                    actor=agent_slug,
                                    session_id=session_id,
                                )
                    await event_ds.append_event(
                        project_id=project_id,
                        task_id=task_id,
                        type="subtask_failed",
                        actor=agent_slug,
                        session_id=session_id,
                        payload={**manifest, **({"subtask_key": key} if key else {})},
                    )
        except Exception:  # noqa: BLE001
            logger.exception("_finalize_actor: failed to record terminal run for %s", session_id)

    async def dispatch_async(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        subtask_key: str,
        agent: str | None = None,
        goal: str | None = None,
        refs: list[str] | None = None,
        project_mode: str | None = None,
    ) -> dict[str, Any]:
        """Start a planned subtask's member actor (non-blocking); return its handle.

        Plan-first (VALUZ-TASK): the subtask must be dispatchable in the plan;
        agent/goal default to the plan node. Unlike :meth:`dispatch`, this
        records the run, starts the member's actor loop as a sibling task, and
        returns ``{session_id, agent, status:"dispatched"}`` immediately. The
        lead is re-woken via ``member_done``; the node goes ``in_review`` then
        and is completed only by ``review_subtask``.
        """
        return await self._dispatcher.dispatch_async(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            subtask_key=subtask_key,
            agent=agent,
            goal=goal,
            refs=refs,
            project_mode=project_mode,
        )

    # send_to_member / inject_into_task implementations live in
    # tasks/messaging.py (T1.1 split) — callers invoke messaging.* directly.

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

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so the dispatch-MCP ``await_members`` handler keeps
        calling it on the orchestrator (and tests can drive
        ``orch.await_member_results``).
        """
        return await self._coordination.await_member_results(
            lead_session_id=lead_session_id,
            project_id=project_id,
            task_id=task_id,
            keys=keys,
            mode=mode,
            timeout_s=timeout_s,
        )

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

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so existing tests can drive ``orch._heartbeat_pending``.
        """
        return await self._coordination._heartbeat_pending(
            task_id=task_id,
            project_id=project_id,
            pending_keys=pending_keys,
        )

    def _broadcast_shutdown(self, task_id: str) -> None:
        """Tell every still-running member of a task to finalize after its turn.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so ``finish_task`` / ``stop_task`` drive it on the
        orchestrator (and tests can call ``orch._broadcast_shutdown``).
        """
        self._coordination._broadcast_shutdown(task_id)

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
                task_row = await task_ds.get_task_by_project(project_id, task_id)
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

        self._broadcast_shutdown(task_id)
        mailbox_registry.put(lead_session_id, InboxMsg(kind="shutdown"))
        return {"ok": True, "status": final_status}

    # ------------------------------------------------------------------
    # Plan / review — lead orchestration (VALUZ-TASK)
    # ------------------------------------------------------------------

    # Plan authoring / review / node mutation live in tasks/planning.py
    # (T1.1 split). Callers (dispatch-MCP tools, task routes) invoke
    # planning.* directly; the orchestrator's own dispatch/actor/recovery
    # methods do the same.

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


# ---------------------------------------------------------------------------
# Module-level singleton (used by app.py startup + dispatch_mcp handlers)
# ---------------------------------------------------------------------------

task_orchestrator = TaskOrchestrator()

__all__ = [
    "TaskOrchestrator",
    "task_orchestrator",
    "run_session_to_idle",
    "collect_manifest",
    "_member_run_dir",
    "_credential_gap",
    "_provider_resolver_deps",
]
