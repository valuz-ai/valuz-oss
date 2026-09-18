"""HTTP layer for the Settings "Browser" panel.

Thin wrappers over the bound ``ext.browser_engine`` (the same engine the
``browser_start``/``browser_stop`` MCP tools call; OSS default is the local
``modules.browser.service``). The panel is the human front door — status /
diagnostics / login helper — while the MCP tools are the agent's
lazy-activation front door. See docs/design/browser-feature.md §1
(architecture) and §9 (engine port).

The owner comes from the request (``Depends(get_current_user_id)``) and is
passed explicitly: a remote engine needs it to find the owner's sandbox. Panel
calls carry no session — they address the owner's daemon, not one turn's.

``open`` raises ``BrowserError`` (e.g. Node missing) which the app middleware
maps to a 422 with the error's message; the panel surfaces it as a hint.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.browser.schemas import (
    BrowserStartResult,
    BrowserStatus,
    BrowserStopResult,
)
from valuz_agent.ports.extensions import ext

router = APIRouter(prefix="/v1/browser", tags=["browser"])


@router.get("/status", response_model=BrowserStatus)
async def get_browser_status(user_id: str = Depends(get_current_user_id)) -> BrowserStatus:
    """Cheap, read-only snapshot for the Settings panel (safe to poll)."""
    return await ext.browser_engine.status(user_id=user_id)


@router.post("/open", response_model=BrowserStartResult)
async def open_browser(user_id: str = Depends(get_current_user_id)) -> BrowserStartResult:
    """Login helper: start (or reuse) the managed browser so the user can log
    into sites in the isolated profile. Raises ``BrowserError`` (→ 422) when the
    environment isn't ready (e.g. Node missing)."""
    return await ext.browser_engine.start(user_id=user_id)


@router.post("/stop", response_model=BrowserStopResult)
async def stop_browser(user_id: str = Depends(get_current_user_id)) -> BrowserStopResult:
    await ext.browser_engine.stop(user_id=user_id)
    return BrowserStopResult()
