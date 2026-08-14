"""Cross-process execution ownership: one holder per ``(scope, key)``.

Why this exists
---------------
Several subsystems coordinate long-running work through PROCESS-LOCAL memory
while the work itself lives in a database any number of host processes may
share — ``uvicorn --workers N``, or several replicas behind one service.
Nothing reconciled those two facts, and each subsystem broke differently:

- **Tasks.** ``TaskHealthMonitor`` asked a per-process mailbox registry, true
  only for loops in the ASKING process, so a lead running in a sibling process
  read as dead and the watchdog flipped a healthy task to
  ``blocked(reason="lead_dead")`` mid-run. And ``recover_active_tasks``
  re-drives every active task at boot, so N processes booting put N lead loops
  on one task.
- **Session queue drains** (``sessions/recovery.py``) guard re-entry with the
  in-memory ``_active_drains`` set, so every process re-kicks the same drain at
  boot — duplicate turns, duplicate model spend, duplicate assistant output.
- **Polling** (``parser/polling.py``) reads due rows with no claim before
  calling ``handler.submit()``, an external side effect.

``infra/single_writer.py`` is not the guard for any of it: it short-circuits on
Postgres, on the reasoning that Postgres handles concurrency natively. It does
— for *storage*. The hazards that file's own docstring enumerates are about
duplicated host SIDE EFFECTS, which no storage engine can prevent.

What a lease is
---------------
One row per ``(scope, key)`` naming its current holder, carrying a TTL the
holder renews. ``fence_token`` increases on EVERY acquisition, so a holder that
stalled long enough to lose its lease finds out (:meth:`ActorLease.renew`
returns False) and stands down instead of fighting the process that took over.

Generic on purpose. Tasks are the first consumer (``scope="task"``); the two
subsystems above are the next, and a bespoke mechanism per subsystem would be
three chances to get fencing subtly wrong.

Deliberately re-acquirable, unlike an automation execution claim — which
refuses re-acquisition after expiry because its side effects are not idempotent
at run granularity. A task MUST be re-drivable: ``resume_task`` and boot
recovery are built on exactly that. The protection is the fence, not refusal.

Absence is not death. No row means nobody has claimed that key under this
scheme — work that predates the table, or a holder still running older code
mid-rollout. Readers treat that as "unknown", never as "dead"; only an expired
or released lease means nobody is holding it.

Single-process deployments pay nothing: see :func:`_exclusive_by_construction`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import BigInteger, Index, String, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, TimestampMixin
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.time_utils import now_ms

logger = logging.getLogger(__name__)

# How long an acquisition stays valid without a renewal. Must comfortably exceed
# the renewal interval AND any stall a healthy holder can hit between renewals —
# renewal runs on its own task, so a long turn does not block it, but it does
# share the event loop and the DB pool.
LEASE_TTL_MS = 90_000
# Renewal cadence. Six renewals fit inside one TTL, so a lease survives several
# consecutive failures (a DB blip, a slow pool) before it is considered lost.
LEASE_RENEW_INTERVAL_S = 15.0
# A holder that has not renewed for this fraction of the TTL stops trusting
# itself: see ``ExecutionLease.renew`` and the self-fence note there.
_SELF_FENCE_FRACTION = 0.5

# Effectively "never" — used only where exclusivity is already proven.
_NEVER_MS = 1 << 62

# Opaque per-process identity. The boot suffix distinguishes a restarted process
# that reused a pid — without it, a crashed process's stale row could be
# mistaken for the new one's and silently renewed.
_HOLDER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def holder_id() -> str:
    """This process's holder identity. Stable for the process lifetime."""
    return _HOLDER_ID


class ExecutionLeaseRow(Base, TimestampMixin):
    """Who currently holds ``(scope, key)``.

    The composite ``(scope, key)`` IS the primary key: a key has at most one
    holder, and that constraint is what settles a concurrent insert. A
    surrogate id would let two rows exist for one key, which is the single
    thing this table is here to prevent.

    No ``UserMixin``: a lease is process coordination, not user-owned business
    data. Scoping lives in the key, which callers namespace themselves.
    """

    __tablename__ = "valuz_execution_lease"

    __table_args__ = (Index("ix_valuz_execution_lease_expires", "lease_expires_at"),)

    # Consumer namespace — "task", "session-drain", "polling", …
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    holder_id: Mapped[str] = mapped_column(String(128))
    # Bumped on EVERY acquisition, including re-acquisition by the same process
    # (mirrors ``mailbox_registry.claim``: a later claim invalidates earlier
    # tokens). A holder whose token no longer matches has been fenced.
    fence_token: Mapped[int] = mapped_column(BigInteger, default=1)
    # held | released
    state: Mapped[str] = mapped_column(String(16), default="held")
    heartbeat_at: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_expires_at: Mapped[int] = mapped_column(BigInteger, default=0)
    # Free-form diagnostic (for tasks: the lead session driving it). Never read
    # for decisions — a stale value must not be able to change behaviour.
    note: Mapped[str] = mapped_column(String(128), default="")


def _exclusive_by_construction() -> bool:
    """Is this process PROVABLY the only one touching the store?

    True for the desktop shape: SQLite plus the ``single_writer`` flock, which
    refuses to start a second backend on the same data dir. There the lease can
    be taken once and never renewed — renewing would be a disk write every
    ``LEASE_RENEW_INTERVAL_S`` per active task, bought on a laptop to solve a
    problem that deployment cannot have.

    Deliberately narrow: it asks whether the lock is HELD, not merely whether
    the runtime is SQLite. OSS on Postgres, and SQLite with the lock skipped via
    ``VALUZ_SKIP_WRITER_LOCK``, both fall through to real leases.
    """
    try:
        from valuz_agent.infra import single_writer
        from valuz_agent.infra.db_urls import is_sqlite_runtime

        return is_sqlite_runtime() and single_writer.is_lock_held()
    except Exception:  # noqa: BLE001 — never let a probe break acquisition
        return False


@dataclass(frozen=True, slots=True)
class LeaseState:
    """A lease row as read by an observer (a watchdog, a recovery sweep)."""

    scope: str
    key: str
    holder_id: str
    state: str
    fence_token: int
    lease_expires_at: int

    def is_live(self, now: int) -> bool:
        """Someone holds this key right now."""
        return self.state == "held" and self.lease_expires_at > now

    def is_foreign(self) -> bool:
        """Held by a process other than this one."""
        return self.holder_id != _HOLDER_ID


@dataclass(slots=True)
class ExecutionLease:
    """A held lease. Renew it periodically; release it when the holder stops."""

    scope: str
    key: str
    fence_token: int
    holder_id: str
    # 0 = renewal not required (exclusivity already proven at acquisition).
    _last_renewed_at: int = 0
    _renewal_required: bool = True

    @property
    def needs_renewal(self) -> bool:
        """False when exclusivity was already proven at acquisition.

        Callers that run a renewal loop must check this: with renewal a no-op,
        such a loop can never observe the ``False`` that ends it, so it would
        spin for the life of the holder.
        """
        return self._renewal_required

    async def renew(self) -> bool:
        """Extend the TTL. ``False`` means we must stop working on this key.

        Fence-guarded on ``(holder_id, fence_token)``: a stale holder cannot
        extend a lease that has moved on.

        SELF-FENCE: ``False`` is also returned when renewal has been FAILING
        (raising) for longer than half the TTL. Without that, a holder whose
        database is unreachable keeps working while its lease quietly expires,
        and a peer's watchdog is free to declare it dead and hand the key on —
        two holders, which is the one outcome this module exists to prevent.
        Standing down early turns that into "no holder", which every consumer
        already knows how to recover from.
        """
        if not self._renewal_required:
            return True
        now = now_ms()
        try:
            async with async_unit_of_work() as db:
                result = await db.execute(
                    update(ExecutionLeaseRow)
                    .where(
                        ExecutionLeaseRow.scope == self.scope,
                        ExecutionLeaseRow.key == self.key,
                        ExecutionLeaseRow.holder_id == self.holder_id,
                        ExecutionLeaseRow.fence_token == self.fence_token,
                        ExecutionLeaseRow.state == "held",
                    )
                    .values(heartbeat_at=now, lease_expires_at=now + LEASE_TTL_MS)
                )
        except Exception:  # noqa: BLE001
            # A transient failure is not eviction — the TTL spans several
            # renewals — but a PERSISTENT one is indistinguishable from it from
            # the outside, so stand down once we can no longer prove we hold it.
            stale_for = now - self._last_renewed_at
            if stale_for > LEASE_TTL_MS * _SELF_FENCE_FRACTION:
                logger.warning(
                    "lease %s/%s: renewal failing for %dms — standing down",
                    self.scope,
                    self.key,
                    stale_for,
                )
                return False
            logger.debug("lease %s/%s: renewal failed, retrying", self.scope, self.key)
            return True
        # ``AsyncSession.execute`` is typed as returning ``Result``; a DML
        # statement always yields a ``CursorResult``, which is where rowcount —
        # the whole point of the fence-guarded UPDATE — lives.
        if not cast("CursorResult[Any]", result).rowcount:
            return False
        self._last_renewed_at = now
        return True

    async def release(self) -> None:
        """Hand the key back. Idempotent; a no-op once fenced.

        ``lease_expires_at`` is zeroed as well as the state flipped, so a reader
        looking only at the expiry agrees with one looking at the state.
        """
        async with async_unit_of_work() as db:
            await db.execute(
                update(ExecutionLeaseRow)
                .where(
                    ExecutionLeaseRow.scope == self.scope,
                    ExecutionLeaseRow.key == self.key,
                    ExecutionLeaseRow.holder_id == self.holder_id,
                    ExecutionLeaseRow.fence_token == self.fence_token,
                )
                .values(state="released", lease_expires_at=0)
            )


async def acquire_lease(*, scope: str, key: str, note: str = "") -> ExecutionLease | None:
    """Take ``(scope, key)``, or ``None`` if another live process holds it.

    Takes over a lease that is released, expired, or held by THIS process (see
    the module docstring on intra-process semantics). Refuses only a live lease
    held elsewhere. Every acquisition bumps ``fence_token``.
    """
    now = now_ms()
    exclusive = _exclusive_by_construction()
    expires = _NEVER_MS if exclusive else now + LEASE_TTL_MS

    def _lease(token: int) -> ExecutionLease:
        return ExecutionLease(
            scope=scope,
            key=key,
            fence_token=token,
            holder_id=_HOLDER_ID,
            _last_renewed_at=now,
            _renewal_required=not exclusive,
        )

    async with async_unit_of_work() as db:
        result = await db.execute(
            update(ExecutionLeaseRow)
            .where(
                ExecutionLeaseRow.scope == scope,
                ExecutionLeaseRow.key == key,
                or_(
                    ExecutionLeaseRow.holder_id == _HOLDER_ID,
                    ExecutionLeaseRow.state == "released",
                    ExecutionLeaseRow.lease_expires_at <= now,
                ),
            )
            .values(
                holder_id=_HOLDER_ID,
                note=note,
                state="held",
                fence_token=ExecutionLeaseRow.fence_token + 1,
                heartbeat_at=now,
                lease_expires_at=expires,
            )
        )
        if cast("CursorResult[Any]", result).rowcount:
            # Read the token back inside the same transaction. A Core SELECT
            # (not ``db.get``) so the identity map cannot serve the pre-update
            # value; ``RETURNING`` is avoided because it needs SQLite >= 3.35 and
            # the desktop build does not pin the system sqlite.
            token = (
                await db.execute(
                    select(ExecutionLeaseRow.fence_token).where(
                        ExecutionLeaseRow.scope == scope, ExecutionLeaseRow.key == key
                    )
                )
            ).scalar_one()
            return _lease(int(token))
        existing = (
            await db.execute(
                select(ExecutionLeaseRow.holder_id).where(
                    ExecutionLeaseRow.scope == scope, ExecutionLeaseRow.key == key
                )
            )
        ).scalar_one_or_none()
    if existing is not None:
        logger.info("lease %s/%s is held by %s — standing down", scope, key, existing)
        return None

    # No row yet. The loser of a concurrent insert gets an IntegrityError on the
    # primary key, which is the same answer as losing the update race above.
    try:
        async with async_unit_of_work() as db:
            db.add(
                ExecutionLeaseRow(
                    scope=scope,
                    key=key,
                    holder_id=_HOLDER_ID,
                    note=note,
                    fence_token=1,
                    state="held",
                    heartbeat_at=now,
                    lease_expires_at=expires,
                )
            )
    except IntegrityError:
        logger.info("lease %s/%s was claimed concurrently — standing down", scope, key)
        return None
    return _lease(1)


@asynccontextmanager
async def hold_lease(
    *, scope: str, key: str, note: str = ""
) -> AsyncIterator[ExecutionLease | None]:
    """Hold ``(scope, key)`` for the duration of the block, renewed in the
    background; yields ``None`` when another live process holds it.

    For consumers whose work is a plain ``async with`` body — a queue drain, a
    sweep. The task actor loop does NOT use this: it has to react to losing the
    lease by waking a loop parked on a mailbox, which needs the fenced event
    rather than a context manager's implicit teardown.

    Losing the lease mid-body is LOGGED, not enforced: a context manager cannot
    interrupt arbitrary code. That is deliberate rather than lax — the win here
    is refusing to START duplicate work, which is the failure that actually
    happens (every process kicking the same drain at boot). Mid-body takeover
    still needs the whole TTL to elapse first, i.e. it is no worse than the
    behaviour before any lease existed.
    """
    lease = await acquire_lease(scope=scope, key=key, note=note)
    if lease is None:
        yield None
        return

    async def _renew_forever() -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_INTERVAL_S)
            if not await lease.renew():
                logger.warning("lease %s/%s lost while its holder was still working", scope, key)
                return

    renewer = (
        asyncio.create_task(_renew_forever(), name=f"lease-{scope}-{key}")
        if lease.needs_renewal
        else None
    )
    try:
        yield lease
    finally:
        if renewer is not None:
            renewer.cancel()
        with contextlib.suppress(Exception):
            await lease.release()


async def load_lease_states(scope: str, keys: list[str]) -> dict[str, LeaseState]:
    """Lease rows for *keys* within *scope*, in one query.

    Missing keys are simply absent — and absent means "unknown", not "dead".
    """
    if not keys:
        return {}
    async with async_unit_of_work(commit=False) as db:
        rows = (
            await db.execute(
                select(
                    ExecutionLeaseRow.key,
                    ExecutionLeaseRow.holder_id,
                    ExecutionLeaseRow.state,
                    ExecutionLeaseRow.fence_token,
                    ExecutionLeaseRow.lease_expires_at,
                ).where(ExecutionLeaseRow.scope == scope, ExecutionLeaseRow.key.in_(keys))
            )
        ).all()
    return {
        key: LeaseState(
            scope=scope,
            key=key,
            holder_id=holder,
            state=state,
            fence_token=int(token),
            lease_expires_at=int(expires),
        )
        for key, holder, state, token, expires in rows
    }


async def is_held_elsewhere(scope: str, key: str) -> bool:
    """True when another live process holds this key.

    Advisory — for skipping work a peer is already doing. The authoritative
    check is :func:`acquire_lease`, which is atomic; this only avoids the
    duplicated effort ahead of it.
    """
    state = (await load_lease_states(scope, [key])).get(key)
    return state is not None and state.is_live(now_ms()) and state.is_foreign()


__all__ = [
    "LEASE_RENEW_INTERVAL_S",
    "LEASE_TTL_MS",
    "ExecutionLease",
    "ExecutionLeaseRow",
    "LeaseState",
    "acquire_lease",
    "hold_lease",
    "holder_id",
    "is_held_elsewhere",
    "load_lease_states",
]
