"""Cross-session event routes — ``{KERNEL_API_PREFIX}/v1/events`` (default
``/api/v1/events`` — see ``app.routes.KERNEL_API_PREFIX``).

The global stream is the remote analog of the in-process
``SessionOrchestrator.attach_global_tap``: one SSE connection carries
``(session_id, event)`` for every session, so host-level aggregators
(decision inbox, activity overview) follow the whole kernel without
opening N per-session streams.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from app.dependencies import get_orchestrator
from app.event_stream import GlobalQueueTap
from app.routes import KERNEL_API_PREFIX
from app.serializers import live_event_to_data
from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix=f"{KERNEL_API_PREFIX}/v1/events", tags=["events"])

STREAM_HEARTBEAT_SECONDS = 15.0


@router.get("/stream")
async def stream_all_events(
    request: Request,
    orchestrator: Annotated[Any, Depends(get_orchestrator)],
    types: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated event-type allowlist. When set, only events "
                "whose type is in the list are emitted — e.g. a lifecycle-only "
                "consumer (the host control plane) passes "
                "``user_message,session_idle,session_error,session_update`` so "
                "token deltas never cross the wire just to be dropped."
            ),
        ),
    ] = None,
) -> EventSourceResponse:
    """Live event stream across ALL sessions, as Server-Sent Events.

    Live-only by design: aggregators hydrate their initial state from the
    REST surface (sessions + events queries), then follow this stream.
    Each frame's payload carries ``session_id``.
    """
    allowed: frozenset[str] | None = None
    if types is not None:
        allowed = frozenset(t.strip() for t in types.split(",") if t.strip()) or None
    # Disconnect is handled by ``EventSourceResponse``'s cancel scope: when
    # the client drops, sse-starlette cancels the generator, which fires the
    # ``finally`` below and detaches the tap. Do NOT poll
    # ``await request.is_disconnected()`` inside the loop — sse-starlette 3.x
    # runs its own disconnect listener on the same ASGI ``receive`` channel,
    # and a second concurrent reader contends with it, stalling the generator
    # so frames batch up (the exact bug class the host's SSE routes hit and
    # reverted; see valuz_agent/api/routes/sessions.py::subscribe_events).
    del request

    async def _frames() -> AsyncIterator[dict[str, Any]]:
        tap = GlobalQueueTap()
        orchestrator.attach_global_tap(tap)
        try:
            while True:
                try:
                    session_id, event = await asyncio.wait_for(
                        tap.queue.get(), timeout=STREAM_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                if allowed is not None and str(event.type) not in allowed:
                    continue
                yield {
                    "event": "event",
                    "data": live_event_to_data(
                        event, session_id=session_id
                    ).model_dump_json(),
                }
        finally:
            orchestrator.detach_global_tap(tap)

    return EventSourceResponse(_frames())
