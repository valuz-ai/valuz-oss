"""Persistence for ``valuz_feedback`` — the only layer that touches the DB session."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.feedback.models import FeedbackRow


class FeedbackDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_subject(
        self, user_id: str, message_id: str, action: str, block_ref: str = ""
    ) -> FeedbackRow | None:
        stmt = select(FeedbackRow).where(
            FeedbackRow.user_id == user_id,
            FeedbackRow.message_id == message_id,
            FeedbackRow.action == action,
            FeedbackRow.block_ref == block_ref,
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def list_by_session(self, user_id: str, session_id: str) -> list[FeedbackRow]:
        stmt = (
            select(FeedbackRow)
            .where(FeedbackRow.user_id == user_id, FeedbackRow.session_id == session_id)
            .order_by(FeedbackRow.updated_at.asc(), FeedbackRow.id.asc())
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def upsert(
        self,
        user_id: str,
        *,
        session_id: str,
        message_id: str,
        action: str,
        block_ref: str = "",
        value: str | None = None,
        reason_code: str | None = None,
        reason: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        source: str,
        surface: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackRow:
        """Insert the ``(user, message, action, block_ref)`` row or bump the existing one.

        Repeats increment ``occurrences``; the scalar fields are last-write-wins
        and ``metadata`` is shallow-merged. A concurrent first insert loses the
        unique-index race gracefully: the savepoint rolls back and the row the
        other writer created is updated instead.
        """
        row = await self.get_by_subject(user_id, message_id, action, block_ref)
        if row is None:
            candidate = FeedbackRow(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                action=action,
                block_ref=block_ref,
                value=value,
                reason_code=reason_code,
                reason=reason,
                target_type=target_type,
                target_id=target_id,
                source=source,
                surface=surface,
                occurrences=1,
                metadata_=dict(metadata or {}),
            )
            try:
                async with self._db.begin_nested():
                    self._db.add(candidate)
                    await self._db.flush()
            except IntegrityError:
                row = await self.get_by_subject(user_id, message_id, action, block_ref)
                if row is None:  # pragma: no cover — unique violation without a row
                    raise
            else:
                return candidate

        row.occurrences += 1
        row.value = value
        row.reason_code = reason_code
        row.reason = reason
        row.target_type = target_type
        row.target_id = target_id
        row.source = source
        row.surface = surface
        if metadata:
            row.metadata_ = {**(row.metadata_ or {}), **metadata}
        row.updated_at = now_ms()
        await self._db.flush()
        return row

    async def delete_by_subject(
        self, user_id: str, message_id: str, action: str, block_ref: str = ""
    ) -> bool:
        stmt = delete(FeedbackRow).where(
            FeedbackRow.user_id == user_id,
            FeedbackRow.message_id == message_id,
            FeedbackRow.action == action,
            FeedbackRow.block_ref == block_ref,
        )
        result = await self._db.execute(stmt)
        return bool(result.rowcount)
