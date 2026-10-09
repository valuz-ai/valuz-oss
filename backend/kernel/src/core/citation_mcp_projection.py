"""The citation projection of one MCP result, for runtimes without their own.

Claude (PostToolUse, ``runtimes/claude_agent``) and DeepAgents (middleware,
``runtimes/deepagents``) turn an MCP result's source metadata into Evidence
the model can cite: they validate the provider's ``_meta`` descriptor, give
the model the compacted content with evidence handles, and hand the
orchestrator a private sidecar with the immutable descriptors. Codex and DSH
had nothing — their models saw the raw result and no Evidence was ever
registered, so their answers carried no citations.

:func:`project_mcp_result` is Claude's PostToolUse step for an MCP result
(the content blocks, carrying the source-metadata transport marker the MCP
proxy appends), minus Claude's own caches for its built-in grep / bash /
raw-document reads, which never go through MCP. ``tests/core/
test_citation_mcp_projection.py`` pins it byte for byte to Claude's output.

The private sidecar cannot ride the runtime's own ``tool_result`` event (the
runtime only sees what the model sees), so :func:`stash_projection` keeps it
per session, keyed by the evidence handles in the model content, and the
orchestrator takes it back when the matching result arrives
(:func:`take_projection`).
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from src.core.citation import (
    compact_citation_tool_content,
    private_citation_tool_content,
    rebase_collection_projections,
)
from src.core.citation_document_search import augment_indexed_document_evidence
from src.core.mcp_source_metadata import (
    adapt_mcp_source_result,
    unwrap_mcp_source_content_transport,
)

#: Same bound as Claude's persisted sidecar.
MAX_PRIVATE_CONTENT_BYTES = 16_000_000


@dataclass(frozen=True)
class McpCitationProjection:
    """What the model gets and what only the Evidence registry gets."""

    #: Replacement content blocks for the model; ``None`` = leave it unchanged.
    model_output: list[Any] | None
    #: The private descriptors (``None`` when there are none).
    private_content: str | None
    #: The model-visible projection the private descriptors point into.
    model_content: Any


def _captured_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def stringify_tool_result_content(content: Any) -> str:
    """Preserve structured MCP content blocks as valid JSON text."""
    if isinstance(content, str):
        return content

    def default(value: Any) -> Any:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        return str(value)

    try:
        return json.dumps(content, ensure_ascii=False, default=default)
    except (TypeError, ValueError):
        return str(content)


def _is_mcp_content_block(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    block_type = value.get("type")
    if block_type == "text":
        return isinstance(value.get("text"), str)
    if block_type in {"image", "audio"}:
        return isinstance(value.get("data"), str) and isinstance(value.get("mimeType"), str)
    if block_type == "resource":
        resource = value.get("resource")
        return (
            isinstance(resource, Mapping)
            and isinstance(resource.get("uri"), str)
            and (isinstance(resource.get("text"), str) or isinstance(resource.get("blob"), str))
        )
    if block_type == "resource_link":
        return isinstance(value.get("name"), str) and isinstance(value.get("uri"), str)
    return False


def is_mcp_content_block_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_mcp_content_block(block) for block in value)


def normalize_mcp_tool_output(value: Any) -> list[Any]:
    """A real MCP content list as is; any other JSON value as one text block."""
    if is_mcp_content_block_list(value):
        return cast(list[Any], value)
    return [{"type": "text", "text": stringify_tool_result_content(value)}]


def project_mcp_result(tool_name: str, content: Any) -> McpCitationProjection | None:
    """Claude's PostToolUse citation step for one MCP result's content blocks.

    ``None`` when the result carries nothing citation-related (the result
    goes to the model unchanged and nothing is registered privately).
    """
    effective: Any = content
    descriptor, structured_content, restored = unwrap_mcp_source_content_transport(effective)
    transport_handled = restored is not None
    if transport_handled:
        effective = restored
    adaptation = adapt_mcp_source_result(
        effective,
        tool_name=tool_name or None,
        descriptor=descriptor,
        structured_content=structured_content,
    )
    if adaptation is not None and adaptation.resource_kinds != {"operational"}:
        effective = adaptation.model_content
    simple_name = tool_name.rsplit("__", 1)[-1].lower()
    if adaptation is not None and not adaptation.citable and simple_name == "kb_search":
        augmented = augment_indexed_document_evidence(
            effective, tool_name=tool_name, captured_at=_captured_at()
        )
        if augmented is not None:
            effective = augmented
    if adaptation is None:
        augmented = augment_indexed_document_evidence(
            effective, tool_name=tool_name, captured_at=_captured_at()
        )
        if augmented is not None:
            effective = augmented
    model_projection = rebase_collection_projections(effective)
    compacted = compact_citation_tool_content(model_projection)
    model_content = compacted if compacted is not None else model_projection
    private = private_citation_tool_content(model_projection, model_content=model_content)
    if private is not None and len(private.encode()) > MAX_PRIVATE_CONTENT_BYTES:
        private = None
    if compacted is not None:
        model_output: list[Any] | None = normalize_mcp_tool_output(compacted)
    elif transport_handled:
        model_output = normalize_mcp_tool_output(effective)
    else:
        model_output = None
    if model_output is None and private is None:
        return None
    return McpCitationProjection(
        model_output=model_output, private_content=private, model_content=model_content
    )


# -- the per-session side channel -------------------------------------------

_HANDLE_RE = re.compile(r'"(?:evidenceHandle|collectionHandle)"\s*:\s*"([^"]+)"')
_MAX_PENDING_PER_SESSION = 64


@dataclass(frozen=True)
class _Pending:
    handles: frozenset[str]
    private_content: str
    model_content: Any


_pending: dict[str, OrderedDict[int, _Pending]] = {}
_lock = threading.Lock()
_seq = 0


def _handles(model_content: Any) -> frozenset[str]:
    """The evidence / collection handles the model content shows the model."""
    return frozenset(_HANDLE_RE.findall(stringify_tool_result_content(model_content)))


def stash_projection(session_id: str, projection: McpCitationProjection) -> None:
    """Keep *projection*'s private side until the orchestrator registers it."""
    global _seq
    if not session_id or projection.private_content is None:
        return
    handles = _handles(projection.model_content)
    if not handles:
        return
    with _lock:
        queue = _pending.setdefault(session_id, OrderedDict())
        _seq += 1
        queue[_seq] = _Pending(handles, projection.private_content, projection.model_content)
        while len(queue) > _MAX_PENDING_PER_SESSION:
            queue.popitem(last=False)


def take_projection(session_id: str, visible_content: Any) -> tuple[str, Any] | None:
    """The private side of the stashed projection the model saw as *visible_content*.

    Matched by evidence handles (content hashes, so they survive any
    re-serialization). A runtime may report only a preview of a large result
    — DSH spills it to a file and the model reads the rest from there — so
    the stashed projection sharing the most handles with what was reported
    wins, the oldest on a tie.
    """
    if not session_id:
        return None
    text = (
        visible_content
        if isinstance(visible_content, str)
        else stringify_tool_result_content(visible_content)
    )
    with _lock:
        queue = _pending.get(session_id)
        if not queue:
            return None
        best_key: int | None = None
        best_score = 0
        for key, pending in queue.items():
            score = sum(1 for handle in pending.handles if handle in text)
            if score > best_score:
                best_key, best_score = key, score
        if best_key is None:
            return None
        pending = queue.pop(best_key)
        if not queue:
            _pending.pop(session_id, None)
        return pending.private_content, pending.model_content


def forget_session(session_id: str) -> None:
    with _lock:
        _pending.pop(session_id, None)


__all__ = [
    "McpCitationProjection",
    "forget_session",
    "is_mcp_content_block_list",
    "normalize_mcp_tool_output",
    "project_mcp_result",
    "stash_projection",
    "stringify_tool_result_content",
    "take_projection",
]
