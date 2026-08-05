"""HTTP routes for the unified notification ledger (docs/design/notifications.md).

Supersedes ``/v1/decisions/*`` (questions are now a notification kind) and the
interim ``/v1/tasks/attention`` poll (failures stream here). One snapshot + one
SSE + read/dismiss — the frontend faces a single, backend-reconciled account.

Delivery is **DB-poll**, not an in-process broadcast: the stream re-reads the
durable ``valuz_notification`` table on an interval and pushes a fresh snapshot
only when it changed. That makes it correct across a SaaS multi-pod deployment
(a notification written by any pod is visible to a stream on any other), with no
shared bus — the persisted ledger IS the shared state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.sse import shielded
from valuz_agent.modules.notifications.schemas import (
    NotificationEntry,
    NotificationHistoryResponse,
    NotificationListResponse,
    NotificationStreamEvent,
)
from valuz_agent.modules.notifications.service import notification_service

logger = logging.getLogger(__name__)

router = APIRouter()

# How often the stream re-reads the ledger. A notification badge tolerates a
# couple of seconds of latency, and each poll is one indexed read per open
# stream (``ix_notification_user_created``). Env-tunable so a busy deployment can
# trade latency for DB load; a shared pub/sub is the overlay path if this ever
# becomes the bottleneck.
_POLL_SECONDS = max(0.5, float(os.getenv("VALUZ_NOTIFICATION_POLL_SECONDS", "2.5")))
# Emit a comment-only heartbeat after this long with no change so idle proxies
# don't cut the connection.
_HEARTBEAT_SECONDS = 25.0


def _snapshot_signature(
    entries: list[NotificationEntry], unread: int
) -> tuple[tuple[str, int | None], ...]:
    """Cheap change key over the open set. Captures adds (new id), resolves (id
    drops out — ``list_open`` returns only unresolved rows) and read-state flips
    (``read_at``). ``unread`` rides along as the first element for good measure."""
    head: tuple[str, int | None] = ("__unread__", unread)
    return (head, *((e.id, e.read_at) for e in entries))


@router.get("/v1/notifications", response_model=NotificationListResponse)
async def list_notifications(
    user_id: str = Depends(get_current_user_id),
) -> NotificationListResponse:
    """Open (unresolved) notifications + the unread count, for cold-start."""
    entries, unread = await notification_service.snapshot(user_id)
    return NotificationListResponse(entries=entries, unread=unread)


@router.get("/v1/notifications/history", response_model=NotificationHistoryResponse)
async def list_notification_history(
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, description="created_at ms cursor (strictly below)"),
    user_id: str = Depends(get_current_user_id),
) -> NotificationHistoryResponse:
    """Resolved notifications, newest first — the drawer's History tab. Page by
    passing the last entry's ``created_at`` as ``before``."""
    entries, has_more = await notification_service.history(user_id, limit=limit, before=before)
    return NotificationHistoryResponse(entries=entries, has_more=has_more)


@router.get("/v1/notifications/stream")
async def stream_notifications(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> EventSourceResponse:
    """SSE stream of the caller's open notifications.

    Every frame is a full ``snapshot`` (``{entries, unread}``) — the first on
    connect, then one whenever a poll observes the ledger changed. The frontend
    treats a snapshot as a full reset, so no per-row delta protocol is needed and
    the stream is stateless across pods.

    Does NOT poll ``request.is_disconnected()`` in-loop: sse-starlette 3.x runs
    its own disconnect listener on the same ASGI ``receive`` channel, and a
    second concurrent reader stalls the generator so frames batch instead of
    streaming. Client disconnect cancels the generator; there is nothing to
    release (no subscriber registration).
    """

    async def event_source() -> AsyncIterator[dict[str, str]]:
        loop = asyncio.get_event_loop()
        last_sig: tuple[tuple[str, int | None], ...] | None = None
        last_emit = loop.time()
        while True:
            # ``shielded``: a client disconnect cancels this generator; landing
            # that cancellation inside an in-flight DB read would tear the pooled
            # connection down mid-checkin.
            entries, unread = await shielded(notification_service.snapshot(user_id))
            sig = _snapshot_signature(entries, unread)
            now = loop.time()
            if sig != last_sig:
                last_sig = sig
                last_emit = now
                frame = NotificationStreamEvent(
                    kind="snapshot",
                    payload={
                        "entries": [e.model_dump(mode="json") for e in entries],
                        "unread": unread,
                    },
                )
                yield {"event": "snapshot", "data": frame.model_dump_json()}
            elif now - last_emit >= _HEARTBEAT_SECONDS:
                last_emit = now
                yield {"event": "heartbeat", "data": json.dumps({})}
            await asyncio.sleep(_POLL_SECONDS)

    del request  # disconnect handled by EventSourceResponse cancel scope
    return EventSourceResponse(event_source(), ping=30)


@router.post("/v1/notifications/{notification_id}:read")
async def mark_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    await notification_service.mark_read(user_id, notification_id)
    return {"ok": True}


@router.post("/v1/notifications:read-all")
async def mark_all_read(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    await notification_service.mark_all_read(user_id)
    return {"ok": True}


@router.post("/v1/notifications:dismiss-all")
async def dismiss_all(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    """The drawer's "clear all" — every open entry moves to history. A pending
    question stays answerable in its session; only its notification clears."""
    await notification_service.dismiss_all(user_id)
    return {"ok": True}


@router.post("/v1/notifications/{notification_id}:dismiss")
async def dismiss(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    await notification_service.dismiss(user_id, notification_id)
    return {"ok": True}


__all__ = ["router"]
