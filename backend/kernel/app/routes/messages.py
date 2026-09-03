"""Message routes — ``{KERNEL_API_PREFIX}/v1/sessions/{id}/messages`` and
``{KERNEL_API_PREFIX}/v1/messages/{id}`` (default ``/api/v1/...`` — see
``app.routes.KERNEL_API_PREFIX``)."""

from __future__ import annotations

import copy
import dataclasses
import uuid
from typing import Annotated, Any

from app.dependencies import get_orchestrator, get_owner_id, get_store
from app.routes import KERNEL_API_PREFIX
from app.schemas import (
    AttachmentSchema,
    EventData,
    EventListResponse,
    ImportMessageRequest,
    MessageData,
    MessageListResponse,
    MessageResponse,
    StopReasonSchema,
    TodoItem,
    UserMessageSchema,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from src.core import Event, Message, StorePort, UserMessage
from src.core.types import now_ms

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
    f"{KERNEL_API_PREFIX}/v1/sessions/{{session_id}}/messages",
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


@router.get(f"{KERNEL_API_PREFIX}/v1/messages/{{message_id}}", response_model=MessageResponse)
async def get_message(message_id: str, store: StoreDep, owner: OwnerDep) -> dict[str, Any]:
    message = await store.load_message(owner, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"data": _message_to_data(message)}


@router.post(
    f"{KERNEL_API_PREFIX}/v1/sessions/{{session_id}}/messages/import",
    response_model=MessageResponse,
    status_code=201,
)
async def import_canonical_message(
    session_id: str,
    body: ImportMessageRequest,
    store: StoreDep,
    orchestrator: Annotated[Any, Depends(get_orchestrator)],
    owner: OwnerDep,
) -> dict[str, Any]:
    """Import a completed canonical assistant message without trusting the UI.

    This is deliberately a server-side copy operation: callers cannot submit
    assistant prose or citation metadata.  Both source and target are loaded
    through the same owner-scoped store, so a guessed id cannot cross users.
    """

    target = await store.load_session(owner, session_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target session not found")
    if target.status == "running":
        raise HTTPException(status_code=409, detail="Target session is running")

    source = await store.load_message(owner, body.source_message_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source message not found")
    if source.session_id == session_id:
        raise HTTPException(status_code=409, detail="Source and target sessions must differ")
    if source.status != "completed" or not source.assistant_message:
        raise HTTPException(status_code=409, detail="Source message is not completed")

    citation_bundle = source.metadata.get("citation_bundle")
    if not (
        isinstance(citation_bundle, dict)
        and citation_bundle.get("version") == 1
        and isinstance(citation_bundle.get("citations"), list)
    ):
        raise HTTPException(status_code=409, detail="Source message has no canonical citations")

    timestamp = now_ms()
    imported = Message(
        id=str(uuid.uuid4()),
        session_id=session_id,
        user_message=UserMessage(text=body.user_text),
        assistant_message=source.assistant_message,
        started_at=timestamp,
        ended_at=timestamp,
        status="completed",
        total_turns=1,
        metadata={
            "citation_bundle": copy.deepcopy(citation_bundle),
            "imported_from": {
                "session_id": source.session_id,
                "message_id": source.id,
            },
        },
    )
    await store.save_message(owner, imported)
    events = [
        Event(
            type="user_message",
            data={
                "message": body.user_text,
                "attachments": [],
                "message_id": imported.id,
            },
            timestamp=timestamp,
        ),
        Event(
            type="assistant_message",
            data={
                "text": imported.assistant_message,
                "citation_bundle": copy.deepcopy(citation_bundle),
                "message_id": imported.id,
            },
            timestamp=timestamp,
        ),
    ]
    for event in events:
        event_uid = str(uuid.uuid4())
        seq = await store.append_event(
            owner,
            session_id,
            imported.id,
            event,
            request_id=event_uid,
        )
        live_data = {**event.data, "event_uid": event_uid}
        if seq is not None:
            live_data["seq"] = seq
        await orchestrator.emit_session_event(
            session_id,
            Event(type=event.type, data=live_data, timestamp=event.timestamp),
            create_bus=True,
        )
    return {"data": _message_to_data(imported)}


@router.get(
    f"{KERNEL_API_PREFIX}/v1/messages/{{message_id}}/events", response_model=EventListResponse
)
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
