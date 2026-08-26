"""Subagent event attribution in the Claude runtime.

The SDK stamps every message produced INSIDE a Task/Agent tool run with
``parent_tool_use_id``. Background agents execute CONCURRENTLY with the
lead's own streaming, so their events arrive interleaved with the lead's
``text_delta`` frames. The rule pinned here is pure ATTRIBUTION — nothing
is suppressed or reordered, so existing behavior is unchanged:

1. Every event emitted from a nested message (deltas AND canonicals)
   carries ``parent_tool_use_id`` in its data, so consumers can route each
   flow separately instead of splicing a subagent's stream into the lead's.
2. Top-level events carry NO ``parent_tool_use_id`` key — an event stream
   from this runtime renders byte-for-byte the same as before for
   non-subagent turns.
3. ``_tool_block_by_index`` is keyed by ``(parent, index)`` — content-block
   indices restart per stream, so concurrent streams no longer collide.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json
from types import SimpleNamespace

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_bg_tasks.py.
import kernel  # noqa: F401

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from claude_agent_sdk import UserMessage as SdkUserMessage
from claude_agent_sdk.types import StreamEvent

from src.runtimes.claude_agent.runtime import (
    ClaudeAgentRuntime,
    _load_persisted_tool_result_content,
)


def _make_runtime() -> ClaudeAgentRuntime:
    rt = object.__new__(ClaudeAgentRuntime)
    emitted: list = []

    async def _emit(event) -> None:
        emitted.append(event)

    rt.event_sink = SimpleNamespace(emit=_emit)
    rt._tool_block_by_index = {}
    rt._todo_tool_use_ids = set()
    rt._workflow_tool_use_ids = set()
    rt._live_bg_tasks = {}
    rt._citation_tool_result_sidecars = {}
    rt._emitted = emitted  # test handle
    return rt


def _session() -> SimpleNamespace:
    return SimpleNamespace(status="running", stop_reason=None, runtime_session_id=None)


def _stream_text_delta(parent: str | None) -> StreamEvent:
    return StreamEvent(
        uuid="uuid-1",
        session_id="s",
        event={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "chunk"},
        },
        parent_tool_use_id=parent,
    )


def _stream_tool_start(parent: str | None, index: int, tool_id: str, name: str) -> StreamEvent:
    return StreamEvent(
        uuid="uuid-s",
        session_id="s",
        event={
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": tool_id, "name": name},
        },
        parent_tool_use_id=parent,
    )


def _stream_input_delta(parent: str | None, index: int, partial: str) -> StreamEvent:
    return StreamEvent(
        uuid="uuid-d",
        session_id="s",
        event={
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        },
        parent_tool_use_id=parent,
    )


async def test_nested_stream_delta_still_emits_and_carries_parent_tool_use_id() -> None:
    rt = _make_runtime()
    await rt._handle_message(_session(), _stream_text_delta(parent="toolu_agent"))
    assert [e.type for e in rt._emitted] == ["text_delta"]
    assert rt._emitted[0].data.get("parent_tool_use_id") == "toolu_agent"
    assert rt._emitted[0].data.get("text") == "chunk"


async def test_top_level_stream_delta_carries_no_parent_key() -> None:
    rt = _make_runtime()
    await rt._handle_message(_session(), _stream_text_delta(parent=None))
    assert [e.type for e in rt._emitted] == ["text_delta"]
    assert "parent_tool_use_id" not in rt._emitted[0].data


async def test_concurrent_streams_do_not_collide_on_content_block_index() -> None:
    """The lead and a subagent both use content-block index 0 — the
    (parent, index) key must route each input_json_delta to its own tool."""
    rt = _make_runtime()
    await rt._handle_message(_session(), _stream_tool_start(None, 0, "toolu_lead", "Write"))
    await rt._handle_message(_session(), _stream_tool_start("toolu_agent", 0, "toolu_sub", "Bash"))
    await rt._handle_message(_session(), _stream_input_delta(None, 0, '{"lead'))
    await rt._handle_message(_session(), _stream_input_delta("toolu_agent", 0, '{"sub'))

    assert [e.type for e in rt._emitted] == ["tool_input_delta", "tool_input_delta"]
    lead, sub = rt._emitted
    assert lead.data["id"] == "toolu_lead"
    assert lead.data["text"] == '{"lead'
    assert "parent_tool_use_id" not in lead.data
    assert sub.data["id"] == "toolu_sub"
    assert sub.data["text"] == '{"sub'
    assert sub.data["parent_tool_use_id"] == "toolu_agent"


async def test_nested_assistant_message_events_carry_parent_tool_use_id() -> None:
    rt = _make_runtime()
    message = AssistantMessage(
        content=[
            TextBlock(text="subagent narration"),
            ToolUseBlock(id="t1", name="mcp__valuz-search__news_search", input={"q": "x"}),
            ThinkingBlock(thinking="hmm", signature="sig"),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id="toolu_agent",
    )
    await rt._handle_message(_session(), message)
    assert [e.type for e in rt._emitted] == ["assistant_message", "tool_use", "thinking"]
    assert all(e.data.get("parent_tool_use_id") == "toolu_agent" for e in rt._emitted)


async def test_top_level_assistant_message_events_carry_no_parent_key() -> None:
    rt = _make_runtime()
    message = AssistantMessage(
        content=[
            TextBlock(text="lead narration"),
            ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
        ],
        model="claude-sonnet-4-6",
    )
    await rt._handle_message(_session(), message)
    assert [e.type for e in rt._emitted] == ["assistant_message", "tool_use"]
    assert all("parent_tool_use_id" not in e.data for e in rt._emitted)


async def test_nested_todo_write_behavior_is_unchanged() -> None:
    """A subagent's TodoWrite keeps feeding todo_update exactly as before —
    attribution must not alter existing behavior (scoping the Todos panel
    per-flow is a separate product decision)."""
    rt = _make_runtime()
    message = AssistantMessage(
        content=[
            ToolUseBlock(
                id="todo-1",
                name="TodoWrite",
                input={"todos": [{"content": "sub plan", "status": "pending"}]},
            ),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id="toolu_agent",
    )
    await rt._handle_message(_session(), message)
    assert [e.type for e in rt._emitted] == ["todo_update"]
    # The matching ToolResultBlock stays suppressed, as before.
    await rt._handle_message(
        _session(),
        SdkUserMessage(
            content=[ToolResultBlock(tool_use_id="todo-1", content="ok")],
            parent_tool_use_id="toolu_agent",
        ),
    )
    assert [e.type for e in rt._emitted] == ["todo_update"]


async def test_nested_tool_result_carries_parent_tool_use_id() -> None:
    rt = _make_runtime()
    await rt._handle_message(
        _session(),
        SdkUserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="401", is_error=True)],
            parent_tool_use_id="toolu_agent",
        ),
    )
    assert [e.type for e in rt._emitted] == ["tool_result"]
    assert rt._emitted[0].data.get("parent_tool_use_id") == "toolu_agent"
    assert rt._emitted[0].data.get("is_error") is True


async def test_structured_tool_result_content_remains_valid_json() -> None:
    rt = _make_runtime()
    content = [
        {
            "type": "text",
            "text": '{"_valuz_evidence":{"evidenceHandle":"ev_example_12345678"}}',
        }
    ]
    await rt._handle_message(
        _session(),
        SdkUserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content=content)],
        ),
    )

    emitted = json.loads(rt._emitted[0].data["content"])
    assert emitted == content


def test_persisted_tool_result_is_loaded_only_from_matching_claude_tool_path(tmp_path) -> None:
    projects_root = tmp_path / "projects"
    result_path = projects_root / "project-a" / "session-a" / "tool-results" / "tool-1.txt"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        '{"_valuz_evidence":{"evidenceHandle":"ev_example_12345678"}}',
        encoding="utf-8",
    )
    notice = f"<persisted-output>\nOutput too large (250KB). Full output saved to: {result_path}\n"

    assert _load_persisted_tool_result_content(
        notice,
        tool_use_id="tool-1",
        projects_root=projects_root,
    ) == result_path.read_text(encoding="utf-8")
    assert (
        _load_persisted_tool_result_content(
            notice,
            tool_use_id="another-tool",
            projects_root=projects_root,
        )
        is None
    )


def test_persisted_citation_result_accepts_bounded_multi_megabyte_payload(tmp_path) -> None:
    projects_root = tmp_path / "projects"
    result_path = projects_root / "project-a" / "session-a" / "tool-results" / "tool-1.txt"
    result_path.parent.mkdir(parents=True)
    payload = '{"padding":"' + ("x" * 2_100_000) + '"}'
    result_path.write_text(payload, encoding="utf-8")
    notice = f"<persisted-output>\nOutput too large (2MB). Full output saved to: {result_path}\n"

    assert (
        _load_persisted_tool_result_content(
            notice,
            tool_use_id="tool-1",
            projects_root=projects_root,
        )
        == payload
    )


def test_persisted_tool_result_rejects_symlink(tmp_path) -> None:
    projects_root = tmp_path / "projects"
    target = tmp_path / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    result_path = projects_root / "project-a" / "session-a" / "tool-results" / "tool-1.txt"
    result_path.parent.mkdir(parents=True)
    result_path.symlink_to(target)
    notice = f"<persisted-output>\nOutput too large (250KB). Full output saved to: {result_path}\n"

    assert (
        _load_persisted_tool_result_content(
            notice,
            tool_use_id="tool-1",
            projects_root=projects_root,
        )
        is None
    )
