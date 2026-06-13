"""In-memory pending snapshot + broadcast subscription (ADR-022).

The :class:`DecisionAggregator` is a singleton process-scoped service
that maintains the global Decision Inbox snapshot. It:

1. **Hydrates on startup** — scans every kernel session with
   ``status="running"``, walks recent events, and reconstructs each
   unresolved ``requires_action(clarifying_questions)`` into a
   ``DecisionEntry``. Without this, the inbox would be empty until the
   next live event lands; users coming back to a parked task would see
   nothing.
2. **Subscribes to the global broadcast bus** — every kernel event the
   host emits is fanned into the aggregator. The aggregator filters in-
   process for ``requires_action`` / ``action_resolved`` belonging to
   task-driven sessions, enriches each entry via
   :func:`enrich_pending`, and pushes the result into per-subscriber
   SSE queues.
3. **Sweeps on session terminal** — when a session reaches the
   ``terminated`` kernel status (or gets removed altogether), its
   pendings are cleared from the snapshot so the drawer doesn't
   surface stale entries.

Concurrency: one writer task (``_broadcast_loop``) serializes all
snapshot mutations. SSE adapters each get their own fan-out queue via
:meth:`subscribe`; the broadcast loop pushes new events into every
fan-out queue under the same lock that guards snapshot mutation, so the
two stay in sync.
"""

# ruff: noqa: I001 — kernel_bootstrap MUST import before src.core (sys.path setup)
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from app.schemas import EventData as Event
from app.schemas import SessionData as Session

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.decisions.schemas import (
    DecisionEntry,
    DecisionStreamEvent,
)
from valuz_agent.modules.decisions.service import (
    enrich_pending,
    is_task_driven,
)

logger = logging.getLogger(__name__)


# Kernel-native event types (NOT the legacy ``session.requires_action``
# names from event_sse_adapter — those translations live in the SSE
# adapter, we hit the upstream type directly).
_KERNEL_REQUIRES_ACTION = "requires_action"
_KERNEL_ACTION_RESOLVED = "action_resolved"

# Per ADR-022 phase 1: only clarifying_questions is surfaced. Shell /
# file / MCP approvals have their own UX (the strip above the composer);
# unifying them is a phase 2 decision.
_INBOX_SUBJECTS = frozenset({"clarifying_questions"})


class DecisionAggregator:
    """Maintain a live snapshot of pending decisions across all sessions.

    Lifecycle:

    >>> agg = DecisionAggregator()
    >>> await agg.start()                # scan history + subscribe
    >>> snapshot = agg.snapshot()        # for REST GET /pending
    >>> q = await agg.subscribe()        # for SSE /stream
    >>> ev = await q.get()               # DecisionStreamEvent
    >>> await agg.unsubscribe(q)
    >>> await agg.stop()
    """

    def __init__(self) -> None:
        self._pending: dict[str, DecisionEntry] = {}
        """Snapshot keyed by ``pending_id``."""

        # session_id → set of pending_ids — needed for terminal-status
        # sweep (we delete by session, not by pending_id).
        self._by_session: dict[str, set[str]] = {}

        self._subscribers: list[asyncio.Queue[DecisionStreamEvent]] = []
        self._lock = asyncio.Lock()
        self._sub_task: asyncio.Task[None] | None = None
        self._stopped = False

    # ---- Lifecycle --------------------------------------------------

    async def start(self) -> None:
        """Hydrate from history + start the broadcast subscription loop.

        Safe to call repeatedly — second + later calls are no-ops.
        """
        if self._sub_task is not None:
            return
        await self._hydrate_from_history()
        self._sub_task = asyncio.create_task(self._broadcast_loop(), name="decisions-aggregator")
        logger.info(
            "DecisionAggregator started; hydrated %d pending entries",
            len(self._pending),
        )

    async def stop(self) -> None:
        """Cancel the subscription loop + release the broadcast queue.

        Safe to call from FastAPI shutdown handlers — idempotent.
        """
        self._stopped = True
        task = self._sub_task
        self._sub_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        async with self._lock:
            # Signal any live subscribers to drain — they'll exit on the
            # sentinel and clean themselves up via unsubscribe().
            for q in self._subscribers:
                try:
                    q.put_nowait(None)  # type: ignore[arg-type]
                except asyncio.QueueFull:
                    pass
            self._subscribers.clear()
        logger.info("DecisionAggregator stopped")

    # ---- Public read API --------------------------------------------

    def snapshot(self) -> list[DecisionEntry]:
        """Return a stable list of all currently-pending entries.

        Sorted ``raised_at`` ASC so the drawer can render oldest-first
        without further sorting on the client.
        """
        return sorted(self._pending.values(), key=lambda e: e.raised_at)

    async def subscribe(self) -> asyncio.Queue[DecisionStreamEvent]:
        """Open a new fan-out queue for SSE delivery.

        The queue receives a ``snapshot`` event first (carrying the full
        current state), then ``added`` / ``resolved`` events as they
        happen. Caller MUST call :meth:`unsubscribe` to release the
        queue when the SSE connection closes.
        """
        async with self._lock:
            q: asyncio.Queue[DecisionStreamEvent] = asyncio.Queue(maxsize=512)
            # Initial state — caller sees the current snapshot before any
            # live events. Wrapped in the same ``DecisionStreamEvent``
            # shape the HTTP layer already knows how to serialise.
            from valuz_agent.modules.decisions.schemas import (
                _DecisionStreamSnapshotPayload,
            )

            snap_ev = DecisionStreamEvent(
                kind="snapshot",
                payload=_DecisionStreamSnapshotPayload(entries=self.snapshot()),
            )
            await q.put(snap_ev)
            self._subscribers.append(q)
            return q

    async def unsubscribe(self, q: asyncio.Queue[DecisionStreamEvent]) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ---- Internal: hydration ----------------------------------------

    async def _hydrate_from_history(self) -> None:
        """Rebuild the snapshot from kernel events at startup."""
        from valuz_agent.adapters import kernel_client

        # Scan all sessions (the kernel doesn't index by status; the
        # filter is cheap in-memory since active sessions are small in
        # the typical desktop deployment).
        try:
            # Cross-owner: the decision inbox aggregates every owner's
            # task-driven sessions into one process-wide snapshot.
            sessions = await kernel_client.list_all_sessions(limit=500)
        except Exception:  # noqa: BLE001
            logger.warning("decisions hydration: list_sessions failed", exc_info=True)
            return

        for session in sessions:
            if getattr(session, "status", None) != "running":
                continue
            if not is_task_driven(session):
                continue
            try:
                events = await kernel_client.get_events(session.user_id, session.id, limit=200)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "decisions hydration: get_events(%s) failed",
                    session.id,
                    exc_info=True,
                )
                continue

            await self._replay_session_events(session, events)

    async def _replay_session_events(self, session: Session, events: list[Event]) -> None:
        """Reproduce per-pending state from a session's recent events.

        Two-pass over the slice: pass 1 collects resolved pending_ids,
        pass 2 finds unresolved ``requires_action(clarifying_questions)``
        and enriches each into a ``DecisionEntry``. Mirrors the same
        logic ConversationPage.tsx ``refreshEvents`` uses on the
        frontend cold-open path — keeping the two in sync prevents the
        backend snapshot from disagreeing with the inline session UI.
        """
        resolved_ids: set[str] = set()
        for ev in events:
            if ev.type == _KERNEL_ACTION_RESOLVED:
                pid = (ev.data or {}).get("pending_id")
                if isinstance(pid, str):
                    resolved_ids.add(pid)

        for ev in events:
            if ev.type != _KERNEL_REQUIRES_ACTION:
                continue
            data = ev.data or {}
            subject = data.get("subject")
            if subject not in _INBOX_SUBJECTS:
                continue
            pending_id = data.get("pending_id")
            if not isinstance(pending_id, str) or pending_id in resolved_ids:
                continue
            raw_payload = data.get("payload")
            payload_dict = _coerce_payload(raw_payload)
            entry = await enrich_pending(
                session,
                pending_id=pending_id,
                question_payload=payload_dict,
                raised_at=_event_timestamp(ev),
            )
            if entry is None:
                continue
            self._pending[pending_id] = entry
            self._by_session.setdefault(session.id, set()).add(pending_id)

    # ---- Internal: live broadcast loop ------------------------------

    async def _broadcast_loop(self) -> None:
        try:
            async for event in kernel_client.subscribe_all_events():
                if self._stopped:
                    break
                session_id = event.session_id or ""
                if not session_id:
                    continue
                try:
                    await self._handle_event(session_id, event)
                except Exception:  # noqa: BLE001 — broad-catch keeps loop alive
                    logger.warning(
                        "decisions: broadcast handler crashed for %s",
                        event.type,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            return

    async def _handle_event(self, session_id: str, event: Event) -> None:
        if event.type == _KERNEL_REQUIRES_ACTION:
            await self._on_requires_action(session_id, event)
            return
        if event.type == _KERNEL_ACTION_RESOLVED:
            await self._on_action_resolved(session_id, event)
            return
        # Future: when the kernel adds a ``session.terminated`` event
        # type we'll sweep here. For now, terminal-state cleanup
        # happens only through ``action_resolved`` (which the kernel
        # emits with ``decision="interrupted"`` / ``"expired"`` on
        # session shutdown).

    async def _on_requires_action(self, session_id: str, event: Event) -> None:
        data = event.data or {}
        if data.get("subject") not in _INBOX_SUBJECTS:
            return
        pending_id = data.get("pending_id")
        if not isinstance(pending_id, str):
            return
        # Resolve the session to determine run_kind + enrichment join keys.
        session = await self._load_session(session_id)
        if session is None or not is_task_driven(session):
            return
        payload = _coerce_payload(data.get("payload"))
        entry = await enrich_pending(
            session,
            pending_id=pending_id,
            question_payload=payload,
            raised_at=_event_timestamp(event),
        )
        if entry is None:
            return
        async with self._lock:
            # Idempotent on re-emit (kernel doesn't dedupe). Latest
            # write wins on the off chance the payload changed.
            self._pending[pending_id] = entry
            self._by_session.setdefault(session_id, set()).add(pending_id)
            await self._fan_out(
                DecisionStreamEvent(
                    kind="added",
                    payload=_added_payload(entry),
                )
            )

    async def _on_action_resolved(self, session_id: str, event: Event) -> None:
        pending_id = (event.data or {}).get("pending_id")
        if not isinstance(pending_id, str):
            return
        async with self._lock:
            if pending_id not in self._pending:
                return
            del self._pending[pending_id]
            siblings = self._by_session.get(session_id)
            if siblings is not None:
                siblings.discard(pending_id)
                if not siblings:
                    self._by_session.pop(session_id, None)
            await self._fan_out(
                DecisionStreamEvent(
                    kind="resolved",
                    payload=_resolved_payload(pending_id),
                )
            )

    async def _fan_out(self, ev: DecisionStreamEvent) -> None:
        """Push to every subscriber. Caller holds ``self._lock``."""
        for q in self._subscribers:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                logger.warning(
                    "decisions: subscriber queue full, dropping %s",
                    ev.kind,
                )

    # ---- Helpers ----------------------------------------------------

    async def _load_session(self, session_id: str) -> Session | None:
        from valuz_agent.adapters import kernel_client

        try:
            # Cross-owner lookup by id (the live event carries no owner) — the
            # inbox is a process-wide aggregator across every owner.
            sessions = await kernel_client.list_all_sessions(ids=[session_id], limit=1)
            return sessions[0] if sessions else None
        except Exception:  # noqa: BLE001
            logger.warning("decisions: get_session(%s) failed", session_id, exc_info=True)
            return None


# ---- Module-level helpers ------------------------------------------


def _coerce_payload(raw: Any) -> dict[str, Any]:
    """Normalise the kernel ``payload`` field to a dict.

    The kernel sometimes ships ``payload`` as a JSON string (the legacy
    ``Record<string, string>`` SSE contract — see event_sse_adapter
    ``_stringify``); other paths emit it as a native dict. Accept both.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _event_timestamp(event: Event) -> int:
    """Kernel ``Event.timestamp`` as Unix epoch ms (UTC). Defaults to now on
    the (defensive) path where the attribute is missing/wrong-typed."""
    ts = getattr(event, "timestamp", None)
    if isinstance(ts, int):
        return ts
    return now_ms()


def _added_payload(entry: DecisionEntry) -> Any:
    from valuz_agent.modules.decisions.schemas import (
        _DecisionStreamAddedPayload,
    )

    return _DecisionStreamAddedPayload(entry=entry)


def _resolved_payload(pending_id: str) -> Any:
    from valuz_agent.modules.decisions.schemas import (
        _DecisionStreamResolvedPayload,
    )

    return _DecisionStreamResolvedPayload(pending_id=pending_id)


__all__ = ["DecisionAggregator"]
