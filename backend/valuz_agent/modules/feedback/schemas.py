"""Wire schemas for ``/v1/sessions/{id}/feedback`` (mirrors ``api/openapi.yaml``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from valuz_agent.ports.feedback import (
    FEEDBACK_BLOCK_REF_MAX_LEN,
    FEEDBACK_REASON_MAX_LEN,
    FeedbackRecord,
)


class RecordFeedbackRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=36)
    #: Clients may only submit these two; the rest are server-emitted.
    action: Literal["rating", "copy"]
    value: Literal["up", "down"] | None = None
    reason_code: str | None = Field(default=None, max_length=32)
    reason: str | None = Field(default=None, max_length=FEEDBACK_REASON_MAX_LEN)
    block_ref: str = Field(default="", max_length=FEEDBACK_BLOCK_REF_MAX_LEN)
    source: Literal["ui", "api"] = "api"
    surface: str | None = Field(default=None, max_length=16)
    metadata: dict[str, Any] | None = None


class FeedbackTargetSchema(BaseModel):
    type: Literal["message", "session", "share", "research_message"]
    id: str


class FeedbackRecordSchema(BaseModel):
    id: str
    session_id: str
    message_id: str
    action: str
    block_ref: str
    value: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    target: FeedbackTargetSchema | None = None
    source: str
    surface: str | None = None
    occurrences: int
    created_at: int
    updated_at: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: FeedbackRecord) -> FeedbackRecordSchema:
        return cls(
            id=record.id,
            session_id=record.session_id,
            message_id=record.message_id,
            action=record.action,
            block_ref=record.block_ref,
            value=record.value,
            reason_code=record.reason_code,
            reason=record.reason,
            target=(
                FeedbackTargetSchema(type=record.target.type, id=record.target.id)
                if record.target
                else None
            ),
            source=record.source,
            surface=record.surface,
            occurrences=record.occurrences,
            created_at=record.created_at,
            updated_at=record.updated_at,
            metadata=dict(record.metadata),
        )


class FeedbackListResponse(BaseModel):
    items: list[FeedbackRecordSchema]
