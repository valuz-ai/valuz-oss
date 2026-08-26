"""User-level control-plane SSE — the always-on multiplexed lifecycle stream.

One connection per user carries lifecycle events (run started / finished /
status) across ALL of the caller's sessions, so the client derives its
running/finished run lists, badges, and the created→running bridge from push
instead of polling. Token deltas and per-session transcript stay on the
per-session data-plane stream (``/v1/sessions/{id}/events/stream``).

See docs/design/event-delivery-unification.md §4 (control plane vs data plane).
"""

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from valuz_agent.adapters.event_sse_adapter import iter_user_events_sse
from valuz_agent.api.deps import get_current_user_id

router = APIRouter(prefix="/v1", tags=["stream"])


@router.get("/stream")
async def subscribe_user_stream(
    request: Request,
    after_seq: int = 0,
    user_id: str = Depends(get_current_user_id),
) -> EventSourceResponse:
    """Reconnectable SSE control-plane stream for the caller's sessions.

    Backfills lifecycle events after ``after_seq`` (the global durable cursor)
    then follows new ones on a ~1s server-side poll, multiplexed across every
    session the user owns. The client resumes with ``?after_seq=<last_seen>``.

    Disconnect handling mirrors the session stream: ``sse-starlette`` cancels
    the generator on client drop, firing its cleanup — so ``request`` is not
    threaded into the (synchronous) disconnect predicate.
    """
    del request  # disconnect handled by EventSourceResponse cancel scope
    return EventSourceResponse(iter_user_events_sse(user_id, after_seq=after_seq))
