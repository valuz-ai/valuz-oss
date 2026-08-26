"""Codex event mapper: thread items that must surface as harness events.

Regression for silent drops: codex's app-server delivers built-in tool
activity as ``item/started`` / ``item/completed`` thread items, but the
mapper originally only handled command / fileChange / mcpToolCall items —
everything else fell through to ``[]`` and the client showed nothing.

Covered here:

- ``webSearch — {id, query, action?}``: the started snapshot is an empty
  placeholder (``query: ""``, ``action: {type: "other"}``) and is ignored.
  Codex never exposes the fetched results — the action is all there is —
  so the pair splits it: tool_use input carries just the action *type*,
  tool_result content carries the full action.
- ``imageView — {id, path}``: started+completed arrive back-to-back with
  identical data; the pair is emitted at completed (``view_image``).
- ``contextCompaction — {id}``: completed becomes the shared ``compaction``
  marker (started is skipped — compaction can still fail after it fires).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ContextCompactionThreadItem,
    ImageViewThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    ThreadItem,
    WebSearchAction,
    WebSearchThreadItem,
)
from openai_codex.models import Notification
from src.runtimes.codex.event_mapper import map_notification


def _web_search_item(query: str, action: dict | None = None) -> ThreadItem:
    return ThreadItem(
        root=WebSearchThreadItem(
            id="ws_123",
            type="webSearch",
            query=query,
            action=WebSearchAction.model_validate(action) if action else None,
        )
    )


def _started(item: ThreadItem) -> Notification:
    return Notification(
        method="item/started",
        payload=ItemStartedNotification.model_validate(
            {"item": item, "startedAtMs": 1, "threadId": "th_1", "turnId": "tu_1"}
        ),
    )


def _completed(item: ThreadItem) -> Notification:
    return Notification(
        method="item/completed",
        payload=ItemCompletedNotification.model_validate(
            {"item": item, "completedAtMs": 2, "threadId": "th_1", "turnId": "tu_1"}
        ),
    )


def _assistant_item(item_id: str, text: str) -> ThreadItem:
    return ThreadItem(
        root=AgentMessageThreadItem(id=item_id, type="agentMessage", text=text)
    )


def test_assistant_messages_remain_visible_before_and_after_tools() -> None:
    events = [
        *map_notification(_completed(_assistant_item("msg-1", "先说明当前进度。"))),
        *map_notification(
            _completed(
                _web_search_item(
                    "annual report",
                    {"type": "search", "query": "annual report"},
                )
            )
        ),
        *map_notification(_completed(_assistant_item("msg-2", "再给出最终答案。"))),
    ]

    assert [event.type for event in events] == [
        "assistant_message",
        "tool_use",
        "tool_result",
        "assistant_message",
    ]
    assert [
        event.data["text"] for event in events if event.type == "assistant_message"
    ] == ["先说明当前进度。", "再给出最终答案。"]


def test_web_search_item_started_placeholder_is_dropped() -> None:
    # Codex's started snapshot carries no real data — query is empty and
    # the action is the "other" placeholder. Emitting it would render a
    # junk `{"query": "", "action": {"type": "other"}}` input in the UI.
    assert map_notification(_started(_web_search_item("", {"type": "other"}))) == []


def test_web_search_search_action_type_in_input_full_action_in_result() -> None:
    action = {
        "type": "search",
        "query": "贵州茅台 2026 最新公告",
        "queries": ["贵州茅台 2026 最新公告", "贵州茅台 2026 半年度业绩"],
    }
    events = map_notification(_completed(_web_search_item("贵州茅台 2026 最新公告", action)))

    assert [e.type for e in events] == ["tool_use", "tool_result"]
    assert events[0].data == {
        "id": "ws_123",
        "name": "web_search",
        "input": {"action": {"type": "search"}},
    }
    assert events[1].data["id"] == "ws_123"
    assert events[1].data["is_error"] is False
    assert json.loads(events[1].data["content"]) == action


def test_web_search_open_page_action_type_in_input_url_in_result() -> None:
    events = map_notification(
        _completed(
            _web_search_item(
                "https://example.com/ir",
                {"type": "openPage", "url": "https://example.com/ir"},
            )
        )
    )

    assert events[0].data["input"] == {"action": {"type": "openPage"}}
    assert json.loads(events[1].data["content"]) == {
        "type": "openPage",
        "url": "https://example.com/ir",
    }


def test_web_search_without_action_falls_back_to_query() -> None:
    events = map_notification(_completed(_web_search_item("moutai investor relations")))

    assert events[0].data["input"] == {"query": "moutai investor relations"}
    assert json.loads(events[1].data["content"]) == {
        "id": "ws_123",
        "type": "webSearch",
        "query": "moutai investor relations",
    }


def _image_view_item(path: str) -> ThreadItem:
    return ThreadItem(root=ImageViewThreadItem(id="img_1", type="imageView", path=path))


def test_image_view_item_started_is_dropped() -> None:
    # Core emits started+completed back-to-back with identical data; only
    # the completed item is surfaced (as the tool_use/tool_result pair).
    assert map_notification(_started(_image_view_item("/tmp/chart.png"))) == []


def test_image_view_completed_emits_pair_with_path() -> None:
    events = map_notification(_completed(_image_view_item("/tmp/图表.png")))

    assert [e.type for e in events] == ["tool_use", "tool_result"]
    assert events[0].data == {
        "id": "img_1",
        "name": "view_image",
        "input": {"path": "/tmp/图表.png"},
    }
    assert events[1].data["id"] == "img_1"
    assert events[1].data["is_error"] is False
    assert json.loads(events[1].data["content"]) == {
        "id": "img_1",
        "type": "imageView",
        "path": "/tmp/图表.png",
    }


def _context_compaction_item() -> ThreadItem:
    return ThreadItem(root=ContextCompactionThreadItem(id="cc_1", type="contextCompaction"))


def test_context_compaction_started_is_dropped() -> None:
    # Started fires before the compact turn runs — the compaction can still
    # fail or be aborted, so only completed produces the marker.
    assert map_notification(_started(_context_compaction_item())) == []


def test_context_compaction_completed_emits_compaction_marker() -> None:
    events = map_notification(_completed(_context_compaction_item()))

    assert [e.type for e in events] == ["compaction"]
    # Same empty payload as the runtime's synthetic ``/compact`` marker —
    # codex exposes no compaction metadata (the item is bare ``{id}``).
    assert events[0].data == {}
