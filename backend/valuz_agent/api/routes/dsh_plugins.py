"""``/v1/dsh/plugins`` — manage the Valuz-managed dsh profile the dsh way.

Every call is proxied to the upstream ``pluginManager`` Remote service of the
resident dsh manager host (``modules/dsh_plugins/manager.py``), so install /
enable / configure / version exemptions / remove behave exactly as in dsh
(guided installs, build approvals, rollback, peer gating, HMR). ``ui_url`` is
the token URL of that host's native web UI, for the dsh Plugins page itself.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.dsh_plugins.manager import (
    PLUGIN_MANAGER_METHODS,
    DshManagedBundleError,
    DshManagerUnavailableError,
    get_dsh_manager,
)
from valuz_agent.modules.dsh_plugins.remote import DshRemoteError

router = APIRouter(prefix="/v1/dsh/plugins", tags=["dsh-plugins"])


class RemoteCall(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
async def dsh_plugins_status(_user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return asdict(get_dsh_manager().status())


@router.post("/manager/start")
async def start_dsh_manager(_user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    manager = get_dsh_manager()
    try:
        url = await manager.ensure_started()
    except DshManagerUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**asdict(manager.status()), "ui_url": url}


@router.post("/manager/stop")
async def stop_dsh_manager(_user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    manager = get_dsh_manager()
    await manager.stop()
    return asdict(manager.status())


@router.post("/remote/{method}")
async def call_plugin_manager(
    method: str,
    body: RemoteCall,
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    if method not in PLUGIN_MANAGER_METHODS:
        raise HTTPException(status_code=404, detail=f"unknown pluginManager method {method!r}")
    try:
        value = await get_dsh_manager().call(method, body.args)
    except (DshManagerUnavailableError, DshManagedBundleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DshRemoteError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "error": exc.details}
        ) from exc
    return {"value": value}
