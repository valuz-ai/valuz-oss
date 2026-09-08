"""``/v1/sessions/{session_id}/feedback`` — user outcome signals on assistant turns.

Thin HTTP over :class:`~valuz_agent.modules.feedback.service.FeedbackService`.
All three operations act on the CALLER's rows only (per-user visibility);
the owner comes from ``get_current_user_id`` and is threaded explicitly.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.feedback.schemas import (
    FeedbackListResponse,
    FeedbackRecordSchema,
    RecordFeedbackRequest,
)
from valuz_agent.modules.feedback.service import FeedbackService

router = APIRouter(prefix="/v1/sessions", tags=["feedback"])


def get_feedback_service() -> FeedbackService:
    return FeedbackService()


@router.get("/{session_id}/feedback", response_model=FeedbackListResponse)
async def list_session_feedback(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: FeedbackService = Depends(get_feedback_service),
) -> FeedbackListResponse:
    """Every feedback row the caller wrote in this session (UI rehydration)."""
    records = await svc.list_for_session(user_id, session_id)
    return FeedbackListResponse(items=[FeedbackRecordSchema.from_record(r) for r in records])


@router.post("/{session_id}/feedback", status_code=201, response_model=FeedbackRecordSchema)
async def record_session_feedback(
    session_id: str,
    body: RecordFeedbackRequest,
    user_id: str = Depends(get_current_user_id),
    svc: FeedbackService = Depends(get_feedback_service),
) -> FeedbackRecordSchema:
    """Upsert a ``rating`` / ``copy`` row on one of the session's messages."""
    record = await svc.record_from_client(user_id, session_id, body)
    return FeedbackRecordSchema.from_record(record)


@router.delete("/{session_id}/feedback", status_code=204)
async def withdraw_session_feedback(
    session_id: str,
    message_id: str = Query(min_length=1),
    action: Literal["rating", "copy"] = Query(),
    block_ref: str = Query(default="", max_length=128),
    user_id: str = Depends(get_current_user_id),
    svc: FeedbackService = Depends(get_feedback_service),
) -> Response:
    """Delete the caller's row (un-rate); 404 when there was none."""
    removed = await svc.withdraw(
        user_id, session_id, message_id=message_id, action=action, block_ref=block_ref
    )
    if not removed:
        from valuz_agent.modules.feedback.errors import FeedbackMessageNotFound

        raise FeedbackMessageNotFound("No feedback row to withdraw")
    return Response(status_code=204)
