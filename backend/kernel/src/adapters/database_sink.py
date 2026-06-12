"""DatabaseEventSink — persists events to the database via StorePort."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.events import Event

if TYPE_CHECKING:
    from src.core.store_port import StorePort


# All ``*_delta`` events are per-token streaming details meant for live
# WebSocket UX only. Persisting them would flood the events table
# (hundreds of rows per turn) and push the later canonical rows past the
# GET /events page limit, so they'd vanish on refresh. Each delta has an
# assembled counterpart that is persisted instead:
#
#   text_delta         -> assistant_message
#   thinking_delta     -> thinking
#   tool_input_delta   -> tool_use      (final ``input`` shape only)
#   tool_output_delta  -> tool_result   (final aggregated output)
#
# ``workflow_progress`` is also live-only: it is a high-frequency snapshot of
# a dynamic workflow's ``wf_<id>.json`` re-emitted on every poll tick while
# the run executes. Persisting each tick would flood the table; the canonical
# persisted row for the run is the ``Workflow`` ``tool_result``. A mid-run
# reconnect re-syncs within one poll interval off the live bus.
_NON_PERSISTED_TYPES: frozenset[str] = frozenset(
    {
        "text_delta",
        "thinking_delta",
        "tool_input_delta",
        "tool_output_delta",
        "workflow_progress",
    }
)


class DatabaseEventSink:
    """EventSink that persists events to the database, scoped to one run."""

    def __init__(self, store: StorePort, session_id: str, message_id: str) -> None:
        self._store = store
        self._session_id = session_id
        self._message_id = message_id

    async def emit(self, event: Event) -> None:
        await self.persist(event)

    async def persist(self, event: Event) -> int | None:
        """Persist ``event`` and return its row id (``seq``), or ``None``
        for live-only types / backends that can't report the id."""
        if event.type in _NON_PERSISTED_TYPES:
            return None
        return await self._store.append_event(self._session_id, self._message_id, event)
