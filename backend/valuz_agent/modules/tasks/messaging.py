"""Lead ↔ member / chat → task text DELIVERY (mailbox-backed).

Extracted from ``TaskOrchestrator`` (T1.1 split). Every function here is
stateless — it validates against the DB and posts an :class:`InboxMsg` to the
global ``mailbox_registry`` — so they live as module functions. Callers
(dispatch-MCP handlers in ``tools/handlers.py``, task routes) import this
module directly; there is deliberately no wrapper class in front of it.

Scope rule: **if it does not put something in a mailbox, it does not belong
here.** ``record_awaiting_user`` / ``record_user_answered`` used to live in
this file despite being pure task-event appends with no delivery at all (their
only caller is the decision-inbox aggregator); they now sit with the other
task-event writes in ``tasks/events.py``.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
    pick_lead_run,
)
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry


async def send_to_member(
    *,
    from_session_id: str,
    to_session_id: str,
    text: str,
    project_id: str,
    task_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Deliver a free-text follow-up from the lead to a running member.

    Task-level isolation (dual isolation): the target must be a member of
    the **caller lead's task**. The mailbox is a global per-session
    registry, so without this check a lead could (if it ever obtained a
    sibling task's session id) deliver across tasks. We refuse any target
    whose run doesn't belong to ``task_id``.
    """

    async with async_unit_of_work(commit=False) as db:
        target_run = await TaskSessionDatastore(db).get_run(to_session_id)
    if target_run is None or target_run.task_id != task_id or target_run.project_id != project_id:
        return {
            "delivered": False,
            "error": (
                f"session {to_session_id!r} is not a member of this task — "
                "you can only send to members you dispatched in this task"
            ),
        }

    delivered = mailbox_registry.put(
        to_session_id,
        InboxMsg(kind="text", text=text, from_session=from_session_id, origin="lead-send"),
    )
    if not delivered:
        return {
            "delivered": False,
            "error": (
                f"member session {to_session_id!r} is not running "
                "(already finished or never started)"
            ),
        }

    async with async_unit_of_work() as db:
        # ``subtask_message`` is the lead → member direction only. The member →
        # lead counterpart is ``subtask_reported`` (coordination.py); the two
        # shared this type — split apart by ``payload.direction`` — until
        # 2026-07. ``direction`` is kept on the payload because pre-split rows
        # carry it and it costs nothing; new readers key on the type.
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="subtask_message",
            actor="lead",
            session_id=to_session_id,
            payload={"direction": "lead->member", "text": text},
        )
    return {"delivered": True, "session_id": to_session_id}


async def inject_into_task(
    *,
    task_id: str,
    project_id: str,
    text: str,
    from_session_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Inject a free-text instruction from a chat session into a running task's lead.

    VALUZ-CHATPLAN S4: chat sessions can talk to an already-committed task
    without taking the writer-gate lock (which is lead-only on active
    tasks). The text is wrapped in a ``<user-instruction source="chat">``
    envelope so the lead recognises it as authoritative user intent (see
    COMMITTED_LEAD_PLAYBOOK §8) and typically converts it into modify_plan
    + dispatch (or rework).

    Returns ``{delivered: bool, lead_session_id: str | None, reason: str | None}``:
      - active task + registered lead inbox → ``delivered=True``
      - paused / blocked / stopped task → ``delivered=False,
        reason=TASK_HALTED`` plus ``task_status``. Reviving it is usually the
        right move, but it is ORCHESTRATION and therefore the caller's
        decision — this module only delivers.
      - no lead run found for the task → ``delivered=False, reason=NO_LEAD``
      - lead run exists but mailbox unregistered (already finished) →
        ``delivered=False, reason=LEAD_OFFLINE``
      - draft / completed / abandoned task →
        ``delivered=False, reason=TASK_NOT_ACTIVE``
    """

    async with async_unit_of_work(commit=False) as db:
        task_row = await TaskDatastore(db).get_task_by_project(
            user_id, project_id, task_id
        )
    if task_row is None:
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "TASK_NOT_FOUND",
        }
    if task_row.status in ("paused", "blocked", "stopped"):
        # Halted: the lead loop is torn down and its mailbox unregistered, so a
        # plain put() can never deliver. Reviving it IS usually what the user
        # meant — but reviving is orchestration, and deciding to do it is the
        # CALLER's call, not a delivery helper's. This module puts messages in
        # mailboxes; reaching up to the composition root for ``resume_task``
        # from here inverted the dependency (a leaf importing the root, through
        # a function-local import to dodge the cycle it created).
        #
        # So: report the state and let the caller act. ``tools/handlers`` and
        # ``routes/tasks`` both already hold the orchestrator.
        # ``completed`` is deliberately NOT in this set — reopening a finished
        # task is a deliberate act, not a side effect of a stray chat message.
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "TASK_HALTED",
            "task_status": task_row.status,
        }
    if task_row.status != "active":
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "TASK_NOT_ACTIVE",
        }

    async with async_unit_of_work(commit=False) as db:
        runs = await TaskSessionDatastore(db).list_runs(user_id, task_id)
    lead_run = pick_lead_run(runs)
    if lead_run is None:
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "NO_LEAD",
        }

    lead_session_id = lead_run.session_id
    wrapped = f'<user-instruction source="chat">\n{text}\n</user-instruction>'
    # OWNED, not merely registered. ``put`` succeeds whenever a box exists, and
    # a box can exist with no loop reading it (a sender pre-seeded it for a lead
    # that then failed to start). That reported ``delivered`` and wrote a
    # ``user_inject`` event while the message sat in a queue nobody drains —
    # strictly worse than LEAD_OFFLINE, which the caller can act on.
    delivered = mailbox_registry.is_owned(lead_session_id) and mailbox_registry.put(
        lead_session_id,
        InboxMsg(kind="text", text=wrapped, from_session=from_session_id, origin="user-inject"),
    )

    async with async_unit_of_work() as db:
        event_ds = TaskEventDatastore(db)
        if delivered:
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="user_inject",
                actor=from_session_id,
                session_id=lead_session_id,
                payload={"text": text, "lead_session_id": lead_session_id},
            )
        else:
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="user_inject_dropped",
                actor=from_session_id,
                session_id=lead_session_id,
                payload={"text": text, "reason": "LEAD_OFFLINE"},
            )

    return {
        "delivered": delivered,
        "lead_session_id": lead_session_id,
        "reason": None if delivered else "LEAD_OFFLINE",
    }


async def notify_lead_goal_revised(
    *,
    task_id: str,
    project_id: str,
    new_goal: str,
    user_id: str,
) -> dict[str, Any]:
    """Wake a running task's lead after the user revised ``task.goal``.

    MVP for the "push, not pull" goal-revision gap: the goal is the lead's
    initial brief AND its goal-mode loop condition, both baked into the session
    at spawn — a bare ``task.goal`` DB write never reaches a running lead. We
    deliver a ``revise_goal`` mailbox message so the lead re-orients on its next
    turn boundary (and preempts an in-flight ``await_member_results``); the lead
    decides autonomously how to fold it in. Best-effort: a finished/offline lead
    returns ``delivered=False`` — the DB goal is still updated by the caller.

    The wrapper surfaces the goal-mode caveat to the lead: the kernel's
    auto-completion may still track the ORIGINAL goal, so this revision is
    declared authoritative.
    """

    async with async_unit_of_work(commit=False) as db:
        runs = await TaskSessionDatastore(db).list_runs(user_id, task_id)
    lead_run = pick_lead_run(runs)
    if lead_run is None:
        return {"delivered": False, "lead_session_id": None, "reason": "NO_LEAD"}

    lead_session_id = lead_run.session_id
    wrapped = (
        '<goal-revised source="user">\n'
        "The task goal has been revised by the user to:\n\n"
        f"{new_goal}\n\n"
        "Re-evaluate your current plan against this revised goal. If the plan has "
        "drifted, use modify_plan to adjust, then continue; call finish_task only "
        "when the REVISED goal is met. (Goal-mode auto-completion may still track "
        "the original goal — treat this revision as authoritative.)\n"
        "</goal-revised>"
    )
    delivered = mailbox_registry.put(
        lead_session_id,
        InboxMsg(
            kind="revise_goal", text=wrapped, origin="goal-revised", payload={"goal": new_goal}
        ),
    )
    return {
        "delivered": delivered,
        "lead_session_id": lead_session_id,
        "reason": None if delivered else "LEAD_OFFLINE",
    }
