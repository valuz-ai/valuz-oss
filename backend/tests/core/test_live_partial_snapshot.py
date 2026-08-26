"""Mid-turn reconnect recovers the unsealed stream, and only that.

A client that drops mid-turn resumes from the durable cursor, which can
only replay SEALED state — delta types are never persisted. The bus keeps
the accumulated state of whatever is still streaming and hands it to a
joining tap as absolute frames.

The two properties that make this safe are exercised here: the
accumulator holds ONLY what is not yet durable (so a snapshot and a
history backfill can never describe the same bytes), and a snapshot is
idempotent (so redelivery is harmless).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import logging

import pytest

import kernel  # noqa: F401

from src.core.events import Event
from src.core.live_partial import (
    MAX_CHARS_PER_STREAM,
    MAX_STREAMS,
    SNAPSHOT_FLAG,
    LivePartialState,
)
from src.core.session_bus import SessionEventBus


class _CaptureSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _delta(text: str, *, parent: str | None = None, type_: str = "text_delta") -> Event:
    data: dict[str, object] = {"text": text, "message_id": "msg-1"}
    if parent is not None:
        data["parent_tool_use_id"] = parent
    return Event(type=type_, data=data)  # type: ignore[arg-type]


def _texts(frames: list[Event]) -> list[str]:
    return [str(f.data["text"]) for f in frames]


# --- accumulation ---------------------------------------------------------


def test_snapshot_rebuilds_the_stream_as_one_absolute_frame() -> None:
    state = LivePartialState()
    for chunk in ("Hel", "lo, ", "world"):
        state.observe(_delta(chunk))

    frames = state.snapshot()

    assert _texts(frames) == ["Hello, world"]
    assert frames[0].type == "text_delta"
    # Routing metadata rides along so the frame lands on the same block
    # the live deltas were building.
    assert frames[0].data["message_id"] == "msg-1"
    assert frames[0].data[SNAPSHOT_FLAG] is True


def test_snapshot_is_idempotent() -> None:
    """Taking it twice yields the same absolute state — no cursor needed."""
    state = LivePartialState()
    state.observe(_delta("abc"))

    assert _texts(state.snapshot()) == _texts(state.snapshot()) == ["abc"]


def test_concurrent_flows_stay_separate() -> None:
    """A subagent streams alongside its lead under the SAME message_id.

    Only ``parent_tool_use_id`` separates the two texts; merging them
    would splice a background agent's output into the lead's message.
    """
    state = LivePartialState()
    state.observe(_delta("lead "))
    state.observe(_delta("sub ", parent="tool-9"))
    state.observe(_delta("text", parent="tool-9"))
    state.observe(_delta("says"))

    assert sorted(_texts(state.snapshot())) == ["lead says", "sub text"]


def test_text_and_thinking_are_separate_streams() -> None:
    state = LivePartialState()
    state.observe(_delta("answer", type_="text_delta"))
    state.observe(_delta("pondering", type_="thinking_delta"))

    by_type = {str(f.type): str(f.data["text"]) for f in state.snapshot()}
    assert by_type == {"text_delta": "answer", "thinking_delta": "pondering"}


# --- the "only what is not durable" invariant -----------------------------


def test_canonical_event_drops_the_stream_it_sealed() -> None:
    """Otherwise a reconnect renders the same bytes twice.

    Once the canonical ``assistant_message`` is persisted, the durable
    backfill carries that text. Anything still in the accumulator would
    be delivered a second time alongside it.
    """
    state = LivePartialState()
    state.observe(_delta("partial"))
    state.observe(Event(type="assistant_message", data={"text": "partial and final"}))

    assert state.snapshot() == []


def test_sealing_is_scoped_to_its_own_flow() -> None:
    """A subagent finishing must not discard the lead's open text."""
    state = LivePartialState()
    state.observe(_delta("lead is still writing"))
    state.observe(_delta("sub done", parent="tool-9"))
    state.observe(
        Event(
            type="assistant_message",
            data={"text": "sub done", "parent_tool_use_id": "tool-9"},
        )
    )

    assert _texts(state.snapshot()) == ["lead is still writing"]


def test_mid_turn_seal_starts_a_fresh_segment() -> None:
    """Runtimes that seal per segment keep streaming under one message_id.

    Segment 1 is durable after its canonical event, so the snapshot must
    carry segment 2 alone — the backfill supplies the rest.
    """
    state = LivePartialState()
    state.observe(_delta("segment one"))
    state.observe(Event(type="assistant_message", data={"text": "segment one"}))
    state.observe(_delta("segment two"))

    assert _texts(state.snapshot()) == ["segment two"]


def test_turn_boundaries_clear_everything() -> None:
    for boundary in ("session_idle", "session_error", "user_message"):
        state = LivePartialState()
        state.observe(_delta("in flight"))
        state.observe(Event(type=boundary, data={}))  # type: ignore[arg-type]
        assert state.snapshot() == [], boundary


# --- bounds ---------------------------------------------------------------


def test_an_oversized_stream_is_dropped_not_truncated() -> None:
    """A truncated tail would render as if it were the whole message.

    Being visibly behind until the canonical event lands beats being
    confidently wrong.
    """
    state = LivePartialState()
    state.observe(_delta("x" * (MAX_CHARS_PER_STREAM + 1)))

    assert state.snapshot() == []


def test_overflow_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """Hitting the cap means canonical events stopped arriving.

    A well-behaved runtime cannot produce one unsealed segment larger
    than a provider's whole output budget, so this is a bug signal and
    must not degrade in silence.
    """
    state = LivePartialState("ses-42")
    with caplog.at_level(logging.WARNING):
        state.observe(_delta("x" * (MAX_CHARS_PER_STREAM + 1)))

    assert "ses-42" in caplog.text
    assert "text_delta" in caplog.text


def test_overflow_of_one_stream_spares_the_others() -> None:
    """One runaway subagent must not cost the lead its recovery."""
    state = LivePartialState()
    state.observe(_delta("x" * (MAX_CHARS_PER_STREAM + 1), parent="tool-9"))
    state.observe(_delta("lead is fine"))

    assert _texts(state.snapshot()) == ["lead is fine"]


def test_stream_count_is_capped() -> None:
    state = LivePartialState()
    for i in range(MAX_STREAMS + 10):
        state.observe(_delta("x", parent=f"tool-{i}"))

    assert len(state.snapshot()) == MAX_STREAMS


def test_stream_cap_warns_once_not_per_delta(caplog: pytest.LogCaptureFixture) -> None:
    """The cap check sits on the hot streaming path."""
    state = LivePartialState("ses-42")
    for i in range(MAX_STREAMS):
        state.observe(_delta("x", parent=f"tool-{i}"))

    with caplog.at_level(logging.WARNING):
        for i in range(20):
            state.observe(_delta("x", parent=f"overflow-{i}"))

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_empty_chunks_never_create_a_stream() -> None:
    state = LivePartialState()
    state.observe(_delta(""))

    assert state.snapshot() == []


# --- bus integration ------------------------------------------------------


async def test_a_joining_tap_receives_the_unsealed_state() -> None:
    """The reconnect case, end to end through the bus."""
    bus = SessionEventBus()
    await bus.emit(_delta("Once upon "))
    await bus.emit(_delta("a time"))

    late = _CaptureSink()
    await bus.add_tap(late, live_partial=True)

    assert _texts(late.events) == ["Once upon a time"]
    assert late.events[0].data[SNAPSHOT_FLAG] is True


async def test_the_snapshot_precedes_the_live_tail_without_gaps() -> None:
    """Attach is atomic under the bus lock, so nothing slips between.

    The tap must see the state as of attach, then every subsequent event
    exactly once — no duplicate of what the snapshot already covered, and
    no chunk lost to the window between snapshot and registration.
    """
    bus = SessionEventBus()
    await bus.emit(_delta("before "))

    late = _CaptureSink()
    await bus.add_tap(late, live_partial=True)
    await bus.emit(_delta("after"))

    assert _texts(late.events) == ["before ", "after"]


async def test_taps_opt_out_by_default() -> None:
    """Existing subscribers are unaffected until they ask for the state."""
    bus = SessionEventBus()
    await bus.emit(_delta("streamed"))

    late = _CaptureSink()
    await bus.add_tap(late)

    assert late.events == []


async def test_durable_replay_precedes_the_snapshot() -> None:
    """Order matters: the snapshot CONTINUES the replay, never repeats it."""
    bus = SessionEventBus()
    await bus.emit(_delta("unsealed tail"))

    late = _CaptureSink()
    replayed = Event(type="assistant_message", data={"text": "sealed earlier"})
    await bus.add_tap(late, replay=[replayed], live_partial=True)

    assert [str(e.type) for e in late.events] == ["assistant_message", "text_delta"]


async def test_a_tap_joining_between_turns_gets_nothing() -> None:
    bus = SessionEventBus()
    await bus.emit(_delta("answer"))
    await bus.emit(Event(type="assistant_message", data={"text": "answer"}))
    await bus.emit(Event(type="session_idle", data={}))

    late = _CaptureSink()
    await bus.add_tap(late, live_partial=True)

    assert late.events == []
