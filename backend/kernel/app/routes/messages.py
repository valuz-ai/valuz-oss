"""Message routes — /api/v1/sessions/{id}/messages and /api/v1/messages/{id}."""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any

from app.dependencies import get_owner_id, get_store
from app.schemas import (
    AttachmentSchema,
    EventData,
    EventListResponse,
    MessageData,
    MessageListResponse,
    MessageResponse,
    StopReasonSchema,
    TodoItem,
    UserMessageSchema,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from src.core import Event, Message, StorePort

router = APIRouter(tags=["messages"])

StoreDep = Annotated[StorePort, Depends(get_store)]
OwnerDep = Annotated[str, Depends(get_owner_id)]


def _message_to_data(message: Message) -> MessageData:
    stop_reason = None
    if message.stop_reason is not None:
        stop_reason = StopReasonSchema(**dataclasses.asdict(message.stop_reason))
    return MessageData(
        id=message.id,
        session_id=message.session_id,
        user_message=UserMessageSchema(
            text=message.user_message.text,
            attachments=[
                AttachmentSchema(source_path=a.source_path, parsed_path=a.parsed_path)
                for a in message.user_message.attachments
            ],
        ),
        assistant_message=message.assistant_message,
        error_message=message.error_message,
        status=message.status,
        stop_reason=stop_reason,
        total_turns=message.total_turns,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        cache_read_tokens=message.cache_read_tokens,
        cache_write_tokens=message.cache_write_tokens,
        model_usage=message.model_usage,
        started_at=message.started_at,
        ended_at=message.ended_at,
        metadata=message.metadata,
        todos=[TodoItem(**t) for t in message.todos] if message.todos is not None else None,
    )


def _event_to_data(event: Event) -> EventData:
    return EventData(type=event.type, data=event.data, timestamp=event.timestamp)


@router.get(
    "/api/v1/sessions/{session_id}/messages",
    response_model=MessageListResponse,
)
async def list_session_messages(
    session_id: str,
    store: StoreDep,
    owner: OwnerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    session = await store.load_session(owner, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await store.list_messages_for_session(owner, session_id, limit=limit, offset=offset)
    return {"data": [_message_to_data(m) for m in messages]}


@router.get("/api/v1/messages/{message_id}", response_model=MessageResponse)
async def get_message(message_id: str, store: StoreDep, owner: OwnerDep) -> dict[str, Any]:
    message = await store.load_message(owner, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"data": _message_to_data(message)}


@router.get("/api/v1/messages/{message_id}/events", response_model=EventListResponse)
async def get_message_events(
    message_id: str,
    store: StoreDep,
    owner: OwnerDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    message = await store.load_message(owner, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    events = await store.get_events_for_message(owner, message_id, limit=limit, offset=offset)
    return {"data": [_event_to_data(e) for e in events]}
