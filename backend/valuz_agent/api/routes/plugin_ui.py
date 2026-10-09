"""``/v1/ui`` — the UI bus over HTTP (docs: hooks-and-plugin-ui.md §6).

``GET /sites`` tells the frontend which UI slots a plugin draws into (it
mounts a surface only there); ``POST /render`` returns the element trees for
one slot instance; ``POST /action`` delivers a button click / input change to
the plugin that drew it. Pushes (invalidate, toast, status, log, notice) ride
the session and user event streams.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.plugin_ui import StaleAction, UiRequest, ui_registry

router = APIRouter(prefix="/v1/ui", tags=["plugin-ui"])


class RenderRequest(BaseModel):
    site: str
    instance: str = ""
    session_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(RenderRequest):
    owner: str
    action: str
    value: Any = None
    generation: int = 0


def _request(payload: RenderRequest, user_id: str) -> UiRequest:
    return UiRequest(
        site=payload.site,
        instance=payload.instance,
        user_id=user_id,
        session_id=payload.session_id,
        context=payload.context,
    )


@router.get("/sites")
async def list_sites(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return {"sites": ui_registry.sites()}


@router.post("/render")
async def render(
    payload: RenderRequest, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return {"site": payload.site, "items": await ui_registry.render(_request(payload, user_id))}


@router.post("/action")
async def act(
    payload: ActionRequest, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    request = _request(payload, user_id)
    try:
        await ui_registry.act(
            request,
            owner=payload.owner,
            action=payload.action,
            value=payload.value,
            generation=payload.generation,
        )
    except StaleAction:
        # The plugin redrew since this button was drawn: hand back the fresh
        # drawing instead of acting on the old one.
        return {"ok": False, "stale": True, "items": await ui_registry.render(request)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "items": await ui_registry.render(request)}
