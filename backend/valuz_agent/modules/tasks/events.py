"""The tasks module's EVENT WRITE surface — timeline rows + bus topics.

Two things live here, both "something happened to this task, record it":

  * :func:`finalize_task` — the composed terminal write (status flip + bus
    announce + terminal timeline event). Runs on the caller's unit of work.
  * :func:`record_awaiting_user` / :func:`record_user_answered` — timeline
    rows projected from the cross-cutting Decision Inbox. These open their own
    unit of work (their caller, ``modules/decisions/aggregator.py``, has no
    task transaction to join).

ADR-001 additive contract: the bus event NAME and payload FIELD NAMES are the
frozen surface commercial overlays subscribe to (an overlay mirrors the string
rather than importing this module — keep both in sync).

Not here: mailbox DELIVERY (lead↔member text, chat→task inject) — that is
``tasks/messaging.py``. The split is "does it put something in a mailbox?".
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskEventDatastore

logger = logging.getLogger(__name__)

# Published (best-effort) whenever a task reaches a TERMINAL status —
# ``completed`` / ``stopped`` / ``blocked`` — from every terminal write site
# (finish_task, auto-finalize, sync-kickoff failure, health monitor). Payload:
# ``task_id``, ``owner_user_id``, ``status``. First consumer: the commercial
# sandbox allocator clamps the ``task:{id}`` scope sandbox's TTL so a finished
# task's instance is reclaimed after a short grace instead of lingering for the
# full active window (24h under the platform-TTL lease model).
TASK_FINALIZED = "task.finalized"


def publish_task_finalized(task_id: str, owner_user_id: str, status: str) -> None:
    """Announce a task's terminal status on the in-process bus.

    Best-effort by contract: subscribers run synchronously on the global bus,
    so this must never let a subscriber error (or a missing bus) affect task
    finalization.

    Prefer :func:`finalize_task` — it composes this announce with the status
    flip and the terminal event so no write site can ship a partial terminal.
    """
    try:
        from valuz_agent.infra.eventbus import event_bus

        event_bus.publish(
            TASK_FINALIZED, task_id=task_id, owner_user_id=owner_user_id, status=status
        )
    except Exception:  # noqa: BLE001 — never let a subscriber break finalize
        logger.debug("task.finalized publish failed for %s", task_id, exc_info=True)


async def finalize_task(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    status: str,
    event_type: str,
    actor: str,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:  # returns the appended TaskEventRow
    """THE terminal write — every site that ends a task goes through here:
    status flip (through the ``task_state`` guard) + ``task.finalized`` bus
    announce + the terminal event row (returned, for notifications).

    ONE CALL SITE, not one transaction: each datastore write commits on its
    own (repo-wide convention) and the bus publish cannot roll back, so a
    crash between legs leaves a terminal status without its event — readers
    must tolerate that. The value is that no site can FORGET a leg, which is
    the bug class this replaced.

    Returns None when the status flip lost a concurrent race (the winner
    already announced its own terminal) — announce and event are skipped so
    two finalizers can't publish contradictory terminals for one task.
    """
    if not await TaskDatastore(db).update_task_status(user_id, task_id, status):
        logger.error(
            "finalize_task: task %s → %r lost a concurrent status race — "
            "skipping announce/event (the winner recorded its own terminal)",
            task_id,
            status,
        )
        return None
    publish_task_finalized(task_id, user_id, status)
    event_payload = payload or {}
    event = await TaskEventDatastore(db).append_event(
        user_id,
        project_id=project_id,
        task_id=task_id,
        type=event_type,
        actor=actor,
        session_id=session_id,
        payload=event_payload,
    )
    if status == "completed":
        from valuz_agent.modules.notifications.projectors import (
            record_task_completion_notification,
        )

        await record_task_completion_notification(
            task_id=task_id,
            project_id=project_id,
            event_id=event.id,
            summary=str(event_payload.get("summary") or ""),
            user_id=user_id,
        )
    return event


async def block_task(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    event_type: str,
    actor: str,
    reason: str,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:  # returns the appended TaskEventRow
    """Put a task into ``blocked`` AND raise the user-facing notification.

    ``blocked`` is the single "needs your attention" state, so the
    notification is part of the transition — a blocked task nobody is told
    about is a task that silently stops. ``reason`` is the human line, folded
    into both the notification and the event payload so they cannot disagree.
    """
    from valuz_agent.modules.notifications.projectors import (
        record_task_failure_notification,
    )

    event = await finalize_task(
        db,
        user_id=user_id,
        project_id=project_id,
        task_id=task_id,
        status="blocked",
        event_type=event_type,
        actor=actor,
        session_id=session_id,
        payload={**(payload or {}), "error": reason},
    )
    if event is None:  # lost the terminal race — winner owns the notification
        return None
    await record_task_failure_notification(
        task_id=task_id,
        project_id=project_id,
        event_id=event.id,
        event_type=event_type,
        reason=reason,
        user_id=user_id,
    )
    return event


# ---------------------------------------------------------------------------
# Subtask outcome events — one emitter each, every key always populated (they
# used to be emitted from 3/2 places with a different payload per path, so no
# consumer could rely on any field). Add a field HERE, not at a call site.
# ---------------------------------------------------------------------------


async def record_subtask_failed(
    event_ds: TaskEventDatastore,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    session_id: str | None,
    agent_slug: str,
    agent_name: str | None,
    subtask_key: str | None,
    summary: str,
    reason: str,
    artifacts: list[Any] | None = None,
) -> None:
    """A member run ended in failure.

    ``reason`` is the MACHINE-readable cause — which path detected it
    (``dispatch_failed`` / ``heartbeat_detected`` / ``run_error``); ``summary``
    is the human line the timeline shows.
    """
    await event_ds.append_event(
        user_id,
        project_id=project_id,
        task_id=task_id,
        type="subtask_failed",
        actor=agent_slug,
        session_id=session_id,
        payload={
            "agent": agent_slug,
            "agent_name": agent_name,
            "subtask_key": subtask_key,
            "status": "failed",
            "summary": summary,
            "reason": reason,
            "artifacts": artifacts or [],
        },
    )


async def record_subtask_stopped(
    event_ds: TaskEventDatastore,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    session_id: str | None,
    agent_slug: str,
    agent_name: str | None,
    subtask_key: str | None,
) -> None:
    """A member run was stopped by the user — not a failure.

    ``actor`` is ``"user"`` rather than the agent: the timeline renders this
    amber-not-red precisely because a person chose it.
    """
    await event_ds.append_event(
        user_id,
        project_id=project_id,
        task_id=task_id,
        type="subtask_stopped",
        actor="user",
        session_id=session_id,
        payload={
            "agent": agent_slug,
            "agent_name": agent_name,
            "subtask_key": subtask_key,
        },
    )


# ---------------------------------------------------------------------------
# Decision-Inbox projections — the timeline trace of a pending user question
# (which otherwise blocks the turn invisibly). Sole caller:
# modules/decisions/aggregator.py; they open their own UoW because it has no
# task transaction to join.
# ---------------------------------------------------------------------------


async def record_awaiting_user(
    *,
    task_id: str,
    project_id: str,
    session_id: str,
    subtask_key: str | None,
    agent_slug: str,
    agent_name: str | None,
    question: str,
    pending_id: str,
    user_id: str,
) -> None:
    """Append an ``awaiting_user`` task event when an agent (lead or member)
    raises a question through the Decision Inbox.

    Without this the task page shows "Running" while the task is actually
    blocked on the user. We do NOT add an ``awaiting_user`` task *status* (the
    task genuinely is still active, and a status would need racy atomic
    clearing on answer) — this event is the timeline record + the SSE frame the
    attention surfaces drive from. Deduped by ``pending_id`` at the caller (the
    aggregator tracks emitted ids per process).
    """
    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="awaiting_user",
            actor=agent_slug,
            session_id=session_id,
            payload={
                "agent_name": agent_name,
                "question": question,
                "pending_id": pending_id,
                **({"subtask_key": subtask_key} if subtask_key else {}),
            },
        )


async def record_user_answered(
    *,
    task_id: str,
    project_id: str,
    pending_id: str,
    session_id: str | None = None,
    user_id: str,
) -> None:
    """Append a ``user_answered`` task event when a pending question resolves
    (the counterpart to :func:`record_awaiting_user`)."""
    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="user_answered",
            actor="user",
            session_id=session_id,
            payload={"pending_id": pending_id},
        )
