"""Map DeepSeek Harness session events to kernel Events.

Input is the ``event`` object of a ``session.event`` notification — a dsh
``SessionEvent`` envelope ``{type, seq, time, data, ...}``. The vocabulary and
payload shapes were verified against real runs (see
docs/references/deepseek-harness/python-sdk.md §"Verified event vocabulary").

The mapper holds per-turn state (suppressed todo tool-call ids), mirroring the
Claude runtime's TodoWrite handling: dsh's ``todo_write`` tool call is a
planning channel — its ``todo/write`` session event becomes ``todo_update``
and the generic tool_use/tool_result pair is suppressed so the UI trace does
not double-render it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.events import Event

logger = logging.getLogger(__name__)

DSH_TODO_TOOL_NAME = "todo_write"
# ``dsh-tool-ask-user``'s tool. Its raw tool_use/tool_result pair is
# suppressed like todo_write's: the runtime's user-questions park emits the
# ``AskUserQuestion`` anchor pair the interactive clarifying card renders
# from, so the generic pair would double-render the same exchange.
DSH_ASK_USER_TOOL_NAME = "ask_user_question"


class DshEventMapper:
    def __init__(self) -> None:
        self._todo_call_ids: set[str] = set()
        self._ask_user_call_ids: set[str] = set()

    def reset(self) -> None:
        self._todo_call_ids.clear()
        self._ask_user_call_ids.clear()

    def map_session_event(self, event: dict[str, Any]) -> list[Event]:
        etype = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            return []

        if etype == "assistant/chunk":
            return self._map_chunk(data)
        if etype == "assistant/message":
            text = _message_text(data)
            return [Event(type="assistant_message", data={"text": text})] if text else []
        if etype == "tool/call":
            return self._map_tool_call(data)
        if etype == "tool/result":
            return self._map_tool_result(data)
        if etype == "todo/write":
            todos = data.get("todos")
            if isinstance(todos, list):
                return [Event(type="todo_update", data={"todos": list(todos)})]
            return []
        if etype == "plan/mode":
            # dsh-plan-mode's logged state flip. Runtime-attributed even for
            # the spawn-time converge our bridge plugin performs — the
            # kernel row already holds that value, so the write-through is
            # an idempotent no-op there, while the approved-exit flip is
            # exactly the runtime-initiated transition the observer's
            # ``mode_persist`` write-through exists for.
            active = data.get("active")
            if isinstance(active, bool):
                return [
                    Event(
                        type="mode_changed",
                        data={"mode": "plan" if active else "default", "by": "runtime"},
                    )
                ]
            return []
        if etype == "compaction/end":
            return [Event(type="compaction", data={})]
        # turn/step boundaries, request/header, request/context, session/title,
        # inbox splices etc. are runtime bookkeeping — the adapter reads what it
        # needs (stop reason, usage) through the extractors below.
        return []

    def _map_chunk(self, data: dict[str, Any]) -> list[Event]:
        chunk = data.get("chunk")
        if not isinstance(chunk, dict):
            return []
        ctype = chunk.get("type")
        if ctype == "text-delta":
            text = chunk.get("text")
            return [Event(type="text_delta", data={"text": text})] if text else []
        if ctype == "reasoning-delta":
            text = chunk.get("text")
            return [Event(type="thinking_delta", data={"text": text})] if text else []
        if ctype == "tool-call-delta":
            delta = chunk.get("argumentsDelta")
            call_id = chunk.get("id")
            if not delta or not isinstance(call_id, str) or call_id in self._todo_call_ids:
                return []
            return [
                Event(
                    type="tool_input_delta",
                    data={"id": call_id, "name": chunk.get("name"), "text": delta},
                )
            ]
        # block-start / block-end / usage / finish carry no renderable payload
        # of their own (committed text arrives via assistant/message; usage is
        # extracted by the adapter at end of turn).
        return []

    def _map_tool_call(self, data: dict[str, Any]) -> list[Event]:
        call_id = data.get("callId")
        name = data.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str):
            return []
        if name == DSH_TODO_TOOL_NAME:
            self._todo_call_ids.add(call_id)
            return []
        if name == DSH_ASK_USER_TOOL_NAME:
            self._ask_user_call_ids.add(call_id)
            return []
        arguments = data.get("arguments")
        parsed_input: Any = {}
        if isinstance(arguments, str) and arguments:
            try:
                parsed_input = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_input = {"raw": arguments}
        elif isinstance(arguments, dict):
            parsed_input = arguments
        return [Event(type="tool_use", data={"id": call_id, "name": name, "input": parsed_input})]

    def _map_tool_result(self, data: dict[str, Any]) -> list[Event]:
        message = data.get("message")
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        events: list[Event] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool-result":
                continue
            call_id = block.get("toolCallId")
            if not isinstance(call_id, str):
                continue
            if call_id in self._todo_call_ids or call_id in self._ask_user_call_ids:
                continue
            events.append(
                Event(
                    type="tool_result",
                    data={
                        "id": call_id,
                        "content": _tool_result_text(block),
                        "is_error": bool(block.get("isError", False)),
                    },
                )
            )
        return events


def _message_text(data: dict[str, Any]) -> str:
    """Join the text blocks of an ``assistant/message`` event's committed message."""
    message = data.get("message")
    owner = message if isinstance(message, dict) else data
    content = owner.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts)


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif not isinstance(item, dict):
                parts.append(str(item))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(part for part in parts if part)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def extract_turn_end_reason(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``turn/end``'s ``data.reason`` (``{kind, error?}``), else None."""
    if event.get("type") != "turn/end":
        return None
    data = event.get("data")
    reason = data.get("reason") if isinstance(data, dict) else None
    return reason if isinstance(reason, dict) else None


def extract_step_usage(event: dict[str, Any]) -> dict[str, int] | None:
    """Per-step usage from an ``assistant/message`` event's ``data.usage``.

    dsh reports ``{inputTokens, outputTokens, cacheReadTokens,
    reasoningTokens}`` per committed step message. The counts are DISJOINT
    (llm-deepseek ``mapUsage``): ``inputTokens`` is the uncached prompt
    portion only, ``cacheReadTokens`` the cached portion, and
    ``outputTokens`` the full ``completion_tokens`` with reasoning as a
    detail sub-bucket.
    """
    if event.get("type") != "assistant/message":
        return None
    data = event.get("data")
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": int(usage.get("inputTokens") or 0),
        "output_tokens": int(usage.get("outputTokens") or 0),
        "cache_read_tokens": int(usage.get("cacheReadTokens") or 0),
        "reasoning_tokens": int(usage.get("reasoningTokens") or 0),
    }


def extract_assistant_text(event: dict[str, Any]) -> str | None:
    """Committed assistant text for the transcript sidecar; None for other events."""
    if event.get("type") != "assistant/message":
        return None
    data = event.get("data")
    return _message_text(data) if isinstance(data, dict) else None
