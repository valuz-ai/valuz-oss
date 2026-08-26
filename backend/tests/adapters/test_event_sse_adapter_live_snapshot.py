"""The live-snapshot marker survives the kernel→legacy translation.

The kernel rebuilds an interrupted stream by re-sending it as an ordinary
delta event carrying an absolute payload plus a marker. The marker is the
only thing telling the client to REPLACE rather than append, so dropping
it in translation would make every mid-turn reconnect render the
recovered prefix twice — a silent, data-shaped corruption that no type
checker catches.
"""

from __future__ import annotations

from src.core.live_partial import SNAPSHOT_FLAG

from valuz_agent.adapters.event_sse_adapter import (
    LIVE_SNAPSHOT_FLAG,
    _translate_kernel_event,
)


def test_live_snapshot_flag_matches_kernel():
    """The wire constant is duplicated across the module boundary.

    The host may not import ``src.core``, so the two spellings are pinned
    to each other here rather than shared by import.
    """
    assert LIVE_SNAPSHOT_FLAG == SNAPSHOT_FLAG


def test_should_mark_text_delta_when_kernel_sends_a_snapshot():
    result = _translate_kernel_event(
        "text_delta",
        {"text": "recovered so far", "message_id": "msg-1", SNAPSHOT_FLAG: True},
    )

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.assistant.text_delta"
    assert payload["text"] == "recovered so far"
    assert payload["message_id"] == "msg-1"
    assert payload[LIVE_SNAPSHOT_FLAG] == "true"


def test_should_mark_thinking_delta_when_kernel_sends_a_snapshot():
    result = _translate_kernel_event(
        "thinking_delta", {"text": "reasoning so far", SNAPSHOT_FLAG: True}
    )

    assert result is not None
    _, payload = result
    assert payload[LIVE_SNAPSHOT_FLAG] == "true"


def test_should_preserve_flow_attribution_on_a_snapshot():
    """A subagent's recovered text must not land in the lead's block."""
    result = _translate_kernel_event(
        "text_delta",
        {"text": "sub output", "parent_tool_use_id": "tool-9", SNAPSHOT_FLAG: True},
    )

    assert result is not None
    _, payload = result
    assert payload["parent_tool_use_id"] == "tool-9"
    assert payload[LIVE_SNAPSHOT_FLAG] == "true"


def test_should_leave_ordinary_deltas_unmarked():
    """Absence of the marker is what keeps append the default."""
    result = _translate_kernel_event("text_delta", {"text": "chunk"})

    assert result is not None
    _, payload = result
    assert LIVE_SNAPSHOT_FLAG not in payload
