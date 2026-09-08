"""``FeedbackService`` validation in front of the port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from valuz_agent.modules.feedback.errors import (
    FeedbackInvalid,
    FeedbackMessageNotFound,
    FeedbackMessageRunning,
)
from valuz_agent.modules.feedback.schemas import RecordFeedbackRequest
from valuz_agent.modules.feedback.service import FeedbackService
from valuz_agent.ports.feedback import (
    FeedbackActor,
    FeedbackPort,
    FeedbackRecord,
    FeedbackSubject,
    FeedbackTarget,
)

USER = "owner-1"


@dataclass
class _Msg:
    id: str
    session_id: str
    status: str = "completed"


class FakePort(FeedbackPort):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.withdrawn: list[tuple[str, str, str]] = []

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
        self.calls.append(
            {
                "user_id": actor.user_id,
                "session_id": subject.session_id,
                "message_id": subject.message_id,
                "block_ref": subject.block_ref,
                "action": action,
                "value": value,
                "reason_code": reason_code,
                "source": source,
            }
        )
        return FeedbackRecord(
            id="f1",
            user_id=actor.user_id,
            session_id=subject.session_id,
            message_id=subject.message_id,
            action=action,
            block_ref=subject.block_ref,
            value=value,
            reason_code=reason_code,
            reason=reason,
            target=target,
            source=source,
            surface=surface,
            occurrences=1,
            created_at=1,
            updated_at=1,
        )

    async def withdraw(self, actor: FeedbackActor, subject: FeedbackSubject, action: str) -> bool:
        self.withdrawn.append((actor.user_id, subject.message_id, action))
        return True

    async def list_for_session(self, actor: FeedbackActor, session_id: str) -> list[FeedbackRecord]:
        return []


def _service(port: FakePort, messages: dict[str, _Msg]) -> FeedbackService:
    async def get_message(user_id: str, message_id: str) -> _Msg | None:
        return messages.get(message_id)

    return FeedbackService(port=port, get_message=get_message)


@pytest.mark.asyncio
async def test_rating_records_through_port_with_explicit_owner() -> None:
    port = FakePort()
    svc = _service(port, {"m1": _Msg("m1", "s1")})
    record = await svc.record_from_client(
        USER,
        "s1",
        RecordFeedbackRequest(
            message_id="m1", action="rating", value="down", reason_code="too_slow"
        ),
    )
    assert record.value == "down"
    assert port.calls == [
        {
            "user_id": USER,
            "session_id": "s1",
            "message_id": "m1",
            "block_ref": "",
            "action": "rating",
            "value": "down",
            "reason_code": "too_slow",
            "source": "api",
        }
    ]


@pytest.mark.asyncio
async def test_message_must_belong_to_session_and_have_ended() -> None:
    port = FakePort()
    svc = _service(port, {"m1": _Msg("m1", "other"), "m2": _Msg("m2", "s1", status="running")})
    with pytest.raises(FeedbackMessageNotFound):
        await svc.record_from_client(
            USER, "s1", RecordFeedbackRequest(message_id="m1", action="copy")
        )
    with pytest.raises(FeedbackMessageNotFound):
        await svc.record_from_client(
            USER, "s1", RecordFeedbackRequest(message_id="missing", action="copy")
        )
    with pytest.raises(FeedbackMessageRunning):
        await svc.record_from_client(
            USER, "s1", RecordFeedbackRequest(message_id="m2", action="copy")
        )
    assert port.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "req",
    [
        RecordFeedbackRequest(message_id="m1", action="rating"),
        RecordFeedbackRequest(message_id="m1", action="rating", value="up", reason_code="nope"),
        RecordFeedbackRequest(message_id="m1", action="copy", value="up"),
        RecordFeedbackRequest(message_id="m1", action="copy", reason="why"),
    ],
)
async def test_payload_rules(req: RecordFeedbackRequest) -> None:
    port = FakePort()
    svc = _service(port, {"m1": _Msg("m1", "s1")})
    with pytest.raises(FeedbackInvalid):
        await svc.record_from_client(USER, "s1", req)
    assert port.calls == []


@pytest.mark.asyncio
async def test_withdraw_only_client_actions() -> None:
    port = FakePort()
    svc = _service(port, {})
    assert await svc.withdraw(USER, "s1", message_id="m1", action="rating") is True
    assert port.withdrawn == [(USER, "m1", "rating")]
    with pytest.raises(FeedbackInvalid):
        await svc.withdraw(USER, "s1", message_id="m1", action="regenerate")
