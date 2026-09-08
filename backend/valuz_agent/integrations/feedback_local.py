"""OSS ``FeedbackPort`` — writes the local ``valuz_feedback`` table.

Owns its own unit of work per call: feedback is a separate table with
best-effort semantics, so it never rides the caller's transaction.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.modules.feedback.datastore import FeedbackDatastore
from valuz_agent.modules.feedback.mappers import row_to_record
from valuz_agent.ports.feedback import (
    FeedbackActor,
    FeedbackPort,
    FeedbackRecord,
    FeedbackSubject,
    FeedbackTarget,
)


class LocalFeedbackProvider(FeedbackPort):
    async def record(
        self,
        actor: FeedbackActor,
        subject: FeedbackSubject,
        action: str,
        *,
        value: str | None = None,
        reason_code: str | None = None,
        reason: str | None = None,
        target: FeedbackTarget | None = None,
        source: str = "ui",
        surface: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            row = await FeedbackDatastore(db).upsert(
                actor.user_id,
                session_id=subject.session_id,
                message_id=subject.message_id,
                action=action,
                block_ref=subject.block_ref,
                value=value,
                reason_code=reason_code,
                reason=reason,
                target_type=target.type if target else None,
                target_id=target.id if target else None,
                source=source,
                surface=surface,
                metadata=metadata,
            )
            return row_to_record(row)

    async def withdraw(self, actor: FeedbackActor, subject: FeedbackSubject, action: str) -> bool:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            return await FeedbackDatastore(db).delete_by_subject(
                actor.user_id, subject.message_id, action, subject.block_ref
            )

    async def list_for_session(self, actor: FeedbackActor, session_id: str) -> list[FeedbackRecord]:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work(commit=False) as db:
            rows = await FeedbackDatastore(db).list_by_session(actor.user_id, session_id)
            return [row_to_record(row) for row in rows]
