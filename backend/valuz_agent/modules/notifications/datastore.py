"""Persistence for the notification ledger — the ONLY layer touching the DB
session for ``valuz_notification``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.notifications.models import NotificationRow

# Kinds that report something FINISHED rather than asking for an action. They
# are ingested already-resolved; this set also drives the one-time backlog
# cleanup for rows written before that was true.
_INFORMATIONAL_KINDS = ("task_completed",)


class NotificationDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_dedup(self, user_id: str, dedup_key: str) -> NotificationRow | None:
        return (
            await self._db.execute(
                select(NotificationRow).where(
                    NotificationRow.user_id == user_id,
                    NotificationRow.dedup_key == dedup_key,
                )
            )
        ).scalar_one_or_none()

    async def get_by_id(self, user_id: str, notification_id: str) -> NotificationRow | None:
        return (
            await self._db.execute(
                select(NotificationRow).where(
                    NotificationRow.id == notification_id,
                    NotificationRow.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def upsert(
        self,
        user_id: str,
        *,
        dedup_key: str,
        kind: str,
        title: str,
        body: str = "",
        route: str | None = None,
        action: str = "none",
        urgency: str = "actionable",
        task_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        pending_id: str | None = None,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
        resolved: bool = False,
    ) -> tuple[NotificationRow, bool]:
        """Idempotent create by ``(user_id, dedup_key)``. Returns
        ``(row, created)`` — ``created=False`` when the row already existed
        (a projector re-fire); the caller then knows not to re-broadcast an
        ``added`` frame.

        ``resolved``: stamp ``resolved_at`` at creation. The open list is
        ``resolved_at IS NULL``, so a purely INFORMATIONAL notification lands
        in history and fires its OS toast without taking a slot in the
        action inbox — nothing for the user to dismiss."""
        existing = await self.get_by_dedup(user_id, dedup_key)
        if existing is not None:
            return existing, False
        row = NotificationRow(
            user_id=user_id,
            dedup_key=dedup_key,
            kind=kind,
            title=title,
            body=body,
            route=route,
            action=action,
            urgency=urgency,
            task_id=task_id,
            project_id=project_id,
            session_id=session_id,
            pending_id=pending_id,
            source_event_id=source_event_id,
            payload=payload or {},
            resolved_at=now_ms() if resolved else None,
        )
        self._db.add(row)
        await async_commit_with_retry(self._db, where="NotificationDatastore.upsert")
        return row, True

    async def list_open(
        self, user_id: str, *, limit: int = 100
    ) -> list[NotificationRow]:
        """Open (unresolved) notifications, newest first."""
        return list(
            (
                await self._db.execute(
                    select(NotificationRow)
                    .where(
                        NotificationRow.user_id == user_id,
                        NotificationRow.resolved_at.is_(None),
                    )
                    .order_by(NotificationRow.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def list_history(
        self, user_id: str, *, limit: int = 50, before: int | None = None
    ) -> list[NotificationRow]:
        """Resolved (dismissed / handled) notifications, newest first.
        ``before`` is a ``created_at`` cursor — only rows strictly older are
        returned, so the caller pages by passing the last entry's stamp."""
        stmt = select(NotificationRow).where(
            NotificationRow.user_id == user_id,
            NotificationRow.resolved_at.is_not(None),
        )
        if before is not None:
            stmt = stmt.where(NotificationRow.created_at < before)
        stmt = stmt.order_by(NotificationRow.created_at.desc()).limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def count_unread(self, user_id: str) -> int:
        return int(
            (
                await self._db.execute(
                    select(func.count())
                    .select_from(NotificationRow)
                    .where(
                        NotificationRow.user_id == user_id,
                        NotificationRow.resolved_at.is_(None),
                        NotificationRow.read_at.is_(None),
                    )
                )
            ).scalar_one()
        )

    async def mark_read(self, user_id: str, notification_id: str) -> NotificationRow | None:
        row = await self.get_by_id(user_id, notification_id)
        if row is None or row.read_at is not None:
            return row
        row.read_at = now_ms()
        await async_commit_with_retry(self._db, where="NotificationDatastore.mark_read")
        return row

    async def mark_all_read(self, user_id: str) -> int:
        res = await self._db.execute(
            update(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.read_at.is_(None),
                NotificationRow.resolved_at.is_(None),
            )
            .values(read_at=now_ms())
        )
        await async_commit_with_retry(self._db, where="NotificationDatastore.mark_all_read")
        return int(getattr(res, "rowcount", 0) or 0)

    async def resolve_by_dedup(self, user_id: str, dedup_key: str) -> NotificationRow | None:
        row = await self.get_by_dedup(user_id, dedup_key)
        if row is None or row.resolved_at is not None:
            return row
        ts = now_ms()
        row.resolved_at = ts
        if row.read_at is None:
            row.read_at = ts
        await async_commit_with_retry(self._db, where="NotificationDatastore.resolve_by_dedup")
        return row

    async def resolve_informational_backlog(self) -> int:
        """Resolve every OPEN notification of a purely informational kind.

        A one-time cleanup for rows created before completions were ingested
        already-resolved: they are sitting in the action inbox with a Resume
        button and no action to take, and nothing else would ever clear them.
        Cross-owner by design — it runs at boot, before any request scopes an
        owner. Returns how many rows were closed.
        """
        rows = list(
            (
                await self._db.execute(
                    select(NotificationRow).where(
                        NotificationRow.kind.in_(_INFORMATIONAL_KINDS),
                        NotificationRow.resolved_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        ts = now_ms()
        for row in rows:
            row.resolved_at = ts
        await async_commit_with_retry(
            self._db, where="NotificationDatastore.resolve_informational_backlog"
        )
        return len(rows)

    async def resolve_by_pending_id(self, pending_id: str) -> NotificationRow | None:
        """Resolve the open question notification for ``pending_id`` regardless
        of owner. ``pending_id`` is globally unique per question, so no user
        scope is needed — used for conversation questions, whose owner isn't
        held in memory at resolve time (and to survive a restart that cleared
        the aggregator's pending map). Returns the row (carrying ``user_id`` for
        the fan-out) or ``None`` if absent / already resolved."""
        row = (
            await self._db.execute(
                select(NotificationRow).where(
                    NotificationRow.pending_id == pending_id,
                    NotificationRow.resolved_at.is_(None),
                )
            )
        ).scalars().first()
        if row is None:
            return None
        ts = now_ms()
        row.resolved_at = ts
        if row.read_at is None:
            row.read_at = ts
        await async_commit_with_retry(self._db, where="NotificationDatastore.resolve_by_pending_id")
        return row

    async def resolve_open_by_task(
        self, user_id: str, task_id: str, kinds: tuple[str, ...]
    ) -> list[str]:
        """Resolve every open notification of ``kinds`` for one task (e.g. clear
        a ``task_failed`` when the user resumes the task). Returns resolved ids
        so the service can broadcast them."""
        rows = list(
            (
                await self._db.execute(
                    select(NotificationRow).where(
                        NotificationRow.user_id == user_id,
                        NotificationRow.task_id == task_id,
                        NotificationRow.kind.in_(kinds),
                        NotificationRow.resolved_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        ts = now_ms()
        for row in rows:
            row.resolved_at = ts
            if row.read_at is None:
                row.read_at = ts
        await async_commit_with_retry(self._db, where="NotificationDatastore.resolve_open_by_task")
        return [r.id for r in rows]

    async def resolve_open_by_session(
        self, user_id: str, session_id: str, kinds: tuple[str, ...]
    ) -> list[str]:
        """Resolve every open notification of ``kinds`` for one session — used to
        clear a conversation's ``run_failed`` items when a later turn recovers.
        Returns resolved ids so the service can broadcast them."""
        rows = list(
            (
                await self._db.execute(
                    select(NotificationRow).where(
                        NotificationRow.user_id == user_id,
                        NotificationRow.session_id == session_id,
                        NotificationRow.kind.in_(kinds),
                        NotificationRow.resolved_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        ts = now_ms()
        for row in rows:
            row.resolved_at = ts
            if row.read_at is None:
                row.read_at = ts
        await async_commit_with_retry(
            self._db, where="NotificationDatastore.resolve_open_by_session"
        )
        return [r.id for r in rows]

    async def resolve_all_open(self, user_id: str) -> list[str]:
        """Resolve every open notification — the drawer's "clear all". Marks
        unread ones read too. Returns resolved ids."""
        rows = list(
            (
                await self._db.execute(
                    select(NotificationRow).where(
                        NotificationRow.user_id == user_id,
                        NotificationRow.resolved_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        ts = now_ms()
        for row in rows:
            row.resolved_at = ts
            if row.read_at is None:
                row.read_at = ts
        await async_commit_with_retry(self._db, where="NotificationDatastore.resolve_all_open")
        return [r.id for r in rows]

    async def resolve_by_id(self, user_id: str, notification_id: str) -> NotificationRow | None:
        row = await self.get_by_id(user_id, notification_id)
        if row is None or row.resolved_at is not None:
            return row
        ts = now_ms()
        row.resolved_at = ts
        if row.read_at is None:
            row.read_at = ts
        await async_commit_with_retry(self._db, where="NotificationDatastore.resolve_by_id")
        return row
