"""HTTP layer for ``GET /v1/system/status``.

The desktop ``服务`` page (status card + log viewer) hits this once on
mount and again every few seconds. Cheap on every call — the heavy
lifting (kernel pin parse, version read) is memoised inside
``service.collect_system_status``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from valuz_agent.modules.system.schemas import SystemStatusResponse
from valuz_agent.modules.system.service import (
    collect_system_status,
    listen_port,
)

router = APIRouter(prefix="/v1/system", tags=["system"])


class NetworkEgressReconfigureRequest(BaseModel):
    bootstrap: dict[str, Any] | None = None
    required_unavailable: bool = False
    prewarm_limit: int = Field(default=1, ge=0, le=1)


class NetworkEgressReconfigureResponse(BaseModel):
    configured: bool
    prewarmed_session_ids: list[str]
    prewarm_failed_session_ids: list[str]


class DesktopNetworkEgressActivityResponse(BaseModel):
    active_session_ids: list[str]


class DesktopNetworkEgressInterruptRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=100)


class DesktopNetworkEgressInterruptResponse(BaseModel):
    interrupted_session_ids: list[str]
    inactive_session_ids: list[str]


def _require_desktop_control(token: str | None) -> None:
    from src.runtimes.network_egress import desktop_control_authorized

    if not desktop_control_authorized(token):
        raise HTTPException(status_code=401, detail="desktop_control_unauthorized")


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Snapshot of the running backend process.

    Drives the desktop ``服务`` panel. See
    ``components.schemas.SystemStatusResponse`` in
    ``api/openapi.yaml`` for the wire shape.
    """
    return await collect_system_status(port=listen_port())


@router.post("/network-egress", response_model=NetworkEgressReconfigureResponse)
async def reconfigure_network_egress(
    body: NetworkEgressReconfigureRequest,
    x_valuz_desktop_token: str | None = Header(default=None),
) -> NetworkEgressReconfigureResponse:
    """Replace desktop model networking without restarting the API process."""
    from app.dependencies import get_orchestrator
    from src.runtimes.network_egress import replace_network_egress

    _require_desktop_control(x_valuz_desktop_token)

    orchestrator = get_orchestrator()
    if orchestrator.active_sessions:
        raise HTTPException(status_code=409, detail="model_runtimes_still_active")

    candidates = orchestrator.warm_runtime_candidates(limit=body.prewarm_limit)
    await orchestrator.evict_all_warm_runtimes()
    await replace_network_egress(
        body.bootstrap,
        required_unavailable=body.required_unavailable,
    )

    prewarmed: list[str] = []
    failed: list[str] = []
    for owner_id, session_id in candidates:
        try:
            await orchestrator.prepare_runtime(owner_id, session_id)
            prewarmed.append(session_id)
        except Exception:  # noqa: BLE001 - networking is already reconfigured
            failed.append(session_id)
    return NetworkEgressReconfigureResponse(
        configured=True,
        prewarmed_session_ids=prewarmed,
        prewarm_failed_session_ids=failed,
    )


@router.get(
    "/network-egress/activity",
    response_model=DesktopNetworkEgressActivityResponse,
)
async def get_network_egress_activity(
    x_valuz_desktop_token: str | None = Header(default=None),
) -> DesktopNetworkEgressActivityResponse:
    """Return process-local active sessions without an owner-scoped DB read."""
    from app.dependencies import get_orchestrator

    _require_desktop_control(x_valuz_desktop_token)
    return DesktopNetworkEgressActivityResponse(
        active_session_ids=sorted(get_orchestrator().active_sessions),
    )


@router.post(
    "/network-egress/interrupt",
    response_model=DesktopNetworkEgressInterruptResponse,
)
async def interrupt_network_egress_activity(
    body: DesktopNetworkEgressInterruptRequest,
    x_valuz_desktop_token: str | None = Header(default=None),
) -> DesktopNetworkEgressInterruptResponse:
    """Interrupt only the explicitly confirmed sessions that remain active."""
    from app.dependencies import get_orchestrator

    _require_desktop_control(x_valuz_desktop_token)
    orchestrator = get_orchestrator()
    interrupted: list[str] = []
    inactive: list[str] = []
    for session_id in dict.fromkeys(body.session_ids):
        if await orchestrator.interrupt(session_id):
            interrupted.append(session_id)
        else:
            inactive.append(session_id)
    return DesktopNetworkEgressInterruptResponse(
        interrupted_session_ids=interrupted,
        inactive_session_ids=inactive,
    )
