"""Durable delivery for actor messages — the channel itself.

``mailbox.MailboxRegistry`` is an ``asyncio.Queue`` per session and therefore
a channel only within one process. The host runs ``uvicorn --workers N`` across
several replicas, so every message that crossed a process boundary was dropped
silently: a chat instruction refused as ``LEAD_OFFLINE`` about a lead that was
running, a member's report that never woke its lead, a follow-up that vanished.

This module is the replacement. See docs/design/task-delivery-and-control.md;
two rules from it are enforced by this file's shape rather than by convention.

**Facts only.** ``enqueue`` refuses ``shutdown``. Control signals revoke an
actor's right to run and belong to the execution lease, whose fence token names
the one incarnation being revoked; a persisted shutdown would be replayed to
the replacement loop and kill it. That is not a hypothetical — it happened, and
was patched with a claim-token check that this design removes the need for.

**A fact is enqueued in the transaction that records it.** ``enqueue`` takes
the caller's session instead of opening its own, so the message and the state
change it describes commit together. Without that there is a window in which
something happened but its delivery does not exist, and closing that window
after the fact is what the repair mechanisms were for.

Consumption is at-most-once by conditional UPDATE, not by lock: two loops may
read the same pending row during a handover, and exactly one can flip it.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.execution_lease import holder_id
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks import notifier
from valuz_agent.modules.tasks.mailbox import InboxMsg
from valuz_agent.modules.tasks.models import TaskMailboxRow

logger = logging.getLogger(__name__)

# Kinds that may be persisted. ``shutdown`` is absent on purpose — see above.
DELIVERABLE_KINDS = frozenset({"text", "member_done", "revise_goal"})

class ControlSignalNotDeliverableError(ValueError):
    """Raised when something tries to persist a control signal as a message.

    Deliberately loud. The whole class of bugs this module exists for began
    with a control signal being modelled as a queued message, so silently
    accepting one would reintroduce it in the one place designed to prevent it.
    """


async def enqueue(
    db: AsyncSession,
    *,
    session_id: str,
    task_id: str,
    project_id: str,
    user_id: str,
    kind: str,
    text: str = "",
    from_session: str = "",
    origin: str = "",
    payload: dict[str, Any] | None = None,
) -> TaskMailboxRow:
    """Queue a message for *session_id* on the CALLER's transaction.

    Takes ``db`` rather than opening its own unit of work so the message
    commits with the state change it reports. Callers that have no state to
    write (recovery re-seeding, for instance) may pass a session of their own.
    """
    if kind not in DELIVERABLE_KINDS:
        raise ControlSignalNotDeliverableError(
            f"{kind!r} is not a deliverable message. Control signals (stop, pause, "
            "takeover) revoke an actor's right to run and belong to the execution "
            "lease — persisting one would replay it to the replacement loop."
        )
    next_position = (
        await db.scalar(
            select(func.coalesce(func.max(TaskMailboxRow.position), 0) + 1).where(
                TaskMailboxRow.session_id == session_id
            )
        )
    ) or 1
    row = TaskMailboxRow(
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
        project_id=project_id,
        position=next_position,
        kind=kind,
        text=text,
        from_session=from_session,
        origin=origin,
        payload=payload or {},
    )
    db.add(row)
    await db.flush()
    return row


async def ring_for(session_id: str) -> None:
    """Wake whoever runs *session_id*, AFTER the enqueue has committed.

    Separate from ``enqueue`` on purpose: enqueue runs inside the caller's
    transaction, and ringing from in there would wake a reader that then finds
    nothing — the row is not visible until commit. Callers ring once their unit
    of work closes.
    """
    await notifier.ring(session_id)


async def has_pending(session_id: str) -> bool:
    """Is anything waiting for this actor, anywhere?

    The loop's early exit ("nothing outstanding, finalize now") consulted the
    in-process queue alone, which cannot see a message another process wrote —
    so a task could finish with the user's instruction still unread.
    """
    async with async_unit_of_work(commit=False) as db:
        found = await db.scalar(
            select(TaskMailboxRow.id)
            .where(
                TaskMailboxRow.session_id == session_id,
                TaskMailboxRow.state == "pending",
            )
            .limit(1)
        )
    return found is not None


async def drain(session_id: str, *, limit: int) -> list[InboxMsg]:
    """Claim up to *limit* pending messages for *session_id*, oldest first.

    Call this only from the loop that drives the session: a claimed row is out
    of the queue, so whoever claims it is committed to acting on it.

    ``limit`` has no default ON PURPOSE, and both callers in the runtime pass
    ``1``. A claimed row is ``consumed`` in the table but only exists in the
    claimer's memory, so anything taken beyond what the caller acts on RIGHT
    NOW has to be parked somewhere — and that somewhere was a module-level dict
    keyed by session, which nothing emptied and which died with the process,
    taking the messages with it. Taking one at a time costs one extra indexed
    round trip per message and buys back the property this table exists for:
    a message survives the death of whoever was about to handle it.

    Callers do not lose throughput by taking one: both wait loops re-drain at
    the top of the next iteration, before they park. A default here would be an
    invitation to write ``drain(session_id)`` and reintroduce the buffer.
    """
    async with async_unit_of_work(commit=False) as db:
        rows = (
            await db.scalars(
                select(TaskMailboxRow)
                .where(
                    TaskMailboxRow.session_id == session_id,
                    TaskMailboxRow.state == "pending",
                )
                .order_by(TaskMailboxRow.position, TaskMailboxRow.id)
                .limit(limit)
            )
        ).all()
    if not rows:
        return []

    claimed: list[InboxMsg] = []
    for row in rows:
        async with async_unit_of_work() as db:
            # The guard is the STATE, not the id: another loop may have read
            # the same row and be claiming it right now, and only one of the
            # two UPDATEs can match.
            result = await db.execute(
                update(TaskMailboxRow)
                .where(
                    TaskMailboxRow.id == row.id,
                    TaskMailboxRow.state == "pending",
                )
                .values(state="consumed", consumed_at=now_ms(), consumed_by=holder_id())
            )
            if result.rowcount != 1:
                logger.debug(
                    "task mailbox: message %s for %s was claimed elsewhere",
                    row.id,
                    session_id,
                )
                continue
        claimed.append(
            InboxMsg(
                kind=row.kind,  # type: ignore[arg-type]
                text=row.text or "",
                from_session=row.from_session or "",
                origin=row.origin or "",
                payload=row.payload or {},
            )
        )
    return claimed


async def cancel_pending(db: AsyncSession, *, session_id: str) -> int:
    """Drop anything still queued for an actor that is being torn down.

    A member that is stopped, or a task that is finished, leaves messages
    nobody will ever read. They are marked rather than deleted, so the record
    of what was queued and never delivered survives for support questions.
    """
    result = await db.execute(
        update(TaskMailboxRow)
        .where(
            TaskMailboxRow.session_id == session_id,
            TaskMailboxRow.state == "pending",
        )
        .values(state="cancelled", consumed_at=now_ms(), consumed_by=holder_id())
    )
    return int(result.rowcount or 0)


__all__ = [
    "DELIVERABLE_KINDS",
    "ring_for",
    "ControlSignalNotDeliverableError",
    "cancel_pending",
    "drain",
    "enqueue",
    "has_pending",
]
