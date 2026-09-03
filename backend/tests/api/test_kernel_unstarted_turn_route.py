"""A turn that never started gets its OWN message anchor.

Regression for "the second turn's message and its error card render inside the
first turn's answer". A cloud pre-flight failure (workload identity / execution
capability) is caught by the turn driver, which records the user's message so
the failure has something to attach to. That write used ``append_event``, whose
contract is "anchor onto the session's most recent message" — for a turn that
never started that is the PREVIOUS turn's message. Production showed both turns
carrying one ``message_id``; clients key a turn by it, so the second turn's
bubble + error card were folded into the first turn's node and the first turn's
own answer was split around them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.routes.messages import record_unstarted_turn
from app.schemas import AttachmentSchema, RecordUnstartedTurnRequest
from fastapi import HTTPException
from src.core import Message, UserMessage

PREVIOUS_TURN = Message(
    id="message-of-the-previous-turn",
    session_id="sess-1",
    user_message=UserMessage(text="what happened at the close?"),
    assistant_message="Closed at 35.88.",
    started_at=1_000,
    ended_at=2_000,
    status="completed",
    total_turns=1,
)


class _Store:
    """Enough StorePort surface for the route, with the ordering rule kept."""

    def __init__(self, *, session: object | None = SimpleNamespace(id="sess-1")) -> None:
        self._session = session
        self.messages: list[Message] = [PREVIOUS_TURN]
        self.events: list[tuple[str, str, str, Any, str | None]] = []

    async def load_session(self, owner: str, session_id: str) -> object | None:
        assert owner == "owner"
        return self._session if session_id == "sess-1" else None

    async def save_message(self, owner: str, message: Message) -> None:
        assert owner == "owner"
        self.messages.append(message)

    async def append_event(
        self,
        owner: str,
        session_id: str,
        message_id: str,
        event: Any,
        *,
        request_id: str | None = None,
    ) -> int:
        self.events.append((owner, session_id, message_id, event, request_id))
        return len(self.events)

    async def list_messages_for_session(
        self, owner: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        """Same contract as the real store: ``started_at`` DESCENDING."""
        ordered = sorted(self.messages, key=lambda m: m.started_at, reverse=True)
        return ordered[offset : offset + limit]


async def _record(store: _Store, **kwargs: Any) -> str:
    result = await record_unstarted_turn(
        "sess-1",
        RecordUnstartedTurnRequest(message="几个问题哈", **kwargs),
        store,  # type: ignore[arg-type]
        "owner",
    )
    return str(result["data"].message_id)


@pytest.mark.asyncio
async def test_mints_a_message_id_of_its_own() -> None:
    store = _Store()

    message_id = await _record(store)

    # THE bug: reusing the previous turn's id collapses two turns into one.
    assert message_id != PREVIOUS_TURN.id


@pytest.mark.asyncio
async def test_anchors_the_user_message_event_on_the_new_message() -> None:
    store = _Store()

    message_id = await _record(store)

    assert len(store.events) == 1
    (_, session_id, anchor, event, request_id) = store.events[0]
    assert (session_id, anchor) == ("sess-1", message_id)
    assert event.type == "user_message"
    assert event.data["message"] == "几个问题哈"
    # Clients read the id off the event too, not only off the message row.
    assert event.data["message_id"] == message_id
    # Persisted through the uid-keyed path, like every other kernel append.
    assert request_id


@pytest.mark.asyncio
async def test_becomes_the_latest_message_so_finalize_lands_on_it() -> None:
    """``finalize`` appends its ``error_event`` onto ``messages[0]``.

    That is how the failure reaches the right turn without the caller holding
    an id — but only if this message sorts newest.
    """
    store = _Store()

    message_id = await _record(store)

    latest = await store.list_messages_for_session("owner", "sess-1", limit=1)
    assert latest[0].id == message_id


@pytest.mark.asyncio
async def test_records_attachments_verbatim() -> None:
    store = _Store()

    await _record(
        store,
        attachments=[AttachmentSchema(source_path="/w/a.pdf", parsed_path="/w/a.md")],
    )

    (_, _, _, event, _) = store.events[0]
    assert event.data["attachments"] == [{"source_path": "/w/a.pdf", "parsed_path": "/w/a.md"}]
    assert store.messages[-1].user_message.attachments[0].source_path == "/w/a.pdf"


@pytest.mark.asyncio
async def test_rejects_an_unknown_session() -> None:
    store = _Store(session=None)

    with pytest.raises(HTTPException) as caught:
        await _record(store)

    assert caught.value.status_code == 404
