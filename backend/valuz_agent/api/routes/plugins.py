"""HTTP routes for the plugin library (Agent Plugins).

  GET    /v1/plugins                    — installed plugins (with members)
  GET    /v1/plugins/memberships        — {slug: [{id, name}]} for library badges
  POST   /v1/plugins/preview            — dry-run: manifest / members / conflicts
  POST   /v1/plugins/install            — install from zip upload | path | url | market item
  GET    /v1/plugins/{id}
  POST   /v1/plugins/{id}/enable        — toggles members too
  POST   /v1/plugins/{id}/disable
  POST   /v1/plugins/{id}/update        — re-install from source_ref (member diff)
  DELETE /v1/plugins/{id}               — reference-counted uninstall
  GET    /v1/plugins/{id}/export        — zip in the Agent Plugins layout

``install`` / ``preview`` accept EITHER ``multipart/form-data`` (``file`` = the
plugin zip, optional ``on_conflict`` form field) OR a JSON body
``{path?, url?, market_item_id?, on_conflict?}``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from valuz_agent.api.deps import get_current_user_id, get_skill_service
from valuz_agent.api.routes.marketplace import _market_index_client
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.marketplace.install_store import MarketplaceInstallStore
from valuz_agent.modules.plugins.datastore import PluginDatastore
from valuz_agent.modules.plugins.models import (
    PluginInstallRequest,
    PluginInstallResult,
    PluginList,
    PluginMembershipRef,
    PluginPreview,
    PluginUninstallResult,
    PluginUpdateRequest,
    PluginView,
)
from valuz_agent.modules.plugins.service import PluginService
from valuz_agent.modules.skills.service import SkillLibraryService

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def _get_plugin_service(
    db: AsyncSession = Depends(get_async_session),
    skill_service: SkillLibraryService = Depends(get_skill_service),
) -> PluginService:
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService

    return PluginService(
        datastore=PluginDatastore(db),
        skill_service=skill_service,
        connector_service=ConnectorService(ConnectorDatastore(db)),
        market=_market_index_client(),
        installs=MarketplaceInstallStore(db),
    )


def _validate_request(fields: Any) -> PluginInstallRequest:
    try:
        return PluginInstallRequest.model_validate(fields)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


async def _read_source(request: Request) -> tuple[bytes | None, PluginInstallRequest]:
    """Multipart (``file`` zip) or JSON body → ``(zip_bytes, request)``."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        data: bytes | None = None
        if isinstance(upload, UploadFile):
            data = await upload.read()
        fields: dict[str, Any] = {}
        for key in ("path", "url", "market_item_id", "on_conflict"):
            value = form.get(key)
            if isinstance(value, str) and value:
                fields[key] = value
        return data, _validate_request(fields)
    body: Any = None
    raw = await request.body()
    if raw:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Request body must be JSON") from exc
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    return None, _validate_request(body)


# ---------------------------------------------------------------------------
# Routes (static paths before /{plugin_id})
# ---------------------------------------------------------------------------


@router.get("", response_model=PluginList)
async def list_plugins(
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginList:
    return PluginList(items=await svc.list_plugins(user_id))


@router.get("/memberships", response_model=dict[str, list[PluginMembershipRef]])
async def plugin_memberships(
    kind: str = Query(..., pattern="^(skill|connector)$"),
    slugs: str = Query("", description="Comma-separated library slugs"),
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> dict[str, list[PluginMembershipRef]]:
    """Which plugins each skill / connector belongs to (library card badges)."""
    wanted = [s.strip() for s in slugs.split(",") if s.strip()]
    return await svc.memberships(user_id, kind, wanted)


@router.post("/preview", response_model=PluginPreview)
async def preview_plugin(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginPreview:
    """Dry-run: what the plugin contains and which members conflict with the
    library — no side effects. Same inputs as ``install``."""
    data, req = await _read_source(request)
    return await svc.preview(
        user_id, zip_bytes=data, path=req.path, url=req.url, market_item_id=req.market_item_id
    )


@router.post("/install", response_model=PluginInstallResult)
async def install_plugin(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginInstallResult:
    """Install (or re-install/update a same-name plugin from the same source).
    Accepts the Agent Plugins layout and the ``.claude-plugin`` /
    ``.codebuddy-plugin`` layouts (auto-detected)."""
    data, req = await _read_source(request)
    return await svc.install(
        user_id,
        zip_bytes=data,
        path=req.path,
        url=req.url,
        market_item_id=req.market_item_id,
        on_conflict=req.on_conflict,
    )


@router.get("/{plugin_id}", response_model=PluginView)
async def get_plugin(
    plugin_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginView:
    return await svc.get_plugin(user_id, plugin_id)


@router.post("/{plugin_id}/enable", response_model=PluginView)
async def enable_plugin(
    plugin_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginView:
    return await svc.set_enabled(user_id, plugin_id, True)


@router.post("/{plugin_id}/disable", response_model=PluginView)
async def disable_plugin(
    plugin_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginView:
    return await svc.set_enabled(user_id, plugin_id, False)


@router.post("/{plugin_id}/update", response_model=PluginInstallResult)
async def update_plugin(
    plugin_id: str,
    body: PluginUpdateRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginInstallResult:
    on_conflict = body.on_conflict if body is not None else "skip"
    return await svc.update(user_id, plugin_id, on_conflict=on_conflict)


@router.delete("/{plugin_id}", response_model=PluginUninstallResult)
async def uninstall_plugin(
    plugin_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> PluginUninstallResult:
    return await svc.uninstall(user_id, plugin_id)


@router.get("/{plugin_id}/export")
async def export_plugin(
    plugin_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: PluginService = Depends(_get_plugin_service),
) -> StreamingResponse:
    """Download the plugin as a zip in the Agent Plugins layout."""
    filename, data = await svc.export_zip(user_id, plugin_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
