"""HTTP layer for ``GET /v1/system/status``.

The desktop ``服务`` page (status card + log viewer) hits this once on
mount and again every few seconds. Cheap on every call — the heavy
lifting (kernel pin parse, version read) is memoised inside
``service.collect_system_status``.
"""

from __future__ import annotations

from fastapi import APIRouter

from valuz_agent.modules.system.schemas import SystemStatusResponse
from valuz_agent.modules.system.service import (
    collect_system_status,
    listen_port,
)

router = APIRouter(prefix="/v1/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
def get_system_status() -> SystemStatusResponse:
    """Snapshot of the running backend process.

    Drives the desktop ``服务`` panel. See
    ``components.schemas.SystemStatusResponse`` in
    ``api/openapi.yaml`` for the wire shape.
    """
    return collect_system_status(port=listen_port())
