"""In-memory event broadcast for real-time SSE streaming.

Pushes every event the kernel emits during a turn — including
``text_delta`` (per-token streaming detail the kernel doesn't persist)
— into per-session ``asyncio.Queue`` instances that the SSE adapter
subscribes to. This gives SSE clients sub-second token delivery while
the DB path is owned by the kernel orchestrator's internal
``DatabaseEventSink`` (composed per Message inside ``run_turn``).

Since kernel V5+messages, the orchestrator constructs the DB sink with
its own ``message_id`` and the host hands the orchestrator only the
user-facing/broadcast sink. ``BroadcastEventSink`` therefore takes no
inner sink — it only broadcasts.

Lifecycle:
- ``_run_agent_background`` creates a ``BroadcastEventSink`` at turn start.
- ``iter_events_sse`` calls ``subscribe(session_id)`` and reads from the queue.
- On turn completion or SSE disconnect, queues are cleaned up.
"""

from __future__ import annotations

import asyncio
import logging

from src.core.events import Event

logger = logging.getLogger(__name__)

_session_queues: dict[str, list[asyncio.Queue]] = {}
# Global fan-out subscribers — receive EVERY event regardless of
# session id. Used by host-level aggregators (e.g. the Decision Inbox
# under ADR-022) that need to react to events across all running
# sessions without subscribing N session-scoped queues. Each item in
# the queue is a ``(session_id, event)`` tuple so subscribers don't
# need to re-derive routing info.
_global_queues: list[asyncio.Queue] = []
_lock = asyncio.Lock()


async def subscribe(session_id: str) -> asyncio.Queue:
    async with _lock:
        if session_id not in _session_queues:
            _session_queues[session_id] = []
        q: asyncio.Queue = asyncio.Queue(maxsize=4096)
        _session_queues[session_id].append(q)
        return q


async def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    async with _lock:
        subs = _session_queues.get(session_id)
        if subs is not None:
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                del _session_queues[session_id]


async def subscribe_all() -> asyncio.Queue:
    """Subscribe to EVERY broadcast event across all sessions.

    Items in the returned queue are ``(session_id: str, event: Event)``
    tuples. Caller must call :func:`unsubscribe_all` when done so the
    fan-out list stays bounded.

    Intended for singleton host-level aggregators that span the whole
    process lifetime (e.g. ``DecisionAggregator``). Don't use this for
    per-request subscribers — that's what :func:`subscribe` is for.
    """
    async with _lock:
        q: asyncio.Queue = asyncio.Queue(maxsize=8192)
        _global_queues.append(q)
        return q


async def unsubscribe_all(q: asyncio.Queue) -> None:
    async with _lock:
        try:
            _global_queues.remove(q)
        except ValueError:
            pass


async def cleanup_session(session_id: str) -> None:
    async with _lock:
        subs = _session_queues.pop(session_id, None)
        if subs:
            sentinel = None
            for q in subs:
                try:
                    q.put_nowait(sentinel)
                except asyncio.QueueFull:
                    pass


async def broadcast(session_id: str, event: Event) -> None:
    """Push a synthetic event into all subscribers for ``session_id``.

    Used by callers outside the orchestrator-driven turn loop (e.g. the
    interrupt fallback path that needs to surface a ``session_error``
    even when no message exists yet) to reach SSE subscribers without
    going through the DB. Idempotent when no subscribers are listening.

    Also fans out to every global subscriber (:func:`subscribe_all`)
    with the routing info ``(session_id, event)``. Global fan-out is
    best-effort — a full global queue logs + drops rather than blocking
    per-session delivery.
    """
    subs = _session_queues.get(session_id)
    if subs:
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Broadcast queue full for session %s, dropping event %s",
                    session_id,
                    event.type,
                )
    for gq in _global_queues:
        try:
            gq.put_nowait((session_id, event))
        except asyncio.QueueFull:
            logger.warning(
                "Global broadcast queue full, dropping event %s for session %s",
                event.type,
                session_id,
            )


class BroadcastEventSink:
    """Pushes every event into per-session in-memory subscriber queues.

    Implements ``src.core.EventSink``. Orchestrator owns the DB sink;
    this sink owns the live wire to SSE subscribers.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    async def emit(self, event: Event) -> None:
        await broadcast(self._session_id, event)
