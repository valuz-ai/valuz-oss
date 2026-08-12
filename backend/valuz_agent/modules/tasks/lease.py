"""Cross-process execution ownership for a task — one driver at a time.

Why this exists
---------------
The actor loops, the mailbox and the live-member registry are all PROCESS-LOCAL
(``mailbox.py``, ``live_member_registry.py``), while the tasks they drive live
in a database any number of host processes may share — ``uvicorn --workers N``,
or several replicas behind one service. Nothing reconciled those two facts, and
two things broke:

- ``TaskHealthMonitor`` asked ``mailbox_registry.is_owned()``, which is true
  only for loops in the ASKING process. A lead running in a sibling process
  therefore read as dead, and the watchdog flipped a healthy task to
  ``blocked(reason="lead_dead")`` while its conversation was still streaming.
- ``recover_active_tasks`` re-drives every ``active`` task at boot, so N
  processes booting meant N lead loops on one task — N× model spend and
  competing ``plan_version`` CAS writes.

``infra/single_writer.py`` is not that guard: it short-circuits on Postgres.

What a lease is
---------------
One row per task naming its current driver, carrying a TTL the driver renews
from a side task. ``fence_token`` increases on EVERY acquisition, so a holder
that stalled long enough to lose its lease finds out (``heartbeat`` returns
False) and leaves, instead of fighting the process that took over.

Deliberately re-acquirable. This is where a task lease differs from an
automation execution claim, which refuses re-acquisition after expiry because
its side effects are not idempotent at run granularity: a task MUST be
re-drivable — ``resume_task`` and boot recovery are built on exactly that. The
protection is the fence, not refusal.

Scope
-----
CROSS-process only. Two loops racing for one session INSIDE one process stay
``mailbox_registry.claim``'s problem, and this module matches its rule (a later
acquisition invalidates every earlier token) so the two agree rather than
each thinking it won.

Absence is not death. A task with NO lease row is one nobody has claimed under
this scheme — a task active since before the table existed, or one driven by a
process still running older code mid-rollout. Readers treat that as "unknown",
never as "dead"; only an expired or released lease means nobody is driving.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks.models import TaskLeaseRow

logger = logging.getLogger(__name__)

# How long an acquisition stays valid without a renewal. Must comfortably
# exceed the renewal interval AND any stall a healthy driver can hit between
# renewals — the renewal runs on its own task, so it is not blocked by a long
# turn, but it does share the event loop and the DB pool.
TASK_LEASE_TTL_MS = 90_000
# Renewal cadence. Six renewals fit inside one TTL, so a lease survives several
# consecutive failures (a DB blip, a slow pool) before it is considered lost.
TASK_LEASE_RENEW_INTERVAL_S = 15.0

# Opaque per-process identity. The boot suffix distinguishes a restarted
# process that reused a pid — without it, a crashed process's stale row could
# be mistaken for the new one's and silently renewed.
_HOLDER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def holder_id() -> str:
    """This process's lease-holder identity. Stable for the process lifetime."""
    return _HOLDER_ID


@dataclass(frozen=True, slots=True)
class LeaseState:
    """A lease row as read by an observer (the watchdog, recovery)."""

    task_id: str
    holder_id: str
    state: str
    fence_token: int
    lease_expires_at: int

    def is_live(self, now: int) -> bool:
        """Someone is driving this task right now."""
        return self.state == "held" and self.lease_expires_at > now

    def is_foreign(self) -> bool:
        """Held by a process other than this one."""
        return self.holder_id != _HOLDER_ID


@dataclass(slots=True)
class TaskLease:
    """A held lease. Renew it periodically; release it when the driver exits."""

    task_id: str
    fence_token: int
    holder_id: str

    async def renew(self) -> bool:
        """Extend the TTL. ``False`` means we were fenced and must stop driving.

        Fence-guarded on ``(holder_id, fence_token)``: a stale holder cannot
        extend a lease that has moved on.
        """
        now = now_ms()
        async with async_unit_of_work() as db:
            result = await db.execute(
                update(TaskLeaseRow)
                .where(
                    TaskLeaseRow.task_id == self.task_id,
                    TaskLeaseRow.holder_id == self.holder_id,
                    TaskLeaseRow.fence_token == self.fence_token,
                    TaskLeaseRow.state == "held",
                )
                .values(heartbeat_at=now, lease_expires_at=now + TASK_LEASE_TTL_MS)
            )
        # ``AsyncSession.execute`` is typed as returning ``Result``; a DML
        # statement always yields a ``CursorResult``, which is where rowcount —
        # the whole point of the fence-guarded UPDATE — lives.
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def release(self) -> None:
        """Hand the task back. Idempotent; a no-op once fenced.

        ``lease_expires_at`` is zeroed as well as the state flipped so that a
        reader looking only at the expiry (an index-only scan) agrees with one
        looking at the state.
        """
        async with async_unit_of_work() as db:
            await db.execute(
                update(TaskLeaseRow)
                .where(
                    TaskLeaseRow.task_id == self.task_id,
                    TaskLeaseRow.holder_id == self.holder_id,
                    TaskLeaseRow.fence_token == self.fence_token,
                )
                .values(state="released", lease_expires_at=0)
            )


async def acquire_task_lease(
    *, user_id: str, task_id: str, lead_session_id: str
) -> TaskLease | None:
    """Become the driver of *task_id*, or ``None`` if someone else already is.

    Takes over a lease that is released, expired, or held by THIS process (see
    the module docstring on intra-process semantics). Refuses only a live lease
    held elsewhere. Every acquisition bumps ``fence_token``.
    """
    now = now_ms()
    async with async_unit_of_work() as db:
        result = await db.execute(
            update(TaskLeaseRow)
            .where(
                TaskLeaseRow.task_id == task_id,
                or_(
                    TaskLeaseRow.holder_id == _HOLDER_ID,
                    TaskLeaseRow.state == "released",
                    TaskLeaseRow.lease_expires_at <= now,
                ),
            )
            .values(
                holder_id=_HOLDER_ID,
                lead_session_id=lead_session_id,
                state="held",
                fence_token=TaskLeaseRow.fence_token + 1,
                heartbeat_at=now,
                lease_expires_at=now + TASK_LEASE_TTL_MS,
            )
        )
        if cast("CursorResult[Any]", result).rowcount:
            # Read the token back inside the same transaction. A Core SELECT
            # (not ``db.get``) so the identity map cannot serve the pre-update
            # value; ``RETURNING`` is avoided because it needs SQLite ≥ 3.35 and
            # the desktop build does not pin the system sqlite.
            token = (
                await db.execute(
                    select(TaskLeaseRow.fence_token).where(TaskLeaseRow.task_id == task_id)
                )
            ).scalar_one()
            return TaskLease(task_id=task_id, fence_token=int(token), holder_id=_HOLDER_ID)
        existing = (
            await db.execute(select(TaskLeaseRow.holder_id).where(TaskLeaseRow.task_id == task_id))
        ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "task lease: %s is driven by %s — not starting a second driver here",
            task_id,
            existing,
        )
        return None

    # No row yet. The loser of a concurrent insert gets an IntegrityError on the
    # primary key, which is the same answer as losing the update race above.
    try:
        async with async_unit_of_work() as db:
            db.add(
                TaskLeaseRow(
                    user_id=user_id,
                    task_id=task_id,
                    lead_session_id=lead_session_id,
                    holder_id=_HOLDER_ID,
                    fence_token=1,
                    state="held",
                    heartbeat_at=now,
                    lease_expires_at=now + TASK_LEASE_TTL_MS,
                )
            )
    except IntegrityError:
        logger.info("task lease: %s was claimed concurrently — not starting here", task_id)
        return None
    return TaskLease(task_id=task_id, fence_token=1, holder_id=_HOLDER_ID)


async def load_lease_states(task_ids: list[str]) -> dict[str, LeaseState]:
    """Lease rows for *task_ids*, in one query. Missing ids are simply absent.

    Absent means "unknown", not "dead" — see the module docstring.
    """
    if not task_ids:
        return {}
    async with async_unit_of_work(commit=False) as db:
        rows = (
            await db.execute(
                select(
                    TaskLeaseRow.task_id,
                    TaskLeaseRow.holder_id,
                    TaskLeaseRow.state,
                    TaskLeaseRow.fence_token,
                    TaskLeaseRow.lease_expires_at,
                ).where(TaskLeaseRow.task_id.in_(task_ids))
            )
        ).all()
    return {
        task_id: LeaseState(
            task_id=task_id,
            holder_id=holder,
            state=state,
            fence_token=int(token),
            lease_expires_at=int(expires),
        )
        for task_id, holder, state, token, expires in rows
    }


async def is_driven_elsewhere(task_id: str) -> bool:
    """True when another live process holds this task's lease.

    Advisory — for skipping work that a peer is already doing (boot recovery).
    The authoritative check is :func:`acquire_task_lease`, which is atomic;
    this one only avoids the duplicated effort ahead of it.
    """
    state = (await load_lease_states([task_id])).get(task_id)
    return state is not None and state.is_live(now_ms()) and state.is_foreign()


__all__ = [
    "TASK_LEASE_RENEW_INTERVAL_S",
    "TASK_LEASE_TTL_MS",
    "LeaseState",
    "TaskLease",
    "acquire_task_lease",
    "holder_id",
    "is_driven_elsewhere",
    "load_lease_states",
]
