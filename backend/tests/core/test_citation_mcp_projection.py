"""Citation projection for Codex and DSH MCP results (runtime-capabilities G1).

Claude projects an MCP result's source metadata into citable Evidence in its
PostToolUse hook; Codex and DSH got nothing, so their answers had no
citations. They now get the same projection on the hook bus: the kernel MCP
proxy carries ``_meta`` alongside the content, the builtin handler compacts
it, and the private descriptors reach the orchestrator through a per-session
side channel. The model output must be byte-identical to Claude's.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from mcp.types import CallToolResult, TextContent
from src.core.agent_config import AgentConfig
from src.core.citation_mcp_projection import (
    forget_session,
    project_mcp_result,
    stash_projection,
    take_projection,
)
from src.core.events import Event
from src.core.hooks import TOOL_CALL, SessionHooks, SessionRef, ToolOutcome, hook_registry
from src.core.mcp_source_metadata import (
    MCP_SOURCE_CONTENT_TRANSPORT_PREFIX,
    MCP_SOURCE_METADATA_KEY,
    wrap_mcp_result_metadata_in_content_for_transport,
)
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.mcp_proxy.dispatch import dispatch_mcp_call

OWNER = "test.citation-projection"


@pytest.fixture(autouse=True)
def _clean() -> Any:
    yield
    hook_registry.unregister_owner(OWNER)
    for session_id in ("s-codex", "s-bare", "s-orch"):
        forget_session(session_id)


class _Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def _chunks_result(doc_id: str = "msft-q1") -> CallToolResult:
    payload = {
        "doc_id": doc_id,
        "title": "Microsoft FY2026 Q1 transcript",
        "url": f"https://reportify.cn/transcripts/{doc_id}",
        "document_version": "v1",
        "chunks": [
            {
                "id": "chunk-1",
                "content": "Demand continues to exceed available supply.",
                "metadata": {"document_page": 9},
            },
            {
                "id": "chunk-2",
                "content": "Azure grew 39% in constant currency.",
                "metadata": {"document_page": 10},
            },
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    descriptor = {
        "version": 1,
        "provider": {"id": "valuz-data", "name": "Valuz Data"},
        "operation": {"toolName": "get_chunks"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": digest},
            "capturedAt": "2026-08-21T00:00:00Z",
        },
        "resources": [
            {
                "resourceId": "document-chunks",
                "kind": "document-chunks",
                "authority": "authoritative",
                "rootPointer": "",
                "document": {
                    "scope": "resource",
                    "sourceId": "/doc_id",
                    "documentId": "/doc_id",
                    "documentVersion": "/document_version",
                    "title": "/title",
                    "url": "/url",
                },
                "itemsPointer": "/chunks",
                "mapping": {
                    "chunkId": "/id",
                    "text": "/content",
                    "page": "/metadata/document_page",
                },
            }
        ],
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        _meta={MCP_SOURCE_METADATA_KEY: descriptor},
    )


def _blocks(result: CallToolResult) -> list[dict[str, Any]]:
    return [block.model_dump(by_alias=True, exclude_none=True) for block in result.content]


async def test_the_projection_matches_claudes_post_tool_use_byte_for_byte() -> None:
    tool_name = "mcp__valuz-search__get_chunks"
    transported = _blocks(
        wrap_mcp_result_metadata_in_content_for_transport(
            _chunks_result(), server_name="valuz-search"
        )
    )

    claude = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _Sink())
    hook = claude._map_hooks()["PostToolUse"][0].hooks[0]
    output = await hook(
        {"tool_name": tool_name, "tool_input": {}, "tool_response": transported},
        "call-1",
        None,  # type: ignore[arg-type]
    )
    projection = project_mcp_result(tool_name, transported)

    assert projection is not None
    claude_output = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert json.dumps(projection.model_output) == json.dumps(claude_output)
    assert projection.private_content == claude._citation_tool_result_sidecars["call-1"]
    assert projection.model_content == claude._citation_tool_result_model_contents["call-1"]
    assert MCP_SOURCE_CONTENT_TRANSPORT_PREFIX not in json.dumps(projection.model_output)
    assert "ev_mcp_" in json.dumps(projection.model_output)


def test_a_plain_result_is_left_alone() -> None:
    assert project_mcp_result("mcp__srv__echo", [{"type": "text", "text": "hi"}]) is None


async def test_codex_mcp_results_reach_the_model_compacted(monkeypatch: pytest.MonkeyPatch) -> None:
    hooks = SessionHooks(hook_registry, SessionRef(session_id="s-codex", runtime_provider="codex"))
    upstream = _chunks_result()

    async def call(_args: dict[str, Any]) -> CallToolResult:
        return upstream

    result = await dispatch_mcp_call(
        hooks, "valuz-search", "get_chunks", {}, call, carry_source_metadata=True
    )

    text = result.content[0].text
    assert "ev_mcp_" in text and MCP_SOURCE_CONTENT_TRANSPORT_PREFIX not in text
    # The provider's own fields still go back to the client as sent.
    assert result.structuredContent == upstream.structuredContent
    assert result.meta == upstream.meta
    # What codex reports the model saw (its item serialization) finds the
    # private side again.
    private, model_content = take_projection("s-codex", result.model_dump_json())  # type: ignore[misc]
    assert json.loads(private)["_valuz_evidence"]
    assert "ev_mcp_" in json.dumps(model_content)
    assert take_projection("s-codex", result.model_dump_json()) is None  # taken once


@pytest.mark.parametrize(
    "ref",
    [
        SessionRef(session_id="s-bare", runtime_provider="deepagents"),
        SessionRef(session_id="s-bare", runtime_provider="claude_agent"),
    ],
)
async def test_the_transport_marker_never_leaks(ref: SessionRef) -> None:
    """No projection here; another handler rewrites the content."""

    async def stamp(ctx, event, next_):  # noqa: ANN001
        outcome = await next_()
        return ToolOutcome(content=[*outcome.content, {"type": "text", "text": "[seen]"}])

    hook_registry.register(TOOL_CALL, stamp, owner=OWNER, matcher={"tool.source": "mcp"})

    async def call(_args: dict[str, Any]) -> CallToolResult:
        return _chunks_result()

    result = await dispatch_mcp_call(
        SessionHooks(hook_registry, ref), "srv", "get_chunks", {}, call, carry_source_metadata=True
    )
    texts = [block.text for block in result.content]
    assert texts[-1] == "[seen]"
    assert not any(MCP_SOURCE_CONTENT_TRANSPORT_PREFIX in text for text in texts)


async def test_the_orchestrator_registers_the_evidence_from_the_side_channel() -> None:
    from src.core.orchestrator import _MessageObserverSink

    transported = _blocks(
        wrap_mcp_result_metadata_in_content_for_transport(_chunks_result(), server_name="srv")
    )
    projection = project_mcp_result("mcp__srv__get_chunks", transported)
    assert projection is not None and projection.model_output is not None
    stash_projection("s-orch", projection)
    visible = CallToolResult(
        content=[TextContent(**block) for block in projection.model_output]
    ).model_dump_json()

    observer = _MessageObserverSink(_Sink(), session_id="s-orch")
    await observer.emit(
        Event(type="tool_use", data={"id": "c1", "name": "valuz-search/get_chunks", "input": {}})
    )
    await observer.emit(Event(type="tool_result", data={"id": "c1", "content": visible}))

    assert len(observer._evidence_registry) == 2  # one per returned chunk

    # Without the side channel the same result registers nothing.
    bare = _MessageObserverSink(_Sink(), session_id="s-other")
    await bare.emit(Event(type="tool_result", data={"id": "c1", "content": visible}))
    assert len(bare._evidence_registry) == 0


def test_a_preview_of_a_large_result_still_finds_its_projection() -> None:
    """DSH reports only the head of a large result (the rest is spilled)."""
    first = project_mcp_result(
        "mcp__srv__get_chunks",
        _blocks(
            wrap_mcp_result_metadata_in_content_for_transport(_chunks_result("a"), server_name="s")
        ),
    )
    second = project_mcp_result(
        "mcp__srv__get_chunks",
        _blocks(
            wrap_mcp_result_metadata_in_content_for_transport(_chunks_result("b"), server_name="s")
        ),
    )
    assert first is not None and second is not None
    stash_projection("s-orch", first)
    stash_projection("s-orch", second)

    preview = json.dumps(second.model_output)
    preview = preview[: preview.index("chunk-2")]  # only the first chunk's handle survives
    private, _model = take_projection("s-orch", preview)  # type: ignore[misc]
    assert private == second.private_content
    private, _model = take_projection("s-orch", json.dumps(first.model_output))  # type: ignore[misc]
    assert private == first.private_content
    assert take_projection("s-orch", "no handles here") is None
