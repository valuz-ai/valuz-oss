"""Task plan authoring, review, and plan-node mutation (VALUZ-TASK / CHATPLAN).

Extracted from ``TaskOrchestrator`` (T1.1 god-object split). The whole cluster
is **stateless** — every function operates on the DB (TaskDatastore et al.) +
the ``TaskPlan`` value object, holding no orchestrator instance state — so it
lives as plain module functions with a one-directional ``orchestrator →
planning`` dependency.

Two groups share this module:

- **Lead plan service** — ``plan_task`` / ``get_plan`` / ``modify_plan`` /
  ``review_subtask``: the public surface the dispatch-MCP tools + task routes
  drive (today via thin ``TaskOrchestrator`` delegators).
- **Plan-node mutations** — ``resolve_dispatch_node`` / ``mark_node_dispatched``
  / ``mark_in_review`` + the shared ``emit_plan_update`` / ``render_plan_md``
  primitives: called by the orchestrator's dispatch / actor / recovery methods.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskRow
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


async def emit_plan_update(
    event_ds: TaskEventDatastore,
    *,
    workspace_id: str,
    task_id: str,
    plan: TaskPlan,
    actor: str,
    session_id: str | None,
) -> None:
    """Append a ``task_plan_update`` snapshot event (frontend Todo panel)."""
    await event_ds.append_event(
        workspace_id=workspace_id,
        task_id=task_id,
        type="task_plan_update",
        actor=actor,
        session_id=session_id,
        payload={"subtasks": plan.to_panel()},
    )


def render_plan_md(task_row: TaskRow, plan: TaskPlan) -> None:
    """Best-effort mirror of the plan into the task markdown file (file-as-truth).

    Never raises — the DB plan column is the source of truth; the md is a
    human/agent-readable mirror.
    """
    try:
        path = Path(task_row.file_path)
        lines = [f"# {task_row.title}", "", f"> Goal: {task_row.goal}", "", "## Plan", ""]
        for n in plan.nodes:
            deps = f" (after: {', '.join(n.depends_on)})" if n.depends_on else ""
            agent = f" — {n.agent}" if n.agent else ""
            lines.append(f"- [{n.status}] **{n.key}**{agent}: {n.title}{deps}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("plan md render skipped for task %s", task_row.id, exc_info=True)


# ---------------------------------------------------------------------------
# Lead plan service — plan_task / get_plan / modify_plan / review_subtask
# ---------------------------------------------------------------------------


async def plan_task(
    *,
    task_id: str,
    workspace_id: str,
    lead_session_id: str,
    subtasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Lay down the structured subtask plan (DAG) before any dispatch.

    Callable from both draft-state (by the originating chat session, see
    ``_check_plan_writer_gate``) and active-state (by the lead, legacy
    kickoff path). Fails if a plan with execution progress already exists
    — use ``modify_plan`` to change a plan whose subtasks have started.
    Bumps ``plan_version`` on success (CAS token for concurrent writers).

    Returns ``{subtasks, ready, current_version}``.
    """
    async with async_unit_of_work() as db:
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
        if task_row is None:
            return {"error": f"task {task_id!r} not found"}
        existing = TaskPlan.from_dict(task_row.plan)
        if not existing.is_empty and any(n.status != "planned" for n in existing.nodes):
            return {"error": "a plan with progress already exists — use modify_plan to change it"}
        if not subtasks:
            return {"error": "plan_task: 'subtasks' is required and must be non-empty"}
        try:
            plan = TaskPlan()
            plan.add(subtasks)
        except PlanError as exc:
            return {"error": f"invalid plan: {exc}"}
        task_row.plan = plan.to_dict()
        task_row.plan_version = (task_row.plan_version or 0) + 1
        await task_ds.update_task(task_row)
        await event_ds.append_event(
            workspace_id=workspace_id,
            task_id=task_id,
            type="task_planned",
            actor=lead_session_id,
            session_id=lead_session_id,
            payload={**plan.to_dict(), "plan_version": task_row.plan_version},
        )
        await emit_plan_update(
            event_ds,
            workspace_id=workspace_id,
            task_id=task_id,
            plan=plan,
            actor=lead_session_id,
            session_id=lead_session_id,
        )
        render_plan_md(task_row, plan)
        return {
            "subtasks": plan.to_panel(),
            "ready": plan.ready_keys(),
            "current_version": task_row.plan_version,
        }


async def get_plan(*, task_id: str, workspace_id: str) -> dict[str, Any]:
    """Return the plan snapshot + ready keys + status counts (read-only).

    Includes ``current_version`` so the caller knows what to pass as
    ``expected_version`` on the next ``modify_plan`` call.
    """
    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
        if task_row is None:
            return {"error": f"task {task_id!r} not found"}
        plan = TaskPlan.from_dict(task_row.plan)
        return {
            "subtasks": plan.to_panel(),
            "ready": plan.ready_keys(),
            "counts": plan.counts(),
            "all_done": plan.all_done(),
            "current_version": task_row.plan_version or 0,
        }


async def modify_plan(
    *,
    task_id: str,
    workspace_id: str,
    lead_session_id: str,
    add: list[dict[str, Any]] | None = None,
    update: list[dict[str, Any]] | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Mutate the plan: add nodes / patch nodes (by key).

    Validates the DAG after each batch. ``update`` items are dicts with a
    ``key`` plus the fields to change (e.g. goal/agent/depends_on/title).
    Node REMOVAL is intentionally not supported — subtasks are a durable
    record of the plan; to retire one, patch its goal/deps via ``update``
    rather than deleting it.

    CAS optimistic-lock (VALUZ-CHATPLAN D1): when ``expected_version`` is
    passed it must equal the task's current ``plan_version`` or this
    returns ``{"error": "PLAN_VERSION_CONFLICT", ...}`` — caller refreshes
    via get_plan and retries. Lead callers (single-actor, no concurrent
    writers) may omit it; chat callers (multi-session concurrency)
    should always pass it.

    Bumps ``plan_version`` on success.
    """
    async with async_unit_of_work() as db:
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
        if task_row is None:
            return {"error": f"task {task_id!r} not found"}
        current_version = task_row.plan_version or 0
        if expected_version is not None and expected_version != current_version:
            return {
                "error": "PLAN_VERSION_CONFLICT",
                "current_version": current_version,
                "you_passed": expected_version,
                "hint": (
                    "call get_plan to read the latest plan + current_version, "
                    "merge your changes against it, then retry"
                ),
            }
        plan = TaskPlan.from_dict(task_row.plan)
        try:
            if add:
                plan.add(add)
            for patch in update or []:
                key = str(patch.get("key") or "")
                fields = {k: v for k, v in patch.items() if k != "key"}
                plan.update_node(key, **fields)
        except PlanError as exc:
            return {"error": f"invalid plan change: {exc}"}
        task_row.plan = plan.to_dict()
        task_row.plan_version = current_version + 1
        await task_ds.update_task(task_row)
        await event_ds.append_event(
            workspace_id=workspace_id,
            task_id=task_id,
            type="plan_revised",
            actor=lead_session_id,
            session_id=lead_session_id,
            payload={
                "add": add or [],
                "update": update or [],
                "plan_version": task_row.plan_version,
            },
        )
        await emit_plan_update(
            event_ds,
            workspace_id=workspace_id,
            task_id=task_id,
            plan=plan,
            actor=lead_session_id,
            session_id=lead_session_id,
        )
        render_plan_md(task_row, plan)
        return {
            "subtasks": plan.to_panel(),
            "ready": plan.ready_keys(),
            "current_version": task_row.plan_version,
        }


async def review_subtask(
    *,
    task_id: str,
    workspace_id: str,
    lead_session_id: str,
    decision: str,
    subtask_key: str | None = None,
    session_id: str | None = None,
    feedback: str | None = None,
) -> dict[str, Any]:
    """Lead quality gate on a subtask: approve (→done) or rework (→re-run).

    ``subtask_key`` or ``session_id`` (the member run) identifies the node.
    approve: node→done, run→completed, dependents unlock.
    rework: store feedback; async → deliver to the live member via the
    mailbox (it redoes); sync → node→rework so the lead re-dispatches by key.
    """
    if decision not in ("approve", "rework"):
        return {"error": "decision must be 'approve' or 'rework'"}

    # Phase 1 (DB read): resolve the node key + its target run session.
    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        run_ds = TaskSessionDatastore(db)
        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
        if task_row is None:
            return {"error": f"task {task_id!r} not found"}
        plan = TaskPlan.from_dict(task_row.plan)
        key = subtask_key
        if not key and session_id:
            run = await run_ds.get_run(session_id)
            key = run.subtask_key if run else None
        if not key:
            return {"error": "review_subtask: provide subtask_key or a member session_id"}
        node = plan.get(key)
        if node is None:
            return {"error": f"no subtask with key {key!r}"}
        target_session = session_id or node.latest_run_session_id

    if decision == "approve":
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
            plan = TaskPlan.from_dict(task_row.plan)
            node = plan.get(key)
            plan.update_node(key, status="done", review_feedback=None)
            if target_session:
                await run_ds.update_run_by_session(
                    session_id=target_session,
                    status="completed",
                    ended_at=now_ms(),
                )
            await event_ds.append_event(
                workspace_id=workspace_id,
                task_id=task_id,
                type="subtask_reviewed",
                actor=lead_session_id,
                session_id=target_session,
                payload={"subtask_key": key, "decision": "approve", "feedback": feedback or ""},
            )
            await event_ds.append_event(
                workspace_id=workspace_id,
                task_id=task_id,
                type="subtask_completed",
                actor=(node.agent or "") if node else "",
                session_id=target_session,
                payload={"subtask_key": key, "title": node.title if node else key},
            )
            task_row.plan = plan.to_dict()
            await task_ds.update_task(task_row)
            await emit_plan_update(
                event_ds,
                workspace_id=workspace_id,
                task_id=task_id,
                plan=plan,
                actor=lead_session_id,
                session_id=lead_session_id,
            )
            render_plan_md(task_row, plan)
            return {
                "decision": "approve",
                "subtask_key": key,
                "ready": plan.ready_keys(),
                "all_done": plan.all_done(),
            }

    # decision == "rework": mailbox delivery must run on the event loop
    # (asyncio.Queue is NOT thread-safe), then the DB write reflects it.
    from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

    delivered = False
    if target_session and mailbox_registry.is_registered(target_session):
        delivered = mailbox_registry.put(
            target_session,
            InboxMsg(
                kind="message",
                from_session=lead_session_id,
                text=f"Your previous attempt was sent back for rework.\n\n{feedback or ''}",
            ),
        )

    async with async_unit_of_work() as db:
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
        plan = TaskPlan.from_dict(task_row.plan)
        plan.update_node(
            key,
            status="in_progress" if delivered else "rework",
            review_feedback=feedback,
        )
        await event_ds.append_event(
            workspace_id=workspace_id,
            task_id=task_id,
            type="subtask_reviewed",
            actor=lead_session_id,
            session_id=target_session,
            payload={"subtask_key": key, "decision": "rework", "feedback": feedback or ""},
        )
        task_row.plan = plan.to_dict()
        await task_ds.update_task(task_row)
        await emit_plan_update(
            event_ds,
            workspace_id=workspace_id,
            task_id=task_id,
            plan=plan,
            actor=lead_session_id,
            session_id=lead_session_id,
        )
        render_plan_md(task_row, plan)
        return {
            "decision": "rework",
            "subtask_key": key,
            "delivered_to_live_member": delivered,
            "next": (
                "the live member is redoing; wait for its next result"
                if delivered
                else "re-dispatch this subtask by key when ready"
            ),
        }


# ---------------------------------------------------------------------------
# Plan-node mutations — used by the orchestrator's dispatch / actor methods
# ---------------------------------------------------------------------------


def resolve_dispatch_node(
    plan: TaskPlan, subtask_key: str, agent_override: str | None, goal_override: str | None
) -> tuple[str, str] | str:
    """Plan-first gate for dispatch. Returns (agent, goal) or an error string.

    A node is dispatchable when it exists, its status is ``planned``,
    ``rework`` (re-dispatch after sync rework), or ``paused`` (re-dispatch a
    node parked by a user pause/stop whose member run did not survive resume),
    and all its deps are ``done``. agent/goal come from the node unless the
    caller overrides them.
    """
    node = plan.get(subtask_key)
    if node is None:
        return (
            f"no subtask {subtask_key!r} in the plan — call plan_task first, "
            "then dispatch by subtask_key"
        )
    if node.status not in ("planned", "rework", "paused"):
        return f"subtask {subtask_key!r} is {node.status!r}, not dispatchable"
    done = {n.key for n in plan.nodes if n.status == "done"}
    unmet = [d for d in node.depends_on if d not in done]
    if unmet:
        return f"subtask {subtask_key!r} is blocked on unfinished deps: {unmet}"
    agent = (agent_override or node.agent or "").strip()
    if not agent:
        return f"subtask {subtask_key!r} has no agent — set one in the plan or pass agent"
    goal = goal_override or node.goal or node.title
    # Re-dispatch after a sync rework: fold the lead's review feedback into
    # the brief so the member knows WHY its prior attempt was sent back
    # (the async path delivers feedback via the mailbox instead). Attempt
    # count makes the retry explicit.
    if node.status == "rework" and node.review_feedback:
        goal = (
            f"{goal}\n\n## Rework feedback (attempt {node.attempts + 1})\n\n"
            f"Your previous attempt was sent back. Address this:\n{node.review_feedback}"
        )
    return agent, goal


async def mark_node_dispatched(
    *,
    workspace_id: str,
    task_id: str,
    subtask_key: str,
    agent: str,
    session_id: str,
) -> None:
    """Flip a plan node to in_progress on dispatch (attempts++, link run)."""
    async with async_unit_of_work() as db:
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
        if task_row is None:
            return
        plan = TaskPlan.from_dict(task_row.plan)
        node = plan.get(subtask_key)
        if node is None:
            return
        plan.update_node(
            subtask_key,
            status="in_progress",
            attempts=node.attempts + 1,
            agent=agent,
            latest_run_session_id=session_id,
        )
        task_row.plan = plan.to_dict()
        await task_ds.update_task(task_row)
        await emit_plan_update(
            event_ds,
            workspace_id=workspace_id,
            task_id=task_id,
            plan=plan,
            actor=agent,
            session_id=session_id,
        )


async def mark_in_review(*, task_id: str, workspace_id: str, member_session_id: str) -> None:
    """Lead-side: flip the member's plan node to in_review on member_done.

    Runs inside the lead's actor loop (single actor, D7) so plan writes stay
    serialized. Best-effort — a member with no plan node (legacy/free
    dispatch) is a no-op.
    """
    try:
        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run = await run_ds.get_run(member_session_id)
            key = run.subtask_key if run else None
            if not key:
                return
            task_row = await task_ds.get_task_by_workspace(workspace_id, task_id)
            if task_row is None:
                return
            plan = TaskPlan.from_dict(task_row.plan)
            node = plan.get(key)
            if node is None or node.status not in ("in_progress", "rework"):
                return
            plan.update_node(key, status="in_review")
            task_row.plan = plan.to_dict()
            await task_ds.update_task(task_row)
            await emit_plan_update(
                event_ds,
                workspace_id=workspace_id,
                task_id=task_id,
                plan=plan,
                actor="system",
                session_id=member_session_id,
            )
    except Exception:  # noqa: BLE001
        logger.debug("mark_in_review skipped for %s", member_session_id, exc_info=True)
