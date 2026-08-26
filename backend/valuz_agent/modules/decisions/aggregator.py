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
4. **Reconciles against durable truth** — the live tap can lose events
   (bounded global queue, durable-read races, boot windows) while
   ``requires_action`` is always persisted first. LOCAL mode therefore
   re-reads the durable events table on every client read (debounced
   per owner) and on a periodic sweep for connected subscribers,
   fanning out the diff — so a lost live event costs latency, never a
   permanently invisible question.

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
import time
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from app.schemas import EventData as Event
from app.schemas import SessionData as Session

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
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

# Reconciliation cadence. The live tap is the low-latency feed, but it can
# lose events (bounded global queue drops on overflow, durable-read races,
# events emitted before the tap attached at boot) — while ``requires_action``
# itself is ALWAYS persisted (persist-then-broadcast). So durable truth is
# re-read: on every client read path (debounced per owner) and on a periodic
# sweep for connected subscribers. A lost live event then costs latency, never
# a permanently invisible question.
_LOCAL_HYDRATE_DEBOUNCE_SECONDS = 5.0
_RECONCILE_INTERVAL_SECONDS = 30.0
# One quick retry for the live tap's durable-read races (the session row /
# ``valuz_task`` row landing just after the event broadcast).
_ENRICH_RETRY_DELAY_SECONDS = 0.5


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

        # pending_ids for which we've already appended an ``awaiting_user`` task
        # event, so a kernel re-emit (it doesn't dedupe) doesn't spam the task
        # timeline. Per-process — questions raised before a restart keep their
        # prior-process timeline state; we don't re-emit for them.
        self._awaiting_task_events: set[str] = set()

        # (owner_user_id, queue): fan-out is filtered by owner so a shared
        # multi-tenant host never delivers one owner's inbox to another.
        self._subscribers: list[tuple[str, asyncio.Queue[DecisionStreamEvent]]] = []
        self._lock = asyncio.Lock()
        # Per-owner live tap tasks + refcounts. One ``subscribe_all_events(owner)``
        # loop per owner that has ≥1 open SSE subscription; started lazily on the
        # first subscribe, cancelled when the owner's last subscriber leaves.
        # (Was a single process-global tap. A multi-tenant host runs one kernel
        # per owner, so the "all events" stream must be taken per-owner from that
        # owner's kernel — mirroring event_sse_adapter's history=DataService /
        # live=kernel split, at cross-session granularity.)
        self._owner_taps: dict[str, asyncio.Task[None]] = {}
        self._owner_refs: dict[str, int] = {}
        # Mode is decided at start(): LOCAL (single process/boot kernel) keeps the
        # proven single global tap; MULTITENANT (a per-user allocator is bound)
        # uses per-owner taps on each owner's kernel. Dual-mode so local behavior
        # is byte-for-byte unchanged.
        self._multitenant = False
        self._global_tap: asyncio.Task[None] | None = None
        # LOCAL-mode reconciliation state: per-owner debounce stamps for the
        # read-path hydrate + the periodic sweep task.
        self._owner_hydrated_at: dict[str, float] = {}
        self._reconcile_task: asyncio.Task[None] | None = None
        self._stopped = False

    # ---- Lifecycle --------------------------------------------------

    async def start(self) -> None:
        """Decide the mode + (LOCAL only) hydrate history + start the global tap.

        LOCAL (no per-user allocator / ``BootSingletonAllocator``): behavior is
        unchanged — one global hydrate + one global broadcast tap.
        MULTITENANT (a per-user allocator is bound): no global tap; hydration is
        a per-owner durable read and the live tap is per-owner, started lazily on
        the first ``subscribe(owner)`` (one kernel per owner → the "all events"
        stream is inherently per-owner).
        """
        if self._global_tap is not None:
            return
        from valuz_agent.ports.extensions import ext
        from valuz_agent.ports.sandbox_allocator import BootSingletonAllocator

        alloc = getattr(ext, "sandbox_allocator", None)
        self._multitenant = alloc is not None and not isinstance(alloc, BootSingletonAllocator)
        if self._multitenant:
            logger.info("DecisionAggregator started (multi-tenant, per-owner taps)")
            return
        await self._hydrate_all()
        self._global_tap = asyncio.create_task(self._global_tap_loop(), name="decisions-aggregator")
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="decisions-reconcile"
        )
        logger.info(
            "DecisionAggregator started (local); hydrated %d pending entries", len(self._pending)
        )

    async def stop(self) -> None:
        """Cancel the global tap + every per-owner tap + release queues. Idempotent."""
        self._stopped = True
        taps = list(self._owner_taps.values())
        self._owner_taps.clear()
        self._owner_refs.clear()
        if self._global_tap is not None:
            taps.append(self._global_tap)
            self._global_tap = None
        if self._reconcile_task is not None:
            taps.append(self._reconcile_task)
            self._reconcile_task = None
        for task in taps:
            task.cancel()
        for task in taps:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        async with self._lock:
            # Signal any live subscribers to drain — they'll exit on the
            # sentinel and clean themselves up via unsubscribe().
            for _owner, q in self._subscribers:
                try:
                    q.put_nowait(None)  # type: ignore[arg-type]
                except asyncio.QueueFull:
                    pass
            self._subscribers.clear()
        logger.info("DecisionAggregator stopped")

    # ---- Public read API --------------------------------------------

    def _owner_entries(self, owner_user_id: str) -> list[DecisionEntry]:
        """Sorted (raised_at ASC) in-memory pendings for one owner. Caller may
        hold ``self._lock``; pure read over ``self._pending``."""
        return sorted(
            (e for e in self._pending.values() if e.owner_user_id == owner_user_id),
            key=lambda e: e.raised_at,
        )

    async def snapshot(self, owner_user_id: str) -> list[DecisionEntry]:
        """Return ``owner_user_id``'s currently-pending entries (oldest first).

        MULTITENANT: history is a per-owner durable read (DataService) so the
        REST snapshot is correct with no live subscription and even when the
        owner's sandbox is gone (mirrors event_sse_adapter: history=durable).
        LOCAL: the global tap keeps ``_pending`` fresh in the common case, but
        it CAN lose events (bounded queue, races, boot windows) — so client
        reads also reconcile against durable, debounced per owner.
        """
        if self._multitenant:
            await self._hydrate_owner(owner_user_id)
        else:
            await self._hydrate_owner_if_due(owner_user_id)
        async with self._lock:
            return self._owner_entries(owner_user_id)

    async def subscribe(self, owner_user_id: str) -> asyncio.Queue[DecisionStreamEvent]:
        """Open a fan-out queue for SSE delivery, scoped to ``owner_user_id``.

        First frame is a ``snapshot`` of this owner's state, then ``added`` /
        ``resolved`` deltas for this owner only. MULTITENANT: hydrate from durable
        + start a per-owner live tap on that owner's kernel. LOCAL: the global tap
        feeds ``_pending`` live, and a debounced durable reconcile here makes the
        snapshot frame authoritative on every (re)connect. Caller MUST call
        :meth:`unsubscribe` when the SSE connection closes.
        """
        if self._multitenant:
            # Hydrate from durable BEFORE taking the lock (durable I/O).
            await self._hydrate_owner(owner_user_id)
        else:
            await self._hydrate_owner_if_due(owner_user_id)
        from valuz_agent.modules.decisions.schemas import (
            _DecisionStreamSnapshotPayload,
        )

        async with self._lock:
            q: asyncio.Queue[DecisionStreamEvent] = asyncio.Queue(maxsize=512)
            snap_ev = DecisionStreamEvent(
                kind="snapshot",
                payload=_DecisionStreamSnapshotPayload(entries=self._owner_entries(owner_user_id)),
            )
            await q.put(snap_ev)
            self._subscribers.append((owner_user_id, q))
            if self._multitenant:
                self._owner_refs[owner_user_id] = self._owner_refs.get(owner_user_id, 0) + 1
                # First subscriber for this owner → start the live tap on their kernel.
                if owner_user_id not in self._owner_taps and not self._stopped:
                    self._owner_taps[owner_user_id] = asyncio.create_task(
                        self._owner_tap_loop(owner_user_id),
                        name=f"decisions-tap-{owner_user_id}",
                    )
            return q

    async def unsubscribe(self, q: asyncio.Queue[DecisionStreamEvent]) -> None:
        async with self._lock:
            owners = [o for (o, sq) in self._subscribers if sq is q]
            self._subscribers = [(o, sq) for (o, sq) in self._subscribers if sq is not q]
            if not self._multitenant:
                return  # local: global tap owns _pending; nothing per-owner to tear down
            tap_to_stop: asyncio.Task[None] | None = None
            for owner_user_id in owners:
                left = self._owner_refs.get(owner_user_id, 1) - 1
                if left > 0:
                    self._owner_refs[owner_user_id] = left
                    continue
                # Last subscriber for this owner left → drop refcount, stop tap,
                # and clear their in-memory pendings (durable stays authoritative).
                self._owner_refs.pop(owner_user_id, None)
                tap_to_stop = self._owner_taps.pop(owner_user_id, None)
                self._forget_owner_locked(owner_user_id)
        if tap_to_stop is not None:
            tap_to_stop.cancel()

    def _forget_owner_locked(self, owner_user_id: str) -> None:
        """Drop an owner's in-memory pendings. Caller holds ``self._lock``."""
        stale = [pid for pid, e in self._pending.items() if e.owner_user_id == owner_user_id]
        for pid in stale:
            self._pending.pop(pid, None)
        for sid, pids in list(self._by_session.items()):
            pids.difference_update(stale)
            if not pids:
                self._by_session.pop(sid, None)

    # ---- Internal: hydration ----------------------------------------

    async def _hydrate_all(self) -> None:
        """LOCAL: rebuild the snapshot from ALL sessions (cross-owner) at startup."""
        try:
            sessions = await data_reader().list_all_sessions(limit=500)
        except kernel_client.KernelNotImplementedError:
            logger.info("decisions hydration: cross-owner listing unavailable — starting empty")
            return
        except Exception:  # noqa: BLE001
            logger.warning("decisions hydration: list_all_sessions failed", exc_info=True)
            return
        for session in sessions:
            if getattr(session, "status", None) != "running" or not is_task_driven(session):
                continue
            try:
                events = await kernel_client.get_events(session.user_id, session.id, limit=200)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "decisions hydration: get_events(%s) failed", session.id, exc_info=True
                )
                continue
            fresh = await self._collect_pending(session, events)
            async with self._lock:
                self._pending.update(fresh)
                for pid in fresh:
                    self._by_session.setdefault(session.id, set()).add(pid)

    async def _global_tap_loop(self) -> None:
        """LOCAL: single process-global tap over ``subscribe_all_events()``."""
        try:
            async for event in kernel_client.subscribe_all_events():
                if self._stopped:
                    break
                session_id = event.session_id or ""
                if not session_id:
                    continue
                try:
                    await self._handle_event(None, session_id, event)
                except Exception:  # noqa: BLE001 — keep the tap alive
                    logger.warning(
                        "decisions: global tap handler crashed for %s", event.type, exc_info=True
                    )
        except asyncio.CancelledError:
            return

    async def _hydrate_owner_if_due(self, owner_user_id: str) -> None:
        """LOCAL read-path reconcile, debounced per owner.

        ``requires_action`` is always persisted before it is broadcast, so the
        durable events table is the source of truth the in-memory snapshot can
        silently diverge from (dropped tap events, races, boot windows). A
        debounced re-read on every ``snapshot()`` / ``subscribe()`` guarantees a
        client can never be stuck with a permanently missing pending.
        """
        now = time.monotonic()
        last = self._owner_hydrated_at.get(owner_user_id)
        if last is not None and now - last < _LOCAL_HYDRATE_DEBOUNCE_SECONDS:
            return
        # Stamp before the (slow) durable read so concurrent readers don't
        # stampede into parallel hydrates.
        self._owner_hydrated_at[owner_user_id] = now
        await self._hydrate_owner(owner_user_id)

    async def _reconcile_loop(self) -> None:
        """LOCAL periodic durable sweep for owners with live subscribers.

        The read-path debounce only heals clients that re-read (page open, SSE
        reconnect). A client whose stream stays connected but whose ``added``
        delta was lost upstream has no other recovery signal — this sweep plus
        the diff fan-out inside :meth:`_hydrate_owner` converges them too.
        """
        try:
            while not self._stopped:
                await asyncio.sleep(_RECONCILE_INTERVAL_SECONDS)
                async with self._lock:
                    owners = {o for (o, _q) in self._subscribers}
                for owner_user_id in owners:
                    try:
                        await self._hydrate_owner(owner_user_id)
                    except Exception:  # noqa: BLE001 — keep sweeping
                        logger.warning(
                            "decisions reconcile: hydrate(%s) failed",
                            owner_user_id,
                            exc_info=True,
                        )
        except asyncio.CancelledError:
            return

    async def _hydrate_owner(self, owner_user_id: str) -> None:
        """Reconcile ``owner_user_id``'s pending snapshot against the durable store.

        Per-owner (the durable read is owner-scoped and works even when the
        owner's sandbox is gone). All durable I/O happens without the lock; the
        diff is applied under the lock:

        - Entries present in durable but not in memory are added AND fanned out
          as ``added`` deltas, so already-connected subscribers converge too —
          not only the next snapshot reader.
        - In-memory entries absent from durable are removed (fanned out as
          ``resolved``) only when ``raised_at`` predates the scan start — an
          entry the live tap added while the durable read was in flight is
          kept, so reconcile never undoes a concurrent live add.
        """
        started_at = now_ms()
        try:
            sessions = await data_reader().list_sessions(owner_user_id, limit=500)
        except kernel_client.KernelNotImplementedError:
            return
        except Exception:  # noqa: BLE001
            logger.warning(
                "decisions hydration: list_sessions(%s) failed", owner_user_id, exc_info=True
            )
            return

        fresh: dict[str, DecisionEntry] = {}
        fresh_by_session: dict[str, set[str]] = {}
        for session in sessions:
            if getattr(session, "status", None) != "running" or not is_task_driven(session):
                continue
            try:
                events = await kernel_client.get_events(session.user_id, session.id, limit=200)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "decisions hydration: get_events(%s) failed", session.id, exc_info=True
                )
                continue
            for pending_id, entry in (await self._collect_pending(session, events)).items():
                fresh[pending_id] = entry
                fresh_by_session.setdefault(session.id, set()).add(pending_id)

        async with self._lock:
            current = {
                pid: e for pid, e in self._pending.items() if e.owner_user_id == owner_user_id
            }
            added_ids = [pid for pid in fresh if pid not in current]
            removed_ids = [
                pid
                for pid, e in current.items()
                if pid not in fresh and e.raised_at < started_at
            ]
            if added_ids:
                logger.info(
                    "decisions reconcile: recovered %d pending(s) missing from memory for %s",
                    len(added_ids),
                    owner_user_id,
                )
            for pid in removed_ids:
                entry = self._pending.pop(pid, None)
                if entry is None:
                    continue
                siblings = self._by_session.get(entry.session_id)
                if siblings is not None:
                    siblings.discard(pid)
                    if not siblings:
                        self._by_session.pop(entry.session_id, None)
            self._pending.update(fresh)
            for sid, pids in fresh_by_session.items():
                self._by_session.setdefault(sid, set()).update(pids)
            for pid in added_ids:
                await self._fan_out(
                    DecisionStreamEvent(kind="added", payload=_added_payload(fresh[pid])),
                    owner_user_id,
                )
                await self._project_question_added(fresh[pid])
            for pid in removed_ids:
                await self._fan_out(
                    DecisionStreamEvent(kind="resolved", payload=_resolved_payload(pid)),
                    owner_user_id,
                )
                if owner_user_id:
                    await self._project_question_resolved(owner_user_id, pid)

    async def _collect_pending(
        self, session: Session, events: list[Event]
    ) -> dict[str, DecisionEntry]:
        """Reproduce per-pending state from a session's recent events (no
        mutation, no lock).

        Two-pass over the slice: pass 1 collects resolved pending_ids, pass 2
        finds unresolved ``requires_action(clarifying_questions)`` and enriches
        each into a ``DecisionEntry``. Mirrors ConversationPage.tsx
        ``refreshEvents`` on the frontend cold-open path so the backend snapshot
        never disagrees with the inline session UI.
        """
        resolved_ids: set[str] = set()
        for ev in events:
            if ev.type == _KERNEL_ACTION_RESOLVED:
                pid = (ev.data or {}).get("pending_id")
                if isinstance(pid, str):
                    resolved_ids.add(pid)

        out: dict[str, DecisionEntry] = {}
        for ev in events:
            if ev.type != _KERNEL_REQUIRES_ACTION:
                continue
            data = ev.data or {}
            if data.get("subject") not in _INBOX_SUBJECTS:
                continue
            pending_id = data.get("pending_id")
            if not isinstance(pending_id, str) or pending_id in resolved_ids:
                continue
            entry = await enrich_pending(
                session,
                pending_id=pending_id,
                question_payload=_coerce_payload(data.get("payload")),
                raised_at=_event_timestamp(ev),
                user_id=session.user_id,
            )
            if entry is not None:
                out[pending_id] = entry
        return out

    # ---- Internal: live broadcast loop ------------------------------

    async def _owner_tap_loop(self, owner_user_id: str) -> None:
        """Per-owner live tap: consume ``owner_user_id``'s kernel event stream
        (``subscribe_all_events`` routed to that owner's kernel) and turn
        requires_action / action_resolved into inbox deltas.

        Resilient: if the owner's kernel isn't up yet (or restarts) the stream
        ends immediately; we re-hydrate from durable and retry with a short
        backoff while the owner still has subscribers, so an inbox opened before
        a turn starts still catches its live decisions.
        """
        try:
            while not self._stopped and owner_user_id in self._owner_taps:
                try:
                    async for event in kernel_client.subscribe_all_events_for(owner_user_id):
                        if self._stopped:
                            break
                        session_id = event.session_id or ""
                        if not session_id:
                            continue
                        try:
                            await self._handle_event(owner_user_id, session_id, event)
                        except Exception:  # noqa: BLE001 — keep the tap alive
                            logger.warning(
                                "decisions: tap handler crashed for %s", event.type, exc_info=True
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — transient kernel/transport error
                    logger.warning(
                        "decisions: tap(%s) stream errored; retrying", owner_user_id, exc_info=True
                    )
                if self._stopped or owner_user_id not in self._owner_taps:
                    break
                # Stream ended (no kernel yet / kernel restarted) — refresh the
                # durable snapshot and retry attaching.
                await self._hydrate_owner(owner_user_id)
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            return

    async def _handle_event(self, owner_user_id: str | None, session_id: str, event: Event) -> None:
        if event.type == _KERNEL_REQUIRES_ACTION:
            await self._on_requires_action(owner_user_id, session_id, event)
            return
        if event.type == _KERNEL_ACTION_RESOLVED:
            await self._on_action_resolved(session_id, event)
            return
        # Future: when the kernel adds a ``session.terminated`` event
        # type we'll sweep here. For now, terminal-state cleanup
        # happens only through ``action_resolved`` (which the kernel
        # emits with ``decision="interrupted"`` / ``"expired"`` on
        # session shutdown).

    async def _on_requires_action(
        self, owner_user_id: str | None, session_id: str, event: Event
    ) -> None:
        data = event.data or {}
        if data.get("subject") not in _INBOX_SUBJECTS:
            return
        pending_id = data.get("pending_id")
        if not isinstance(pending_id, str):
            return
        payload = _coerce_payload(data.get("payload"))
        # The live broadcast can outrun the durable writes this joins against
        # (the session row via write-through, the ``valuz_task`` row). One
        # short retry absorbs that lag; a real miss costs only latency — the
        # read-path / periodic reconcile re-reads durable truth later.
        entry: DecisionEntry | None = None
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(_ENRICH_RETRY_DELAY_SECONDS)
            # Resolve the session (per-owner durable read) for run_kind + join keys.
            session = await self._load_session(owner_user_id, session_id)
            if session is None:
                continue
            if not is_task_driven(session):
                # Conversation (non-task) question. It renders inline on the
                # page, but we STILL project it into the notification ledger so
                # the user gets an OS notification when the window is
                # backgrounded (the frontend gates the OS popup on window focus,
                # so a user sitting on the page is never disturbed). It is NOT
                # added to the in-memory pending map / Decision Inbox — that
                # surface is task-scoped and renders task-shaped rows. The
                # answer clears it via ``resolve_pending`` in _on_action_resolved.
                await self._project_conversation_question(session, pending_id, payload)
                return
            entry = await enrich_pending(
                session,
                pending_id=pending_id,
                question_payload=payload,
                raised_at=_event_timestamp(event),
                user_id=session.user_id,
            )
            if entry is not None:
                break
        if entry is None:
            logger.warning(
                "decisions: dropping requires_action %s (session %s) — session/task rows "
                "unreadable after retry; the reconcile sweep recovers it once they land",
                pending_id,
                session_id,
            )
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
                ),
                entry.owner_user_id,
            )
        await self._record_awaiting_task_event(entry)
        await self._project_question_added(entry)

    async def _on_action_resolved(self, session_id: str, event: Event) -> None:
        pending_id = (event.data or {}).get("pending_id")
        if not isinstance(pending_id, str):
            return
        async with self._lock:
            existing = self._pending.get(pending_id)
            if existing is not None:
                owner_user_id = existing.owner_user_id  # capture before delete
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
                    ),
                    owner_user_id,
                )
        if existing is not None:
            # Task-driven question: fan out the inbox resolve + task event, then
            # clear its ledger notification via the owner we already hold.
            await self._record_answered_task_event(existing, pending_id)
            await self._project_question_resolved(existing.owner_user_id, pending_id)
            return
        # Conversation question: never tracked in ``_pending`` (nor after a
        # restart wiped the map). Clear its ledger notification by the globally
        # unique ``pending_id`` — owner-agnostic, so no lookup is needed.
        from valuz_agent.modules.notifications.service import notification_service

        await notification_service.resolve_pending(pending_id)

    async def _record_awaiting_task_event(self, entry: DecisionEntry) -> None:
        """Mirror a newly-added task-driven question onto the task's own event
        timeline (``awaiting_user``). Best-effort + deduped by pending_id —
        never let a task-event write failure break inbox fan-out."""
        if entry.pending_id in self._awaiting_task_events:
            return
        self._awaiting_task_events.add(entry.pending_id)
        question = ""
        qs = (entry.question_payload or {}).get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            question = str(qs[0].get("question") or "")
        try:
            from valuz_agent.modules.tasks import events as task_events

            await task_events.record_awaiting_user(
                task_id=entry.task_id,
                project_id=entry.project_id or "",
                session_id=entry.session_id,
                subtask_key=entry.subtask_key,
                agent_slug=entry.agent_slug,
                agent_name=None,
                question=question,
                pending_id=entry.pending_id,
                user_id=entry.owner_user_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "decisions: failed to record awaiting_user task event for %s",
                entry.pending_id,
                exc_info=True,
            )

    async def _record_answered_task_event(
        self, entry: DecisionEntry, pending_id: str
    ) -> None:
        """Counterpart to ``_record_awaiting_task_event`` — emit ``user_answered``
        only if we emitted the awaiting event this process (bounds it to a
        matched pair; a resolve for a pre-restart question is a silent no-op)."""
        if pending_id not in self._awaiting_task_events:
            return
        self._awaiting_task_events.discard(pending_id)
        try:
            from valuz_agent.modules.tasks import events as task_events

            await task_events.record_user_answered(
                task_id=entry.task_id,
                project_id=entry.project_id or "",
                pending_id=pending_id,
                session_id=entry.session_id,
                user_id=entry.owner_user_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "decisions: failed to record user_answered task event for %s",
                pending_id,
                exc_info=True,
            )

    # -- Notification projection (docs/design/notifications.md) --------
    # A question is a notification of kind ``question``. The aggregator is the
    # question PROJECTOR: it feeds the durable ledger via ``notification_service``
    # (idempotent by ``q:{pending_id}``, so calling from live + reconcile paths
    # is safe). Snapshots stay data-only (agent_slug / question text) — the
    # frontend localizes the display copy per kind, so the backend stores no UI
    # chrome. Best-effort: a ledger failure never breaks inbox fan-out.

    async def _project_conversation_question(
        self, session: Session, pending_id: str, payload: dict[str, Any]
    ) -> None:
        """Project a NON-task (conversation) question into the notification
        ledger only — no Decision Inbox fan-out (that surface is task-scoped).

        Built straight from the session's ``valuz`` metadata (no ``valuz_task``
        join): title is the agent slug (the frontend renders "{agent} 需要你确认",
        matching the task path), the deep link is the flat ``/conversation/{id}``
        route. Idempotent via ``q:{pending_id}`` + best-effort inside ``ingest``."""
        from valuz_agent.modules.notifications.service import notification_service

        owner = getattr(session, "user_id", None)
        if not owner:
            return
        meta = getattr(session, "metadata", None) or {}
        v = meta.get("valuz") if isinstance(meta, dict) else None
        v = v if isinstance(v, dict) else {}
        agent_slug = str(v.get("agent_slug") or "")
        project_id = v.get("project_id")
        project_id = project_id if isinstance(project_id, str) and project_id else None
        question = ""
        qs = payload.get("questions") if isinstance(payload, dict) else None
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            question = str(qs[0].get("question") or "")
        await notification_service.ingest(
            owner,
            dedup_key=f"q:{pending_id}",
            kind="question",
            title=agent_slug or str(v.get("name") or ""),
            body=question,
            route=f"/conversation/{session.id}",
            action="answer",
            task_id=None,
            project_id=project_id,
            session_id=session.id,
            pending_id=pending_id,
            payload={
                "question_payload": payload,
                "agent_slug": agent_slug,
            },
        )

    async def _project_question_added(self, entry: DecisionEntry) -> None:
        from valuz_agent.modules.notifications.service import notification_service

        question = ""
        qs = (entry.question_payload or {}).get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            question = str(qs[0].get("question") or "")
        route = (
            f"/tasks/{entry.task_id}"
            if entry.task_id
            else f"/conversation/{entry.session_id}"
        )
        await notification_service.ingest(
            entry.owner_user_id,
            dedup_key=f"q:{entry.pending_id}",
            kind="question",
            title=entry.agent_slug,  # frontend builds "{agent} 需要你确认"
            body=question or entry.task_title,
            route=route,
            action="answer",
            task_id=entry.task_id or None,
            project_id=entry.project_id,
            session_id=entry.session_id,
            pending_id=entry.pending_id,
            payload={
                "question_payload": entry.question_payload,
                "agent_slug": entry.agent_slug,
                "task_title": entry.task_title,
                "subtask_label": entry.subtask_label,
            },
        )

    async def _project_question_resolved(self, user_id: str, pending_id: str) -> None:
        from valuz_agent.modules.notifications.service import notification_service

        await notification_service.resolve(user_id, f"q:{pending_id}")

    async def _fan_out(self, ev: DecisionStreamEvent, owner_user_id: str) -> None:
        """Push to ``owner_user_id``'s subscribers only. Caller holds ``self._lock``."""
        for owner, q in self._subscribers:
            if owner != owner_user_id:
                continue
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                logger.warning(
                    "decisions: subscriber queue full, dropping %s",
                    ev.kind,
                )

    # ---- Helpers ----------------------------------------------------

    async def _load_session(self, owner_user_id: str | None, session_id: str) -> Session | None:
        try:
            if owner_user_id is not None:
                # MULTITENANT: per-owner durable read (the tap is scoped to this
                # owner's kernel). Works where cross-owner listing is unavailable.
                return await data_reader().get_session(owner_user_id, session_id)
            # LOCAL global tap: the event carries no owner — cross-owner lookup by
            # id (in-process durable) as before.
            sessions = await data_reader().list_all_sessions(ids=[session_id], limit=1)
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


# ---------------------------------------------------------------------------
# Process-scoped singleton (ADR-022), set at startup by ``boot/steps.py``.
#
# It lives HERE, with the class that owns it, not in ``api/deps.py``. The API
# layer is one consumer among several — ``modules/tasks`` also needs to ask
# "is this member parked on a user question?" while a lead waits — and having
# the holder in ``api/deps`` forced that module to import the API layer, an
# inverted dependency (modules → api). ``api/deps`` re-exports
# :func:`get_decision_aggregator` for its ``Depends(...)`` use.
# ---------------------------------------------------------------------------

_decision_aggregator: DecisionAggregator | None = None


def set_decision_aggregator(agg: DecisionAggregator) -> None:
    """Register the process-scoped aggregator. Called by app startup."""
    global _decision_aggregator
    _decision_aggregator = agg


def get_decision_aggregator() -> DecisionAggregator:
    """Return the singleton wired up at startup.

    Raises ``RuntimeError`` if called before startup — defensive: it means a
    consumer is live but the lifecycle hook didn't fire. Callers that must
    tolerate an unwired aggregator (early boot, tests) should catch it; see
    ``tasks/coordination._pending_asks_by_session``.
    """
    if _decision_aggregator is None:
        raise RuntimeError("decision aggregator not initialized — startup hook didn't run")
    return _decision_aggregator


__all__ = [
    "DecisionAggregator",
    "get_decision_aggregator",
    "set_decision_aggregator",
]
