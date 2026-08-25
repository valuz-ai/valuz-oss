"""Loopback forwarding endpoint for PTC subprocess tool calls.

``POST {KERNEL_API_PREFIX}/v1/ptc/exec/{token}/call`` — the ONLY surface a
PTC subprocess talks to. The generated ``mcp_client.py`` in the workspace
POSTs ``{"server", "tool", "arguments"}`` here; the kernel authenticates by
the one-shot execution token, enforces the per-execution server whitelist
and call budget, forwards to the upstream MCP server with the session's
real credentials (which never reach the subprocess), records the trace
kernel-side, and returns the unwrapped canonical JSON value.

Auth model: the execution token IS the credential — random, minted per
``execute_code`` run, revoked when the run settles. The standalone kernel's
bearer middleware exempts this path for exactly that reason (the subprocess
has no kernel bearer token); see ``app/main.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.routes import KERNEL_API_PREFIX
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from src.ptc.execution_registry import (
    get_execution,
    take_sub_call_slot,
)
from src.ptc.results import build_trace_entry, source_metadata_of, unwrap_tool_result
from src.ptc.upstream import UpstreamPool

logger = logging.getLogger(__name__)

PTC_ROUTE_SEGMENT = "/v1/ptc"

router = APIRouter(prefix=f"{KERNEL_API_PREFIX}{PTC_ROUTE_SEGMENT}", tags=["ptc"])


class PtcCallRequest(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def _error(status: int, code: str, message: str) -> JSONResponse:
    """Uniform error envelope the generated client renders to the program."""
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


@router.post("/exec/{token}/call")
async def forward_call(token: str, request: PtcCallRequest) -> Any:
    record = get_execution(token)
    if record is None:
        # Unknown OR already-settled execution — same answer on purpose.
        return _error(404, "unknown_execution", "execution token is not active")

    if request.server not in record.servers:
        return _error(
            403,
            "server_not_allowed",
            f"server {request.server!r} is not in this execution's allowlist",
        )

    if not take_sub_call_slot(record):
        return _error(
            429,
            "sub_call_budget_exhausted",
            f"execution exceeded {record.max_sub_calls} tool calls",
        )

    pool = record.upstream_pool
    if pool is None:
        pool = UpstreamPool(record.servers)
        record.upstream_pool = pool

    started_at = time.monotonic()
    try:
        result = await pool.call(request.server, request.tool, request.arguments)
    except Exception as exc:  # noqa: BLE001 — projected into the program as ToolCallError
        logger.warning(
            "ptc: upstream call failed (session=%s server=%s tool=%s): %s",
            record.session_id,
            request.server,
            request.tool,
            exc,
        )
        return _error(502, "upstream_error", f"{request.tool} on {request.server}: {exc}")

    value, is_error = unwrap_tool_result(result)
    record.trace.append(
        build_trace_entry(
            server=request.server,
            tool=request.tool,
            arguments=request.arguments,
            value=value,
            is_error=is_error,
            source_metadata=source_metadata_of(result),
            started_at=started_at,
        )
    )
    if is_error:
        return {"ok": False, "error": {"code": "tool_error", "message": _render_error(value)}}
    return {"ok": True, "value": value}


def _render_error(value: Any) -> str:
    if isinstance(value, str):
        return value[:2_000]
    try:
        import json

        return json.dumps(value, ensure_ascii=False, default=str)[:2_000]
    except (TypeError, ValueError):
        return str(value)[:2_000]
