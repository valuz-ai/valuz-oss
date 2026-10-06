"""Third-party plugin routes (docs task card 04 §D). Local deployments only.

Every route answers ``403 {"detail": {"code": "third_party_unavailable"}}`` on a
cloud deployment (ADR-034). Errors of the module (``ThirdPartyError``) render as
``{"detail": {"code", "message", "errors"?, ...}}`` with their own status.
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.third_party.errors import ThirdPartyError
from valuz_agent.modules.third_party.service import deployment_type, third_party_service


class _ThirdPartyRoute(APIRoute):
    """Render ``ThirdPartyError`` as ``{"detail": {...}}`` (a stable string ``code``)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except ThirdPartyError as exc:
                return JSONResponse({"detail": exc.detail()}, status_code=exc.status_code)

        return handler


def _require_local() -> None:
    from valuz_agent.modules.third_party.errors import ThirdPartyUnavailable

    if deployment_type() != "local":
        raise ThirdPartyUnavailable()


router = APIRouter(
    prefix="/v1/extensions/third-party",
    tags=["extensions"],
    route_class=_ThirdPartyRoute,
    dependencies=[Depends(_require_local)],
)

#: ``GET /v1/ext-assets/{id}/{revision}/{path}`` — a plugin's frontend files.
assets_router = APIRouter(
    prefix="/v1/ext-assets",
    tags=["extensions"],
    route_class=_ThirdPartyRoute,
    dependencies=[Depends(_require_local)],
)


# ---- bodies ------------------------------------------------------------------------


class SourceBody(BaseModel):
    source_path: str | None = None
    url: str | None = None

    def spec(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.source_path:
            out["source_path"] = self.source_path
        if self.url:
            out["url"] = self.url
        return out


class InstallBody(SourceBody):
    expected_sha256: str | None = None
    enable: bool = True


class DevLinkBody(BaseModel):
    path: str = Field(min_length=1)


class SafeModeBody(BaseModel):
    enabled: bool
    reason: str | None = None


class LogBody(BaseModel):
    level: str = "info"
    message: str = ""


class ConfigBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class StorageBody(BaseModel):
    value: Any = None


class RunBody(BaseModel):
    input: Any = None
    wait_seconds: int = Field(default=0, ge=0, le=60)


class PathBody(BaseModel):
    path: str = Field(min_length=1)
    out_dir: str | None = None


# ---- the list, watch and safe mode -------------------------------------------------


@router.get("")
async def list_third_party(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return await third_party_service.list(user_id)


@router.get("/watch")
async def watch_third_party(
    since: int = Query(0), timeout: float = Query(25.0, ge=0, le=60)
) -> dict[str, Any]:
    return await third_party_service.watch(since, timeout)


@router.post("/safe-mode")
async def set_safe_mode(body: SafeModeBody) -> dict[str, Any]:
    return await third_party_service.set_safe_mode(body.enabled, body.reason)


# ---- install and development -------------------------------------------------------


@router.post("/inspect")
async def inspect_third_party(
    body: SourceBody, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.inspect(body.spec(), user_id=user_id)


@router.post("/install")
async def install_third_party(
    body: InstallBody, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.install(
        user_id, body.spec(), expected_sha256=body.expected_sha256, enable=body.enable
    )


@router.post("/dev-link")
async def dev_link_third_party(
    body: DevLinkBody, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.dev_link(user_id, body.path)


@router.post("/validate")
async def validate_third_party(body: PathBody) -> dict[str, Any]:
    return await asyncio.to_thread(third_party_service.validate, body.path)


@router.post("/pack")
async def pack_third_party(body: PathBody) -> dict[str, Any]:
    return await asyncio.to_thread(third_party_service.pack, body.path, body.out_dir)


# ---- one plugin --------------------------------------------------------------------


@router.post("/{plugin_id}/reload")
async def reload_third_party(
    plugin_id: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.reload(plugin_id, user_id=user_id)


@router.post("/{plugin_id}/enable")
async def enable_third_party(
    plugin_id: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.set_enabled(user_id, plugin_id, True)


@router.post("/{plugin_id}/disable")
async def disable_third_party(
    plugin_id: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.set_enabled(user_id, plugin_id, False)


@router.delete("/{plugin_id}")
async def uninstall_third_party(
    plugin_id: str,
    purge_data: bool = Query(False),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return await third_party_service.uninstall(user_id, plugin_id, purge_data=purge_data)


@router.get("/{plugin_id}/logs")
async def read_third_party_logs(plugin_id: str, limit: int = Query(200, ge=1, le=1000)) -> Any:
    return third_party_service.read_logs(plugin_id, limit)


@router.post("/{plugin_id}/logs", status_code=204)
async def write_third_party_log(plugin_id: str, body: LogBody) -> Response:
    third_party_service.write_log(plugin_id, body.level, body.message, "frontend")
    return Response(status_code=204)


@router.get("/{plugin_id}/config")
async def get_third_party_config(
    plugin_id: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.get_config(user_id, plugin_id)


@router.put("/{plugin_id}/config")
async def put_third_party_config(
    plugin_id: str, body: ConfigBody, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.put_config(user_id, plugin_id, body.values)


# ---- storage -----------------------------------------------------------------------


@router.get("/{plugin_id}/storage")
async def list_third_party_storage(
    plugin_id: str, prefix: str = Query(""), user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.storage_list(user_id, plugin_id, prefix)


@router.get("/{plugin_id}/storage/{key:path}")
async def get_third_party_storage(
    plugin_id: str, key: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.storage_get(user_id, plugin_id, key)


@router.put("/{plugin_id}/storage/{key:path}")
async def put_third_party_storage(
    plugin_id: str, key: str, body: StorageBody, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.storage_put(user_id, plugin_id, key, body.value)


@router.delete("/{plugin_id}/storage/{key:path}", status_code=204)
async def delete_third_party_storage(
    plugin_id: str, key: str, user_id: str = Depends(get_current_user_id)
) -> Response:
    await third_party_service.storage_delete(user_id, plugin_id, key)
    return Response(status_code=204)


# ---- plugin-declared automations ---------------------------------------------------


@router.get("/{plugin_id}/automations")
async def list_third_party_automations(
    plugin_id: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.automations(user_id, plugin_id)


@router.post("/{plugin_id}/automations/{name}/run")
async def run_third_party_automation(
    plugin_id: str,
    name: str,
    body: RunBody | None = None,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    run = body or RunBody()
    return await third_party_service.run_automation(
        user_id, plugin_id, name, input=run.input, wait_seconds=run.wait_seconds
    )


@router.get("/{plugin_id}/automations/{name}/runs/latest")
async def latest_third_party_automation_run(
    plugin_id: str, name: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.latest_automation_run(user_id, plugin_id, name)


@router.get("/{plugin_id}/automation-runs/{run_id}")
async def get_third_party_automation_run(
    plugin_id: str, run_id: str, user_id: str = Depends(get_current_user_id)
) -> dict[str, Any]:
    return await third_party_service.automation_run(user_id, plugin_id, run_id)


# ---- assets ------------------------------------------------------------------------

_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".html": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".wasm": "application/wasm",
}


def _media_type(name: str) -> str:
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _MIME.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"


@assets_router.get("/{plugin_id}/{revision}/{path:path}")
async def get_ext_asset(plugin_id: str, revision: int, path: str) -> FileResponse:
    file, dev = third_party_service.asset_path(plugin_id, revision, path)
    cache = "no-cache" if dev else "public, max-age=31536000, immutable"
    return FileResponse(
        file,
        media_type=_media_type(file.name),
        headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"},
    )
