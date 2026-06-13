"""Run routes — WebSocket agent execution and interrupt.

The WS handler is a thin attach/detach + receive-loop layer over the
orchestrator's session bus. The agent's lifecycle is decoupled from
this socket: a refresh or transient drop only detaches the subscriber;
the runtime keeps running, the DB sink keeps persisting, and the next
WS attach replays the in-progress message's events so the new client
catches up seamlessly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Annotated, Any

from app.dependencies import get_orchestrator, get_owner_id, get_store
from app.schemas import DataResponse
from app.ws_sink import WebSocketEventSink
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from src.core import StorePort
from src.core.events import EventSink
from src.core.orchestrator import SessionNotFoundError
from src.core.types import Attachment, UserMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["run"])

StoreDep = Annotated[StorePort, Depends(get_store)]
OwnerDep = Annotated[str, Depends(get_owner_id)]


def _parse_user_message(msg: dict[str, Any]) -> UserMessage:
    """Parse the inbound `{"message": {...}}` frame into a UserMessage.

    Accepts the structured shape
    ``{"text": str, "attachments": [{"source_path": str, "parsed_path": str?}],
    "additional_context": str}``. ``attachments`` and ``additional_context`` are
    optional; ``parsed_path`` is optional per attachment. The legacy ``filepath``
    key is still read (as ``source_path``) for callers mid-migration. The legacy
    string ``message`` form is rejected so callers migrate to the new contract.
    """
    raw = msg.get("message")
    if isinstance(raw, str):
        raise ValueError(
            "Legacy string `message` is no longer accepted; "
            'send {"message": {"text": "...", "attachments": []}}'
        )
    if not isinstance(raw, dict):
        raise ValueError("Missing or invalid `message`")

    text = raw.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("`message.text` must be a non-empty string")

    raw_attachments = raw.get("attachments", [])
    if not isinstance(raw_attachments, list):
        raise ValueError("`message.attachments` must be a list")

    attachments: list[Attachment] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            raise ValueError("Each attachment must be an object with a `source_path` string")
        # ``filepath`` is the legacy single-path key; accept it as ``source_path``.
        source = item.get("source_path", item.get("filepath"))
        if not isinstance(source, str) or not source:
            raise ValueError("Each attachment must have a non-empty `source_path` string")
        parsed = item.get("parsed_path")
        if parsed is not None and not isinstance(parsed, str):
            raise ValueError("`parsed_path` must be a string when present")
        attachments.append(Attachment(source_path=source, parsed_path=parsed or None))

    raw_additional = raw.get("additional_context", "")
    if not isinstance(raw_additional, str):
        raise ValueError("`message.additional_context` must be a string")

    return UserMessage(
        text=text,
        attachments=tuple(attachments),
        additional_context=raw_additional,
    )


@router.websocket("/{session_id}/run")
async def run_session(websocket: WebSocket, session_id: str) -> None:
    # Standalone-kernel auth (HTTP middleware doesn't cover websockets):
    # same bearer token contract as the REST surface. AppConfig re-reads
    # env per construction — negligible cost, avoids importing app.main.
    # Accept first so the close code (4401) reaches the client as a WS
    # frame instead of an opaque HTTP 403 handshake rejection.
    from app.config import AppConfig

    await websocket.accept()
    auth_token = AppConfig().auth_token
    if auth_token:
        supplied = websocket.headers.get("authorization", "")
        if supplied != f"Bearer {auth_token}":
            await websocket.close(code=4401, reason="Unauthorized")
            return

    # Owner id (HTTP middleware / Header-Depends don't cover websockets):
    # the host sends ``X-Valuz-Owner-Id``. No header = an owner-less call → close.
    owner = websocket.headers.get("x-valuz-owner-id")
    if not owner:
        await websocket.close(code=4403, reason="owner id required")
        return

    store = get_store()
    session = await store.load_session(owner, session_id)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    orchestrator = get_orchestrator()
    sink: EventSink = WebSocketEventSink(websocket)

    # Attach this WS as the session's live subscriber. If a turn is
    # already in flight (reconnect mid-run), the bus also replays the
    # in-progress message's persisted events to this sink first so the
    # client catches up before live events resume.
    await orchestrator.attach_session_sink(owner, session_id, sink)

    current_run: asyncio.Task[Any] | None = None

    async def _execute_turn(user_message: UserMessage) -> None:
        try:
            await orchestrator.run_turn(owner, session_id, user_message)
        except SessionNotFoundError:
            await _send_safely(
                websocket,
                {"type": "error", "data": {"message": "Session not found"}},
            )
        except Exception as run_exc:
            logger.exception("Runtime error for session %s", session_id)
            await _send_safely(
                websocket,
                {"type": "error", "data": {"message": str(run_exc)}},
            )

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "data": {"message": "Invalid JSON"}})
                )
                continue

            if msg.get("type") == "interrupt":
                await orchestrator.interrupt(session_id)
                await websocket.send_text(json.dumps({"type": "interrupted"}))
                continue

            try:
                user_message = _parse_user_message(msg)
            except ValueError as parse_exc:
                await websocket.send_text(
                    json.dumps({"type": "error", "data": {"message": str(parse_exc)}})
                )
                continue

            # Wait for the prior turn to finish before dispatching a new
            # one. Awaiting closes the brief race window where ``done()``
            # is still False after the final sink emit.
            if current_run is not None:
                with contextlib.suppress(Exception):
                    await current_run

            current_run = asyncio.create_task(_execute_turn(user_message))

    except WebSocketDisconnect:
        # Detach only — the agent's run task continues, the bus stays,
        # the DB sink keeps persisting. Reconnect re-attaches and
        # replays. No interrupt, no cleanup. The runtime cache is also
        # preserved so subsequent turns reuse it.
        await orchestrator.detach_session_sink(session_id, sink)
        logger.info("WebSocket detached for session %s", session_id)
    except Exception:
        await orchestrator.detach_session_sink(session_id, sink)
        logger.exception("Error in WebSocket session %s", session_id)
        try:
            await websocket.close(code=1011, reason="Internal error")
        except RuntimeError:
            pass


async def _send_safely(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Best-effort send — silently drops if the WS is already closed."""
    try:
        await websocket.send_text(json.dumps(payload))
    except (RuntimeError, WebSocketDisconnect):
        pass


@router.post("/{session_id}/interrupt", response_model=DataResponse)
async def interrupt_session(
    session_id: str,
    store: StoreDep,
    owner: OwnerDep,
) -> dict[str, Any]:
    session = await store.load_session(owner, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    orchestrator = get_orchestrator()
    interrupted = await orchestrator.interrupt(session_id)
    if not interrupted:
        raise HTTPException(status_code=404, detail="Session not running")

    return {"data": None}
