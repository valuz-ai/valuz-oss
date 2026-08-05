from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.sessions.models import (
    QueuedInputRow,
    SessionAttachmentRow,
)


class SessionDatastore:
    """Attachment-only datastore.

    Session and event storage is now owned by the V5 kernel (``sessions`` and
    ``events`` tables). Only attachment metadata (``valuz_session_attachment``)
    remains in the valuz layer.

    User-facing reads take the caller's ``user_id`` first and filter on it;
    writes stamp the owner. ``update_attachment_parse`` and
    ``mark_attachments_consumed`` stay cross-owner — they target rows by their
    globally-unique id(s) from the fire-and-forget parse task / post-turn
    finalize, which run without an ambient request owner.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---- Attachment operations ----

    async def list_attachments(
        self, user_id: str, session_id: str, *, include_consumed: bool = False
    ) -> list[SessionAttachmentRow]:
        """List a session's attachments.

        By default returns only the **pending** set (``consumed_at IS
        NULL``) — the files staged for the next turn. Attachments are
        per-turn: once a turn ships, its rows are stamped
        ``consumed_at`` and drop out of this list, so the panel /
        composer / runtime all see a clean staging set. Pass
        ``include_consumed=True`` for the full history (debugging /
        admin).
        """
        stmt = select(SessionAttachmentRow).where(
            SessionAttachmentRow.session_id == session_id,
            SessionAttachmentRow.user_id == user_id,
        )
        if not include_consumed:
            stmt = stmt.filter(SessionAttachmentRow.consumed_at.is_(None))
        stmt = stmt.order_by(SessionAttachmentRow.created_at)
        return list((await self._db.execute(stmt)).scalars().all())

    async def create_attachment(
        self, user_id: str, row: SessionAttachmentRow
    ) -> SessionAttachmentRow:
        row.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        return row

    async def get_attachment(self, user_id: str, attachment_id: str) -> SessionAttachmentRow | None:
        return (
            (
                await self._db.execute(
                    select(SessionAttachmentRow).where(
                        SessionAttachmentRow.id == attachment_id,
                        SessionAttachmentRow.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def update_attachment_parse(
        self,
        attachment_id: str,
        *,
        parsed_path: str | None,
        parse_status: str,
        parse_mode: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Persist the result of a background parse.

        SYSTEM path: keyed on the globally-unique ``attachment_id``. Called by
        the fire-and-forget parse task spawned from the upload routes once the
        configured ``ParserRouter`` finishes (off the event loop), so it has no
        ambient request owner. The upload request has already returned with
        ``parse_status="parsing"``; this flips the row to ``ready`` (with
        ``parsed_path``) or ``failed``, and records ``parse_mode`` — the
        plugin/engine that actually ran (e.g. ``mineru`` / ``paddleocr`` /
        ``light_local``) for provenance. No-op-safe if the row was deleted
        mid-parse (user removed the attachment): the ``UPDATE`` matches zero
        rows.
        """
        await self._db.execute(
            update(SessionAttachmentRow)
            .where(SessionAttachmentRow.id == attachment_id)
            .values(
                parsed_path=parsed_path,
                parse_status=parse_status,
                parse_mode=parse_mode,
                error_message=error_message,
            )
        )
        await self._db.commit()

    async def mark_attachments_consumed(self, attachment_ids: list[str]) -> None:
        """Stamp ``consumed_at`` on the given rows.

        SYSTEM path: keyed on the globally-unique ``attachment_ids``. Called once
        a turn has run with these attachments baked into its ``UserMessage`` —
        they then drop out of the pending set so the next turn starts with an
        empty staging area.
        """
        if not attachment_ids:
            return
        await self._db.execute(
            update(SessionAttachmentRow)
            .where(SessionAttachmentRow.id.in_(attachment_ids))
            .values(consumed_at=now_ms())
        )
        await self._db.commit()

    async def delete_attachment(self, user_id: str, attachment_id: str) -> None:
        await self._db.execute(
            SessionAttachmentRow.__table__.delete().where(
                SessionAttachmentRow.id == attachment_id,
                SessionAttachmentRow.user_id == user_id,
            )
        )
        await self._db.commit()

    async def delete_attachments_for_session(self, user_id: str, session_id: str) -> None:
        await self._db.execute(
            SessionAttachmentRow.__table__.delete().where(
                SessionAttachmentRow.session_id == session_id,
                SessionAttachmentRow.user_id == user_id,
            )
        )
        await self._db.commit()

    # ---- Queued input operations (session input queue) ----

    _LISTED_QUEUE_STATUSES = ("queued", "blocked")

    async def list_queued(self, user_id: str, session_id: str) -> list[QueuedInputRow]:
        """List a session's user-visible queue (``queued`` + ``blocked``), FIFO.

        ``dispatched`` / ``cancelled`` rows are history and excluded — the
        composer only ever shows what is still waiting or stuck.
        """
        stmt = (
            select(QueuedInputRow)
            .where(
                QueuedInputRow.session_id == session_id,
                QueuedInputRow.user_id == user_id,
                QueuedInputRow.status.in_(self._LISTED_QUEUE_STATUSES),
            )
            .order_by(QueuedInputRow.position, QueuedInputRow.created_at)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def count_queued(self, user_id: str, session_id: str) -> int:
        """Number of still-``queued`` items — the soft-cap check input."""
        stmt = select(func.count(QueuedInputRow.id)).where(
            QueuedInputRow.session_id == session_id,
            QueuedInputRow.user_id == user_id,
            QueuedInputRow.status == "queued",
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def _next_position(self, session_id: str) -> int:
        stmt = select(func.max(QueuedInputRow.position)).where(
            QueuedInputRow.session_id == session_id
        )
        current = (await self._db.execute(stmt)).scalar_one_or_none()
        return int(current) + 1 if current is not None else 0

    async def create_queued(self, user_id: str, row: QueuedInputRow) -> QueuedInputRow:
        row.user_id = user_id
        row.position = await self._next_position(row.session_id)
        self._db.add(row)
        await self._db.commit()
        return row

    async def get_queued(
        self, user_id: str, session_id: str, queue_id: str
    ) -> QueuedInputRow | None:
        return (
            (
                await self._db.execute(
                    select(QueuedInputRow).where(
                        QueuedInputRow.id == queue_id,
                        QueuedInputRow.session_id == session_id,
                        QueuedInputRow.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def update_queued_input(
        self, user_id: str, session_id: str, queue_id: str, input_payload: dict[str, Any]
    ) -> QueuedInputRow | None:
        """Edit a still-``queued`` item's payload. No-op (returns None) if the
        row is gone or already left the ``queued`` state (dispatched/blocked)."""
        row = await self.get_queued(user_id, session_id, queue_id)
        if row is None or row.status != "queued":
            return None
        row.input = input_payload
        row.updated_at = now_ms()
        await self._db.commit()
        return row

    async def delete_queued(self, user_id: str, session_id: str, queue_id: str) -> bool:
        row = await self.get_queued(user_id, session_id, queue_id)
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.commit()
        return True

    async def delete_queue_for_session(self, user_id: str, session_id: str) -> None:
        await self._db.execute(
            delete(QueuedInputRow).where(
                QueuedInputRow.session_id == session_id,
                QueuedInputRow.user_id == user_id,
            )
        )
        await self._db.commit()

    async def promote_to_front(
        self, user_id: str, session_id: str, queue_id: str
    ) -> QueuedInputRow | None:
        """Move a still-``queued`` item to the FIFO head (steer / send-now).

        Sets ``position`` one below the session's current minimum so the next
        ``peek_next_queued`` returns it first. No-op (returns None) if the row
        is gone or already left the ``queued`` state.
        """
        row = await self.get_queued(user_id, session_id, queue_id)
        if row is None or row.status != "queued":
            return None
        stmt = select(func.min(QueuedInputRow.position)).where(
            QueuedInputRow.session_id == session_id,
            QueuedInputRow.status == "queued",
        )
        current_min = (await self._db.execute(stmt)).scalar_one_or_none()
        row.position = int(current_min) - 1 if current_min is not None else 0
        row.updated_at = now_ms()
        await self._db.commit()
        return row

    async def peek_next_queued(self, session_id: str) -> QueuedInputRow | None:
        """Oldest still-``queued`` row for a session (SYSTEM / drain path).

        Cross-owner by id like ``mark_attachments_consumed`` — the drain runs in
        a background task without an ambient request owner.
        """
        return (
            (
                await self._db.execute(
                    select(QueuedInputRow)
                    .where(
                        QueuedInputRow.session_id == session_id,
                        QueuedInputRow.status == "queued",
                    )
                    .order_by(QueuedInputRow.position, QueuedInputRow.created_at)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    async def mark_queued_status(
        self, queue_id: str, status: str, error_message: str | None = None
    ) -> None:
        """Transition a queued row (SYSTEM / drain path), keyed on its unique id."""
        await self._db.execute(
            update(QueuedInputRow)
            .where(QueuedInputRow.id == queue_id)
            .values(status=status, error_message=error_message, updated_at=now_ms())
        )
        await self._db.commit()

    async def list_queued_session_owners(self) -> list[tuple[str, str]]:
        """Distinct ``(session_id, user_id)`` pairs that still have ``queued``
        items (SYSTEM / boot recovery). Owner-agnostic — boot reconcile runs
        without a request user and needs each session's owner to re-establish
        the owner context before resuming its drain."""
        stmt = (
            select(QueuedInputRow.session_id, QueuedInputRow.user_id)
            .where(QueuedInputRow.status == "queued")
            .distinct()
        )
        return [(sid, uid) for sid, uid in (await self._db.execute(stmt)).all()]
