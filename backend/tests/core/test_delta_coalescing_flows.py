"""Per-flow coalescing in ``DeltaCoalescingSink``.

Background subagents (Task/Agent tool runs) stream text CONCURRENTLY with
the lead's own stream. The sink must buffer each flow separately — one
shared per-type buffer would concatenate chunks from different flows into a
single merged event stamped with whichever flow opened the buffer,
scrambling both the text and the ``parent_tool_use_id`` attribution the
runtimes stamp on nested deltas.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import kernel  # noqa: F401

from src.adapters.delta_coalescing_sink import DeltaCoalescingSink
from src.core.events import Event


class _CaptureSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


async def test_concurrent_flows_coalesce_into_separate_events() -> None:
    inner = _CaptureSink()
    sink = DeltaCoalescingSink(inner, flush_ms=10_000)  # manual flush only

    await sink.emit(Event(type="text_delta", data={"text": "lead-1 "}))
    await sink.emit(
        Event(type="text_delta", data={"text": "sub-1 ", "parent_tool_use_id": "agent-1"})
    )
    await sink.emit(Event(type="text_delta", data={"text": "lead-2"}))
    await sink.emit(
        Event(type="text_delta", data={"text": "sub-2", "parent_tool_use_id": "agent-1"})
    )
    await sink.flush()

    assert [e.type for e in inner.events] == ["text_delta", "text_delta"]
    lead, sub = inner.events
    assert lead.data["text"] == "lead-1 lead-2"
    assert "parent_tool_use_id" not in lead.data
    assert sub.data["text"] == "sub-1 sub-2"
    assert sub.data["parent_tool_use_id"] == "agent-1"


async def test_two_subagent_flows_stay_separate() -> None:
    inner = _CaptureSink()
    sink = DeltaCoalescingSink(inner, flush_ms=10_000)

    await sink.emit(Event(type="text_delta", data={"text": "A1", "parent_tool_use_id": "agent-1"}))
    await sink.emit(Event(type="text_delta", data={"text": "B1", "parent_tool_use_id": "agent-2"}))
    await sink.emit(Event(type="text_delta", data={"text": "A2", "parent_tool_use_id": "agent-1"}))
    await sink.flush()

    texts = {e.data.get("parent_tool_use_id"): e.data["text"] for e in inner.events}
    assert texts == {"agent-1": "A1A2", "agent-2": "B1"}


async def test_single_flow_behavior_unchanged() -> None:
    """No subagents → exactly the pre-change single-buffer behavior."""
    inner = _CaptureSink()
    sink = DeltaCoalescingSink(inner, flush_ms=10_000)

    await sink.emit(Event(type="text_delta", data={"text": "Hel"}))
    await sink.emit(Event(type="text_delta", data={"text": "lo"}))
    await sink.emit(Event(type="thinking_delta", data={"text": "hmm"}))
    await sink.flush()

    assert [(e.type, e.data["text"]) for e in inner.events] == [
        ("text_delta", "Hello"),
        ("thinking_delta", "hmm"),
    ]


async def test_non_delta_event_flushes_all_flows_in_order() -> None:
    inner = _CaptureSink()
    sink = DeltaCoalescingSink(inner, flush_ms=10_000)

    await sink.emit(Event(type="text_delta", data={"text": "lead"}))
    await sink.emit(Event(type="text_delta", data={"text": "sub", "parent_tool_use_id": "agent-1"}))
    await sink.emit(Event(type="tool_use", data={"id": "t1", "name": "Read", "input": {}}))

    assert [e.type for e in inner.events] == ["text_delta", "text_delta", "tool_use"]
    assert inner.events[0].data["text"] == "lead"
    assert inner.events[1].data["parent_tool_use_id"] == "agent-1"
