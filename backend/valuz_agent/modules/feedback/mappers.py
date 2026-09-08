"""``FeedbackRow`` → ``FeedbackRecord``."""

from __future__ import annotations

from valuz_agent.modules.feedback.models import FeedbackRow
from valuz_agent.ports.feedback import FeedbackRecord, FeedbackTarget, FeedbackTargetType


def row_to_record(row: FeedbackRow) -> FeedbackRecord:
    target: FeedbackTarget | None = None
    if row.target_type and row.target_id:
        # The CHECK-free column is validated at the service boundary; a row
        # written by an older build with an unknown type still round-trips.
        target = FeedbackTarget(type=_target_type(row.target_type), id=row.target_id)
    return FeedbackRecord(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        message_id=row.message_id,
        action=row.action,
        block_ref=row.block_ref,
        value=row.value,
        reason_code=row.reason_code,
        reason=row.reason,
        target=target,
        source=row.source,
        surface=row.surface,
        occurrences=row.occurrences,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=dict(row.metadata_ or {}),
    )


def _target_type(value: str) -> FeedbackTargetType:
    # ``Literal`` narrowing for mypy; the datastore only ever stores the four
    # known values (see ``FEEDBACK_TARGET_TYPES``).
    return value  # type: ignore[return-value]
