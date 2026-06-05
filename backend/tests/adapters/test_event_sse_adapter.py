"""Translation tests for ``event_sse_adapter._translate_kernel_event``.

The adapter is the boundary between the kernel's event names (V5+messages
introduced ``todo_update`` / ``usage_update`` and dropped ``cost_update``)
and the legacy frontend wire shape. These tests pin the contract so a
future kernel rev or renderer drift surfaces as a fast-failing test
rather than a silent SSE stream that ignores half the agent's output.
"""

from __future__ import annotations

import json

from valuz_agent.adapters.event_sse_adapter import _translate_kernel_event, _with_row_message_id


def test_should_translate_todo_update_when_kernel_emits_todowrite_snapshot():
    todos = [
        {"content": "Plan migration", "status": "in_progress", "activeForm": "Planning migration"},
        {"content": "Write code", "status": "pending"},
    ]
    result = _translate_kernel_event("todo_update", {"todos": todos, "message_id": "msg-1"})

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.todos.update"
    # Payload is JSON-stringified to preserve the legacy
    # ``Record<string, string>`` SSE contract.
    assert json.loads(payload["todos"]) == todos
    assert payload["message_id"] == "msg-1"


def test_should_translate_mode_changed_when_kernel_emits_session_mode_transition():
    result = _translate_kernel_event(
        "mode_changed", {"mode": "goal", "by": "user", "message_id": "msg-m"}
    )

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.mode_changed"
    assert payload["mode"] == "goal"
    assert payload["by"] == "user"
    assert payload["message_id"] == "msg-m"


def test_should_translate_plan_update_when_codex_emits_plan_steps():
    steps = [{"step": "research", "status": "pending"}]
    result = _translate_kernel_event("plan_update", {"steps": steps, "message_id": "msg-p"})

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.plan_update"
    assert json.loads(payload["steps"]) == steps


def test_should_translate_usage_update_with_token_counts():
    result = _translate_kernel_event(
        "usage_update",
        {
            "input_tokens": 1234,
            "output_tokens": 567,
            "cache_read_tokens": 89,
            "cache_write_tokens": 0,
            "model_usage": {"claude-sonnet-4-6": {"input": 1234, "output": 567}},
            "message_id": "msg-2",
        },
    )

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "runtime.engine.usage"
    assert payload["input_tokens"] == "1234"
    assert payload["output_tokens"] == "567"
    assert payload["cache_read_tokens"] == "89"
    assert payload["cache_write_tokens"] == "0"
    assert json.loads(payload["model_usage"]) == {
        "claude-sonnet-4-6": {"input": 1234, "output": 567}
    }
    assert payload["message_id"] == "msg-2"


def test_should_drop_cost_update_event_after_v5_messages_rename():
    # ``cost_update`` was renamed to ``usage_update`` in the kernel; the
    # old name is no longer emitted. If something synthetic still sends
    # it, the adapter must filter it out (returning ``None``) instead
    # of fabricating a frame the renderer wouldn't know how to handle.
    assert _translate_kernel_event("cost_update", {"total_cost_usd": 0.42}) is None


def test_should_propagate_message_id_on_assistant_message_frames():
    # Every event the kernel emits during an active turn carries
    # ``message_id`` (stamped by the orchestrator's
    # ``_MessageIdStampSink``); preserving it on the wire is what
    # lets the frontend group events per-message later.
    result = _translate_kernel_event(
        "assistant_message",
        {"text": "hello", "message_id": "msg-3"},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.assistant.delta"
    assert payload["text"] == "hello"
    assert payload["message_id"] == "msg-3"


def test_should_translate_thinking_delta_when_kernel_streams_reasoning_chunks():
    # Reasoning content streams in incrementally (V5+streaming) so the
    # frontend can render a live "Thinking..." preview before the full
    # ``thinking`` block lands at end-of-message.
    result = _translate_kernel_event(
        "thinking_delta",
        {"text": "Let me think", "message_id": "msg-4"},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.assistant.thinking_delta"
    assert payload["text"] == "Let me think"
    assert payload["message_id"] == "msg-4"


def test_should_omit_message_id_when_kernel_event_does_not_carry_one():
    # Out-of-band events (recovery, candidate detector) may not have a
    # message_id. The adapter must not invent one or include an empty
    # string — the field should simply be absent from the payload.
    result = _translate_kernel_event("session_error", {"message": "boom"})
    assert result is not None
    _, payload = result
    assert "message_id" not in payload


def test_should_attach_db_row_message_id_before_translating_persisted_event():
    kernel_data = _with_row_message_id({"message": "hi"}, "msg-from-row")

    result = _translate_kernel_event("user_message", kernel_data)

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.user"
    assert payload["text"] == "hi"
    assert payload["message_id"] == "msg-from-row"
