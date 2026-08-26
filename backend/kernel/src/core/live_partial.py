"""Accumulated live-only state of the in-flight turn.

Delta events (``text_delta``, ``thinking_delta``, the tool deltas,
``workflow_progress``) are deliberately never persisted — see
``DatabaseEventSink._NON_PERSISTED_TYPES``. That decision stands, and
this module does not walk it back: nothing here touches the database,
and nothing here survives the process.

What it fixes is the reconnect hole the decision leaves behind. A client
that drops mid-turn (tab hidden, session switch, network blip) resumes
from the durable cursor, which by construction can only replay *sealed*
state. Everything the model has streamed since the last canonical event
is unrecoverable, so the transcript renders an empty assistant block
until the turn ends — visibly stuck, though the run is perfectly healthy.

The fix is to keep, per session, the **accumulated state** of the
in-flight turn — not the delta sequence that produced it. A reconnecting
client gets one absolute frame per open stream and is immediately whole.
That asymmetry is the whole design:

* A replay log needs a cursor, a monotonic sequence, a generation tag (to
  tell a restarted producer's sequence from the old one), a retention
  window, an overflow policy, and an explicit gap frame for each of those
  that can fail. State needs none of them: applying it twice equals
  applying it once, and a frame from a brand-new producer is just the
  truth. Nothing has to be sized to the client's absence.
* Replaying buffered deltas cannot restore the typewriter *timing*
  anyway. Thirty seconds of buffered chunks flushed at reconnect render
  exactly like assigning the text once — so the sequence buys nothing the
  state doesn't already give.

**Invariant: this holds only what is not yet durable.** Every canonical
event clears the state it seals, so a snapshot and a durable backfill can
never describe the same bytes. Sizing follows from the invariant — at
most one unsealed segment per open stream, bounded by the model's own
output limit.

Scope, deliberately: assistant text and thinking only. Those are the
streams whose loss is the reported symptom, and their consumer already
has a proven staleness guard to extend. The tool-input/output streams
are sealed within seconds by ``tool_use`` / ``tool_result``, and their
card reconciliation already has three canonical writers — adding a
fourth ordering case there earns its own change and its own QA pass.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.events import Event

logger = logging.getLogger(__name__)

# Marks a frame as absolute state rather than an increment. Consumers
# REPLACE the open block's content instead of appending, and must not
# treat the block as finished — the turn is still streaming.
SNAPSHOT_FLAG = "live_snapshot"

# Delta streams this accumulator reconstructs. See the module docstring
# on why the tool streams are out of scope for now; adding one is a row
# here plus its seal rule below.
_TRACKED_TYPES = frozenset({"text_delta", "thinking_delta"})

# Which canonical event seals which delta stream. Reaching one means the
# bytes are durable now and must leave the accumulator, or a reconnecting
# client would render them twice.
_SEALED_BY: dict[str, str] = {
    "assistant_message": "text_delta",
    "thinking": "thinking_delta",
}

# Anything that bounds a turn drops everything: the canonical record is
# complete, so nothing is left that lives only in memory.
_TURN_BOUNDARY_TYPES = frozenset({"user_message", "session_idle", "session_error"})

# A stream past this many characters stops being tracked.
#
# This is an ABSURDITY THRESHOLD, not a quota. What accumulates here is
# one unsealed segment — the text between two canonical events — which is
# bounded by the provider's own single-message output limit. At ~64K
# output tokens that is ~64K characters of CJK, ~256K of English, so the
# cap sits at roughly 2x the worst legitimate case. Lowering it toward
# "typical" usage would silently disable recovery for exactly the long
# answers this feature exists to recover.
#
# On overflow the stream is dropped rather than truncated: a partial tail
# would render as if it were the whole message, and being visibly behind
# beats being confidently wrong — the canonical event repairs it either
# way. The drop is per-stream, never global: one runaway subagent must
# not cost the lead its recovery.
MAX_CHARS_PER_STREAM = 512 * 1024

# Ceiling on concurrently tracked streams. A lead plus its subagents sit
# far below this; the cap backstops a runtime minting unbounded flow ids.
MAX_STREAMS = 64


# Identity of one delta stream: its type plus its flow. Runtimes stamp
# ``parent_tool_use_id`` on deltas produced inside a subagent, which
# streams CONCURRENTLY with its lead and shares the lead's message_id —
# the flow tag is the only thing separating the two texts. This mirrors
# ``DeltaCoalescingSink._key_for``; a divergence here would merge two
# flows into one snapshot.
_StreamKey = tuple[str, str | None]


def _flow_of(event: Event) -> str | None:
    parent = event.data.get("parent_tool_use_id")
    return str(parent) if parent is not None else None


class _Stream:
    """One accumulating delta stream."""

    __slots__ = ("data", "text", "timestamp", "overflowed")

    def __init__(self, data: dict[str, Any], timestamp: int) -> None:
        # Everything except the incremental payload — message_id, flow
        # tag. Replayed verbatim so the snapshot routes to the same block
        # the live deltas were building.
        self.data = data
        self.text = ""
        self.timestamp = timestamp
        self.overflowed = False


class LivePartialState:
    """Per-session accumulator of unsealed live-only state.

    Not internally locked: :class:`~src.core.session_bus.SessionEventBus`
    owns one and drives it from inside its own lock, which is what makes
    "snapshot, then live tail" atomic for a joining subscriber.
    """

    def __init__(self, session_id: str | None = None) -> None:
        self._streams: dict[_StreamKey, _Stream] = {}
        self._session_id = session_id
        self._warned_stream_cap = False

    def observe(self, event: Event) -> None:
        """Fold one outbound event into the accumulated state."""
        event_type = str(event.type)

        if event_type in _TURN_BOUNDARY_TYPES:
            self._streams.clear()
            return

        sealed = _SEALED_BY.get(event_type)
        if sealed is not None:
            # Scoped by flow, not by type alone: a subagent's canonical
            # message must not discard the lead's open text.
            self._streams.pop((sealed, _flow_of(event)), None)
            return

        if event_type not in _TRACKED_TYPES:
            return

        chunk = event.data.get("text")
        if chunk is None:
            chunk = event.data.get("delta")
        if not isinstance(chunk, str) or not chunk:
            return

        key = (event_type, _flow_of(event))
        stream = self._streams.get(key)
        if stream is None:
            if len(self._streams) >= MAX_STREAMS:
                # Once per session rather than once per delta — this fires
                # on the hot streaming path.
                if not self._warned_stream_cap:
                    self._warned_stream_cap = True
                    logger.warning(
                        "live-partial: session %s reached %d concurrent streams — "
                        "further streams are untracked; mid-turn reconnect will fall "
                        "back to durable history for them",
                        self._session_id or "?",
                        MAX_STREAMS,
                    )
                return
            stream = _Stream(
                {k: v for k, v in event.data.items() if k not in ("text", "delta")},
                event.timestamp,
            )
            self._streams[key] = stream
        if stream.overflowed:
            return
        if len(stream.text) + len(chunk) > MAX_CHARS_PER_STREAM:
            # Reaching this means one unsealed segment outgrew a
            # provider's whole output budget, which a well-behaved
            # runtime cannot do — it means canonical events stopped
            # arriving. Recovery for this stream is off until the next
            # seal; say so rather than degrading in silence.
            logger.warning(
                "live-partial: %s stream (flow=%s) on session %s exceeded %d chars "
                "without a canonical event — dropping it; mid-turn reconnect will "
                "fall back to durable history for this stream",
                event_type,
                key[1] or "lead",
                self._session_id or "?",
                MAX_CHARS_PER_STREAM,
            )
            stream.overflowed = True
            stream.text = ""
            return
        stream.text += chunk
        stream.timestamp = event.timestamp

    def snapshot(self) -> list[Event]:
        """Absolute frames rebuilding every unsealed stream, oldest first.

        Frames reuse the delta type they reconstruct, so consumers need no
        new event vocabulary — only the :data:`SNAPSHOT_FLAG` rule that a
        flagged frame replaces rather than appends.
        """
        frames: list[Event] = []
        for (event_type, _), stream in self._streams.items():
            if stream.overflowed or not stream.text:
                continue
            frames.append(
                Event(
                    type=event_type,  # type: ignore[arg-type]
                    data={**stream.data, "text": stream.text, SNAPSHOT_FLAG: True},
                    timestamp=stream.timestamp,
                )
            )
        return frames

    def clear(self) -> None:
        self._streams.clear()
