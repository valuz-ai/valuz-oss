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
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import InvalidRequestError

from valuz_agent.adapters.agent_resolver import (
    resolve_agent_display_name,
    resolve_agent_display_names,
)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.tasks.models import PLAN_SNAPSHOT_EVENT, TaskRow
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan
from valuz_agent.modules.tasks.plan_render import render_plan_md

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


_PLAN_CAS_RETRIES = 5


# What the lead is told when its review lost the CAS. It has to say "retry the
# same call" explicitly: a bare "conflict" reads as a dead end, and the model's
# default recovery from a failed review is to re-plan the subtask.
_RETRY_HINT = (
    "could not record the {action} of {key!r} — another writer changed the plan "
    "at the same moment. Nothing was written; call review_subtask again with the "
    "same arguments."
)


class PlanConflictError(RuntimeError):
    """The plan write was ABANDONED after losing every CAS round.

    Deliberately not a :class:`PlanError` — that means "this mutation is
    invalid" (bad key, illegal transition) and is answered by fixing the
    request. This means "your mutation was fine and did not land"; the answer
    is to try again. ``persist_plan`` returning ``None`` is the third, distinct
    outcome: ``mutate`` itself declined, so there was nothing to write.

    Collapsing conflict into ``None`` is what this type exists to prevent: the
    review doors then reported a lost write as ``no subtask with key 'x'``,
    which reads to the lead as "that key is gone" and steers it into re-planning
    instead of retrying.
    """


async def persist_plan(
    task_ds: TaskDatastore,
    event_ds: TaskEventDatastore,
    task_row: TaskRow,
    *,
    mutate: Callable[[TaskPlan], bool],
    actor: str,
    session_id: str | None,
    user_id: str,
) -> TaskPlan | None:
    """Mutate the plan and write it back — the ONE door for node mutations.

    ``mutate`` runs against the freshest plan and returns False to abort (its
    node is gone, or already in the target state). The write is a CAS on
    ``plan_version``: on conflict the row is refreshed and ``mutate`` is
    re-applied to the winner's plan, so concurrent writers (lead loop vs
    heartbeat vs a user stop) compose instead of silently reverting each
    other's nodes. Announcing (``task_plan_update``) always follows the write —
    persisting without it leaves the Todo panel silently stale.

    ``plan_task`` / ``modify_plan`` keep their own copy of the write+announce
    pair: they append ``task_planned`` / ``plan_revised`` BETWEEN the two (that
    order is on the wire) — but their write leg is the same CAS.

    Returns the persisted plan, or None when ``mutate`` declined (nothing to
    write). Raises :class:`PlanConflictError` when the write was valid but lost
    every CAS round — a caller that cannot act on that should say so by using
    :func:`persist_plan_best_effort`, not by ignoring the return value.
    """
    for _ in range(_PLAN_CAS_RETRIES):
        expected = task_row.plan_version or 0
        plan = TaskPlan.from_dict(task_row.plan)
        if not mutate(plan):
            return None
        try:
            wrote = await task_ds.cas_update_plan(
                user_id, task_row, plan.to_dict(), expected_version=expected
            )
        except InvalidRequestError:  # row vanished (task deleted concurrently)
            return None
        if wrote:
            await emit_plan_update(
                event_ds,
                task_row,
                plan,
                actor=actor,
                session_id=session_id,
                user_id=user_id,
                plan_version=expected + 1,
            )
            return plan
    raise PlanConflictError(
        f"plan write for task {task_row.id} lost {_PLAN_CAS_RETRIES} CAS rounds"
    )


async def persist_plan_best_effort(
    task_ds: TaskDatastore,
    event_ds: TaskEventDatastore,
    task_row: TaskRow,
    *,
    mutate: Callable[[TaskPlan], bool],
    actor: str,
    session_id: str | None,
    user_id: str,
    diverges: str,
) -> TaskPlan | None:
    """:func:`persist_plan` for sweeps that must keep going if the write is lost.

    Every caller here is a bookkeeping write inside a larger sequence — parking
    a node during a stop, reconciling after a crash, flipping a node on
    dispatch. Aborting the sequence over a lost node write would leave a worse
    state than the stale node does, and recovery's reconcile re-derives node
    status from the run rows anyway.

    ``diverges`` names what is now inconsistent, because that is the one thing
    the log cannot infer: "node stays 'planned' while its member runs" is
    actionable, "persist_plan gave up" is not.
    """
    try:
        return await persist_plan(
            task_ds,
            event_ds,
            task_row,
            mutate=mutate,
            actor=actor,
            session_id=session_id,
            user_id=user_id,
        )
    except PlanConflictError:
        logger.error(
            "task %s: plan write lost to concurrent writers — %s", task_row.id, diverges
        )
        return None


async def emit_plan_update(
    event_ds: TaskEventDatastore,
    task_row: TaskRow,
    plan: TaskPlan,
    *,
    actor: str,
    session_id: str | None,
    user_id: str,
    plan_version: int | None = None,
    structural: bool = False,
) -> None:
    """Append a ``task_plan_update`` SNAPSHOT — every field is load-bearing.

    A consumer must render the whole card from one event (SSE gives no
    guarantee the previous one was seen). ``plan_version`` in particular is
    the feed's dedup key: without it every event was silently discarded.
    Shape locked by test_plan_update_payload_is_a_self_contained_snapshot.

    ``plan_version``: the version THIS write installed (CAS ``expected + 1``).
    Pass it explicitly — re-reading the row can pick up a LATER writer's
    version (cas_update_plan refreshes after commit) and stamp it onto this
    older snapshot, making the feed's dedup drop the real newer snapshot.

    ``structural``: True only when the plan DOCUMENT changed (plan_task /
    modify_plan — nodes added or re-specified), False for execution progress
    (a node flipping dispatched → in_review → done). The chat plan-card feed
    spawns a NEW card per structural revision and updates in place otherwise;
    without the flag, every node flip would append another card to the
    conversation (every write bumps the version — that is the CAS token's
    job, not a UI signal).
    """
    panel = plan.to_panel()
    # Stamp each node's member display name so the Todo panel renders it
    # directly rather than joining the ``agent`` slug against an async members
    # list (which races the load / misses removed agents — the same
    # "成员智能体名称查询不到" bug as the timeline). Batched into one read UoW.
    names = await resolve_agent_display_names(
        task_row.project_id, [n["agent"] for n in panel], user_id
    )
    for n in panel:
        slug = n.get("agent")
        if slug:
            n["agent_name"] = names.get(slug, slug)
    await event_ds.append_event(
        user_id,
        project_id=task_row.project_id,
        task_id=task_row.id,
        type=PLAN_SNAPSHOT_EVENT,
        actor=actor,
        session_id=session_id,
        payload={
            "subtasks": panel,
            # Monotonic CAS token — the consumer's dedup/ordering key.
            "plan_version": (
                plan_version if plan_version is not None else task_row.plan_version or 0
            ),
            # Did the plan DOCUMENT change (vs execution progress)? Drives
            # "new card" vs "update the card" in the chat feed.
            "structural": structural,
            # Named ``task_status``, not ``status``: a plan snapshot also
            # carries per-node statuses, and an unqualified ``status`` in this
            # payload reads as "the plan's".
            "task_status": task_row.status,
            "title": task_row.title,
        },
    )


# ---------------------------------------------------------------------------
# Lead plan service — plan_task / get_plan / modify_plan / review_subtask
# ---------------------------------------------------------------------------


async def plan_task(
    *,
    task_id: str,
    project_id: str,
    lead_session_id: str,
    subtasks: list[dict[str, Any]],
    user_id: str,
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
        task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
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
        expected = task_row.plan_version or 0
        if not await task_ds.cas_update_plan(
            user_id, task_row, plan.to_dict(), expected_version=expected
        ):
            return {
                "error": "PLAN_VERSION_CONFLICT",
                "current_version": task_row.plan_version or 0,
                "hint": "another writer changed the plan mid-call; re-read and retry",
            }
        # Stamp the version THIS write installed — the refreshed row may
        # already carry a later writer's version (see emit_plan_update).
        installed = expected + 1
        await event_ds.append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="task_planned",
            actor=lead_session_id,
            session_id=lead_session_id,
            payload={**plan.to_dict(), "plan_version": installed},
        )
        await emit_plan_update(
            event_ds,
            task_row,
            plan,
            actor=lead_session_id,
            session_id=lead_session_id,
            user_id=user_id,
            plan_version=installed,
            structural=True,
        )
        render_plan_md(task_row, plan)
        return {
            "subtasks": plan.to_panel(),
            "ready": plan.ready_keys(),
            "current_version": installed,
        }


async def get_plan(*, task_id: str, project_id: str, user_id: str) -> dict[str, Any]:
    """Return the plan snapshot + ready keys + status counts (read-only).

    Includes ``current_version`` so the caller knows what to pass as
    ``expected_version`` on the next ``modify_plan`` call.
    """
    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
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
    project_id: str,
    lead_session_id: str,
    add: list[dict[str, Any]] | None = None,
    update: list[dict[str, Any]] | None = None,
    expected_version: int | None = None,
    user_id: str,
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
        task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
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
        if not await task_ds.cas_update_plan(
            user_id, task_row, plan.to_dict(), expected_version=current_version
        ):
            return {
                "error": "PLAN_VERSION_CONFLICT",
                "current_version": task_row.plan_version or 0,
                "you_passed": expected_version,
                "hint": (
                    "another writer changed the plan mid-call; call get_plan to "
                    "re-read, merge your changes, then retry"
                ),
            }
        installed = current_version + 1  # the version THIS write installed
        await event_ds.append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="plan_revised",
            actor=lead_session_id,
            session_id=lead_session_id,
            payload={
                "add": add or [],
                "update": update or [],
                "plan_version": installed,
            },
        )
        await emit_plan_update(
            event_ds,
            task_row,
            plan,
            actor=lead_session_id,
            session_id=lead_session_id,
            user_id=user_id,
            plan_version=installed,
            structural=True,
        )
        render_plan_md(task_row, plan)
        return {
            "subtasks": plan.to_panel(),
            "ready": plan.ready_keys(),
            "current_version": installed,
        }


async def review_subtask(
    *,
    task_id: str,
    project_id: str,
    lead_session_id: str,
    decision: str,
    subtask_key: str | None = None,
    session_id: str | None = None,
    feedback: str | None = None,
    user_id: str,
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
        task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
        if task_row is None:
            return {"error": f"task {task_id!r} not found"}
        if task_row.status != "active":
            # Same rationale as plan_commands' writable-status guard: a halted
            # (paused/stopped/completed) task's plan must not move under review.
            return {"error": f"task is {task_row.status!r} — review applies to an active task"}
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
            task_row = await task_ds.get_task_by_project(
                user_id, project_id, task_id
            )
            # Re-guard. Phase 1 read on a SEPARATE read-only unit of work, so
            # everything it established can be gone by now: the task may have
            # been deleted, and the node may have been dropped by a concurrent
            # modify_plan. Without these two checks the next lines raise
            # (AttributeError on None / PlanError on a missing key) and the tool
            # returns a 500 instead of the same actionable error phase 1 gives.
            if task_row is None:
                return {"error": f"task {task_id!r} not found"}
            if task_row.status != "active":
                return {
                    "error": f"task is {task_row.status!r} — review applies to an active task"
                }
            fresh_plan = TaskPlan.from_dict(task_row.plan)
            node = fresh_plan.get(key)
            if node is None:
                return {"error": f"no subtask with key {key!r}"}
            if node.status == "done":
                # Duplicate approve (double tool call / CAS-race echo): the
                # first one already announced and settled — re-announcing
                # appends a second subtask_reviewed/subtask_completed pair.
                return {
                    "decision": "approve",
                    "subtask_key": key,
                    "already_done": True,
                    "ready": fresh_plan.ready_keys(),
                    "all_done": fresh_plan.all_done(),
                }

            def _approve(p: TaskPlan) -> bool:
                if p.get(key) is None:
                    return False
                p.update_node(key, status="done", review_feedback=None)
                return True

            try:
                persisted = await persist_plan(
                    task_ds,
                    event_ds,
                    task_row,
                    mutate=_approve,
                    actor=lead_session_id,
                    session_id=lead_session_id,
                    user_id=user_id,
                )
            except PlanError as exc:
                # e.g. approving a never-dispatched node — the transition table
                # (plan.NODE_TRANSITIONS) refuses planned → done.
                return {"error": f"invalid review: {exc}"}
            except PlanConflictError:
                return {"error": _RETRY_HINT.format(action="approve", key=key)}
            if persisted is None:
                return {"error": f"no subtask with key {key!r}"}
            if target_session:
                await run_ds.update_run_by_session(
                    session_id=target_session,
                    status="completed",
                    ended_at=now_ms(),
                )
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="subtask_reviewed",
                actor=lead_session_id,
                session_id=target_session,
                payload={"subtask_key": key, "decision": "approve", "feedback": feedback or ""},
            )
            completed_agent = node.agent or ""
            # Stamp the member's display name into the payload so the frontend
            # renders it directly rather than joining the ``actor`` slug against
            # an async members list (which races the load / misses removed
            # agents — and here ``actor`` can even be empty). See
            # ``resolve_agent_display_name``.
            completed_agent_name = await resolve_agent_display_name(
                project_id, completed_agent, user_id
            )
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="subtask_completed",
                actor=completed_agent,
                session_id=target_session,
                payload={
                    "subtask_key": key,
                    "title": node.title if node else key,
                    "agent_name": completed_agent_name,
                },
            )
            render_plan_md(task_row, persisted)
            return {
                "decision": "approve",
                "subtask_key": key,
                "ready": persisted.ready_keys(),
                "all_done": persisted.all_done(),
            }

    # decision == "rework": mailbox delivery must run on the event loop
    # (asyncio.Queue is NOT thread-safe), then the DB write reflects it.

    delivered = False
    # OWNED, not merely registered — an unread box would report the rework
    # as delivered and then silently swallow it (the member re-dispatches
    # from the plan node instead).
    if target_session and mailbox_registry.is_owned(target_session):
        delivered = mailbox_registry.put(
            target_session,
            InboxMsg(
                kind="text",
                from_session=lead_session_id,
                origin="rework",
                text=f"Your previous attempt was sent back for rework.\n\n{feedback or ''}",
            ),
        )

    async with async_unit_of_work() as db:
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
        # Re-guard — see the approve branch: phase 1 ran on a separate
        # read-only unit of work, so neither the task nor the node is
        # guaranteed to still exist.
        if task_row is None:
            return {"error": f"task {task_id!r} not found"}
        if task_row.status != "active":
            return {"error": f"task is {task_row.status!r} — review applies to an active task"}
        if TaskPlan.from_dict(task_row.plan).get(key) is None:
            return {"error": f"no subtask with key {key!r}"}

        def _rework(p: TaskPlan) -> bool:
            if p.get(key) is None:
                return False
            p.update_node(
                key,
                status="in_progress" if delivered else "rework",
                review_feedback=feedback,
            )
            return True

        try:
            persisted = await persist_plan(
                task_ds,
                event_ds,
                task_row,
                mutate=_rework,
                actor=lead_session_id,
                session_id=lead_session_id,
                user_id=user_id,
            )
        except PlanError as exc:
            return {"error": f"invalid review: {exc}"}
        except PlanConflictError:
            return {"error": _RETRY_HINT.format(action="rework", key=key)}
        if persisted is None:
            return {"error": f"no subtask with key {key!r}"}
        await event_ds.append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="subtask_reviewed",
            actor=lead_session_id,
            session_id=target_session,
            payload={"subtask_key": key, "decision": "rework", "feedback": feedback or ""},
        )
        render_plan_md(task_row, persisted)
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
) -> tuple[str, str] | Failure:
    """Plan-first gate for dispatch. Returns ``(agent, goal)`` or a Failure.

    A node is dispatchable when it exists, its status is ``planned``,
    ``rework`` (re-dispatch after sync rework), or ``paused`` (re-dispatch a
    node parked by a user pause/stop whose member run did not survive resume),
    and all its deps are ``done``. agent/goal come from the node unless the
    caller overrides them.
    """
    node = plan.get(subtask_key)
    if node is None:
        return Failure(
            f"no subtask {subtask_key!r} in the plan — call plan_task first, "
            "then dispatch by subtask_key"
        )
    if node.status not in ("planned", "rework", "paused"):
        return Failure(f"subtask {subtask_key!r} is {node.status!r}, not dispatchable")
    done = {n.key for n in plan.nodes if n.status == "done"}
    unmet = [d for d in node.depends_on if d not in done]
    if unmet:
        return Failure(f"subtask {subtask_key!r} is blocked on unfinished deps: {unmet}")
    agent = (agent_override or node.agent or "").strip()
    if not agent:
        return Failure(
            f"subtask {subtask_key!r} has no agent — set one in the plan or pass agent"
        )
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
    project_id: str,
    task_id: str,
    subtask_key: str,
    agent: str,
    session_id: str,
    user_id: str,
) -> None:
    """Flip a plan node to in_progress on dispatch (attempts++, link run)."""
    async with async_unit_of_work() as db:
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        task_row = await task_ds.get_task_by_project(user_id, project_id, task_id)
        if task_row is None:
            return

        def _dispatch(p: TaskPlan) -> bool:
            node = p.get(subtask_key)
            if node is None or node.status not in ("planned", "rework", "paused"):
                return False  # mirrors the resolve_dispatch_node gate
            p.update_node(
                subtask_key,
                status="in_progress",
                attempts=node.attempts + 1,
                agent=agent,
                latest_run_session_id=session_id,
            )
            return True

        await persist_plan_best_effort(
            task_ds,
            event_ds,
            task_row,
            mutate=_dispatch,
            actor=agent,
            session_id=session_id,
            user_id=user_id,
            # The member spawns right after this call either way — raising here
            # would strand a session row + subtask_spawned event with no actor.
            diverges=f"node {subtask_key!r} stays pre-dispatch while its member "
            f"session {session_id} runs; recovery reconcile repairs it",
        )


async def mark_in_review(
    *, task_id: str, project_id: str, member_session_id: str, user_id: str
) -> None:
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
            task_row = await task_ds.get_task_by_project(
                user_id, project_id, task_id
            )
            if task_row is None:
                return

            def _in_review(p: TaskPlan) -> bool:
                node = p.get(key)
                if node is None or node.status not in ("in_progress", "rework"):
                    return False
                p.update_node(key, status="in_review")
                return True

            await persist_plan_best_effort(
                task_ds,
                event_ds,
                task_row,
                mutate=_in_review,
                actor="system",
                session_id=member_session_id,
                user_id=user_id,
                diverges=f"node {key!r} stays in_progress though its member "
                "finished and is awaiting review",
            )
    except Exception:  # noqa: BLE001
        logger.debug("mark_in_review skipped for %s", member_session_id, exc_info=True)
