"""Per-session outbound event bus.

The bus owns the live event channel for one session. The runtime always
emits to the bus regardless of whether a client is connected; the bus
forwards to whichever sink is currently attached, or silently drops if
none. A reconnecting client attaches with a list of replay events (the
events that were emitted while it was gone) and the bus delivers replay
+ live atomically under a lock so the subscriber sees a strict ordering.

Why this exists: binding a runtime emit chain to a single WebSocket
ties the agent's lifecycle to the network. A page refresh kills the
sink, the next emit explodes with "Unexpected ASGI message", the
runtime crashes mid-turn. Decoupling lets the agent keep running across
WS drops; the DB sink (a separate node in the composite) persists every
event regardless, so reconnect can be made whole from history.

History alone can only make a client whole up to the last *sealed*
event, because delta types are never persisted. The bus therefore also
folds every event into a :class:`~src.core.live_partial.LivePartialState`
— the accumulated state of whatever is still streaming — and can hand a
joining subscriber that state as absolute frames. Keeping it here rather
than in a module of its own is deliberate: the bus already serializes
replay-then-live under its lock, so a snapshot taken inside that lock
cannot race the live tail it precedes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from src.core.events import Event, EventSink
from src.core.live_partial import LivePartialState

logger = logging.getLogger(__name__)


class SessionEventBus:
    """Single-subscriber fanout with replay-on-attach, plus passive taps.

    Multiple concurrent *attachers* are not supported by design: in this
    product a session has at most one live client. A new attach replaces
    the prior subscriber (used to swap sinks across reconnects); detach
    only fires if the caller's sink is the current subscriber, so a
    stale handler can't clobber a fresh one.

    **Taps** are the multi-subscriber side channel: every emit is also
    forwarded to each registered tap, and taps are never displaced by
    ``attach``. They exist for observers — SSE streams, host-level
    aggregators — that must not compete with the client slot. A tap that
    raises is dropped (same policy as the subscriber).
    """

    def __init__(
        self, taps: Iterable[EventSink] = (), *, session_id: str | None = None
    ) -> None:
        self._subscriber: EventSink | None = None
        self._taps: list[EventSink] = list(taps)
        self._lock = asyncio.Lock()
        # ``session_id`` is carried for log context only — the bus itself
        # is session-agnostic and the orchestrator owns the routing.
        self._partial = LivePartialState(session_id)

    @property
    def has_subscriber(self) -> bool:
        return self._subscriber is not None

    async def emit(self, event: Event) -> None:
        async with self._lock:
            # Fold BEFORE fanout so the accumulated state is never behind
            # what subscribers have already seen: a tap joining between
            # these two steps would otherwise miss this event in both the
            # snapshot and the live tail.
            self._partial.observe(event)
            dead_taps: list[EventSink] = []
            for tap in self._taps:
                try:
                    await tap.emit(event)
                except Exception as exc:
                    logger.debug("Bus tap failed, dropping: %s", exc)
                    dead_taps.append(tap)
            for tap in dead_taps:
                self._taps.remove(tap)
            sub = self._subscriber
            if sub is None:
                return
            try:
                await sub.emit(event)
            except Exception as exc:
                # Subscriber blew up (e.g. WS closed mid-emit). Drop it
                # so subsequent events don't keep hitting a dead sink;
                # the next reconnect will re-attach.
                logger.debug("Bus subscriber failed, detaching: %s", exc)
                self._subscriber = None

    async def add_tap(
        self,
        sink: EventSink,
        replay: Iterable[Event] = (),
        *,
        live_partial: bool = False,
    ) -> None:
        """Register a passive tap, optionally replaying events to it first.

        Replay-then-live is serialized under the bus lock, mirroring
        :meth:`attach`. A replay failure abandons the registration.

        ``live_partial=True`` appends the accumulated state of whatever is
        still streaming (see :mod:`src.core.live_partial`) after
        ``replay``. That order matters: ``replay`` carries durable events,
        the snapshot carries the unsealed tail that continues them.
        Callers that backfill from the store themselves pass an empty
        ``replay`` and take only the snapshot — the two never overlap,
        because reaching the store means the state was sealed and dropped.
        """
        async with self._lock:
            frames = list(replay)
            if live_partial:
                frames.extend(self._partial.snapshot())
            for ev in frames:
                try:
                    await sink.emit(ev)
                except Exception as exc:
                    logger.debug("Tap replay emit failed, not registering: %s", exc)
                    return
            self._taps.append(sink)

    async def remove_tap(self, sink: EventSink) -> None:
        async with self._lock:
            try:
                self._taps.remove(sink)
            except ValueError:
                pass

    async def attach(
        self,
        sink: EventSink,
        replay: Iterable[Event] = (),
        *,
        live_partial: bool = False,
    ) -> None:
        """Atomically install ``sink`` and replay pending events to it.

        Replay-then-live is serialized under the bus lock — concurrent
        live emits await the lock, so the subscriber sees replay first
        and the live tail second, never interleaved. ``live_partial``
        behaves as in :meth:`add_tap`.
        """
        async with self._lock:
            self._subscriber = sink
            frames = list(replay)
            if live_partial:
                frames.extend(self._partial.snapshot())
            for ev in frames:
                try:
                    await sink.emit(ev)
                except Exception as exc:
                    logger.debug("Replay emit failed, detaching: %s", exc)
                    self._subscriber = None
                    return

    async def detach(self, sink: EventSink) -> None:
        """Detach only if ``sink`` is the current subscriber.

        The identity check prevents an old WS handler's late teardown
        from kicking out a fresh subscriber that already replaced it.
        """
        async with self._lock:
            if self._subscriber is sink:
                self._subscriber = None
