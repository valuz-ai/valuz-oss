"""DispatcherService — subtask dispatch (ADR-023, Step 3a).

Peeled verbatim out of ``TaskOrchestrator`` (the sync ``dispatch`` /
``dispatch_batch`` paths + the live async ``dispatch_async`` path). Owns no
task state — it receives the shared :class:`LiveMemberRegistry` and the runtime
:class:`ActorRunner` by constructor injection (the same instances the
composition root wires into every other task service), plus the event bus and
the max-concurrent fan-out cap.

Collaboration with sibling MODULES (planning, the build-session helpers) stays
direct-import; only the registry + runner are injected.

CRITICAL invariant (``dispatch_async``): the sync-before-spawn block must run
``mailbox_registry.register(lead) -> registry.add_member(...) ->
mailbox_registry.register(member) -> asyncio.create_task(run_actor_loop)`` with
NO ``await`` in between — a racing ``finish_task`` shutdown broadcast that sees
an empty live set would otherwise drop the just-spawned member.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal, cast

from valuz_agent.adapters import kernel_client
from valuz_agent.modules.sessions import project_index
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
    _member_run_dir,
)
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.models import TaskSessionRow
from valuz_agent.modules.tasks.plan import TaskPlan

logger = logging.getLogger(__name__)


class DispatcherService:
    """Drives single + batch + async subtask dispatch.

    Constructed once at the composition root with the shared registry and the
    runtime ActorRunner; ``max_concurrent`` mirrors the orchestrator's fan-out
    cap and ``bus`` is the event bus the sync dispatch path drives turns on.
    """

    def __init__(
        self,
        *,
        registry: LiveMemberRegistry,
        actor_runner: ActorRunner,
        bus: EventBus,
        max_concurrent: int = 4,
    ) -> None:
        self._members = registry
        self._actor = actor_runner
        self._bus = bus
        self.max_concurrent = max_concurrent

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
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            member_ds = ProjectMemberDatastore(db)

            task_row = await task_ds.get_task_by_project(project_id, task_id)
            if task_row is None:
                return {"error": f"task {task_id!r} not found", "status": "failed"}

            # Plan-first gate: resolve agent/goal from the planned node.
            resolved = planning.resolve_dispatch_node(
                TaskPlan.from_dict(task_row.plan), subtask_key, agent, goal
            )
            if isinstance(resolved, str):
                return {"error": resolved, "status": "failed"}
            agent, goal = resolved

            # Determine project cwd
            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(project_id)
            if ws_row is None:
                return {"error": f"project {project_id!r} not found", "status": "failed"}
            project_cwd = fs_registry.project_cwd(
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )

            # Resolve member working dir: shared project cwd by default (v2.1),
            # isolated git worktree only for repo-worktree mode.
            run_seq = await run_ds.next_sequence(task_id)
            mode = project_mode or "shared"
            # ``repo-worktree`` mode shells out to ``git worktree add`` (blocking
            # subprocess); offload so dispatch never blocks the event loop. The
            # default ``shared`` mode is a no-op Path() and stays instant.
            run_dir = await asyncio.to_thread(
                _member_run_dir, project_cwd, task_id, run_seq, mode
            )
            started = time.time()

            # Build brief for the member
            refs_text = "\n".join(f"- {r}" for r in (refs or []))
            # Goal mode prepends ``/goal `` (wrap_for_mode); drop the redundant
            # ``## Goal`` header so it doesn't land inside the goal condition.
            member_brief = goal + (f"\n\n## References\n\n{refs_text}" if refs_text else "")

            # Fetch project context
            ws_ctx = await ws_ds.get_context(project_id)
            project_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            # Build and save member session
            member_session = await build_member_session(
                project_id=project_id,
                agent_slug=agent,
                members=member_ds,
                is_lead=False,
                task_id=task_id,
                run_dir=str(run_dir),
                brief=member_brief,
                project_name=ws_row.name,
                project_instructions_md=project_instructions_md,
                # Member sub-run in goal mode: self-loops until its scoped
                # goal is met, then auto-exits to default before returning
                # its SubtaskResult to the lead. Lead's review_subtask
                # (approve / rework) authority is unchanged.
                goal_mode=True,
                **_provider_resolver_deps(db),
            )
            if member_session is None:
                return {
                    "error": f"agent {agent!r} not found in project {project_id!r}",
                    "status": "failed",
                }

            # Fail fast on a credential-less member — return a clear reason to
            # the lead instead of spawning a doomed "Not logged in" run.
            gap = await _credential_gap(member_session, agent, db=db)
            if gap is not None:
                await event_ds.append_event(
                    project_id=project_id,
                    task_id=task_id,
                    type="subtask_failed",
                    actor=agent,
                    session_id=member_session.id,
                    payload={"agent": agent, "status": "failed", "error": gap},
                )
                return {"error": gap, "status": "failed", "agent": agent}

            await kernel_client.create_session(member_session)
            await project_index.record(
                project_id, member_session.id, kind="task_subtask", origin="task"
            )

            # Record run in index (linked to the plan node via subtask_key)
            run_row = TaskSessionRow(
                project_id=project_id,
                task_id=task_id,
                session_id=member_session.id,
                agent_slug=agent,
                sequence=run_seq,
                kind="subtask",
                status="active",
                goal=goal,
                dispatched_by=lead_session_id,
                project_mode=mode,
                run_dir=str(run_dir),
                subtask_key=subtask_key,
            )
            await run_ds.create_run(run_row)

            # Append spawned event
            await event_ds.append_event(
                project_id=project_id,
                task_id=task_id,
                type="subtask_spawned",
                actor=agent,
                session_id=member_session.id,
                payload={
                    "agent": agent,
                    "goal": goal,
                    "run_dir": str(run_dir),
                    "subtask_key": subtask_key,
                },
            )

        # Flip the plan node to in_progress (attempts++, link this run).
        await planning.mark_node_dispatched(
            project_id=project_id,
            task_id=task_id,
            subtask_key=subtask_key,
            agent=agent,
            session_id=member_session.id,
        )

        # Run member as sibling asyncio task (proven non-recursive, §8)
        member_task = asyncio.create_task(
            run_session_to_idle(
                session_id=member_session.id,
                content=member_brief,
                event_bus=self._bus,
            )
        )
        final_status = await member_task

        # Collect manifest (attribute artifacts by mtime since dispatch — the
        # member shares the project cwd under v2.1).
        manifest = await collect_manifest(
            member_session.id, run_dir, final_status, since_epoch=started
        )

        # Persist result
        async with async_unit_of_work() as db2:
            run_ds2 = TaskSessionDatastore(db2)
            event_ds2 = TaskEventDatastore(db2)
            task_ds2 = TaskDatastore(db2)

            failed = final_status in ("terminated", "error")
            await run_ds2.update_run_by_session(
                session_id=member_session.id,
                status="archived" if failed else "completed",
                result_manifest=manifest,
                ended_at=now_ms(),
            )

            # Review model (VALUZ-TASK §6.1): a member going idle is NOT
            # completion — the lead decides via review_subtask. A genuine run
            # failure (terminated/error) still fails the node so the lead sees
            # it; otherwise the node goes to in_review awaiting the lead's call.
            task_row2 = await task_ds2.get_task_by_project(project_id, task_id)
            plan2 = TaskPlan.from_dict(task_row2.plan) if task_row2 else None
            if plan2 is not None and plan2.get(subtask_key) is not None:
                plan2.update_node(subtask_key, status="failed" if failed else "in_review")
                task_row2.plan = plan2.to_dict()  # type: ignore[union-attr]
                await task_ds2.update_task(task_row2)  # type: ignore[arg-type]
                await planning.emit_plan_update(
                    event_ds2,
                    project_id=project_id,
                    task_id=task_id,
                    plan=plan2,
                    actor=agent,
                    session_id=member_session.id,
                )
            if failed:
                await event_ds2.append_event(
                    project_id=project_id,
                    task_id=task_id,
                    type="subtask_failed",
                    actor=agent,
                    session_id=member_session.id,
                    payload={**manifest, "subtask_key": subtask_key},
                )

        return manifest

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
        sem = asyncio.Semaphore(self.max_concurrent)

        # Resolve each key's agent from the plan for skill grouping.
        async with async_unit_of_work(commit=False) as db0:
            task_row0 = await TaskDatastore(db0).get_task_by_project(project_id, task_id)
            plan0 = TaskPlan.from_dict(task_row0.plan) if task_row0 else TaskPlan()

        async def _skill_key(agent_slug: str) -> str:
            """Return a deterministic string representing the agent's skills."""
            async with async_unit_of_work(commit=False) as db:
                member_ds = ProjectMemberDatastore(db)
                member = await member_ds.get(project_id, agent_slug)
                if member is None:
                    return agent_slug
                from valuz_agent.adapters.agent_resolver import _member_agent_config

                agent_cfg = await _member_agent_config(member, member_ds)
                if agent_cfg is None:
                    return agent_slug
                skill_names = sorted(
                    s.name if hasattr(s, "name") else str(s) for s in (agent_cfg.skills or [])
                )
                return "|".join(skill_names) or agent_slug

        # Group keys by skill fingerprint (preserve first-seen order).
        groups: dict[str, list[tuple[int, str]]] = {}
        for idx, key in enumerate(keys):
            node = plan0.get(key)
            skill = await _skill_key(node.agent if node and node.agent else key)
            groups.setdefault(skill, []).append((idx, key))

        results: dict[int, dict[str, Any]] = {}

        async def _dispatch_one(idx: int, subtask_key: str) -> None:
            async with sem:
                results[idx] = await self.dispatch(
                    task_id=task_id,
                    project_id=project_id,
                    lead_session_id=lead_session_id,
                    subtask_key=subtask_key,
                )

        # Sequential across groups, parallel within each group
        for group_keys in groups.values():
            group_tasks = [asyncio.create_task(_dispatch_one(idx, key)) for idx, key in group_keys]
            await asyncio.gather(*group_tasks)

        # Return in original order
        return [results[i] for i in range(len(keys))]

    # ==================================================================
    # v2 actor dispatch (M10 附录 B) — async member spawn
    # ==================================================================

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
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            member_ds = ProjectMemberDatastore(db)

            task_row = await task_ds.get_task_by_project(project_id, task_id)
            if task_row is None:
                return {"error": f"task {task_id!r} not found", "status": "failed"}

            _plan = TaskPlan.from_dict(task_row.plan)
            resolved = planning.resolve_dispatch_node(_plan, subtask_key, agent, goal)
            if isinstance(resolved, str):
                return {"error": resolved, "status": "failed"}
            agent, goal = resolved
            _node = _plan.get(subtask_key)
            review_criteria = _node.review_criteria if _node else ""

            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(project_id)
            if ws_row is None:
                return {"error": f"project {project_id!r} not found", "status": "failed"}
            project_cwd = fs_registry.project_cwd(
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )

            run_seq = await run_ds.next_sequence(task_id)
            mode = project_mode or "shared"
            # ``repo-worktree`` mode shells out to ``git worktree add`` (blocking
            # subprocess); offload so dispatch never blocks the event loop. The
            # default ``shared`` mode is a no-op Path() and stays instant.
            run_dir = await asyncio.to_thread(
                _member_run_dir, project_cwd, task_id, run_seq, mode
            )

            refs_text = "\n".join(f"- {r}" for r in (refs or []))
            # Goal mode prepends ``/goal `` (wrap_for_mode); drop the redundant
            # ``## Goal`` header so it doesn't land inside the goal condition.
            # Append the lead's review criteria so the member knows the
            # acceptance bar it will be reviewed against.
            member_brief = (
                goal
                + (f"\n\n## References\n\n{refs_text}" if refs_text else "")
                + (
                    "\n\n## Acceptance criteria (you will be reviewed on this)\n\n"
                    + review_criteria
                    if review_criteria
                    else ""
                )
            )

            ws_ctx = await ws_ds.get_context(project_id)
            project_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            member_session = await build_member_session(
                project_id=project_id,
                agent_slug=agent,
                members=member_ds,
                is_lead=False,
                task_id=task_id,
                run_dir=str(run_dir),
                brief=member_brief,
                project_name=ws_row.name,
                project_instructions_md=project_instructions_md,
                lead_session_id=lead_session_id,
                # Member sub-run in goal mode: kernel wraps the brief into
                # ``/goal <brief>`` so the member self-loops until its scoped
                # goal is met, then auto-exits and reports member_done.
                # (The collapsed ``dispatch`` tool routes here, so this is the
                # live path — the now-unused sync ``dispatch`` had it too.)
                goal_mode=True,
                **_provider_resolver_deps(db),
            )
            if member_session is None:
                return {
                    "error": f"agent {agent!r} not found in project {project_id!r}",
                    "status": "failed",
                }

            # Fail fast on a credential-less member before starting its actor.
            gap = await _credential_gap(member_session, agent, db=db)
            if gap is not None:
                await event_ds.append_event(
                    project_id=project_id,
                    task_id=task_id,
                    type="subtask_failed",
                    actor=agent,
                    session_id=member_session.id,
                    payload={"agent": agent, "status": "failed", "error": gap},
                )
                return {"error": gap, "status": "failed", "agent": agent}

            await kernel_client.create_session(member_session)
            await project_index.record(
                project_id, member_session.id, kind="task_subtask", origin="task"
            )

            await run_ds.create_run(
                TaskSessionRow(
                    project_id=project_id,
                    task_id=task_id,
                    session_id=member_session.id,
                    agent_slug=agent,
                    sequence=run_seq,
                    kind="subtask",
                    status="active",
                    goal=goal,
                    dispatched_by=lead_session_id,
                    project_mode=mode,
                    run_dir=str(run_dir),
                    subtask_key=subtask_key,
                )
            )
            await event_ds.append_event(
                project_id=project_id,
                task_id=task_id,
                type="subtask_spawned",
                actor=agent,
                session_id=member_session.id,
                payload={
                    "agent": agent,
                    "goal": goal,
                    "run_dir": str(run_dir),
                    "subtask_key": subtask_key,
                },
            )

        # Flip the plan node to in_progress (attempts++, link this run).
        await planning.mark_node_dispatched(
            project_id=project_id,
            task_id=task_id,
            subtask_key=subtask_key,
            agent=agent,
            session_id=member_session.id,
        )

        # Track as a live member + start its actor loop (non-blocking).
        # Register the mailbox SYNCHRONOUSLY (before create_task) so a
        # finish_task shutdown that races ahead of the member loop's first tick
        # is still queued rather than dropped — otherwise the member would hang
        # until its idle TTL. run_actor_loop's register() is idempotent.
        from valuz_agent.modules.tasks.mailbox import mailbox_registry

        # Register the LEAD's mailbox too (idempotent) — the member posts
        # ``member_done`` here when it idles, and the lead's ``await_members``
        # drains it. Registering at dispatch time guarantees delivery even
        # when the lead wasn't started via the async-kickoff path (e.g. a
        # goal-mode single-turn lead): otherwise the member's ``put`` lands on
        # an unregistered inbox and is DROPPED, and ``await_members`` raises
        # KeyError + returns empty → the lead wrongly thinks members are stuck.
        mailbox_registry.register(lead_session_id)
        self._members.add_member(task_id, member_session.id, dispatch_epoch=time.time())
        mailbox_registry.register(member_session.id)
        asyncio.create_task(
            self._actor.run_actor_loop(
                session_id=member_session.id,
                initial_prompt=member_brief,
                role="subtask",
                task_id=task_id,
                project_id=project_id,
            )
        )

        return {
            "session_id": member_session.id,
            "agent": agent,
            "status": "dispatched",
        }
