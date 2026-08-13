from __future__ import annotations

from types import SimpleNamespace

from valuz_agent.modules.channels.reply_text import (
    AssistantReplyText,
    session_event_type_and_data,
)


def test_sealing_a_segment_does_not_double_its_streamed_deltas() -> None:
    answer = AssistantReplyText()

    assert answer.observe("text_delta", {"text": "Hel"}) is True
    assert answer.observe("text_delta", {"text": "lo"}) is True
    # The canonical event supersedes the deltas it seals.
    assert answer.observe("assistant_message", {"text": "Hello"}) is False
    assert answer.text == "Hello"


def test_every_segment_of_a_multi_step_turn_survives() -> None:
    """One ``assistant_message`` per segment — the answer is their join, not
    the last one."""
    answer = AssistantReplyText()

    answer.observe("assistant_message", {"text": "先看余额。"})
    answer.observe("assistant_message", {"text": "补一点：扣点是单条记录扣减。"})

    assert answer.text == "先看余额。\n\n补一点：扣点是单条记录扣减。"


def test_a_sealed_segment_is_not_overwritten_by_the_next_one() -> None:
    answer = AssistantReplyText()

    answer.observe("text_delta", {"text": "先看余额。"})
    answer.observe("assistant_message", {"text": "先看余额。"})
    assert answer.observe("text_delta", {"text": "补一点："}) is True

    assert answer.text == "先看余额。\n\n补一点："


def test_subagent_flows_stay_out_of_the_answer() -> None:
    """Subagent text streams onto the same tap; the app renders it inside the
    tool card that spawned it."""
    answer = AssistantReplyText()

    answer.observe("assistant_message", {"text": "查询中。"})
    assert answer.observe("text_delta", {"text": "扫表", "parent_tool_use_id": "toolu-1"}) is False
    assert (
        answer.observe("assistant_message", {"text": "扫完了", "parent_tool_use_id": "toolu-1"})
        is False
    )

    assert answer.text == "查询中。"


def test_a_live_snapshot_replaces_the_open_segment_instead_of_extending_it() -> None:
    """``live_partial`` frames carry absolute state, not the next increment."""
    answer = AssistantReplyText()

    answer.observe("text_delta", {"text": "Hel"})
    answer.observe("text_delta", {"text": "Hello there", "live_snapshot": True})

    assert answer.text == "Hello there"


def test_non_text_and_empty_events_leave_the_answer_alone() -> None:
    answer = AssistantReplyText()

    answer.observe("assistant_message", {"text": "Hello"})
    assert answer.observe("tool_use", {"id": "toolu-1", "name": "bash"}) is False
    assert answer.observe("thinking", {"text": "hmm"}) is False
    assert answer.observe("text_delta", {"text": ""}) is False

    assert answer.text == "Hello"


def test_older_rows_spell_the_payload_content() -> None:
    answer = AssistantReplyText()

    answer.observe("assistant_message", {"content": "Hello"})

    assert answer.text == "Hello"


def test_session_event_type_and_data_reads_objects_and_dicts() -> None:
    assert session_event_type_and_data(SimpleNamespace(type="text_delta", data={"text": "a"})) == (
        "text_delta",
        {"text": "a"},
    )
    assert session_event_type_and_data({"type": "text_delta", "data": {"text": "a"}}) == (
        "text_delta",
        {"text": "a"},
    )
    # A payload-less frame must not blow up the stream loop.
    assert session_event_type_and_data(SimpleNamespace(type="session_idle", data=None)) == (
        "session_idle",
        {},
    )
