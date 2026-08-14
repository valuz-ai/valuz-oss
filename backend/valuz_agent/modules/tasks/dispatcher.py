"""DispatcherService — subtask dispatch (ADR-023, Step 3a).

Owns the async member-spawn path (``dispatch_async`` — the only dispatch
surface since the tool collapse, decision doc §14; the legacy sync
``dispatch`` / ``dispatch_batch`` paths are deleted). Owns no task state — it
receives the runtime :class:`ActorRunner` by constructor injection (the same
instance the composition root wires into every other task service).

Host-knowledge resolution (project cwd, membership, agent config, providers,
credential pre-flight) lives in ``tasks/resolution.py`` — this module only
composes the brief, persists the run rows/events and spawns the actor.

Membership needs no in-process bookkeeping: the run row this module writes
before spawning IS the record that the member is live, and every process reads
it. There used to be a sync-before-spawn invariant here — register in a
per-process set, then ``create_task``, with no ``await`` in between, or a
racing halt would drop the member. The set is gone and so is the race.
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks import launcher
from valuz_agent.modules.tasks.events import record_subtask_failed
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskSessionRow
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.resolution import task_session_resolver
from valuz_agent.modules.tasks.task_worktree import (
    resolve_task_cwd,
    task_worktree_notice,
    task_worktree_snapshot,
)

logger = logging.getLogger(__name__)


class DispatcherService:
    """Drives async subtask dispatch (member actor spawn).

    Constructed once at the composition root with the runtime ActorRunner (the
    same instance every other task service shares).
    """

    def __init__(self, *, actor_runner: ActorRunner) -> None:
        self._actor = actor_runner

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
        user_id: str,
    ) -> dict[str, Any]:
        """Start a planned subtask's member actor (non-blocking); return its handle.

        Plan-first (VALUZ-TASK): the subtask must be dispatchable in the plan;
        agent/goal default to the plan node. Records the run, starts the
        member's actor loop as a sibling task, and returns
        ``{session_id, agent, status:"dispatched"}`` immediately. The lead is
        re-woken via ``member_done``; the node goes ``in_review`` then and is
        completed only by ``review_subtask``.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)

            task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task_row is None:
                return {"error": f"task {task_id!r} not found", "status": "failed"}
            _plan = TaskPlan.from_dict(task_row.plan)
            resolved_node = planning.resolve_dispatch_node(_plan, subtask_key, agent, goal)
            if isinstance(resolved_node, Failure):
                return {"error": resolved_node.reason, "status": "failed"}
            agent, goal = resolved_node
            _node = _plan.get(subtask_key)
            review_criteria = _node.review_criteria if _node else ""

            env = await task_session_resolver.resolve_project_env(
                db, user_id=task_row.user_id, project_id=project_id
            )
            if env is None:
                return {"error": f"project {project_id!r} not found", "status": "failed"}

            run_seq = await run_ds.next_sequence(task_id)
            # Task-level worktree (design §5): members share the task's ONE
            # worktree cwd — no per-member isolation on top; the plan DAG's
            # dependencies remain the write-conflict discipline.
            wt_snapshot = task_worktree_snapshot(task_row)
            if wt_snapshot is not None:
                mode = "shared"
                work_cwd = await resolve_task_cwd(task_row, str(env.project_cwd))
            else:
                mode = project_mode or "shared"
                work_cwd = str(env.project_cwd)
            # v2.1: members run in the SHARED project cwd (a task worktree
            # relocates it wholesale); per-member isolation is retired.
            run_dir = Path(work_cwd)

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

            resolved = await task_session_resolver.resolve_member(
                db,
                env=env,
                project_id=project_id,
                task_id=task_id,
                agent_slug=agent,
                run_dir=str(run_dir),
                brief=member_brief,
                user_id=task_row.user_id,
                spill_label=f"{agent}-{subtask_key}",
                lead_session_id=lead_session_id,
                worktree_notice=task_worktree_notice(wt_snapshot),
            )
            if isinstance(resolved, Failure):
                return {"error": resolved.reason, "status": "failed"}
            member_session = resolved.session
            member_brief = resolved.brief
            agent_name = resolved.agent_name

            # Fail fast on a credential-less member before starting its actor.
            if resolved.credential_gap is not None:
                # No session_id on this event: the kernel session was never
                # created (create_session below is unreached), so an id here
                # would render in the task timeline as a clickable link to a
                # session that 404s ("Session not found.").
                await record_subtask_failed(
                    event_ds,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    session_id=None,
                    agent_slug=agent,
                    agent_name=agent_name,
                    subtask_key=subtask_key,
                    summary=resolved.credential_gap,
                    reason="dispatch_failed",
                )
                return {"error": resolved.credential_gap, "status": "failed", "agent": agent}

            await launcher.create_task_session(
                user_id,
                member_session,
                task_id=task_id,
                project_id=project_id,
                kind="task_subtask",
            )

            await run_ds.create_run(
                user_id,
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
                ),
            )
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="subtask_spawned",
                actor=agent,
                session_id=member_session.id,
                payload={
                    "agent": agent,
                    "agent_name": agent_name,
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
            user_id=user_id,
        )

        launcher.spawn_actor(
            self._actor,
            session_id=member_session.id,
            prompt=member_brief,
            role="subtask",
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            lead_session_id=lead_session_id,
        )

        return {
            "session_id": member_session.id,
            "agent": agent,
            "status": "dispatched",
        }
