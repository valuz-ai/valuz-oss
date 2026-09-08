"""``/v1/sessions/{id}/feedback`` — thin HTTP over FeedbackService, owner threaded explicitly."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.middleware import ErrorHandlerMiddleware
from valuz_agent.api.routes import feedback as routes
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


class MemoryPort(FeedbackPort):
    """In-memory port honouring the one-row-per-subject invariant."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], FeedbackRecord] = {}

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
        key = (actor.user_id, subject.message_id, action, subject.block_ref)
        previous = self.rows.get(key)
        record = FeedbackRecord(
            id=previous.id if previous else f"f{len(self.rows) + 1}",
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
            occurrences=(previous.occurrences + 1) if previous else 1,
            created_at=previous.created_at if previous else 10,
            updated_at=20,
            metadata=dict(metadata or {}),
        )
        self.rows[key] = record
        return record

    async def withdraw(self, actor: FeedbackActor, subject: FeedbackSubject, action: str) -> bool:
        return (
            self.rows.pop((actor.user_id, subject.message_id, action, subject.block_ref), None)
            is not None
        )

    async def list_for_session(self, actor: FeedbackActor, session_id: str) -> list[FeedbackRecord]:
        return [
            r
            for (uid, _m, _a, _b), r in self.rows.items()
            if uid == actor.user_id and r.session_id == session_id
        ]


@pytest.fixture
def port() -> MemoryPort:
    return MemoryPort()


@pytest.fixture
def client(port: MemoryPort) -> Iterator[TestClient]:
    messages = {"m1": _Msg("m1", "s1"), "m2": _Msg("m2", "s1", status="running")}

    async def get_message(user_id: str, message_id: str) -> _Msg | None:
        return messages.get(message_id)

    app = FastAPI()
    app.add_middleware(ErrorHandlerMiddleware)
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[routes.get_feedback_service] = lambda: FeedbackService(
        port=port, get_message=get_message
    )
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


def test_rate_then_flip_then_withdraw_roundtrip(client: TestClient, port: MemoryPort) -> None:
    created = client.post(
        "/v1/sessions/s1/feedback",
        json={"message_id": "m1", "action": "rating", "value": "up", "source": "ui"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["action"], body["value"], body["occurrences"]) == ("rating", "up", 1)

    flipped = client.post(
        "/v1/sessions/s1/feedback",
        json={
            "message_id": "m1",
            "action": "rating",
            "value": "down",
            "reason_code": "inaccurate_or_incomplete",
        },
    )
    assert flipped.status_code == 201
    assert flipped.json()["id"] == body["id"]
    assert flipped.json()["occurrences"] == 2
    assert flipped.json()["value"] == "down"

    listed = client.get("/v1/sessions/s1/feedback")
    assert listed.status_code == 200
    assert [i["value"] for i in listed.json()["items"]] == ["down"]

    gone = client.delete("/v1/sessions/s1/feedback?message_id=m1&action=rating")
    assert gone.status_code == 204
    assert client.get("/v1/sessions/s1/feedback").json()["items"] == []
    again = client.delete("/v1/sessions/s1/feedback?message_id=m1&action=rating")
    assert again.status_code == 404


def test_copy_is_counted_not_duplicated(client: TestClient) -> None:
    for _ in range(3):
        r = client.post("/v1/sessions/s1/feedback", json={"message_id": "m1", "action": "copy"})
        assert r.status_code == 201
    items = client.get("/v1/sessions/s1/feedback").json()["items"]
    assert len(items) == 1 and items[0]["occurrences"] == 3


def test_error_mapping(client: TestClient) -> None:
    running = client.post("/v1/sessions/s1/feedback", json={"message_id": "m2", "action": "copy"})
    assert running.status_code == 409
    missing = client.post("/v1/sessions/s1/feedback", json={"message_id": "zz", "action": "copy"})
    assert missing.status_code == 404
    invalid = client.post("/v1/sessions/s1/feedback", json={"message_id": "m1", "action": "rating"})
    assert invalid.status_code == 422
    # Server-only actions are rejected by the schema before the service runs.
    server_only = client.post(
        "/v1/sessions/s1/feedback", json={"message_id": "m1", "action": "regenerate"}
    )
    assert server_only.status_code == 422
