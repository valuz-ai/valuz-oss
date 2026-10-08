"""Durable extraction candidates and progress, never an alternative memory catalog.

The existing execution lease owns execution. This journal retains a bounded,
redacted plan so a restarted worker replays the same mutations and receipts.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import JSON, BigInteger, Boolean, Integer, String, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, TimestampMixin, UserMixin
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.execution_lease import ExecutionLease, ExecutionLeaseRow
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.ports.memory import SourceRef
from valuz_agent.ports.memory_maintenance import (
    MemoryMaintenanceResult,
    MemoryMaintenanceScope,
)

LEASE_SCOPE = "memory-review"
SubjectKind = Literal["session", "task"]
_ACTIVE = ("pending", "retry", "claimed", "planned", "applying")


@dataclass(frozen=True)
class ReviewLimits:
    idle_delay: float = 60.0
    poll_interval: float = 5.0
    max_attempts: int = 3
    max_daily_calls: int = 8
    model_timeout: float = 60.0
    retry_delay: float = 30.0

    def __post_init__(self) -> None:
        if min(self.idle_delay, self.poll_interval, self.model_timeout, self.retry_delay) < 0:
            raise ValueError("review durations cannot be negative")
        if self.model_timeout <= 0 or self.poll_interval <= 0:
            raise ValueError("review timeout/poll interval must be positive")
        if self.max_attempts < 1 or self.max_daily_calls < 1:
            raise ValueError("review limits must be positive")


class ReviewJobRow(Base, TimestampMixin, UserMixin):
    __tablename__ = "valuz_memory_review_job"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    due_at: Mapped[int] = mapped_column(BigInteger, index=True)
    rerun_due_at: Mapped[int | None] = mapped_column(BigInteger)
    fence_token: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    base_revision: Mapped[int | None] = mapped_column(BigInteger)
    review_id: Mapped[str | None] = mapped_column(String(64))
    authority_id: Mapped[str | None] = mapped_column(String(256))
    authority_epoch: Mapped[int | None] = mapped_column(BigInteger)
    project_id: Mapped[str | None] = mapped_column(String(128))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    plan_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    planned_ops: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    receipts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    next_op: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason_code: Mapped[str | None] = mapped_column(String(48))


class ReviewOwnerRow(Base):
    __tablename__ = "valuz_memory_review_owner"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purge_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    authority_id: Mapped[str | None] = mapped_column(String(256))
    authority_epoch: Mapped[int | None] = mapped_column(BigInteger)
    clear_before_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    day: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)


@dataclass(frozen=True)
class ReviewJob:
    id: str
    user_id: str
    kind: SubjectKind
    subject_id: str
    generation: int
    status: str
    attempts: int
    base_revision: int | None
    review_id: str | None
    authority_id: str | None
    authority_epoch: int | None
    project_id: str | None
    sources: tuple[SourceRef, ...]
    plan_ready: bool
    planned_ops: tuple[dict[str, Any], ...]
    receipts: tuple[dict[str, Any], ...]
    next_op: int
    cursor: tuple[str, ...]


class ReviewFencedError(RuntimeError):
    """This worker no longer owns this job, or owner cleanup stopped it."""


def job_id(owner: str, kind: str, subject: str) -> str:
    if not owner or not subject or kind not in {"session", "task"}:
        raise ValueError("owner and valid review subject are required")
    return hashlib.sha256(f"{owner}\0{kind}\0{subject}".encode()).hexdigest()


def _insert(db: AsyncSession, table: type[Base]) -> Any:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        return sqlite_insert(table)
    if dialect == "postgresql":
        return pg_insert(table)
    raise RuntimeError("unsupported review journal database")


async def _ensure_owner(db: AsyncSession, owner: str) -> None:
    await db.execute(
        _insert(db, ReviewOwnerRow)
        .values(
            user_id=owner,
            purge_revision=0,
            clear_before_ms=0,
            day=0,
            calls=0,
        )
        .on_conflict_do_nothing(index_elements=["user_id"])
    )


def _job(row: ReviewJobRow) -> ReviewJob:
    return ReviewJob(
        row.id,
        row.user_id,
        cast(SubjectKind, row.kind),
        row.subject_id,
        row.generation,
        row.status,
        row.attempts,
        row.base_revision,
        row.review_id,
        row.authority_id,
        row.authority_epoch,
        row.project_id,
        tuple(SourceRef.model_validate(ref) for ref in row.sources),
        row.plan_ready,
        tuple(row.planned_ops),
        tuple(row.receipts),
        row.next_op,
        tuple(row.cursor),
    )


async def _owned(db: AsyncSession, job: ReviewJob, lease: ExecutionLease) -> ReviewJobRow:
    current = (
        await db.execute(select(ReviewJobRow).where(ReviewJobRow.id == job.id).with_for_update())
    ).scalar_one_or_none()
    held = await db.get(ExecutionLeaseRow, (LEASE_SCOPE, job.id))
    if (
        current is None
        or current.user_id != job.user_id
        or current.generation != job.generation
        or current.status not in _ACTIVE
        or current.fence_token != lease.fence_token
        or held is None
        or held.holder_id != lease.holder_id
        or held.fence_token != lease.fence_token
        or held.state != "held"
        or held.lease_expires_at <= now_ms()
    ):
        raise ReviewFencedError("review execution ownership changed")
    return current


@asynccontextmanager
async def _registration_session(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    if db is not None:
        yield db
    else:
        async with async_unit_of_work() as owned:
            yield owned


class ReviewJournal:
    async def schedule(
        self,
        owner: str,
        kind: SubjectKind,
        subject: str,
        due_at: int,
        *,
        db: AsyncSession | None = None,
    ) -> str:
        key = job_id(owner, kind, subject)
        async with _registration_session(db) as db:
            await _ensure_owner(db, owner)
            await db.execute(
                _insert(db, ReviewJobRow)
                .values(
                    id=key,
                    user_id=owner,
                    kind=kind,
                    subject_id=subject,
                    status="pending",
                    due_at=due_at,
                    generation=1,
                    fence_token=0,
                    attempts=0,
                    sources=[],
                    planned_ops=[],
                    plan_ready=False,
                    receipts=[],
                    cursor=[],
                    next_op=0,
                    created_at=now_ms(),
                    updated_at=now_ms(),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            row = (
                await db.execute(
                    select(ReviewJobRow).where(ReviewJobRow.id == key).with_for_update()
                )
            ).scalar_one()
            if row.status in {"claimed", "planned", "applying"}:
                # A new turn re-arms future work, not the already-running model.
                row.rerun_due_at = due_at
            else:
                row.status = "pending"
                row.due_at = due_at
                row.generation += 1
                row.attempts = 0
                row.reason_code = None
                row.base_revision = None
                row.review_id = None
                row.authority_id = None
                row.authority_epoch = None
                row.sources = []
                row.planned_ops = []
                row.plan_ready = False
                row.receipts = []
                row.next_op = 0
        return key

    async def due(self, *, limit: int = 32) -> list[ReviewJob]:
        async with async_unit_of_work(commit=False) as db:
            rows = (
                (
                    await db.execute(
                        select(ReviewJobRow)
                        .where(
                            ReviewJobRow.status.in_(_ACTIVE),
                            ReviewJobRow.due_at <= now_ms(),
                        )
                        .order_by(ReviewJobRow.due_at)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_job(row) for row in rows]

    async def get(self, owner: str, key: str) -> ReviewJob | None:
        async with async_unit_of_work(commit=False) as db:
            row = await db.get(ReviewJobRow, key)
            return _job(row) if row is not None and row.user_id == owner else None

    async def claim(
        self, job: ReviewJob, lease: ExecutionLease, limits: ReviewLimits
    ) -> ReviewJob | None:
        async with async_unit_of_work() as db:
            row = (
                await db.execute(
                    select(ReviewJobRow).where(ReviewJobRow.id == job.id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.user_id != job.user_id
                or row.generation != job.generation
                or row.status not in _ACTIVE
            ):
                raise ReviewFencedError("review was cancelled")
            row.fence_token = lease.fence_token
            # This check also rejects a lease invalidated by another same-process claimant.
            current = await _owned(db, _job(row), lease)
            if current.attempts >= limits.max_attempts:
                current.status = "stopped"
                current.reason_code = "attempt_limit"
                current.planned_ops = []
                current.plan_ready = False
                current.sources = []
                return None
            current.attempts += 1
            current.status = "planned" if current.plan_ready else "claimed"
            return _job(current)

    async def check(self, job: ReviewJob, lease: ExecutionLease) -> None:
        async with async_unit_of_work(commit=False) as db:
            await _owned(db, job, lease)

    async def prepare(
        self,
        job: ReviewJob,
        lease: ExecutionLease,
        *,
        revision: int,
        review_id: str,
        sources: tuple[SourceRef, ...],
        project_id: str | None,
        authority_id: str | None = None,
        authority_epoch: int | None = None,
    ) -> ReviewJob:
        async with async_unit_of_work() as db:
            row = await _owned(db, job, lease)
            owner = await db.get(ReviewOwnerRow, job.user_id)
            if (authority_id is None) != (authority_epoch is None):
                raise ReviewFencedError("incomplete memory authority binding")
            if owner is None:
                raise ReviewFencedError("memory owner state missing")
            if (owner.authority_id, owner.authority_epoch) != (authority_id, authority_epoch):
                # Catalog revisions are meaningful only within their original
                # authority. Old jobs are still fenced by status/generation.
                owner.authority_id, owner.authority_epoch = authority_id, authority_epoch
                owner.purge_revision = 0
            if owner.purge_revision > revision:
                raise ReviewFencedError("memory changed before review")
            row.base_revision = revision
            row.review_id = review_id
            row.authority_id, row.authority_epoch = authority_id, authority_epoch
            row.project_id = project_id
            row.sources = [ref.model_dump(mode="json") for ref in sources]
            return _job(row)

    async def save_plan(
        self, job: ReviewJob, lease: ExecutionLease, ops: list[dict[str, Any]]
    ) -> ReviewJob:
        async with async_unit_of_work() as db:
            row = await _owned(db, job, lease)
            owner = await db.get(ReviewOwnerRow, job.user_id)
            if (
                owner is None
                or row.base_revision is None
                or (
                    (owner.authority_id, owner.authority_epoch)
                    == (row.authority_id, row.authority_epoch)
                    and owner.purge_revision > row.base_revision
                )
            ):
                raise ReviewFencedError("memory cleanup invalidated review")
            row.planned_ops = ops
            row.plan_ready = True
            row.status = "planned"
            return _job(row)

    async def reserve_model(
        self, job: ReviewJob, lease: ExecutionLease, limits: ReviewLimits
    ) -> bool:
        day = int(time.time() // 86400)  # Budget buckets are explicitly UTC days.
        async with async_unit_of_work() as db:
            await _owned(db, job, lease)
            await db.execute(
                update(ReviewOwnerRow)
                .where(
                    ReviewOwnerRow.user_id == job.user_id,
                    ReviewOwnerRow.day != day,
                )
                .values(day=day, calls=0)
            )
            result = await db.execute(
                update(ReviewOwnerRow)
                .where(
                    ReviewOwnerRow.user_id == job.user_id,
                    ReviewOwnerRow.calls < limits.max_daily_calls,
                )
                .values(calls=ReviewOwnerRow.calls + 1)
            )
            return bool(cast(CursorResult[Any], result).rowcount)

    async def clear_before(self, owner: str) -> int:
        async with async_unit_of_work(commit=False) as db:
            state = await db.get(ReviewOwnerRow, owner)
            return state.clear_before_ms if state is not None else 0

    async def checkpoint(
        self, job: ReviewJob, lease: ExecutionLease, receipt: dict[str, Any]
    ) -> ReviewJob:
        async with async_unit_of_work() as db:
            row = await _owned(db, job, lease)
            if row.next_op != job.next_op:
                raise ReviewFencedError("review progress changed")
            row.receipts = [*row.receipts, receipt]
            row.next_op += 1
            row.status = "applying"
            return _job(row)

    async def finish(
        self,
        job: ReviewJob,
        lease: ExecutionLease,
        *,
        reason: str = "completed",
        cursor: tuple[str, ...] | None = None,
    ) -> None:
        async with async_unit_of_work() as db:
            row = await _owned(db, job, lease)
            row.planned_ops = []
            row.plan_ready = False
            row.sources = []
            row.reason_code = reason
            if cursor is not None:
                row.cursor = list(cursor[-200:])
            row.status = "done" if reason == "completed" else "stopped"
            if row.rerun_due_at is not None:
                row.status = "pending"
                row.due_at = row.rerun_due_at
                row.rerun_due_at = None
                row.generation += 1
                row.attempts = 0
                row.base_revision = None
                row.review_id = None
                row.receipts = []
                row.next_op = 0

    async def retry(
        self, job: ReviewJob, lease: ExecutionLease, limits: ReviewLimits, reason: str
    ) -> None:
        async with async_unit_of_work() as db:
            row = await _owned(db, job, lease)
            row.reason_code = reason
            if row.attempts >= limits.max_attempts:
                row.status = "stopped"
                row.planned_ops = []
                row.plan_ready = False
                row.sources = []
            else:
                row.status = "retry"
                row.due_at = now_ms() + int(limits.retry_delay * row.attempts * 1000)

    async def purge_after_mutation(
        self,
        *,
        owner_user_id: str,
        revision: int,
        affected_ids: tuple[str, ...] = (),
        source_refs: tuple[SourceRef, ...] = (),
        scope: MemoryMaintenanceScope | None = None,
    ) -> MemoryMaintenanceResult:
        try:
            async with async_unit_of_work() as db:
                await _ensure_owner(db, owner_user_id)
                await db.execute(
                    update(ReviewOwnerRow)
                    .where(
                        ReviewOwnerRow.user_id == owner_user_id,
                        ReviewOwnerRow.purge_revision < revision,
                    )
                    .values(purge_revision=revision)
                )
                if scope is not None:
                    await db.execute(
                        update(ReviewOwnerRow)
                        .where(
                            ReviewOwnerRow.user_id == owner_user_id,
                        )
                        .values(clear_before_ms=now_ms())
                    )
                rows = (
                    (
                        await db.execute(
                            select(ReviewJobRow).where(
                                ReviewJobRow.user_id == owner_user_id,
                                ReviewJobRow.status.in_(_ACTIVE),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                cancelled = purged = 0
                # All older plans are conservatively invalidated by the catalog
                # revision, including plans not yet tied to a specific record.
                for row in rows:
                    if row.base_revision is None or row.base_revision < revision:
                        purged += bool(row.planned_ops)
                        cancelled += 1
                        row.status = "stopped"
                        row.reason_code = "owner_mutation"
                        row.planned_ops = []
                        row.plan_ready = False
                        row.sources = []
                        row.rerun_due_at = None
                return MemoryMaintenanceResult(True, cancelled, purged)
        except Exception:  # noqa: BLE001 — caller must report partial cleanup
            return MemoryMaintenanceResult(False, reason_code="review_cleanup_unavailable")


review_journal = ReviewJournal()
