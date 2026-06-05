"""Pydantic schemas for ``GET /v1/system/status``.

Mirrors ``components.schemas.SystemStatusResponse`` in
``api/openapi.yaml`` — keep both in lock-step on every change.
"""

from __future__ import annotations

from pydantic import BaseModel


class SystemStatusResponse(BaseModel):
    """Snapshot of the running backend process for the desktop ``服务`` panel."""

    status: str  # Literal["running", "starting", "degraded"] — kept str to match openapi enum
    pid: int
    started_at: int  # Unix epoch milliseconds (UTC)
    uptime_seconds: float
    version: str
    kernel_pin: str
    port: int
    active_session_count: int
    db_path: str
    log_path: str
    log_dir: str
    data_dir: str
    runtimes_available: list[str]
    warnings: list[str]
