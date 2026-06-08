from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.sessions.models import SessionAttachmentRow


class SessionDatastore:
    """Attachment-only datastore.

    Session and event storage is now owned by the V5 kernel (``sessions`` and
    ``events`` tables). Only attachment metadata (``valuz_session_attachment``)
    remains in the valuz layer.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---- Attachment operations ----

    async def list_attachments(
        self, session_id: str, *, include_consumed: bool = False
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
        stmt = select(SessionAttachmentRow).filter_by(session_id=session_id)
        if not include_consumed:
            stmt = stmt.filter(SessionAttachmentRow.consumed_at.is_(None))
        stmt = stmt.order_by(SessionAttachmentRow.created_at)
        return list((await self._db.execute(stmt)).scalars().all())

    async def create_attachment(self, row: SessionAttachmentRow) -> SessionAttachmentRow:
        self._db.add(row)
        await self._db.commit()
        return row

    async def get_attachment(self, attachment_id: str) -> SessionAttachmentRow | None:
        return await self._db.get(SessionAttachmentRow, attachment_id)

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

        Called by the fire-and-forget parse task spawned from the upload routes
        once the configured ``ParserRouter`` finishes (off the event loop). The
        upload request has already returned with ``parse_status="parsing"``;
        this flips the row to ``ready`` (with ``parsed_path``) or ``failed``,
        and records ``parse_mode`` — the plugin/engine that actually ran (e.g.
        ``mineru`` / ``paddleocr`` / ``light_local``) for provenance.
        No-op-safe if the row was deleted mid-parse (user removed the
        attachment): the ``UPDATE`` simply matches zero rows.
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

        Called once a turn has run with these attachments baked into
        its ``UserMessage`` — they then drop out of the pending set so
        the next turn starts with an empty staging area.
        """
        if not attachment_ids:
            return
        await self._db.execute(
            update(SessionAttachmentRow)
            .where(SessionAttachmentRow.id.in_(attachment_ids))
            .values(consumed_at=now_ms())
        )
        await self._db.commit()

    async def delete_attachment(self, attachment_id: str) -> None:
        await self._db.execute(
            SessionAttachmentRow.__table__.delete().where(SessionAttachmentRow.id == attachment_id)
        )
        await self._db.commit()

    async def delete_attachments_for_session(self, session_id: str) -> None:
        await self._db.execute(
            SessionAttachmentRow.__table__.delete().where(
                SessionAttachmentRow.session_id == session_id
            )
        )
        await self._db.commit()
