"""HTTP routes for the global Decision Inbox (ADR-022).

Two endpoints:

- ``GET /v1/decisions/pending`` — REST snapshot of every currently-
  unresolved ``requires_action(clarifying_questions)`` across all
  task-driven sessions. Used by the frontend for cold-start hydration.
- ``GET /v1/decisions/stream`` — SSE incremental stream of inbox
  changes. First frame is always a ``snapshot`` event carrying the
  full current state; subsequent frames are ``added`` / ``resolved``
  as the aggregator's broadcast subscription fans them out.

Both endpoints lean on :class:`DecisionAggregator` (the process-scoped
singleton wired up at app startup in :mod:`valuz_agent.api.app`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from valuz_agent.api.deps import get_decision_aggregator
from valuz_agent.modules.decisions.schemas import DecisionPendingResponse

if TYPE_CHECKING:
    from valuz_agent.modules.decisions.aggregator import DecisionAggregator


router = APIRouter()


@router.get("/v1/decisions/pending", response_model=DecisionPendingResponse)
async def list_pending(
    agg: DecisionAggregator = Depends(get_decision_aggregator),
) -> DecisionPendingResponse:
    """Return all pending decisions across every task-driven session."""
    return DecisionPendingResponse(entries=agg.snapshot())


@router.get("/v1/decisions/stream")
async def stream(
    request: Request,
    agg: DecisionAggregator = Depends(get_decision_aggregator),
) -> EventSourceResponse:
    """SSE stream of inbox changes.

    Client lifecycle:
    - On connect: receives one ``snapshot`` event (full current state)
    - On each ``add`` / ``resolved`` server-side: receives one frame
    - On disconnect: queue is released; nothing to clean up client-side
    """

    async def event_source():
        queue = await agg.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                stream_event = await queue.get()
                if stream_event is None:
                    # Aggregator shutdown sentinel — terminate the SSE
                    # connection cleanly.
                    break
                yield {
                    "event": stream_event.kind,
                    "data": stream_event.payload.model_dump_json(),
                }
        finally:
            await agg.unsubscribe(queue)

    # ``ping`` keeps the connection alive across proxies / browsers
    # idle-timeouts; sse-starlette emits a comment-only heartbeat.
    return EventSourceResponse(event_source(), ping=30)


__all__ = ["router"]
