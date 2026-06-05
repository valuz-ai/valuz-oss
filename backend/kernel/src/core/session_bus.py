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
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from src.core.events import Event, EventSink

logger = logging.getLogger(__name__)


class SessionEventBus:
    """Single-subscriber fanout with replay-on-attach.

    Multiple concurrent attachers are not supported by design: in this
    product a session has at most one live client. A new attach replaces
    the prior subscriber (used to swap sinks across reconnects); detach
    only fires if the caller's sink is the current subscriber, so a
    stale handler can't clobber a fresh one.
    """

    def __init__(self) -> None:
        self._subscriber: EventSink | None = None
        self._lock = asyncio.Lock()

    @property
    def has_subscriber(self) -> bool:
        return self._subscriber is not None

    async def emit(self, event: Event) -> None:
        async with self._lock:
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

    async def attach(self, sink: EventSink, replay: Iterable[Event] = ()) -> None:
        """Atomically install ``sink`` and replay pending events to it.

        Replay-then-live is serialized under the bus lock — concurrent
        live emits await the lock, so the subscriber sees replay first
        and the live tail second, never interleaved.
        """
        async with self._lock:
            self._subscriber = sink
            for ev in replay:
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
