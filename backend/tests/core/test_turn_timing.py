"""``PhaseTimer`` sits on the hot path of every turn, so it must cost nothing
and cannot fail.

The reason it exists is that a turn's wall-clock has repeatedly refused to add
up, and every attribution attempted from OUTSIDE the path reached a different
and wrong answer. That only pays off if the instrumentation itself is
trustworthy — hence the accumulate/label/no-timer cases below.
"""

from __future__ import annotations

import asyncio
import time

from src.core.turn_timing import PhaseTimer, current_timer, timing_turn


def test_should_report_no_timer_outside_a_turn() -> None:
    assert current_timer() is None


def test_should_publish_the_timer_to_callees_and_clear_it_after() -> None:
    """A runtime several layers down adds its own phases without the port
    signature growing a parameter."""
    with timing_turn("session=s1") as timer:
        assert current_timer() is timer
    assert current_timer() is None


def test_should_accumulate_a_phase_that_runs_twice() -> None:
    """A runtime rebuilt mid-turn should show its TOTAL cost, not the last
    occurrence — otherwise a retry hides exactly the time worth seeing."""
    t = PhaseTimer()
    for _ in range(2):
        with t.phase("spawn"):
            time.sleep(0.01)

    assert t._phases["spawn"] >= 19  # both runs, not one


def test_should_record_a_duration_measured_by_a_callee() -> None:
    t = PhaseTimer()
    t.mark("envd", 250.0)
    t.mark("envd", 100.0)

    assert t._phases["envd"] == 350.0


def test_should_still_emit_when_the_body_raises(caplog) -> None:
    """A failed turn is the one whose timing matters most — the line must
    survive the exception on its way out."""
    with caplog.at_level("INFO"):
        try:
            with timing_turn("session=s1") as t, t.phase("boom"):
                raise ValueError("x")
        except ValueError:
            pass

    assert any("turn timing" in r.getMessage() for r in caplog.records)


def test_phase_timing_survives_a_cancelled_task() -> None:
    """Turns get cancelled (user interrupt, host drain); the timer must not
    turn that into a second failure."""

    async def main() -> None:
        with timing_turn("session=s1") as t, t.phase("wait"):
            await asyncio.sleep(5)

    async def driver() -> None:
        task = asyncio.create_task(main())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
