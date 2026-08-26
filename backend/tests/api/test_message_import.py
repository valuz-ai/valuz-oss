from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.routes.messages import import_canonical_message
from app.schemas import ImportMessageRequest
from fastapi import HTTPException
from src.core import Message, UserMessage


def _bundle() -> dict:
    return {
        "version": 1,
        "citations": [
            {
                "citationId": "cit_1",
                "source": {
                    "sourceId": "doc-1",
                    "providerId": "docs",
                    "sourceType": "document",
                    "documentId": "doc-1",
                    "title": "Annual Report",
                    "retrievedAt": "2026-07-30T10:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": "Revenue grew.",
                    "snippet": "Revenue grew.",
                    "capturedAt": "2026-07-30T10:00:00Z",
                },
            }
        ],
    }


class _Store:
    def __init__(self) -> None:
        self.target = SimpleNamespace(id="origin-1", status="idle")
        self.source = Message(
            id="source-message",
            session_id="research-1",
            user_message=UserMessage(text="Summarize"),
            assistant_message="Revenue grew [report](citation://cit_1).",
            started_at=1,
            ended_at=2,
            status="completed",
            total_turns=1,
            metadata={"citation_bundle": _bundle()},
        )
        self.saved: list[Message] = []
        self.events: list[tuple[str, str, str, object, str | None]] = []

    async def load_session(self, owner: str, session_id: str) -> object | None:
        assert owner == "owner"
        return self.target if session_id == "origin-1" else None

    async def load_message(self, owner: str, message_id: str) -> Message | None:
        assert owner == "owner"
        return self.source if message_id == self.source.id else None

    async def save_message(self, owner: str, message: Message) -> None:
        assert owner == "owner"
        self.saved.append(message)

    async def append_event(
        self,
        owner: str,
        session_id: str,
        message_id: str,
        event: object,
        *,
        request_id: str | None = None,
    ) -> int:
        self.events.append((owner, session_id, message_id, event, request_id))
        return len(self.events)


class _Orchestrator:
    def __init__(self) -> None:
        self.events: list[tuple[str, object, bool]] = []

    async def emit_session_event(
        self,
        session_id: str,
        event: object,
        *,
        create_bus: bool,
    ) -> None:
        self.events.append((session_id, event, create_bus))


async def test_import_copies_stored_bundle_and_broadcasts_persisted_events() -> None:
    store = _Store()
    orchestrator = _Orchestrator()

    response = await import_canonical_message(
        "origin-1",
        ImportMessageRequest(
            source_message_id="source-message",
            user_text="Shared from document research",
        ),
        store,
        orchestrator,
        "owner",
    )

    imported = store.saved[0]
    assert response["data"].id == imported.id
    assert imported.session_id == "origin-1"
    assert imported.assistant_message == store.source.assistant_message
    assert imported.metadata["citation_bundle"] == _bundle()
    assert imported.metadata["citation_bundle"] is not store.source.metadata["citation_bundle"]
    assert imported.metadata["imported_from"] == {
        "session_id": "research-1",
        "message_id": "source-message",
    }
    assert [item[3].type for item in store.events] == [
        "user_message",
        "assistant_message",
    ]
    assert all(item[4] for item in store.events)
    assert [item[1].data["seq"] for item in orchestrator.events] == [1, 2]
    assert all(item[1].data["event_uid"] for item in orchestrator.events)


async def test_import_rejects_a_running_target_or_noncanonical_source() -> None:
    store = _Store()
    orchestrator = _Orchestrator()
    store.target.status = "running"

    with pytest.raises(HTTPException, match="Target session is running"):
        await import_canonical_message(
            "origin-1",
            ImportMessageRequest(
                source_message_id="source-message",
                user_text="Share",
            ),
            store,
            orchestrator,
            "owner",
        )

    store.target.status = "idle"
    store.source.metadata = {}
    with pytest.raises(HTTPException, match="canonical citations"):
        await import_canonical_message(
            "origin-1",
            ImportMessageRequest(
                source_message_id="source-message",
                user_text="Share",
            ),
            store,
            orchestrator,
            "owner",
        )
