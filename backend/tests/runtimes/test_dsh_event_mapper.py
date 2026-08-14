"""DeepSeek Harness event mapper — dsh session events → kernel Events."""

from __future__ import annotations

from src.runtimes.deepseek_harness.event_mapper import (
    DshEventMapper,
    extract_assistant_text,
    extract_step_usage,
    extract_turn_end_reason,
)


def _chunk(chunk: dict) -> dict:
    return {"type": "assistant/chunk", "seq": 1, "data": {"turn": 1, "step": 1, "chunk": chunk}}


class TestChunkMapping:
    def test_text_delta(self) -> None:
        events = DshEventMapper().map_session_event(
            _chunk({"type": "text-delta", "index": 0, "text": "hi"})
        )
        assert [(e.type, e.data) for e in events] == [("text_delta", {"text": "hi"})]

    def test_reasoning_delta_maps_to_thinking_delta(self) -> None:
        events = DshEventMapper().map_session_event(
            _chunk({"type": "reasoning-delta", "index": 0, "text": "hmm"})
        )
        assert [(e.type, e.data) for e in events] == [("thinking_delta", {"text": "hmm"})]

    def test_tool_call_delta_maps_to_tool_input_delta(self) -> None:
        events = DshEventMapper().map_session_event(
            _chunk(
                {
                    "type": "tool-call-delta",
                    "index": 1,
                    "id": "call_1",
                    "name": "bash",
                    "argumentsDelta": '{"cmd',
                }
            )
        )
        assert events[0].type == "tool_input_delta"
        assert events[0].data == {"id": "call_1", "name": "bash", "text": '{"cmd'}

    def test_bookkeeping_chunks_are_silent(self) -> None:
        mapper = DshEventMapper()
        for chunk in (
            {"type": "block-start", "index": 0, "blockType": "text"},
            {"type": "block-end", "index": 0, "block": {"type": "text", "text": "x"}},
            {"type": "usage", "usage": {"inputTokens": 1, "outputTokens": 1}},
            {"type": "finish", "reason": {"kind": "stop"}},
        ):
            assert mapper.map_session_event(_chunk(chunk)) == []


class TestMessageAndToolMapping:
    def test_assistant_message_text(self) -> None:
        event = {
            "type": "assistant/message",
            "data": {
                "message": {"role": "assistant", "content": [{"type": "text", "text": "5"}]},
                "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 4},
            },
        }
        mapped = DshEventMapper().map_session_event(event)
        assert [(e.type, e.data) for e in mapped] == [("assistant_message", {"text": "5"})]
        assert extract_assistant_text(event) == "5"
        assert extract_step_usage(event) == {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 4,
            "reasoning_tokens": 0,
        }

    def test_tool_call_parses_json_string_arguments(self) -> None:
        event = {
            "type": "tool/call",
            "data": {"callId": "c1", "name": "bash", "arguments": '{"command": "ls"}'},
        }
        mapped = DshEventMapper().map_session_event(event)
        assert mapped[0].type == "tool_use"
        assert mapped[0].data == {"id": "c1", "name": "bash", "input": {"command": "ls"}}

    def test_tool_result_joins_text_blocks(self) -> None:
        event = {
            "type": "tool/result",
            "data": {
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "c1",
                            "content": [{"type": "text", "text": "out"}],
                            "isError": False,
                        }
                    ],
                }
            },
        }
        mapped = DshEventMapper().map_session_event(event)
        assert mapped[0].type == "tool_result"
        assert mapped[0].data == {"id": "c1", "content": "out", "is_error": False}

    def test_todo_write_tool_pair_is_suppressed_and_event_mapped(self) -> None:
        mapper = DshEventMapper()
        call = {
            "type": "tool/call",
            "data": {"callId": "t1", "name": "todo_write", "arguments": "{}"},
        }
        assert mapper.map_session_event(call) == []
        todo_event = {
            "type": "todo/write",
            "data": {"todos": [{"content": "step", "status": "pending"}]},
        }
        mapped = mapper.map_session_event(todo_event)
        assert mapped[0].type == "todo_update"
        assert mapped[0].data == {"todos": [{"content": "step", "status": "pending"}]}
        result = {
            "type": "tool/result",
            "data": {
                "message": {
                    "content": [
                        {"type": "tool-result", "toolCallId": "t1", "content": [], "isError": False}
                    ]
                }
            },
        }
        assert mapper.map_session_event(result) == []


class TestExtractors:
    def test_turn_end_reason(self) -> None:
        assert extract_turn_end_reason(
            {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}}
        ) == {"kind": "completed"}
        assert extract_turn_end_reason({"type": "step/end", "data": {}}) is None

    def test_error_reason_carries_provider_error(self) -> None:
        reason = extract_turn_end_reason(
            {
                "type": "turn/end",
                "data": {
                    "reason": {"kind": "error", "error": {"message": "bad model", "status": 400}}
                },
            }
        )
        assert reason is not None and reason["kind"] == "error"
