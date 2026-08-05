from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id, get_settings_service
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.errors import NoAvailableProvider, ProviderNotFound
from valuz_agent.modules.providers.service import ProviderService
from valuz_agent.modules.settings.model_options import (
    CurrentDefault,
    ModelOptionsResponse,
    build_model_options,
    to_option_input,
)
from valuz_agent.modules.settings.preferences import (
    detect_system_timezone,
    get_conversation_citations_enabled,
    get_conversation_task_coverage_enabled,
    get_conversation_verification_enabled,
    get_default_effort,
    get_default_locale,
    get_default_model,
    get_default_provider_id,
    get_default_runtime,
    get_default_timezone,
    get_font_size,
    get_theme,
    set_conversation_citations_enabled,
    set_conversation_task_coverage_enabled,
    set_conversation_verification_enabled,
    set_default_effort,
    set_default_locale,
    set_default_model,
    set_default_provider_id,
    set_default_runtime,
    set_default_timezone,
    set_font_size,
    set_theme,
)
from valuz_agent.modules.settings.service import (
    AboutInfo,
    CapabilitiesSnapshot,
    SettingsService,
    UpdateCheckResult,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import SystemProviderImmutable

router = APIRouter(prefix="/v1/settings", tags=["settings"])


# ── Preferences (ADR-010) ────────────────────────────────────────────


class PreferencesResponse(BaseModel):
    default_timezone: str
    default_locale: str
    detected_timezone: str
    theme: str
    font_size: str
    conversation_citations_enabled: bool
    conversation_verification_enabled: bool
    conversation_task_coverage_enabled: bool


class PreferencesPatchPayload(BaseModel):
    default_timezone: str | None = Field(default=None, min_length=1)
    default_locale: str | None = Field(default=None, min_length=1)
    theme: str | None = Field(default=None)
    font_size: str | None = Field(default=None)
    conversation_citations_enabled: bool | None = None
    conversation_verification_enabled: bool | None = None
    conversation_task_coverage_enabled: bool | None = None


async def _read_preferences(db: AsyncSession, user_id: str) -> PreferencesResponse:
    return PreferencesResponse(
        default_timezone=await get_default_timezone(db, user_id=user_id),
        default_locale=await get_default_locale(db, user_id=user_id),
        detected_timezone=detect_system_timezone(),
        theme=await get_theme(db, user_id=user_id),
        font_size=await get_font_size(db, user_id=user_id),
        conversation_citations_enabled=await get_conversation_citations_enabled(
            db, user_id=user_id
        ),
        conversation_verification_enabled=await get_conversation_verification_enabled(
            db, user_id=user_id
        ),
        conversation_task_coverage_enabled=await get_conversation_task_coverage_enabled(
            db, user_id=user_id
        ),
    )


@router.get("/preferences")
async def get_preferences(
    user_id: str = Depends(get_current_user_id),
) -> PreferencesResponse:
    """Return user-level preferences that drive schedule + UI behavior.

    ``detected_timezone`` is a UX hint, not a contract — the frontend
    can use it to seed an initial "Use system timezone" suggestion on
    first-run.
    """
    async with async_unit_of_work(commit=False) as db:
        return await _read_preferences(db, user_id)


@router.patch("/preferences")
async def patch_preferences(
    payload: PreferencesPatchPayload,
    user_id: str = Depends(get_current_user_id),
) -> PreferencesResponse:
    """Update user preferences. Only sent keys are updated."""
    try:
        async with async_unit_of_work() as db:
            if payload.default_timezone is not None:
                await set_default_timezone(db, payload.default_timezone, user_id=user_id)
            if payload.default_locale is not None:
                await set_default_locale(db, payload.default_locale, user_id=user_id)
            if payload.theme is not None:
                await set_theme(db, payload.theme, user_id=user_id)
            if payload.font_size is not None:
                await set_font_size(db, payload.font_size, user_id=user_id)
            if payload.conversation_citations_enabled is not None:
                await set_conversation_citations_enabled(
                    db,
                    payload.conversation_citations_enabled,
                    user_id=user_id,
                )
            if payload.conversation_verification_enabled is not None:
                await set_conversation_verification_enabled(
                    db,
                    payload.conversation_verification_enabled,
                    user_id=user_id,
                )
            if payload.conversation_task_coverage_enabled is not None:
                await set_conversation_task_coverage_enabled(
                    db,
                    payload.conversation_task_coverage_enabled,
                    user_id=user_id,
                )
            return await _read_preferences(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Model defaults (runtime + provider + model + effort) ─────────────


class ModelDefaultsResponse(BaseModel):
    default_runtime: str  # one of RUNTIME_VALUES
    default_provider_id: str | None
    default_model: str | None
    # Kernel V5+bba3014 ``ModelSettings.effort`` — always one of
    # ``low|medium|high|xhigh|max``. The Composer's old "Default"
    # sentinel is gone; unset / cleared rows collapse to
    # ``FALLBACK_EFFORT`` ("high") at read time so the dropdown is
    # never empty.
    default_effort: str


class ModelDefaultsPatchPayload(BaseModel):
    default_runtime: str | None = Field(default=None, min_length=1)
    # Empty string clears the field (e.g. when the user switches runtime
    # and the previous default isn't compatible). ``None`` means "don't
    # touch this key" — required so the UI can update provider+model
    # together without nuking effort/runtime.
    default_provider_id: str | None = None
    default_model: str | None = None
    # Effort accepts one of EFFORT_VALUES, or the empty string (=
    # legacy clear; now reset to FALLBACK_EFFORT). ``None`` means
    # "don't touch this key".
    default_effort: str | None = None


async def _read_model_defaults(db: AsyncSession, user_id: str) -> ModelDefaultsResponse:
    return ModelDefaultsResponse(
        default_runtime=await get_default_runtime(db, user_id=user_id),
        default_provider_id=await get_default_provider_id(db, user_id=user_id),
        default_model=await get_default_model(db, user_id=user_id),
        default_effort=await get_default_effort(db, user_id=user_id),
    )


async def _mirror_to_default_assistant(
    user_id: str, db: AsyncSession, defaults: ModelDefaultsResponse
) -> None:
    """09-assistant: the 默认助手 base agent's brain mirrors the global model
    default (Settings = source of truth). Keeps the always-present default
    conversation agent on the user's chosen runtime/model/effort. Re-syncs its
    kernel AgentConfig via ``update_agent`` so live sessions pick it up."""
    from valuz_agent.modules.agents.seed import DEFAULT_ASSISTANT_SLUG
    from valuz_agent.modules.agents.service import AgentNotFoundError, AgentService

    try:
        await AgentService(db).update_agent(  # type: ignore[arg-type]
            user_id,
            DEFAULT_ASSISTANT_SLUG,
            {
                "runtime": defaults.default_runtime,
                "model": defaults.default_model,
                "provider_id": defaults.default_provider_id,
                "effort": defaults.default_effort,
            },
        )
    except AgentNotFoundError:
        # Not seeded yet (fresh DB before the boot seeder) — nothing to mirror.
        pass


async def _finish_model_defaults(user_id: str, db: AsyncSession) -> ModelDefaultsResponse:
    defaults = await _read_model_defaults(db, user_id)
    await _mirror_to_default_assistant(user_id, db, defaults)
    return defaults


@router.get("/model-defaults")
async def get_model_defaults(
    user_id: str = Depends(get_current_user_id),
) -> ModelDefaultsResponse:
    """Return the global model-default tuple that drives quick-chat and
    scheduled tasks. The four fields together pin one specific
    (runtime, provider, model, effort) combination — the frontend
    "Default" card writes back any subset on change.
    """
    async with async_unit_of_work(commit=False) as db:
        return await _read_model_defaults(db, user_id)


@router.patch("/model-defaults")
async def patch_model_defaults(
    payload: ModelDefaultsPatchPayload,
    user_id: str = Depends(get_current_user_id),
) -> ModelDefaultsResponse:
    """Update the global model-default tuple.

    ``default_provider_id`` behaviour:
    - Non-empty string: delegates to ``ProviderService.set_default`` so that
      the provider row's ``is_default`` flag and ``default_model`` are updated
      atomically alongside the app-setting keys.  This is the path model_resolver
      reads (``providers.get_default()``) and ensures settings-page changes are
      immediately visible to scheduled tasks and quick-chat sessions.
    - Empty string ``""``: clears the default — resets all ``is_default`` flags
      via ``ProviderDatastore.clear_default()`` and writes ``None`` to both
      app-setting keys.
    - ``None``: no change to provider/model defaults (only runtime/effort may change).
    """
    try:
        async with async_unit_of_work() as db:
            if payload.default_runtime is not None:
                await set_default_runtime(db, payload.default_runtime, user_id=user_id)

            if payload.default_provider_id is not None:
                if payload.default_provider_id == "":
                    # Clear: wipe is_default on all rows + clear app-setting keys.
                    ds = ProviderDatastore(db)
                    await ds.clear_default(user_id)
                    await set_default_provider_id(db, None, user_id=user_id)
                    await set_default_model(db, None, user_id=user_id)
                elif any(
                    it.id == payload.default_provider_id
                    for it in await ext.llm_provider.list(user_id=user_id)
                ):
                    # Contributed (catalog) channel (e.g. the commercial
                    # "Valuz 系统模型" channel — ADR-011). It has no providers-table
                    # row to carry ``is_default``, so pin it via preferences only —
                    # NOT through ProviderService.set_default, whose
                    # ``_guard_not_system`` correctly blocks editing system
                    # providers but over-blocks selecting one as the default. Clear
                    # any builtin row's ``is_default`` so model_resolver doesn't see
                    # two defaults.
                    await ProviderDatastore(db).clear_default(user_id)
                    await set_default_provider_id(db, payload.default_provider_id, user_id=user_id)
                    if payload.default_model is not None:
                        await set_default_model(db, payload.default_model or None, user_id=user_id)
                else:
                    # Set: delegate to ProviderService so is_default +
                    # default_model row + app-setting keys all update together.
                    svc = ProviderService(
                        datastore=ProviderDatastore(db),
                        event_bus=event_bus,
                    )
                    await svc.set_default(
                        user_id,
                        payload.default_provider_id,
                        default_model=payload.default_model or None,
                    )
                    # default_model already synced inside set_default; skip the
                    # standalone write below so we don't double-write.
                    return await _finish_model_defaults(user_id, db)
            elif payload.default_model is not None:
                # Provider not being changed — still honour a standalone
                # default_model update (e.g. user picks a different model
                # on the same provider).
                await set_default_model(db, payload.default_model or None, user_id=user_id)

            if payload.default_effort is not None:
                # Empty string is treated as "reset to FALLBACK_EFFORT"
                # by ``set_default_effort``; concrete values are
                # validated against EFFORT_VALUES.
                await set_default_effort(db, payload.default_effort or None, user_id=user_id)
            return await _finish_model_defaults(user_id, db)
    except SystemProviderImmutable as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "system-managed provider is read-only",
                "provider_id": exc.provider_id,
            },
        ) from exc
    except ProviderNotFound as exc:
        raise HTTPException(status_code=404, detail={"reason": str(exc)}) from exc
    except NoAvailableProvider as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Model options (read model for the "pick a default model" pickers) ──


@router.get("/model-options")
async def get_model_options(
    user_id: str = Depends(get_current_user_id),
) -> ModelOptionsResponse:
    """Return the grouped, fully-resolved model options for the default-model
    pickers (onboarding's ConnectStep + Settings default-config card).

    Distinct from ``GET /v1/providers`` (provider management): every model here
    carries the ``runtimes`` it can run on + a preferred ``default_runtime``,
    same-named models inside a provider are disambiguated, and a system channel
    collapses to a single provider. The picker UIs render this verbatim — they
    no longer derive a runtime client-side.

    Subscription providers come back ``status="client_resolved"``: their CLI
    login state lives in the local keychain (invisible to the server), so the
    client fills availability in from its own ``checkCliLogin`` probe.
    """
    async with async_unit_of_work(commit=False) as db:
        defaults = await _read_model_defaults(db, user_id)
        svc = ProviderService(
            datastore=ProviderDatastore(db),
            event_bus=event_bus,
        )
        items = await svc.list_providers(user_id)
        # ADR-011: each model carries its declared ``runtimes`` (or None →
        # derive from compatible_protocols); the builder reads them straight off,
        # no per-source special-casing.
        inputs = [to_option_input(it) for it in items]
        current = CurrentDefault(
            runtime=defaults.default_runtime,
            provider_id=defaults.default_provider_id,
            model=defaults.default_model,
        )
        return build_model_options(inputs, current)


@router.get("")
async def get_settings(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, Any]:
    return await svc.get_app_settings()


@router.patch("")
async def patch_settings(
    updates: dict[str, Any],
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, Any]:
    return await svc.patch_app_settings(updates)


@router.get("/capabilities")
async def get_capabilities(
    svc: SettingsService = Depends(get_settings_service),
) -> CapabilitiesSnapshot:
    return await svc.derive_capabilities()


@router.post("/onboarding/complete")
async def complete_onboarding(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, bool]:
    await svc.patch_onboarding(completed=True)
    return {"completed": True}


@router.get("/shortcuts")
async def list_shortcuts(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, list[dict[str, Any]]]:
    return {"shortcuts": await svc.list_shortcuts()}


@router.patch("/shortcuts")
async def patch_shortcuts(
    updates: list[dict[str, Any]],
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, list[dict[str, Any]]]:
    return {"shortcuts": await svc.patch_shortcuts(updates)}


@router.post("/shortcuts/reset")
async def reset_shortcuts(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, list[dict[str, Any]]]:
    return {"shortcuts": await svc.reset_shortcuts()}


@router.get("/about")
async def get_about(
    svc: SettingsService = Depends(get_settings_service),
) -> AboutInfo:
    return await svc.get_about_info()


@router.post("/about/check-updates")
async def check_updates(
    svc: SettingsService = Depends(get_settings_service),
) -> UpdateCheckResult:
    return await svc.check_updates()
