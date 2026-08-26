"""A long generation must report progress, not just take longer.

The MCP client aborts a tool call that goes silent for its idle window (the
Claude CLI's is 300s). A raised per-server ceiling only buys time; the
heartbeat is what makes the call legitimately alive — and it must stay
throttled so a token-rate delta stream does not become a notification flood.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.genui import runner
from valuz_agent.modules.genui.runner import (
    _PROGRESS_INTERVAL_S,
    _make_progress_heartbeat,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(runner.time, "monotonic", fake)
    return fake


async def test_first_chunk_beats_immediately(clock: _Clock) -> None:
    seen: list[str] = []
    heartbeat = _make_progress_heartbeat(lambda message: _record(seen, message))

    await heartbeat(1)

    assert seen == ["generating UI (1 chunks)"]


async def test_beats_are_throttled_to_the_interval(clock: _Clock) -> None:
    seen: list[str] = []
    heartbeat = _make_progress_heartbeat(lambda message: _record(seen, message))

    await heartbeat(1)
    await heartbeat(2)  # same instant — suppressed
    clock.now += _PROGRESS_INTERVAL_S / 2
    await heartbeat(3)  # inside the window — suppressed
    clock.now += _PROGRESS_INTERVAL_S
    await heartbeat(4)  # window elapsed — beats

    assert seen == ["generating UI (1 chunks)", "generating UI (4 chunks)"]


async def test_without_a_reporter_it_is_a_noop(clock: _Clock) -> None:
    heartbeat = _make_progress_heartbeat(None)

    await heartbeat(1)  # must not raise


def test_interval_stays_well_inside_the_strictest_client_window() -> None:
    # The Claude CLI aborts after 300s of silence; a heartbeat that only just
    # fits would race it. Keep an order of magnitude of headroom.
    assert 0 < _PROGRESS_INTERVAL_S <= 30


async def _record(sink: list[str], message: str) -> None:
    sink.append(message)
