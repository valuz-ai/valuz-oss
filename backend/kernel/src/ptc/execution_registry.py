"""One-shot execution tokens for PTC subprocess runs.

Every ``execute_code`` run mints a random token, registers the servers the
program may reach, and hands the subprocess ONLY that token (never upstream
URLs or credentials). The loopback router authenticates each call by token,
enforces the per-execution server whitelist and call budget, and appends a
trace entry per upstream call — observed by the kernel, so the trace cannot
be forged by agent-authored code (unlike a file the subprocess writes).

Records live in process memory: an execution never outlives the kernel
process that spawned its subprocess, and the executor revokes the token in
a ``finally`` even on cancellation.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

from src.core.types import McpServerConfig

# Backstop against runaway loops inside one program, not a rate limit.
DEFAULT_MAX_SUB_CALLS = 200


@dataclass
class TraceEntry:
    """One upstream tool call, as observed by the kernel-side forwarder."""

    server: str
    tool: str
    arguments: dict[str, Any]
    is_error: bool
    result_sha256: str | None
    result_size: int | None
    result_snippet: str
    source_metadata: Any | None
    duration_ms: int


@dataclass
class ExecutionRecord:
    """State of one in-flight ``execute_code`` run."""

    token: str
    session_id: str
    user_id: str
    cwd: str
    servers: dict[str, McpServerConfig]
    max_sub_calls: int = DEFAULT_MAX_SUB_CALLS
    sub_calls: int = 0
    trace: list[TraceEntry] = field(default_factory=list)
    # Per-execution upstream client pool; typed loosely to keep this module
    # import-light (the pool imports the ``mcp`` client stack).
    upstream_pool: Any | None = None


_REGISTRY: dict[str, ExecutionRecord] = {}
_LOCK = threading.Lock()


def register_execution(
    *,
    session_id: str,
    user_id: str,
    cwd: str,
    servers: dict[str, McpServerConfig],
    max_sub_calls: int = DEFAULT_MAX_SUB_CALLS,
) -> ExecutionRecord:
    """Mint a token and register a new execution record."""
    record = ExecutionRecord(
        token=secrets.token_urlsafe(32),
        session_id=session_id,
        user_id=user_id,
        cwd=cwd,
        servers=dict(servers),
        max_sub_calls=max_sub_calls,
    )
    with _LOCK:
        _REGISTRY[record.token] = record
    return record


def get_execution(token: str) -> ExecutionRecord | None:
    with _LOCK:
        return _REGISTRY.get(token)


def take_sub_call_slot(record: ExecutionRecord) -> bool:
    """Reserve one sub-call against the execution budget (thread-safe)."""
    with _LOCK:
        if record.sub_calls >= record.max_sub_calls:
            return False
        record.sub_calls += 1
        return True


def revoke_execution(token: str) -> ExecutionRecord | None:
    """Drop the record; later calls with the token get 404. Idempotent."""
    with _LOCK:
        return _REGISTRY.pop(token, None)


def reset_registry_for_tests() -> None:
    """Drop every record — pytest cleanup hook only."""
    with _LOCK:
        _REGISTRY.clear()
