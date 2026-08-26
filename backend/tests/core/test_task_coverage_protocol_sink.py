"""_TaskCoverageProtocolSink: the coverage pass streams no meta output.

Everything flowing through this sink IS the coverage continuation, so the
"is this trailing thinking just the completeness check?" classification is
definitional: thinking never reaches the transcript, the private no-op tool
stays hidden, and only genuine user-facing supplement text passes through.
"""

from __future__ import annotations

import pytest
from src.core.events import Event
from src.core.orchestrator import _TaskCoverageProtocolSink
from src.core.task_coverage_continuation import TASK_COVERAGE_NOOP_TOOL_NAME


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.private_events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)

    async def emit_private_task_coverage_event(self, event: Event) -> None:
        self.private_events.append(event)


@pytest.mark.asyncio
async def test_thinking_is_dropped_but_supplement_text_passes() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)

    await sink.emit(Event(type="thinking_delta", data={"text": "check…"}))
    await sink.emit(Event(type="thinking", data={"text": "the answer is fine"}))
    await sink.emit(Event(type="text_delta", data={"text": "补"}))
    await sink.emit(Event(type="assistant_message", data={"text": "补充:发行价为 12 美元"}))
    await sink.finalize()

    assert [event.type for event in inner.events] == [
        "text_delta",
        "assistant_message",
    ]
    assert sink.no_gap_declared is False


@pytest.mark.asyncio
async def test_meta_refusal_text_is_dropped_and_counts_as_no_gap() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)

    await sink.emit(Event(type="text_delta", data={"text": "No response"}))
    await sink.emit(Event(type="text_delta", data={"text": " requested."}))
    await sink.emit(Event(type="assistant_message", data={"text": "No response requested."}))
    await sink.emit(Event(type="usage_update", data={"input_tokens": 1}))
    await sink.finalize()

    assert [event.type for event in inner.events] == ["usage_update"]
    assert sink.no_gap_declared is True


@pytest.mark.asyncio
async def test_request_complete_confirmation_card_meta_text_is_dropped() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)
    meta = "The automation was submitted as a confirmation card. The request is complete."

    await sink.emit(Event(type="text_delta", data={"text": meta}))
    await sink.emit(Event(type="assistant_message", data={"text": meta}))
    await sink.finalize()

    assert inner.events == []
    assert sink.no_gap_declared is True


@pytest.mark.asyncio
async def test_long_supplement_waits_for_terminal_classification() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)

    await sink.emit(Event(type="text_delta", data={"text": "补" * 201}))

    assert inner.events == []
    await sink.finalize()
    assert [event.type for event in inner.events] == ["text_delta"]


@pytest.mark.asyncio
async def test_real_tool_call_is_private_but_supplement_text_is_published() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)

    await sink.emit(Event(type="text_delta", data={"text": "需要补充数据"}))
    await sink.emit(Event(type="tool_use", data={"id": "t9", "name": "web_search"}))
    await sink.emit(Event(type="tool_result", data={"id": "t9", "content": "result"}))

    assert inner.events == []
    await sink.finalize()

    assert [event.type for event in inner.events] == ["text_delta"]
    assert [event.type for event in inner.private_events] == ["tool_use", "tool_result"]


@pytest.mark.asyncio
async def test_tool_call_does_not_make_long_complete_meta_response_visible() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)
    meta = (
        "The answer is already complete and correct: it lists the original figures "
        "for both periods, the year-over-year change, and the full calculation "
        "process. No supplement or correction is needed."
    )

    await sink.emit(Event(type="tool_use", data={"id": "calc-1", "name": "citation_calculate"}))
    await sink.emit(Event(type="tool_result", data={"id": "calc-1", "content": "-1.54"}))
    await sink.emit(Event(type="text_delta", data={"text": meta}))
    await sink.emit(Event(type="assistant_message", data={"text": meta}))
    await sink.finalize()

    assert inner.events == []
    assert [event.type for event in inner.private_events] == ["tool_use", "tool_result"]
    assert sink.no_gap_declared is True


@pytest.mark.asyncio
async def test_noop_tool_stays_private_and_declares_no_gap() -> None:
    inner = _Recorder()
    sink = _TaskCoverageProtocolSink(inner)

    await sink.emit(
        Event(
            type="tool_use",
            data={"id": "t1", "name": f"mcp__x__{TASK_COVERAGE_NOOP_TOOL_NAME}"},
        )
    )
    await sink.emit(Event(type="tool_result", data={"id": "t1", "content": "ok"}))

    assert inner.events == []
    assert sink.no_gap_declared is True
