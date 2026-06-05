"""WebSocketEventSink — pushes events to a connected WebSocket client."""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.events import Event, EventSink

logger = logging.getLogger(__name__)


class WebSocketEventSink:
    """EventSink that serializes events as JSON and sends via WebSocket.

    Tolerates a closed WebSocket: if the client refresh races with an
    in-flight runtime emit, ``ws.send_text`` raises
    ``RuntimeError("Unexpected ASGI message ...")``. Without handling
    here that would propagate up through the composite sink, kill the
    runtime task, and prevent the DB sink in the same composite from
    persisting the event. Instead we mark the sink dead on the first
    failure and turn subsequent emits into no-ops; the orchestrator's
    disconnect handler is responsible for issuing a proper interrupt.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._dead = False

    async def emit(self, event: Event) -> None:
        if self._dead:
            return
        if self._ws.client_state != WebSocketState.CONNECTED:
            self._dead = True
            return
        payload = {
            "type": event.type,
            "data": event.data,
            "timestamp": event.timestamp,
        }
        try:
            await self._ws.send_text(json.dumps(payload))
        except (RuntimeError, WebSocketDisconnect) as exc:
            self._dead = True
            logger.debug("WebSocket sink dropped event %s: %s", event.type, exc)


def _check_protocol(sink: WebSocketEventSink) -> EventSink:
    return sink
