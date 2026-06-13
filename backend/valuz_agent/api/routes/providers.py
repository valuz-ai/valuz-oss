from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.api.deps import get_provider_service, require_current_user_id
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.discover import ModelDiscoveryError
from valuz_agent.modules.providers.errors import ProviderNotFound
from valuz_agent.modules.providers.service import (
    ConnectionTestResult,
    ProviderDescriptor,
    ProviderDetail,
    ProviderListItem,
    ProviderService,
    reset_providers,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import SystemProviderImmutable
from valuz_agent.ports.provider_policy import (
    ProviderWriteContext,
)

router = APIRouter(prefix="/v1/providers", tags=["providers"])


async def _enforce_provider_policy(user_id: str, action: str) -> None:
    """Ask the bound provider policy whether this write is allowed.

    OSS default permits everything; the commercial overlay denies user-provider
    writes when the caller's org has ``lock_member_custom_models`` enabled.
    """
    decision = await ext.policy.authorize_write(
        ProviderWriteContext(user_id=user_id, action=action, provider_source="user")  # type: ignore[arg-type]
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "reason": decision.reason
                or "creating custom models is disabled by your organization",
                "code": "provider.custom_models_locked",
            },
        )


def _system_immutable_409(exc: SystemProviderImmutable) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "reason": "system-managed provider is read-only",
            "provider_id": exc.provider_id,
        },
    )


def _normalize_base_url(value: str | None) -> str | None:
    """Empty / whitespace-only base_url normalizes to ``None``.

    Kernel V5+bba3014 made ``ModelProvider.base_url`` Optional; the
    runtime falls back to the SDK's ambient endpoint when it's ``None``.
    The host should forward ``None`` rather than an empty string so the
    fallback path actually fires.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


class ProviderCreateRequest(BaseModel):
    name: str
    provider_kind: str
    # ``None`` / empty / whitespace → first-party SDK endpoint fallback
    # (kernel V5+bba3014). The route layer normalizes via
    # ``_normalize_base_url`` before forwarding to the service.
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    # User-facing hyphen form (kernel V5+bba3014):
    # ``anthropic | openai-completion | openai-response | gemini``.
    # ``None`` falls through to provider_kind-based defaults.
    protocol: str | None = None
    # For ``compatible`` (custom) channels only: the user-supplied model
    # id list. The upstream may not expose ``/v1/models``, so the user
    # writes the list themselves and the create flow trusts it.
    models: list[str] | None = None


class ProviderUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    protocol: str | None = None
    auth_type: str | None = None  # api_key | oauth
    # ``compatible`` channels only — replaces the stored model_ids list.
    # Ignored for built-in channels (whose models come from descriptor /
    # /v1/models discovery).
    models: list[str] | None = None


class ValidateRequest(BaseModel):
    provider_kind: str
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    protocol: str | None = None


class ProbeModelsRequest(BaseModel):
    provider_kind: str
    api_key: str
    base_url: str | None = None
    protocol: str | None = None


class ProbeModelsResponse(BaseModel):
    models: list[str]
    suggested_default: str | None = None


class PingRequest(BaseModel):
    """Body for ``POST /v1/providers/ping``.

    Validates every model id in ``models`` by sending a 1-token chat /
    messages request. Used by the "Custom (compatible)" channel —
    built-in channels go through ``probe-models`` instead.

    Two modes:
    - **Add mode**: caller supplies ``api_key`` directly (the row
      doesn't exist yet, so there's nothing in secret_store to read).
    - **Edit mode**: caller passes ``provider_id`` and omits
      ``api_key``. The server fetches the stored key from
      ``secret_store`` — saves the user from re-typing a key that's
      already saved when they just want to ping a new model id.
    """

    base_url: str
    api_key: str | None = None
    protocol: str | None = None
    models: list[str]
    provider_id: str | None = None


class PingResponse(BaseModel):
    """Per-model outcome of a ``POST /v1/providers/ping`` call.

    ``ok`` is the verified-working subset (caller persists this).
    ``failed`` is ``[{model, reason}, ...]`` so the UI can show the
    user which ids didn't work and why, without aborting the whole
    batch on the first failure.
    """

    ok: list[str]
    failed: list[dict[str, str]]


class SetDefaultRequest(BaseModel):
    provider_id: str
    # Optional: also write this model id to the provider row's
    # ``default_model`` and sync the ``model.default_model`` app-setting
    # key so model_resolver and the Composer/SettingsPage stay in sync.
    default_model: str | None = None


@router.post("/validate")
async def validate_credentials(
    body: ValidateRequest,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> ConnectionTestResult:
    return await svc.validate_credentials(
        user_id,
        provider_kind=body.provider_kind,
        api_key=body.api_key,
        base_url=_normalize_base_url(body.base_url),
        default_model=body.default_model,
        protocol=body.protocol,
    )


@router.post("/ping")
async def ping_compatible(
    body: PingRequest,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> PingResponse:
    """Ping every model in ``body.models`` and return the verified subset.

    Custom (compatible) channels can't probe ``GET /v1/models`` because
    Anthropic-shape endpoints and many private proxies don't expose it.
    Instead, we POST a minimal chat / messages request per model — the
    round-trip success is what matters for the user's "can this answer
    my next prompt" question.

    Always returns 200 with ``{ok, failed}``. Per-model failures land
    in ``failed`` with a human-readable reason; the caller persists
    ``ok`` and surfaces ``failed`` as diagnostics. Hard failures (no
    models supplied, empty endpoint / key) raise 422.
    """
    if not body.models:
        raise HTTPException(status_code=422, detail={"reason": "至少需要 1 个模型 id"})

    # Edit-mode: caller didn't supply api_key — fetch the stored one
    # so the user doesn't have to re-type a key that's already saved
    # just to ping a freshly-added model id.
    effective_key = body.api_key
    if not effective_key and body.provider_id:
        try:
            effective_key = await svc.read_stored_api_key(user_id, body.provider_id)
        except ProviderNotFound as exc:
            raise HTTPException(status_code=404, detail={"reason": str(exc)}) from exc
    if not effective_key:
        raise HTTPException(
            status_code=422, detail={"reason": "请先填写 API Key 或先保存该模型再测试"}
        )

    try:
        batch = await svc.ping_compatible_batch(
            api_key=effective_key,
            base_url=_normalize_base_url(body.base_url),
            protocol=body.protocol,
            models=body.models,
        )
    except ModelDiscoveryError as exc:
        # Only fires for early validation (empty key / non-ASCII key /
        # etc.). Per-model failures don't abort the batch.
        raise HTTPException(status_code=422, detail={"reason": exc.reason}) from exc
    return PingResponse(
        ok=batch.ok,
        failed=[{"model": m, "reason": r} for m, r in batch.failed],
    )


@router.post("/probe-models")
async def probe_models(
    body: ProbeModelsRequest,
    svc: ProviderService = Depends(get_provider_service),
) -> ProbeModelsResponse:
    """Stateless model-list probe — does NOT write to the DB.

    Used by the add-provider dialog's [test] button to (a) verify the
    api_key against the provider's ``/v1/models`` endpoint and (b) hand
    the user a real model list to pick a default from before pressing
    save. The eventual ``POST /v1/providers`` call will re-run discovery
    server-side anyway, so this is a pure UX helper — no consistency
    obligation between the probe and the persisted row.
    """
    try:
        result = await svc.probe_models(
            provider_kind=body.provider_kind,
            api_key=body.api_key,
            base_url=_normalize_base_url(body.base_url),
            protocol=body.protocol,
        )
    except ModelDiscoveryError as exc:
        raise HTTPException(status_code=422, detail={"reason": exc.reason}) from exc
    return ProbeModelsResponse(**result)


@router.get("/config")
def list_provider_descriptors(
    svc: ProviderService = Depends(get_provider_service),
) -> dict[str, list[ProviderDescriptor]]:
    return {"providers": svc.list_provider_descriptors()}


@router.get("")
async def list_providers(
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> dict[str, list[ProviderListItem]]:
    return {"providers": await svc.list_providers(user_id)}


@router.get("/{provider_id}")
async def get_provider(
    provider_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> ProviderDetail:
    return await svc.get_provider(user_id, provider_id)


@router.post("", status_code=201)
async def create_provider(
    body: ProviderCreateRequest,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> ProviderDetail:
    """Create a provider channel.

    When ``api_key`` is supplied, the service synchronously probes
    ``<base_url>/v1/models`` (or the Anthropic equivalent) before
    persisting the row. The probe doubles as auth validation: a
    successful response confirms the key and hydrates ``model_ids``
    with whatever the upstream actually exposes. Failures (401 / 404 /
    timeout / malformed response) abort the create so the user never
    ends up with a stored channel they can't use.
    """
    await _enforce_provider_policy(user_id, "create")
    try:
        return await svc.create_provider(
            user_id,
            name=body.name,
            provider_kind=body.provider_kind,
            base_url=_normalize_base_url(body.base_url),
            api_key=body.api_key,
            default_model=body.default_model,
            protocol=body.protocol,
            models=body.models,
        )
    except ModelDiscoveryError as exc:
        # 422 — user-actionable: bad API key, wrong base_url, upstream
        # returned non-OpenAI-compatible /v1/models, etc.
        raise HTTPException(status_code=422, detail={"reason": exc.reason}) from exc


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: str,
    body: ProviderUpdateRequest,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> ProviderDetail:
    await _enforce_provider_policy(user_id, "update")
    try:
        return await svc.update_provider(
            user_id,
            provider_id,
            name=body.name,
            base_url=_normalize_base_url(body.base_url),
            api_key=body.api_key,
            default_model=body.default_model,
            protocol=body.protocol,
            auth_type=body.auth_type,
            models=body.models,
        )
    except SystemProviderImmutable as exc:
        raise _system_immutable_409(exc) from exc
    except ModelDiscoveryError as exc:
        # Model-batch validation failed during compatible-channel update.
        raise HTTPException(status_code=422, detail={"reason": exc.reason}) from exc


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> None:
    try:
        await svc.delete_provider(user_id, provider_id)
    except SystemProviderImmutable as exc:
        raise _system_immutable_409(exc) from exc


@router.post("/{provider_id}/test")
async def test_provider(
    provider_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> ConnectionTestResult:
    try:
        return await svc.test_provider(user_id, provider_id)
    except SystemProviderImmutable as exc:
        raise _system_immutable_409(exc) from exc


class DiscoverModelsResponse(BaseModel):
    provider_id: str
    discovered: list[str]
    merged: list[str]


@router.post("/{provider_id}/discover-models")
async def discover_provider_models(
    provider_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> DiscoverModelsResponse:
    """Probe the provider's upstream for the available model list.

    Calls ``GET <base_url>/v1/models`` (OpenAI-compatible) or the
    Anthropic equivalent, then merges the result into the provider's
    ``model_options``. The response carries both the freshly-discovered
    list and the merged set so the UI can highlight new entries.

    Returns 502 with a user-actionable reason when the upstream rejects
    the probe (timeout, 401, 404, malformed response, ...) -- the provider
    edit page should surface the reason and let the user fall back to
    typing model ids manually.
    """
    try:
        result = await svc.discover_models(user_id, provider_id)
    except SystemProviderImmutable as exc:
        raise _system_immutable_409(exc) from exc
    except ModelDiscoveryError as exc:
        raise HTTPException(status_code=502, detail={"reason": exc.reason}) from exc
    return DiscoverModelsResponse(**result)


@router.post("/{provider_id}/enable")
async def enable_provider(
    provider_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> ProviderDetail:
    """Mark an OAuth/subscription provider channel as enabled.

    Called after the user completes an out-of-band CLI login
    (``claude /login`` / ``codex /login``).  Sets ``enabled=True`` and,
    for ``auth_type="oauth"`` rows, sets
    ``credential_source="cli_keychain"`` to signal that credentials are
    now available in the CLI's keychain.  Idempotent — calling again on
    an already-enabled row returns the current state without error.

    Returns 404 when the provider id does not exist.
    Returns 409 when the provider is system-managed (read-only).
    """
    try:
        return await svc.enable_provider(user_id, provider_id)
    except SystemProviderImmutable as exc:
        raise _system_immutable_409(exc) from exc
    except ProviderNotFound as exc:
        raise HTTPException(status_code=404, detail={"reason": str(exc)}) from exc


@router.post("/default")
async def set_default(
    body: SetDefaultRequest,
    user_id: str = Depends(require_current_user_id),
    svc: ProviderService = Depends(get_provider_service),
) -> dict[str, str]:
    try:
        await svc.set_default(user_id, body.provider_id, default_model=body.default_model)
    except SystemProviderImmutable as exc:
        raise _system_immutable_409(exc) from exc
    return {"provider_id": body.provider_id, "message": "Default provider updated"}


class ResetRequest(BaseModel):
    """Body for ``POST /v1/providers/reset``.

    ``drop_table`` defaults to ``False`` so the safe behaviour ("clear
    rows and re-seed from current code") matches the empty body case.
    """

    drop_table: bool = False


@router.post("/reset")
async def reset(
    body: ResetRequest | None = None,
    user_id: str = Depends(require_current_user_id),
) -> dict[str, list[ProviderListItem]]:
    """Reset the model provider table.

    With no body or ``{}`` -- clears all rows and re-runs the boot seeders.
    With ``drop_table=true`` -- drops + recreates the SQL table too (use
    when a colleague's database has stale schema after pulling new code).
    """
    from valuz_agent.modules.providers.models import ProviderRow

    payload = body or ResetRequest()
    async with async_unit_of_work() as db:
        ds = ProviderDatastore(db)
        if payload.drop_table:
            # DDL drop/recreate runs through the async connection's
            # ``run_sync`` bridge — ``ProviderRow.__table__`` DDL is sync.
            def _recreate(connection: object) -> None:
                ProviderRow.__table__.drop(bind=connection, checkfirst=True)
                ProviderRow.__table__.create(bind=connection, checkfirst=True)

            await db.run_sync(lambda s: _recreate(s.connection()))
        providers = await reset_providers(ds, user_id, drop_table=False)
    return {"providers": providers}
