"""Sink wrapper that merges consecutive token-level deltas into batches.

Token-by-token ``text_delta`` / ``thinking_delta`` / ``tool_output_delta``
events fire at the model's emission rate — typically dozens of times per
second. Forwarding each one through the bus -> WS -> browser pipeline
causes per-token re-renders (visible flicker) and bloats the events table
with one row per token. Coalescing consecutive same-stream deltas into
~30ms batches collapses both costs without losing granularity (the
streaming UX still feels live).

Trade-off: on abnormal termination the last buffered batch is lost. The
canonical ``assistant_message`` / ``thinking`` / ``tool_result`` events at
end-of-turn still carry the full text, so chat history reload is
unaffected.

Coalescing key:
- ``text_delta`` / ``thinking_delta``: a single buffer per type (these
  carry no per-stream id).
- ``tool_output_delta``: one buffer per ``(id, stream)`` pair so that
  parallel tool executions and stdout/stderr stay separate.
- ``tool_input_delta``: one buffer per ``id`` (tool_use_id) — partial JSON
  chunks for the same tool call accumulate; parallel tool input streams
  stay independent.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import ClassVar

from src.core.events import Event, EventSink, OutboundEventType

logger = logging.getLogger(__name__)


# Tunable via env so ops can dial the live-streaming feel without a
# code change. 30ms is the conservative default — drop to 15-20 if
# streaming feels chunky, push to 50-60 if event-row count is still
# too dense.
DEFAULT_FLUSH_MS = 30


def _default_flush_ms() -> int:
    raw = os.getenv("DELTA_COALESCE_FLUSH_MS")
    if not raw:
        return DEFAULT_FLUSH_MS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid DELTA_COALESCE_FLUSH_MS=%r — falling back to %d",
            raw,
            DEFAULT_FLUSH_MS,
        )
        return DEFAULT_FLUSH_MS
    return max(0, value)


_BufferKey = tuple[str, str | None, str | None]


@dataclass
class _BufferEntry:
    type: OutboundEventType
    text: str = ""
    data: dict[str, object] = field(default_factory=dict)


class DeltaCoalescingSink:
    """Wraps an :class:`EventSink` and merges consecutive ``*_delta`` events.

    Each delta event maps to a buffer key — same key -> append to the same
    batch, different key -> independent batch. ``text_delta`` and
    ``thinking_delta`` use a single per-type slot; ``tool_output_delta``
    keys on ``(id, stream)`` so parallel tool outputs do not collide. Any
    non-delta event flushes every pending batch first to preserve causal
    ordering with downstream consumers.
    """

    DELTA_TYPES: ClassVar[set[OutboundEventType]] = {
        "text_delta",
        "thinking_delta",
        "tool_input_delta",
        "tool_output_delta",
    }
    # Delta types that carry a per-stream identity in their ``data``.
    KEYED_DELTA_TYPES: ClassVar[set[OutboundEventType]] = {
        "tool_input_delta",
        "tool_output_delta",
    }

    def __init__(self, inner: EventSink, flush_ms: int | None = None) -> None:
        self._inner = inner
        effective = flush_ms if flush_ms is not None else _default_flush_ms()
        self._flush_seconds = effective / 1000.0
        self._buffers: dict[_BufferKey, _BufferEntry] = {}
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def emit(self, event: Event) -> None:
        if event.type in self.DELTA_TYPES:
            await self._buffer_delta(event)
        else:
            async with self._lock:
                if self._buffers:
                    await self._flush_all_locked()
            await self._inner.emit(event)

    async def flush(self) -> None:
        """Emit any pending buffers immediately. Safe to call when empty."""
        async with self._lock:
            await self._flush_all_locked()

    def _key_for(self, event: Event) -> _BufferKey:
        if event.type in self.KEYED_DELTA_TYPES:
            id_ = event.data.get("id")
            stream = event.data.get("stream")
            return (
                event.type,
                str(id_) if id_ is not None else None,
                str(stream) if stream is not None else None,
            )
        return (event.type, None, None)

    async def _buffer_delta(self, event: Event) -> None:
        async with self._lock:
            key = self._key_for(event)
            entry = self._buffers.get(key)
            if entry is None:
                entry = _BufferEntry(
                    type=event.type,  # type: ignore[arg-type]
                    data={k: v for k, v in event.data.items() if k != "text"},
                )
                self._buffers[key] = entry
            text = event.data.get("text", "")
            if isinstance(text, str):
                entry.text += text
            self._ensure_flush_task()

    def _ensure_flush_task(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_after_delay())

    async def _flush_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._flush_seconds)
        except asyncio.CancelledError:
            return
        async with self._lock:
            await self._flush_all_locked()

    async def _flush_all_locked(self) -> None:
        if not self._buffers:
            return
        # dict preserves insertion order, so older batches emit first.
        entries = list(self._buffers.values())
        self._buffers.clear()
        for entry in entries:
            if not entry.text:
                continue
            merged = Event(
                type=entry.type,
                data={**entry.data, "text": entry.text},
            )
            try:
                await self._inner.emit(merged)
            except Exception:
                logger.debug("Coalesced delta emit failed", exc_info=True)
