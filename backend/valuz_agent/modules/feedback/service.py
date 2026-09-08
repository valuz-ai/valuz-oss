"""Feedback service — request validation in front of :class:`FeedbackPort`.

Edition-neutral: an overlay swaps the port, never this class. The only
kernel touch is a read of the subject Message through the ``kernel_client``
seam, to prove it exists, belongs to the caller's session, and has ended.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.modules.feedback.errors import (
    FeedbackInvalid,
    FeedbackMessageNotFound,
    FeedbackMessageRunning,
)
from valuz_agent.modules.feedback.schemas import RecordFeedbackRequest
from valuz_agent.ports.feedback import (
    CLIENT_FEEDBACK_ACTIONS,
    FEEDBACK_REASON_CODES,
    FEEDBACK_REASON_CODES_METADATA_KEY,
    FeedbackActor,
    FeedbackPort,
    FeedbackRecord,
    FeedbackSubject,
    get_feedback_port,
)

MessageReader = Callable[[str, str], Awaitable[Any]]


class FeedbackService:
    def __init__(
        self,
        *,
        port: FeedbackPort | None = None,
        get_message: MessageReader | None = None,
    ) -> None:
        self._port = port
        self._get_message: MessageReader = get_message or kernel_client.get_message

    @property
    def port(self) -> FeedbackPort:
        return self._port or get_feedback_port()

    async def record_from_client(
        self, user_id: str, session_id: str, req: RecordFeedbackRequest
    ) -> FeedbackRecord:
        _validate_client_payload(req)
        await self._require_ended_message(user_id, session_id, req.message_id)
        reason_code, metadata = _normalize_reasons(req)
        return await self.port.record(
            FeedbackActor(user_id),
            FeedbackSubject(session_id, req.message_id, req.block_ref),
            req.action,
            value=req.value,
            reason_code=reason_code,
            reason=req.reason,
            source=req.source,
            surface=req.surface,
            metadata=metadata,
        )

    async def withdraw(
        self,
        user_id: str,
        session_id: str,
        *,
        message_id: str,
        action: str,
        block_ref: str = "",
    ) -> bool:
        if action not in CLIENT_FEEDBACK_ACTIONS:
            raise FeedbackInvalid(f"action {action!r} cannot be withdrawn by a client")
        return await self.port.withdraw(
            FeedbackActor(user_id), FeedbackSubject(session_id, message_id, block_ref), action
        )

    async def list_for_session(self, user_id: str, session_id: str) -> list[FeedbackRecord]:
        return await self.port.list_for_session(FeedbackActor(user_id), session_id)

    async def _require_ended_message(self, user_id: str, session_id: str, message_id: str) -> None:
        message = await self._get_message(user_id, message_id)
        if message is None or getattr(message, "session_id", None) != session_id:
            raise FeedbackMessageNotFound()
        if getattr(message, "status", None) == "running":
            raise FeedbackMessageRunning()


def _validate_client_payload(req: RecordFeedbackRequest) -> None:
    if req.action == "rating":
        if req.value is None:
            raise FeedbackInvalid("rating requires value")
        for code in _requested_reason_codes(req):
            if code not in FEEDBACK_REASON_CODES:
                raise FeedbackInvalid(f"unknown reason_code {code!r}")
        return
    if req.value is not None:
        raise FeedbackInvalid(f"value is only valid for rating, not {req.action!r}")
    if req.reason_code is not None or req.reason is not None or req.reason_codes:
        raise FeedbackInvalid(f"reason is only valid for rating, not {req.action!r}")


def _requested_reason_codes(req: RecordFeedbackRequest) -> list[str]:
    """The multi-select chips, ``reason_code`` first, de-duplicated in order."""
    codes: list[str] = []
    for code in [req.reason_code, *(req.reason_codes or [])]:
        if code and code not in codes:
            codes.append(code)
    return codes


def _normalize_reasons(req: RecordFeedbackRequest) -> tuple[str | None, dict[str, Any] | None]:
    """``reason_code`` = first chip (the indexed column); the full list rides
    ``metadata["reason_codes"]`` so a multi-select needs no schema change."""
    codes = _requested_reason_codes(req)
    metadata: dict[str, Any] = dict(req.metadata or {})
    if codes:
        metadata[FEEDBACK_REASON_CODES_METADATA_KEY] = codes
    return (codes[0] if codes else None), (metadata or None)
