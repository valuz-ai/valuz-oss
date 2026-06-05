"""Unit tests for the goal-mode brief length guard.

Pins the contract: any goal-mode brief over GOAL_BRIEF_MAX_CHARS (3900)
must raise BriefTooLongError BEFORE the kernel hands it to the bundled
Claude CLI — preventing the opaque mid-turn "Goal condition is limited
to 4000 characters" failure the user reported.
"""

from __future__ import annotations

import pytest

from valuz_agent.adapters.agent_resolver import (
    GOAL_BRIEF_MAX_CHARS,
    BriefTooLongError,
    assert_goal_brief_length,
)


def test_short_brief_passes() -> None:
    """A normal-length user prompt should pass cleanly."""
    assert_goal_brief_length("Build a todo app with React")  # ~28 chars
    assert_goal_brief_length("x" * 1000)
    assert_goal_brief_length("x" * GOAL_BRIEF_MAX_CHARS)  # exactly at limit OK


def test_overlong_brief_raises_brief_too_long() -> None:
    """A brief over the cap must raise — exception carries length + limit
    so callers can format friendly messages."""
    too_long = "x" * (GOAL_BRIEF_MAX_CHARS + 1)
    with pytest.raises(BriefTooLongError) as excinfo:
        assert_goal_brief_length(too_long)
    err = excinfo.value
    assert err.length == GOAL_BRIEF_MAX_CHARS + 1
    assert err.limit == GOAL_BRIEF_MAX_CHARS
    # Message must mention the actionable fix (reference file / shorten goal)
    msg = str(err)
    assert "4000" in msg or str(GOAL_BRIEF_MAX_CHARS) in msg
    assert "reference" in msg.lower() or "shorten" in msg.lower()


def test_brief_too_long_is_value_error_subclass() -> None:
    """Subclassing ValueError preserves generic `except ValueError` paths
    in callers that don't care about the specific reason."""
    assert issubclass(BriefTooLongError, ValueError)


def test_real_world_8123_char_brief_rejected() -> None:
    """The exact bug the user hit: an 8123-character goal that the bundled
    CLI rejected with 'Goal condition is limited to 4000 characters'."""
    brief = "x" * 8123
    with pytest.raises(BriefTooLongError) as excinfo:
        assert_goal_brief_length(brief)
    assert excinfo.value.length == 8123
