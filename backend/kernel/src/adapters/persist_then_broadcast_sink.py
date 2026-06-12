"""PersistThenBroadcastSink — sequential persist → stamp ``seq`` → broadcast.

Why this exists (vs the concurrent ``CompositeEventSink``): persisted
events used to reach the live bus and the database in parallel, so live
frames carried no storage coordinates. Any consumer merging a seq-cursor
backfill with the live stream (the kernel's ``events/stream`` endpoint,
the host SSE adapter) hit an unavoidable duplicate window: the same event
could arrive once from the DB (with ``seq``) and once live (without),
and without a stable id the boundary cannot be deduplicated.

This sink serializes the two: persist first (obtaining the autoincrement
row id), stamp it into the broadcast copy's ``data["seq"]``, then emit to
the live sink. Live-only types (``*_delta``, ``workflow_progress``) skip
persistence and flow straight through with no ``seq`` — they never appear
in backfills, so they can't duplicate.

Failure isolation matches the old composite: a DB failure is logged and
the event still broadcasts (without ``seq``); a broadcast failure is
logged and never blocks persistence.
"""

from __future__ import annotations

import logging

from src.adapters.database_sink import DatabaseEventSink
from src.core.events import Event, EventSink

logger = logging.getLogger(__name__)


class PersistThenBroadcastSink(EventSink):
    """EventSink: DB write first, ``seq``-stamped live broadcast second."""

    def __init__(self, db_sink: DatabaseEventSink, live_sink: EventSink) -> None:
        self._db = db_sink
        self._live = live_sink

    async def emit(self, event: Event) -> None:
        seq: int | None = None
        try:
            seq = await self._db.persist(event)
        except Exception:  # noqa: BLE001 — live delivery must survive DB hiccups
            logger.exception("event persistence failed for %s", event.type)

        stamped = event
        if seq is not None:
            stamped = Event(
                type=event.type,
                data={**event.data, "seq": seq},
                timestamp=event.timestamp,
            )
        elif "seq" in event.data:
            # ``seq`` is a RESERVED key on the live wire: consumers advance
            # their dedup cursor on it. A non-persisted event whose payload
            # happens to carry an (agent-controllable) ``seq`` must not be
            # allowed to fast-forward the cursor past legitimate events.
            stamped = Event(
                type=event.type,
                data={k: v for k, v in event.data.items() if k != "seq"},
                timestamp=event.timestamp,
            )
        try:
            await self._live.emit(stamped)
        except Exception:  # noqa: BLE001 — a dead live sink must not block persistence
            logger.debug("live broadcast failed for %s", event.type, exc_info=True)
