"""Regression: codex turn-level failures must emit ``session_error``.

Codex reports some failures as a *completed* turn with
``TurnStatus.failed`` (e.g. missing OPENAI_API_KEY). That branch stores
the error in ``session_idle.stop_reason`` AND must emit a
``session_error`` event like the stream-error / runtime-exception paths
— without it there is no events row (nothing on replay) and no
``run.failed`` SSE frame (the live UI shows a silent idle).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import BudgetExhausted, EndTurn, Error
from src.runtimes.codex.runtime import CodexRuntime, _stop_reason_from_turn


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _runtime(sink: _RecordingSink) -> CodexRuntime:
    return CodexRuntime(
        config=AgentConfig(id="a", name="a"),
        model="gpt-5.5",
        event_sink=sink,
    )


def test_execution_error_stop_emits_session_error() -> None:
    sink = _RecordingSink()
    rt = _runtime(sink)
    stop = Error(
        category="execution_error",
        retry_status="exhausted",
        message="Missing environment variable: `OPENAI_API_KEY`.",
    )

    asyncio.run(rt._emit_session_error_for_stop(stop))

    assert [e.type for e in sink.events] == ["session_error"]
    assert sink.events[0].data == {
        "category": "execution_error",
        "message": "Missing environment variable: `OPENAI_API_KEY`.",
    }


def test_clean_interrupt_and_budget_stops_do_not_emit() -> None:
    sink = _RecordingSink()
    rt = _runtime(sink)

    async def _run() -> None:
        await rt._emit_session_error_for_stop(EndTurn())
        await rt._emit_session_error_for_stop(
            Error(category="user_interrupt", retry_status="terminal", message="cancelled")
        )
        await rt._emit_session_error_for_stop(BudgetExhausted(reason="max_turns"))
        await rt._emit_session_error_for_stop(None)

    asyncio.run(_run())
    assert sink.events == []


def test_failed_turn_status_maps_to_execution_error() -> None:
    """The mapping half: ``TurnStatus.failed`` → execution_error stop reason
    (so the emit gate above fires for completed-but-failed turns)."""
    from types import SimpleNamespace

    from openai_codex.generated.v2_all import TurnStatus

    turn_done = SimpleNamespace(
        turn=SimpleNamespace(
            status=TurnStatus.failed,
            error=SimpleNamespace(message="provider exploded"),
        )
    )
    stop = _stop_reason_from_turn(turn_done)  # type: ignore[arg-type]
    assert isinstance(stop, Error)
    assert stop.category == "execution_error"
    assert stop.message == "provider exploded"
