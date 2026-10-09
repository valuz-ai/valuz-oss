"""Loopback endpoint for out-of-process runtimes on the Valuz hook bus.

``POST {KERNEL_API_PREFIX}/v1/hook-bridge/{token}/dispatch`` starts a
dispatch; ``POST …/dispatch/{dispatch_id}`` posts the result of the core
step the kernel asked for. Each answers with the next step (see
:mod:`src.core.hooks.remote`). The token is minted per runtime spawn and is
the credential — same model as the dsh user-questions bridge; the standalone
kernel's bearer middleware exempts this path (``app/main.py``).
"""

from __future__ import annotations

from typing import Any

from app.routes import KERNEL_API_PREFIX
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.core.hooks.remote import RemoteHookError, get_remote_hooks

HOOK_BRIDGE_ROUTE_SEGMENT = "/v1/hook-bridge"

router = APIRouter(prefix=f"{KERNEL_API_PREFIX}{HOOK_BRIDGE_ROUTE_SEGMENT}", tags=["hook-bridge"])


class StartRequest(BaseModel):
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CoreResultRequest(BaseModel):
    result: Any = None
    error: str | None = None


@router.post("/{token}/dispatch")
async def start_dispatch(token: str, request: StartRequest) -> dict[str, Any]:
    session = get_remote_hooks(token)
    if session is None:
        raise HTTPException(status_code=404, detail="hook bridge token is not active")
    try:
        return await session.start(request.event, request.payload)
    except RemoteHookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{token}/dispatch/{dispatch_id}")
async def post_core_result(
    token: str, dispatch_id: str, request: CoreResultRequest
) -> dict[str, Any]:
    session = get_remote_hooks(token)
    if session is None:
        raise HTTPException(status_code=404, detail="hook bridge token is not active")
    try:
        if request.error is not None:
            return await session.post_core_error(dispatch_id, request.error)
        return await session.post_core_result(dispatch_id, request.result)
    except RemoteHookError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{token}/wants")
async def wants(token: str) -> dict[str, Any]:
    session = get_remote_hooks(token)
    if session is None:
        raise HTTPException(status_code=404, detail="hook bridge token is not active")
    return {"events": session.wants()}
