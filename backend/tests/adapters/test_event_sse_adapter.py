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


def test_should_propagate_citation_bundle_on_final_assistant_frames():
    bundle = {
        "version": 1,
        "citations": [
            {
                "citationId": "cit_1",
                "source": {
                    "sourceId": "doc:1",
                    "providerId": "docs",
                    "sourceType": "document",
                    "title": "Annual report",
                    "retrievedAt": "2026-07-30T08:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": "Revenue increased.",
                    "snippet": "Revenue increased.",
                    "capturedAt": "2026-07-30T08:00:00Z",
                },
            }
        ],
    }

    result = _translate_kernel_event(
        "assistant_message",
        {"text": "Revenue increased. [1](citation://cit_1)", "citation_bundle": bundle},
    )

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.assistant.delta"
    assert json.loads(payload["citation_bundle"]) == bundle


def test_should_translate_post_publish_assistant_sidecar_without_body_text():
    bundle = {"version": 1, "citations": []}
    coverage = {
        "status": "complete",
        "supplemented": True,
        "assistant_segment_indices": [2],
    }

    result = _translate_kernel_event(
        "assistant_message_sidecar",
        {
            "assistant_segment_index": 2,
            "citation_bundle": bundle,
            "task_coverage": coverage,
            "message_id": "msg-3",
        },
    )

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.assistant.sidecar"
    assert payload["assistant_segment_index"] == "2"
    assert json.loads(payload["citation_bundle"]) == bundle
    assert json.loads(payload["task_coverage"]) == coverage
    assert payload["message_id"] == "msg-3"
    assert "text" not in payload


def test_should_propagate_task_coverage_on_final_assistant_frames():
    coverage = {
        "version": 1,
        "status": "partial",
        "metrics": {
            "taskRequirementRequiredCount": 12,
            "answerRequirementFulfilledCount": 10,
        },
    }

    result = _translate_kernel_event(
        "assistant_message",
        {"text": "answer", "task_coverage": coverage},
    )

    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "message.assistant.delta"
    assert json.loads(payload["task_coverage"]) == coverage


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


def test_should_translate_tool_input_delta_to_build_card_signal():
    # Live, non-persisted partial tool-call input. Carries the tool_use_id
    # (``id``) the started/completed events also key on, the tool name (so
    # the card renders its title before the canonical tool_use lands), and
    # the partial-JSON chunk as ``text``.
    result = _translate_kernel_event(
        "tool_input_delta",
        {"id": "tool-1", "name": "Write", "text": '{"file_path":"/a', "message_id": "msg-w"},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "tool.call.input_delta"
    assert payload["tool_use_id"] == "tool-1"
    assert payload["name"] == "Write"
    assert payload["text"] == '{"file_path":"/a'
    assert payload["message_id"] == "msg-w"


def test_should_translate_tool_output_delta_with_stream_discriminator():
    result = _translate_kernel_event(
        "tool_output_delta",
        {"id": "tool-2", "stream": "patch", "text": "+ added line", "message_id": "msg-o"},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "tool.call.output_delta"
    assert payload["tool_use_id"] == "tool-2"
    assert payload["stream"] == "patch"
    assert payload["text"] == "+ added line"
    assert payload["message_id"] == "msg-o"


def test_should_translate_tool_thinking_delta():
    # Tool-scoped reasoning stream (ephemeral generate_ui thinking forwarded
    # onto the calling session). A separate type from tool.call.output_delta —
    # the frontend concatenates output deltas into the tool card's output (the
    # OpenUI code stream) unconditionally, so reasoning text must not ride it.
    result = _translate_kernel_event(
        "tool_thinking_delta",
        {"id": "tool-3", "text": "planning the layout", "message_id": "msg-t"},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "tool.call.thinking_delta"
    assert payload["tool_use_id"] == "tool-3"
    assert payload["text"] == "planning the layout"
    assert payload["message_id"] == "msg-t"


def test_should_translate_workflow_progress_with_nested_state():
    # Claude dynamic-workflow live progress. ``id`` is the Workflow tool_use_id
    # the frontend attaches the progress card to; ``state`` is the nested
    # snapshot, JSON-stringified to honour the legacy ``Record<str,str>`` SSE
    # contract. Before this case existed, the adapter returned ``None`` and the
    # whole feature was invisible on the wire even though the kernel emitted it.
    state = {
        "runId": "wf_abc123",
        "workflowName": "deep-research",
        "status": "running",
        "agentCount": 3,
        "agentsDone": 1,
        "workflowProgress": [
            {"type": "workflow_agent", "agentId": "agent-1", "state": "done"},
            {"type": "workflow_agent", "agentId": "agent-2", "state": "progress"},
        ],
    }
    result = _translate_kernel_event(
        "workflow_progress",
        {"id": "tool-wf", "run_id": "wf_abc123", "state": state, "message_id": "msg-wf"},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.workflow_progress"
    assert payload["id"] == "tool-wf"
    assert payload["run_id"] == "wf_abc123"
    # ``state`` round-trips through JSON so the frontend re-parses it whole.
    assert json.loads(payload["state"]) == state
    assert payload["message_id"] == "msg-wf"


def test_should_translate_workflow_progress_with_empty_state_default():
    # Defensive: a malformed snapshot (no ``state``) still produces a valid
    # frame rather than crashing the SSE stream — the frontend treats an empty
    # object as "no progress yet" and keeps the prior snapshot.
    result = _translate_kernel_event("workflow_progress", {"id": "tool-wf", "run_id": "wf_x"})
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.workflow_progress"
    assert json.loads(payload["state"]) == {}


def test_should_translate_bg_task_started_with_full_payload():
    result = _translate_kernel_event(
        "bg_task_started",
        {
            "task_id": "bh6oql7si",
            "tool_use_id": "toolu_1",
            "description": "run the full test suite",
            "task_type": "local_bash",
            "message_id": "msg-bg",
        },
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.bg_task.started"
    assert payload["task_id"] == "bh6oql7si"
    assert payload["description"] == "run the full test suite"
    assert payload["task_type"] == "local_bash"
    assert payload["message_id"] == "msg-bg"


def test_should_translate_bg_task_finished_and_stringify_nested_usage():
    usage = {"total_tokens": 120, "tool_uses": 3, "duration_ms": 4500}
    result = _translate_kernel_event(
        "bg_task_finished",
        {
            "task_id": "bh6oql7si",
            "status": "completed",
            "summary": "Background command completed (exit code 0)",
            "output_file": "/tmp/tasks/bh6oql7si.output",
            "usage": usage,
        },
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.bg_task.finished"
    assert payload["status"] == "completed"
    assert payload["output_file"] == "/tmp/tasks/bh6oql7si.output"
    # Nested dicts round-trip through JSON per the Record<string,string> contract.
    assert json.loads(payload["usage"]) == usage


def test_should_translate_bg_task_updated_patch():
    result = _translate_kernel_event(
        "bg_task_updated",
        {"task_id": "bh6oql7si", "patch": {"status": "completed", "end_time": 1783939052053}},
    )
    assert result is not None
    legacy_type, payload = result
    assert legacy_type == "session.bg_task.updated"
    assert json.loads(payload["patch"])["status"] == "completed"


def test_should_drop_none_values_from_bg_task_payload():
    # ``usage`` is None on most notifications; the legacy contract has no
    # null — the key is simply omitted.
    result = _translate_kernel_event(
        "bg_task_finished",
        {"task_id": "t1", "status": "stopped", "summary": "", "output_file": "", "usage": None},
    )
    assert result is not None
    _legacy_type, payload = result
    assert "usage" not in payload


def test_should_propagate_parent_tool_use_id_on_subagent_events():
    """Events produced inside a Task/Agent run carry ``parent_tool_use_id``;
    the wire payload must preserve it so the frontend can treat them as
    out-of-band activity (not part of the lead's sequential flow)."""
    for kernel_type, data in [
        ("assistant_message", {"text": "sub", "parent_tool_use_id": "toolu_agent"}),
        ("thinking", {"text": "hmm", "parent_tool_use_id": "toolu_agent"}),
        ("tool_use", {"id": "t1", "name": "Read", "parent_tool_use_id": "toolu_agent"}),
        ("tool_result", {"id": "t1", "content": "ok", "parent_tool_use_id": "toolu_agent"}),
        ("text_delta", {"text": "chu", "parent_tool_use_id": "toolu_agent"}),
        ("thinking_delta", {"text": "hm", "parent_tool_use_id": "toolu_agent"}),
        (
            "tool_input_delta",
            {"id": "t1", "name": "Read", "text": "{", "parent_tool_use_id": "toolu_agent"},
        ),
    ]:
        result = _translate_kernel_event(kernel_type, data)
        assert result is not None, kernel_type
        _, payload = result
        assert payload["parent_tool_use_id"] == "toolu_agent", kernel_type


def test_should_omit_parent_tool_use_id_when_event_is_top_level():
    result = _translate_kernel_event("tool_use", {"id": "t1", "name": "Read"})
    assert result is not None
    _, payload = result
    assert "parent_tool_use_id" not in payload
