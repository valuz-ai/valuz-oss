"""MCP result unwrapping + trace construction for PTC forwarded calls.

The subprocess-facing value contract mirrors what a generated wrapper
documents: the tool's canonical JSON value — ``structuredContent`` first
(unwrapping the SDK's single-``result``-key convention), then a single text
block parsed as JSON, else the raw content. The kernel computes the trace
fingerprint (sha256 over canonical JSON) itself, so provenance reflects
bytes the kernel actually observed.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from src.core.mcp_source_metadata import (
    MCP_LEGACY_SOURCE_METADATA_KEY,
    MCP_SOURCE_METADATA_KEY,
)
from src.ptc.execution_registry import TraceEntry

_SNIPPET_CHARS = 500


def unwrap_tool_result(result: Any) -> tuple[Any, bool]:
    """Project an ``mcp.types.CallToolResult`` to ``(value, is_error)``.

    Accepts the SDK object (attribute access) so the forwarder can pass the
    session's return value straight through.
    """
    is_error = bool(getattr(result, "isError", False))

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        value: Any = structured
        if set(structured) == {"result"}:
            value = structured["result"]
        return value, is_error or _is_error_envelope(value)

    blocks = list(getattr(result, "content", None) or [])
    if len(blocks) == 1 and getattr(blocks[0], "type", None) == "text":
        text = getattr(blocks[0], "text", "") or ""
        if text.startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text, is_error
            return parsed, is_error or _is_error_envelope(parsed)
        return text, is_error
    if not blocks:
        return None, is_error

    # Multiple / non-text blocks: hand the wire shapes through as dicts.
    rendered = [_block_to_plain(block) for block in blocks]
    return rendered, is_error


def _is_error_envelope(value: Any) -> bool:
    """The ``{"error": ...}`` result convention some servers use."""
    return isinstance(value, dict) and bool(value.get("error"))


def _block_to_plain(block: Any) -> Any:
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except Exception:  # noqa: BLE001 — best-effort projection
            pass
    return str(block)


def source_metadata_of(result: Any) -> Any | None:
    """The ``dev.valuz/source-metadata`` descriptor from result ``_meta``.

    Falls back to the legacy key, mirroring ``mcp_source_metadata``'s
    compatibility rule; verification happens at registration time (P4),
    not here — the trace just carries what the server sent.
    """
    meta = getattr(result, "meta", None)
    if not isinstance(meta, dict):
        return None
    descriptor = meta.get(MCP_SOURCE_METADATA_KEY)
    if descriptor is not None:
        return descriptor
    return meta.get(MCP_LEGACY_SOURCE_METADATA_KEY)


def build_trace_entry(
    *,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    value: Any,
    is_error: bool,
    source_metadata: Any | None,
    started_at: float,
) -> TraceEntry:
    """Fingerprint one observed call. Errors keep the snippet but no hash —
    provenance only ever cites data the program actually received."""
    try:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canonical = str(value)
    encoded = canonical.encode("utf-8")
    return TraceEntry(
        server=server,
        tool=tool,
        arguments=dict(arguments),
        is_error=is_error,
        result_sha256=None if is_error else hashlib.sha256(encoded).hexdigest(),
        result_size=None if is_error else len(encoded),
        result_snippet=canonical[:_SNIPPET_CHARS],
        source_metadata=None if is_error else source_metadata,
        duration_ms=int((time.monotonic() - started_at) * 1000),
    )
