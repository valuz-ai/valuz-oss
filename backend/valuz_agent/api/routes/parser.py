"""HTTP layer for the parser routing + plugin setup endpoints.

Two mounted prefixes share this file because they target the same
business domain and reuse the same Pydantic schemas:

- ``/v1/system/parser/setup/*`` — one-time setup jobs (model downloads,
  license gates). System-level because the user authorizes a local
  side-effect; not a "setting" per se.
- ``/v1/settings/parser/*`` — routing configuration + per-plugin user
  config (API keys, options) + health checks.

Routes return the V5 envelope shape (`{data, error}`) implicitly via
FastAPI's Pydantic serialisation; per backend rules each endpoint
documents its schema. The OpenAPI contract lives in ``api/openapi.yaml``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import (
    _parser_registry,
    _secret_store,
    get_setup_controller,
)
from valuz_agent.i18n import t
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.modules.parser.registry import (
    LIGHT_LOCAL_PLUGIN_ID,
    ParserPluginRegistry,
)
from valuz_agent.modules.parser.schemas import (
    ConfigFieldSchema,
    ParserRoutingPatchRequest,
    ParserRoutingResponse,
    PluginCapabilityStatusSchema,
    PluginConfigPatchRequest,
    PluginConfigResponse,
    PluginDescriptorSchema,
    PluginsListResponse,
    PluginTestResponse,
    SetupJobListResponse,
    SetupJobStatusSchema,
    SetupRequirementSchema,
    StartSetupJobRequest,
)
from valuz_agent.modules.parser.setup_jobs import (
    SetupJobAlreadyRunning,
    SetupJobController,
    SetupJobNotFound,
)
from valuz_agent.modules.settings.parser_routing import (
    LOCKED_LOCAL_KINDS,
    get_by_kind,
    get_fallback_to_local_on_error,
    get_plugin_config,
    get_plugin_configs,
    get_primary_plugin_id,
    set_by_kind,
    set_fallback_to_local_on_error,
    set_primary_plugin_id,
    update_plugin_config,
)
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ParserPluginConfig,
)

logger = logging.getLogger(__name__)

# Canonical kinds the UI always needs to render a row for, even if no
# plugin advertises the kind (defensive — the router's classify() can
# produce these). Order matters for the settings page rendering.
_ALL_KINDS: tuple[str, ...] = ("pdf", "image", "office", "spreadsheet", "web", "text")


system_router = APIRouter(prefix="/v1/system/parser", tags=["parser"])
settings_router = APIRouter(prefix="/v1/settings/parser", tags=["parser"])


# ── Setup jobs (system-scoped) ────────────────────────────────────


def _to_requirement_schema(req: Any | None) -> SetupRequirementSchema | None:
    if req is None:
        return None
    return SetupRequirementSchema(
        id=req.id,
        label_zh=req.label_zh,
        kind=req.kind,
        network_required=req.network_required,
        size_bytes=req.size_bytes,
        source=req.source,
        license_name=req.license_name,
        license_url=req.license_url,
        label_key=getattr(req, "label_key", None),
    )


def _find_requirement_for_setup_id(registry: ParserPluginRegistry, setup_id: str) -> Any | None:
    """Locate the ``SetupRequirement`` declared by any plugin's
    capability for the given ``setup_id``. The setup-job framework
    itself is plugin-agnostic, but UI cards want the declared metadata
    (size, license, source) which lives on the plugin descriptor."""
    for plugin in registry:
        for cap in plugin.descriptor.capabilities:
            if cap.setup is not None and cap.setup.id == setup_id:
                return cap.setup
    return None


def _status_to_schema(*, status: Any, requirement: Any | None) -> SetupJobStatusSchema:
    return SetupJobStatusSchema(
        setup_id=status.setup_id,
        status=status.status,
        downloaded_bytes=status.downloaded_bytes,
        total_bytes=status.total_bytes,
        error=status.error,
        source=status.source,
        started_at=status.started_at,
        completed_at=status.completed_at,
        updated_at=status.updated_at,
        requirement=_to_requirement_schema(requirement),
    )


@system_router.get("/setup", response_model=SetupJobListResponse)
async def list_setup_jobs(
    controller: SetupJobController = Depends(get_setup_controller),
) -> SetupJobListResponse:
    """Snapshot of every known setup_id + its current status.

    Drives the settings page's "Local Capabilities" section: each row
    here is rendered with a "Download" or "Re-download" button.
    """
    registry = _parser_registry()
    jobs = []
    for setup_id in controller.known_setup_ids():
        status = await controller.get(setup_id)
        requirement = _find_requirement_for_setup_id(registry, setup_id)
        jobs.append(_status_to_schema(status=status, requirement=requirement))
    return SetupJobListResponse(jobs=jobs)


@system_router.get("/setup/{setup_id}", response_model=SetupJobStatusSchema)
async def get_setup_job(
    setup_id: str,
    controller: SetupJobController = Depends(get_setup_controller),
) -> SetupJobStatusSchema:
    try:
        status = await controller.get(setup_id)
    except SetupJobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown setup_id: {setup_id}") from exc
    registry = _parser_registry()
    return _status_to_schema(
        status=status, requirement=_find_requirement_for_setup_id(registry, setup_id)
    )


@system_router.post("/setup/{setup_id}/start", response_model=SetupJobStatusSchema)
async def start_setup_job(
    setup_id: str,
    payload: StartSetupJobRequest,
    controller: SetupJobController = Depends(get_setup_controller),
) -> SetupJobStatusSchema:
    """Authorize and start a setup job.

    The dual ``accept_license`` + ``confirmed_source`` gate exists so a
    misconfigured client (or a CSRF-style attack) cannot trigger a
    network-fetching side-effect without explicit user intent surfaced
    in the request body. See plan §"反静默契约".
    """
    if not payload.accept_license:
        raise HTTPException(
            status_code=400,
            detail="accept_license must be true to start a setup job",
        )
    if not payload.confirmed_source.strip():
        raise HTTPException(status_code=400, detail="confirmed_source must be non-empty")
    try:
        status = await controller.start(setup_id)
    except SetupJobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown setup_id: {setup_id}") from exc
    except SetupJobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    registry = _parser_registry()
    return _status_to_schema(
        status=status, requirement=_find_requirement_for_setup_id(registry, setup_id)
    )


@system_router.delete("/setup/{setup_id}", response_model=SetupJobStatusSchema)
async def cancel_setup_job(
    setup_id: str,
    controller: SetupJobController = Depends(get_setup_controller),
) -> SetupJobStatusSchema:
    try:
        status = await controller.cancel(setup_id)
    except SetupJobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown setup_id: {setup_id}") from exc
    registry = _parser_registry()
    return _status_to_schema(
        status=status, requirement=_find_requirement_for_setup_id(registry, setup_id)
    )


# ── Plugins + routing (settings-scoped) ──────────────────────────


def _plugin_is_configured(plugin: Any, user_cfg: dict[str, Any]) -> bool:
    """Whether the user's stored config is enough for this plugin to run.

    LightLocal: always configured (no secrets / no toggle). Cloud
    plugins: must have ``enabled=True`` AND, if ``requires_secret``,
    a ``secret_ref`` set.
    """
    if plugin.descriptor.id == LIGHT_LOCAL_PLUGIN_ID:
        return True
    if not user_cfg.get("enabled", False):
        return False
    requires_secret = any(f.type == "secret" for f in plugin.descriptor.config_schema)
    if requires_secret and not user_cfg.get("secret_ref"):
        return False
    return True


def _resolve_capability_status(
    *,
    plugin: Any,
    capability: Any,
    setup_controller: SetupJobController,
) -> tuple[CapabilityStatus, Any | None]:
    """Resolve the *effective* capability status, taking setup-job state
    into account.

    If the descriptor declares ``needs_setup`` with a ``model_download``
    requirement, we consult the controller: if the marker is on disk,
    promote to ``ready``.
    """
    if capability.setup is not None and capability.setup.kind == "model_download":
        if setup_controller.has(capability.setup.id) and setup_controller.is_complete(
            capability.setup.id
        ):
            return CapabilityStatus.READY, None
        return CapabilityStatus.NEEDS_SETUP, capability.setup
    return capability.status, capability.setup


def _plugin_to_descriptor_schema(
    plugin: Any,
    user_cfg: dict[str, Any],
    setup_controller: SetupJobController,
) -> PluginDescriptorSchema:
    capability_schemas = []
    for cap in plugin.descriptor.capabilities:
        status, setup = _resolve_capability_status(
            plugin=plugin, capability=cap, setup_controller=setup_controller
        )
        capability_schemas.append(
            PluginCapabilityStatusSchema(
                kind=cap.kind,
                status=status.value,
                setup=_to_requirement_schema(setup),
                reason_zh=cap.reason_zh,
            )
        )

    config_schemas = [
        ConfigFieldSchema(
            key=f.key,
            label_zh=f.label_zh,
            type=f.type,
            required=f.required,
            default=f.default,
            placeholder=f.placeholder,
            help_zh=f.help_zh,
            help_url=f.help_url,
            label_key=f.label_key,
            help_key=f.help_key,
            placeholder_key=f.placeholder_key,
            options=[list(opt) for opt in f.options] if f.options else None,  # type: ignore[misc]
            option_keys=list(f.option_keys) if f.option_keys is not None else None,
        )
        for f in plugin.descriptor.config_schema
    ]

    requires_secret = any(f.type == "secret" for f in plugin.descriptor.config_schema)

    return PluginDescriptorSchema(
        id=plugin.descriptor.id,
        name_zh=plugin.descriptor.name_zh,
        description_zh=plugin.descriptor.description_zh,
        mode=plugin.descriptor.mode.value,
        capabilities=capability_schemas,
        config_schema=config_schemas,
        supported_kinds=sorted(plugin.descriptor.supported_kinds),
        is_configured=_plugin_is_configured(plugin, user_cfg),
        requires_secret=requires_secret,
        name_key=plugin.descriptor.name_key,
        description_key=plugin.descriptor.description_key,
        i18n_namespace=(plugin.descriptor.i18n_namespace or f"parser_{plugin.descriptor.id}"),
        sort_weight=plugin.descriptor.sort_weight,
    )


@settings_router.get("/plugins", response_model=PluginsListResponse)
async def list_parser_plugins(
    controller: SetupJobController = Depends(get_setup_controller),
) -> PluginsListResponse:
    """Enumerate every plugin shipped with this build, with the
    user-visible runtime status of each capability."""
    registry = _parser_registry()
    async with async_unit_of_work(commit=False) as db:
        configs = await get_plugin_configs(db)
    descriptors = [
        _plugin_to_descriptor_schema(
            plugin,
            configs.get(
                plugin.descriptor.id, {"enabled": False, "secret_ref": None, "options": {}}
            ),
            controller,
        )
        for plugin in registry
    ]
    return PluginsListResponse(plugins=descriptors)


def _compute_effective_by_kind(
    *,
    primary: str,
    by_kind: dict[str, str],
    registry: ParserPluginRegistry,
) -> dict[str, str]:
    """Server-side preview of what the router will actually do for each
    kind. Mirrors ``ParserRouter._resolve_plugin`` logic so the UI can
    show "Word → LightLocal (PaddleOCR 不支持)" without the FE
    duplicating the gate."""
    out: dict[str, str] = {}
    primary_plugin = registry.try_get(primary)
    for kind in _ALL_KINDS:
        if kind in LOCKED_LOCAL_KINDS:
            out[kind] = LIGHT_LOCAL_PLUGIN_ID
            continue
        if kind in by_kind and registry.try_get(by_kind[kind]) is not None:
            chosen = by_kind[kind]
        elif primary_plugin is not None and kind in primary_plugin.descriptor.supported_kinds:
            chosen = primary
        else:
            chosen = LIGHT_LOCAL_PLUGIN_ID
        out[kind] = chosen
    return out


async def _read_parser_routing(db: AsyncSession) -> ParserRoutingResponse:
    registry = _parser_registry()
    primary = await get_primary_plugin_id(db)
    by_kind = await get_by_kind(db)
    fallback = await get_fallback_to_local_on_error(db)
    return ParserRoutingResponse(
        primary_plugin_id=primary,
        by_kind=by_kind,
        fallback_to_local_on_error=fallback,
        effective_by_kind=_compute_effective_by_kind(
            primary=primary, by_kind=by_kind, registry=registry
        ),
        locked_kinds=sorted(LOCKED_LOCAL_KINDS),
    )


@settings_router.get("", response_model=ParserRoutingResponse)
async def get_parser_routing() -> ParserRoutingResponse:
    async with async_unit_of_work(commit=False) as db:
        return await _read_parser_routing(db)


@settings_router.patch("", response_model=ParserRoutingResponse)
async def patch_parser_routing(payload: ParserRoutingPatchRequest) -> ParserRoutingResponse:
    registry = _parser_registry()
    try:
        async with async_unit_of_work() as db:
            if payload.primary_plugin_id is not None:
                if registry.try_get(payload.primary_plugin_id) is None:
                    raise ValueError(f"unknown plugin id: {payload.primary_plugin_id}")
                await set_primary_plugin_id(db, payload.primary_plugin_id)
            if payload.by_kind is not None:
                # Defensive: drop unknown plugin ids so a botched PATCH
                # cannot disable parsing for that kind silently.
                cleaned = {
                    k: v for k, v in payload.by_kind.items() if registry.try_get(v) is not None
                }
                await set_by_kind(db, cleaned)
            if payload.fallback_to_local_on_error is not None:
                await set_fallback_to_local_on_error(db, payload.fallback_to_local_on_error)
            return await _read_parser_routing(db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Per-plugin config (API key + options) ────────────────────────


def _config_to_response(plugin_id: str, cfg: dict[str, Any]) -> PluginConfigResponse:
    return PluginConfigResponse(
        plugin_id=plugin_id,
        enabled=bool(cfg.get("enabled", False)),
        has_secret=bool(cfg.get("secret_ref")),
        options=dict(cfg.get("options", {})),
    )


@settings_router.get("/plugins/{plugin_id}/config", response_model=PluginConfigResponse)
async def get_plugin_config_route(plugin_id: str) -> PluginConfigResponse:
    if _parser_registry().try_get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown plugin id: {plugin_id}")
    async with async_unit_of_work(commit=False) as db:
        cfg = await get_plugin_config(db, plugin_id)
    return _config_to_response(plugin_id, cfg)


@settings_router.patch("/plugins/{plugin_id}/config", response_model=PluginConfigResponse)
async def patch_plugin_config_route(
    plugin_id: str, payload: PluginConfigPatchRequest
) -> PluginConfigResponse:
    if _parser_registry().try_get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown plugin id: {plugin_id}")

    secret_ref_change: tuple[str | None] | None = None
    if payload.secret is not None:
        secret_store: FileSecretStore = _secret_store()
        if payload.secret == "":
            secret_ref_change = (None,)
        else:
            ref = f"parser/{plugin_id}/{uuid.uuid4().hex[:12]}"
            secret_store.put(ref, payload.secret.strip())
            secret_ref_change = (ref,)

    async with async_unit_of_work() as db:
        cfg = await update_plugin_config(
            db,
            plugin_id,
            enabled=payload.enabled,
            secret_ref_change=secret_ref_change,
            options=payload.options,
        )
    return _config_to_response(plugin_id, cfg)


@settings_router.post("/plugins/{plugin_id}/test", response_model=PluginTestResponse)
async def test_plugin(plugin_id: str) -> PluginTestResponse:
    """Build the plugin's backend with the current stored config and
    run its ``health_check``. Used by the settings UI to validate API
    keys + endpoints without uploading a real document."""
    import time as _time

    registry = _parser_registry()
    plugin = registry.try_get(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"unknown plugin id: {plugin_id}")

    async with async_unit_of_work(commit=False) as db:
        cfg_dict = await get_plugin_config(db, plugin_id)

    secret_store = _secret_store()

    class _Resolver:
        def resolve(self, secret_ref: str | None) -> str | None:
            if not secret_ref:
                return None
            return secret_store.get(secret_ref)

    started = _time.perf_counter()
    try:
        backend = plugin.build(
            ParserPluginConfig(
                plugin_id=plugin_id,
                enabled=cfg_dict.get("enabled", False),
                secret_ref=cfg_dict.get("secret_ref"),
                options=cfg_dict.get("options", {}),
            ),
            _Resolver(),
        )
        ok = await backend.health_check()
    except Exception as exc:  # noqa: BLE001
        logger.exception("plugin %s health_check raised", plugin_id)
        return PluginTestResponse(ok=False, plugin_id=plugin_id, error=str(exc))
    latency_ms = int((_time.perf_counter() - started) * 1000)
    if not ok:
        # health_check returned False (not an exception) → it carries no
        # message, so the UI would otherwise show a bare "出错了". Derive an
        # actionable reason: most cloud-plugin failures are an unconfigured /
        # unsaved API token rather than an unreachable service.
        reason = (
            t("settings.parsing.testNotConfigured")
            if not _plugin_is_configured(plugin, cfg_dict)
            else t("settings.parsing.testHealthFailed")
        )
        return PluginTestResponse(
            ok=False, plugin_id=plugin_id, error=reason, latency_ms=latency_ms
        )
    return PluginTestResponse(ok=ok, plugin_id=plugin_id, latency_ms=latency_ms)


__all__ = ["system_router", "settings_router"]
