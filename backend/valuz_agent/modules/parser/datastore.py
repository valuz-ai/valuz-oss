"""Datastore for the parser module's two tables.

Async datastores over an ``AsyncSession``. The parser scheduler + setup-job
controller run on the event loop (on-loop asyncio tasks, mirroring
``InProcessScheduleRunner``), so every caller ``await``s these directly. Callers
wrap each unit of work in ``async_unit_of_work`` (commit-with-retry on clean
exit); these methods never commit themselves.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.parser.models import PollingTaskRow, SetupJobRow


class SetupJobDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, user_id: str, setup_id: str) -> SetupJobRow | None:
        return (
            (
                await self._db.execute(
                    select(SetupJobRow).where(
                        SetupJobRow.setup_id == setup_id, SetupJobRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def upsert(self, user_id: str, row: SetupJobRow) -> SetupJobRow:
        # Owner passed explicitly; composite PK ``(setup_id, user_id)`` keeps the
        # merge per-owner.
        row.user_id = user_id
        row.updated_at = now_ms()
        await self._db.merge(row)
        return row

    async def update_progress(
        self,
        user_id: str,
        setup_id: str,
        *,
        downloaded_bytes: int,
        total_bytes: int | None,
    ) -> None:
        """Hot-path update used during a download ~1Hz. Skips a full merge to
        keep allocation low — single SQL UPDATE."""
        stmt = (
            update(SetupJobRow)
            .where(SetupJobRow.setup_id == setup_id, SetupJobRow.user_id == user_id)
            .values(
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                updated_at=now_ms(),
            )
        )
        await self._db.execute(stmt)


class PollingTaskDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, task_id: str) -> PollingTaskRow | None:
        return await self._db.get(PollingTaskRow, task_id)

    async def insert(self, user_id: str, row: PollingTaskRow) -> PollingTaskRow:
        # Owner passed explicitly (no ContextVar write-stamp default).
        row.user_id = user_id
        now = now_ms()
        if not getattr(row, "created_at", None):
            row.created_at = now
        row.updated_at = now
        self._db.add(row)
        return row

    async def upsert(self, user_id: str, row: PollingTaskRow) -> PollingTaskRow:
        # Owner passed explicitly (no ContextVar write-stamp default).
        row.user_id = user_id
        row.updated_at = now_ms()
        await self._db.merge(row)
        return row

    async def list_due(self, *, now: int, limit: int = 32) -> list[PollingTaskRow]:
        """Rows the scheduler should attempt to poll on its next tick.

        ``status IN (pending, running)`` filters out terminal rows;
        ``next_poll_at IS NULL OR next_poll_at <= now`` covers freshly
        inserted rows (where the scheduler hasn't computed the first
        delay yet) and rows whose backoff window has expired.
        """
        stmt = (
            select(PollingTaskRow)
            .where(PollingTaskRow.status.in_(("pending", "running")))
            .where((PollingTaskRow.next_poll_at.is_(None)) | (PollingTaskRow.next_poll_at <= now))
            .order_by(PollingTaskRow.created_at.asc())
            .limit(limit)
        )
        return list((await self._db.execute(stmt)).scalars().all())
