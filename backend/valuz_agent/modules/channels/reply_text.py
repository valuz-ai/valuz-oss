"""The answer a channel bubble shows, folded from a session's live events."""

from __future__ import annotations

from typing import Any

# Blank line between segments: the app renders each one as its own block, and a
# single newline would fold two paragraphs into one after markdown rendering.
# Same join the skill importer uses to flatten a session's assistant output.
SEGMENT_SEPARATOR = "\n\n"

# Absolute-state flag on a ``text_delta`` frame (kernel ``live_partial``): the
# frame carries everything streamed on that flow so far, not the next
# increment, so it REPLACES the open segment instead of extending it. A tap
# that attaches mid-turn gets one per open stream, because the deltas emitted
# before it connected are never persisted and can arrive no other way.
_LIVE_SNAPSHOT_FLAG = "live_snapshot"

_TEXT_EVENT_TYPES = ("text_delta", "assistant_message")


class AssistantReplyText:
    """Accumulates the visible answer across one turn's event stream.

    Two properties of the kernel stream that a plain accumulator gets wrong,
    and that this class exists to get right:

    * ``assistant_message`` is emitted ONCE PER ASSISTANT SEGMENT, not once
      per turn (kernel ``ResultCollector._publish_runtime_assistant``). A turn
      that calls a tool and keeps talking produces several, and the answer is
      their concatenation — the kernel's own canonical text is the same join.
      Treating each one as the whole answer leaves the closing frame holding
      only whatever the model said after its last tool call, silently
      truncating an answer the user already watched stream in.
    * ``text_delta`` frames stream the segment that is still open, and the
      matching ``assistant_message`` SEALS it. The sealed text supersedes
      those deltas; appending both would print that segment twice.

    Subagent flows (``parent_tool_use_id``) stream onto the same session tap,
    concurrently with the lead. The app renders them inside the tool card that
    spawned them rather than in the answer, so they are skipped here too.
    """

    __slots__ = ("_segments", "_open")

    def __init__(self) -> None:
        self._segments: list[str] = []
        self._open = ""

    @property
    def text(self) -> str:
        """Everything said so far: sealed segments plus the open one."""
        if self._open:
            return SEGMENT_SEPARATOR.join([*self._segments, self._open])
        return SEGMENT_SEPARATOR.join(self._segments)

    def observe(self, event_type: str, data: dict[str, Any]) -> bool:
        """Fold one session event in. True when the visible text changed.

        False for everything else, including a text event that adds nothing —
        the caller uses it to decide whether the bubble is worth re-sending.
        """
        if event_type not in _TEXT_EVENT_TYPES:
            return False
        if data.get("parent_tool_use_id"):
            return False
        text = assistant_event_text(data)
        if not text:
            return False
        before = self.text
        if event_type == "text_delta":
            self._open = text if data.get(_LIVE_SNAPSHOT_FLAG) else self._open + text
        else:
            self._segments.append(text)
            self._open = ""
        return self.text != before


def assistant_event_text(data: dict[str, Any]) -> str:
    """Text payload of an assistant/delta event (``content`` on older rows)."""
    text = data.get("text")
    if text is None:
        text = data.get("content")
    if text is None:
        text = data.get("delta")
    return str(text or "")


def session_event_type_and_data(event: Any) -> tuple[str, dict[str, Any]]:
    """Normalize a kernel event — wire dict or typed object — to (type, data)."""
    if isinstance(event, dict):
        event_type = str(event.get("type") or "")
        data = event.get("data")
    else:
        event_type = str(getattr(event, "type", "") or "")
        data = getattr(event, "data", None)
    return event_type, data if isinstance(data, dict) else {}


__all__ = [
    "SEGMENT_SEPARATOR",
    "AssistantReplyText",
    "assistant_event_text",
    "session_event_type_and_data",
]
