"""``/v1/extensions`` — what the backend plugin host loaded.

The management view of Valuz's own backend extensions (plugin-architecture
design §8): every plugin the running process composed, its status (active /
failed / disabled / skipped), whether it is required, its entitlement key and
its config schema (JSON Schema 2020-12, the dsh ``--dump-config-schema``
shape). Toggles and config edits are recorded and take effect on the next
start (``restart-required``) — FastAPI cannot drop routes from a live app.
A bare OSS app composes without a plugin host and reports an empty list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.plugin_host import active_plugin_host, load_extension_prefs, save_enabled
from valuz_agent.plugin_host.errors import PluginHostError

router = APIRouter(prefix="/v1/extensions", tags=["extensions"])


class EnabledChange(BaseModel):
    enabled: bool


@router.get("/backend")
async def list_backend_extensions(
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    from valuz_agent.infra.config import settings

    host = active_plugin_host()
    editable = getattr(settings, "deployment_type", "local") == "local"
    if host is None:
        return {"composed": False, "editable": editable, "plugins": [], "config_schemas": {}}
    disabled = load_extension_prefs().disabled
    plugins = []
    for info in host.list():
        row = info.to_dict()
        # The persisted desire (applied at the next start), not just this boot's.
        row["desiredEnabled"] = info.id not in disabled
        plugins.append(row)
    return {
        "composed": True,
        "editable": editable,
        "plugins": plugins,
        "config_schemas": host.config_schemas(),
    }


@router.post("/backend/{plugin_id}/enabled")
async def set_backend_extension_enabled(
    plugin_id: str,
    body: EnabledChange,
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    from valuz_agent.infra.config import settings

    if getattr(settings, "deployment_type", "local") != "local":
        # A cloud backend is shared: a deployment-wide toggle is an operator
        # change, not a user action (org-level entitlements gate features there).
        raise HTTPException(status_code=403, detail="extensions are managed by the operator here")
    host = active_plugin_host()
    if host is None:
        raise HTTPException(status_code=404, detail="no backend plugin host in this deployment")
    try:
        record = host.get(plugin_id)
    except PluginHostError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record.plugin.required and not body.enabled:
        raise HTTPException(status_code=409, detail=f"{plugin_id!r} is required")
    save_enabled(plugin_id, body.enabled)
    return {"application": host.set_enabled(plugin_id, body.enabled)}
