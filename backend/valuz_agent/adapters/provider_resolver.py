"""Compose the kernel model-provider wire schema from a valuz provider row.

Background
----------
Every kernel session row must carry a ``model_provider`` describing the
gateway the runtime will talk to: ``base_url``, ``api_key``,
``api_protocol``. The kernel uses ``api_protocol`` to validate the chosen
runtime against ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` and dispatch to
the right SDK code path.

Valuz presents the user with a "providers" abstraction (Anthropic /
OpenAI / DeepSeek / OpenRouter / Gemini / custom-compatible) and
resolves the api_key out of the provider row's ``secret_ref`` (a
user-pasted API key stored in the secret store). This module does the
provider → ``ModelProvider`` translation in one place so both session
creation and any future "test provider" path can share the logic.

Resolution rules
----------------
- ``api_protocol`` (kernel-side underscore form,
  ``anthropic | openai_completion | openai_response | gemini``):
  1. Provider.protocol when set — valuz user-facing hyphen form
     (``anthropic | openai-completion | openai-response | gemini``)
     maps 1:1 to the kernel underscore form.
  2. ``runtime_provider`` when the caller passes it — picks the
     allowlist's default for each runtime:
     ``claude_agent`` → ``"anthropic"``;
     ``codex`` → ``"openai_response"``;
     ``deepagents`` → ``"openai_completion"`` (chat completions API is
     the most common deepagents wire — gemini is opt-in via row protocol).
     This lets dual-protocol upstreams (DeepSeek, Zhipu/GLM, Moonshot,
     MiniMax) follow the chosen runtime without forcing the user to pick
     a protocol on the provider row.
  3. ``provider.provider_kind`` fallback: ``anthropic`` → ``"anthropic"``;
     everything else → ``"openai_completion"`` (the broadest compatibility
     wire — chat completions endpoint).
- ``api_key``: ``secret_ref`` → secret_store lookup.
- ``base_url`` (returned as ``str | None``):
  1. ``compatible`` provider_kind → ``provider.base_url`` verbatim,
     empty/whitespace normalized to ``None``.
  2. Built-in dual-protocol providers (descriptor has
     ``supports_protocol_selection=True``) → derived from the
     descriptor based on the resolved api_protocol; ``None`` if the
     descriptor has no matching URL (first-party SDK fallback).
  3. Anything else (single-protocol built-ins) →
     ``provider.base_url`` verbatim or ``None`` if absent.
  When ``None``, the kernel runtime falls back to the SDK's ambient
  endpoint (``ANTHROPIC_AUTH_TOKEN`` env for Claude, ``OPENAI_API_KEY``
  env for OpenAI, langchain defaults for DeepAgents). Valuz does not
  feed env vars into runtime subprocesses — first-party fallback works
  for ambient developer accounts but not for valuz-managed sessions, so
  in practice the host still relies on row-stored URLs for non-OAuth
  providers.

Errors
------
``ProviderNotResolvable`` is raised when api_key is missing — the
session router translates this to a 422 so the user gets a clear
"set API key for provider X" message rather than the kernel returning
``ValueError`` mid-turn. ``base_url`` is allowed to be ``None``
(first-party fallback path).
"""

from __future__ import annotations

import logging
from typing import Literal

from app.schemas import (
    ModelProviderInputSchema as ModelProvider,
)

# Side-effect import — surfaces ``src.core...`` on sys.path.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.ports.llm_provider import SystemLLMProvider, get_llm_registry

logger = logging.getLogger(__name__)

# Kernel-side underscore form. Mirrors
# ``src.core.types.ApiProtocol`` and
# ``src.runtimes.factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` keys.
ApiProtocol = Literal["anthropic", "openai_completion", "openai_response", "gemini"]
RuntimeProvider = Literal["claude_agent", "codex", "deepagents"]


class ProviderNotResolvable(RuntimeError):  # noqa: N818 — informational, not an Error subclass
    """Raised when provider + creds can't yield a usable ``ModelProvider``.

    Carries a ``reason`` string the API layer surfaces unchanged.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_VALID_RUNTIME_PROVIDERS: set[str] = {"claude_agent", "codex", "deepagents"}


# valuz user-facing hyphen form → kernel underscore form. The row stores
# hyphenated values (URL-like, user editable); the kernel API accepts
# underscored ones (Python identifier shape). Bare legacy "openai" is
# accepted as input and routed to ``openai_completion`` (the broadest
# OpenAI-compatible wire) so historical rows continue to work until the
# next provider edit.
_PROTOCOL_ROW_TO_KERNEL: dict[str, ApiProtocol] = {
    "anthropic": "anthropic",
    "openai-completion": "openai_completion",
    "openai-response": "openai_response",
    "gemini": "gemini",
    # Legacy bare openai — historical rows pre-protocol-split.
    "openai": "openai_completion",
}


# Default api_protocol per runtime when the row pins no protocol. Mirrors
# the FIRST entry of each ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` value
# so the runtime's primary wire is picked. Override by setting an explicit
# ``provider.protocol`` on the row.
_RUNTIME_TO_DEFAULT_PROTOCOL: dict[RuntimeProvider, ApiProtocol] = {
    "claude_agent": "anthropic",
    "codex": "openai_response",
    "deepagents": "openai_completion",
}


async def resolve_model_provider(
    *,
    provider_id: str,
    model_id: str,
    providers: ProviderDatastore,
    secrets: FileSecretStore,
    runtime_provider: RuntimeProvider | None = None,
) -> ModelProvider | None:
    """Translate a chosen provider + model id into a kernel ``ModelProvider``.

    Pure read — no writes. The returned ``ModelProvider`` is the value
    the caller stamps into ``Session.model_provider`` at creation time.

    ``runtime_provider`` is consumed (when non-None) by the api_protocol /
    base_url resolution so dual-protocol built-ins follow the chosen
    runtime without forcing the user to pick a protocol on the row. The
    session-service caller resolves ``runtime_provider`` first and then
    passes it in; legacy callers that omit it fall through to the
    provider_kind-based defaults.

    OAuth subscription providers (``auth_type="oauth"`` — ``claude /login``,
    ``codex /login``) return ``None``: the host has no API key to forward
    because credentials live in the corresponding CLI's keychain. The
    runtime SDK reads them out-of-band — when ``Session.model_provider``
    is ``None``, the kernel runtime skips the env overrides and the
    spawned CLI process picks up the user's ambient login token
    automatically.

    ``base_url`` may be ``None`` in the returned ``ModelProvider`` (first-
    party SDK fallback). Only ``api_key`` is strictly required.
    """
    # Overlay-contributed system providers (ADR-007) live in a
    # process-level registry, not the user table. Check first so an
    # overlay can shadow a colliding user id deterministically.
    descriptor = get_llm_registry().get(provider_id)
    if descriptor is not None:
        return _resolve_system_provider(descriptor)

    provider = await providers.get_by_id(provider_id)
    if provider is None:
        raise ProviderNotResolvable(f"provider {provider_id!r} not found")

    if not provider.enabled:
        raise ProviderNotResolvable(f"provider {provider.name!r} is disabled")

    if provider.auth_type == "oauth":
        # Subscription provider — credentials are CLI-managed; no
        # ModelProvider needed. Runtime selection still works because
        # ``Session.runtime_provider`` is set independently from
        # ``provider_resolver.resolve_runtime_provider``.
        return None

    api_protocol = _resolve_api_protocol(provider, model_id, runtime_provider)
    base_url = _resolve_base_url(provider, api_protocol)

    api_key = _resolve_api_key(provider, secrets)
    if not api_key:
        raise ProviderNotResolvable(
            f"provider {provider.name!r} has no credentials — set an API key"
        )

    return ModelProvider(
        base_url=base_url,
        api_key=api_key,
        api_protocol=api_protocol,
    )


def _resolve_system_provider(descriptor: SystemLLMProvider) -> ModelProvider:
    """Translate a registry descriptor into a kernel ``ModelProvider``.

    The descriptor owns its own credential lifecycle — the overlay's
    ``headers()`` callable produces a per-resolve dict; we pull the
    bearer token out for ``ModelProvider.api_key``. The gateway is
    responsible for token validation, optional headers
    (``Idempotency-Key`` etc.), and producing useful errors when the
    JWT is missing/expired.
    """
    if not descriptor.enabled():
        reason = descriptor.unavailable_reason() or "disabled"
        raise ProviderNotResolvable(f"provider {descriptor.id!r} unavailable: {reason}")

    headers = descriptor.headers()
    authorization = headers.get("Authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip()
    if not bearer:
        raise ProviderNotResolvable(
            f"provider {descriptor.id!r} has no bearer token — overlay headers() "
            f"returned no Authorization header"
        )

    api_protocol = descriptor.api_protocol
    if api_protocol not in {"anthropic", "openai_completion", "openai_response", "gemini"}:
        raise ProviderNotResolvable(
            f"provider {descriptor.id!r} declared unknown api_protocol {api_protocol!r}"
        )

    return ModelProvider(
        base_url=descriptor.api_base or None,
        api_key=bearer,
        api_protocol=api_protocol,  # type: ignore[arg-type]
    )


def _resolve_api_key(
    provider: ProviderRow,
    secrets: FileSecretStore,
) -> str | None:
    """Pull the api_key from the provider's ``secret_ref`` credential source."""
    if provider.credential_source == "secret_ref" and provider.secret_ref:
        return secrets.get(provider.secret_ref)

    return None


def _resolve_api_protocol(
    provider: ProviderRow,
    model_id: str,
    runtime_provider: RuntimeProvider | None,
) -> ApiProtocol:
    """Decide which kernel wire protocol the session should use.

    Priority: explicit row protocol → runtime-driven default →
    provider_kind fallback. The runtime-driven step is what lets
    DeepSeek/GLM/Moonshot/MiniMax follow the user's runtime pick without
    ever storing a protocol on the row.
    """
    # Explicit ``protocol`` field wins. Maps valuz hyphen form to kernel
    # underscore form (and accepts legacy bare "openai" for back-compat).
    row_protocol = (provider.protocol or "").strip().lower()
    if row_protocol:
        mapped = _PROTOCOL_ROW_TO_KERNEL.get(row_protocol)
        if mapped is not None:
            return mapped
        # Unknown row protocol — log and fall through to defaults rather
        # than hard-fail; the factory's ``validate_api_protocol`` will
        # surface the mismatch loudly if the fallback can't satisfy the
        # runtime's allowlist.
        logger.warning(
            "provider %s has unknown protocol %r; falling back to runtime default",
            provider.name,
            row_protocol,
        )

    # Runtime-driven default — only kicks in when the row hasn't pinned
    # a (valid) protocol. Lets dual-protocol upstreams ride the runtime
    # pick.
    if runtime_provider is not None:
        return _RUNTIME_TO_DEFAULT_PROTOCOL[runtime_provider]

    if provider.provider_kind == "anthropic":
        return "anthropic"
    # Broadest OpenAI-compatible wire — chat completions endpoint.
    return "openai_completion"


def _resolve_base_url(provider: ProviderRow, api_protocol: ApiProtocol) -> str | None:
    """Pick the base URL the kernel should call.

    Returns ``None`` when the row has no explicit URL — the kernel
    runtime then falls back to the SDK's ambient endpoint. Empty /
    whitespace-only values normalize to ``None`` so the host never
    forwards a junk URL.

    For built-in dual-protocol providers (``supports_protocol_selection=True``)
    the endpoint is descriptor-derived from the resolved api_protocol, so
    DeepSeek/GLM/Moonshot/MiniMax automatically point at the right shape
    when the user switches runtimes. For ``compatible`` (custom) channels
    and single-protocol built-ins, we trust the row's stored URL.
    """
    row_base_url: str | None = (provider.base_url or "").strip() or None

    # Compatible / custom channel: user told us where to point (or left
    # blank for first-party fallback).
    if provider.provider_kind == "compatible":
        return row_base_url

    # Lazy import to avoid a module-load cycle: providers/service.py
    # already imports from this module's neighbours at boot.
    from valuz_agent.modules.providers.service import _PROVIDER_MAP

    descriptor = _PROVIDER_MAP.get(provider.provider_kind)
    if descriptor is None or not descriptor.supports_protocol_selection:
        # Single-protocol built-ins (Anthropic / OpenAI / OpenRouter /
        # Gemini) keep the row value as-is.
        return row_base_url

    # Dual-protocol built-in: derive from descriptor. For protocols the
    # descriptor doesn't pin, fall back to the row (or None).
    if api_protocol == "anthropic":
        if descriptor.anthropic_base_url:
            return descriptor.anthropic_base_url
        if descriptor.default_base_url:
            return f"{descriptor.default_base_url.rstrip('/')}/anthropic"
        return row_base_url
    return descriptor.default_base_url or row_base_url


async def resolve_runtime_provider(
    *,
    provider_id: str,
    model_id: str,
    providers: ProviderDatastore,
    request_runtime_id: str | None = None,
) -> RuntimeProvider:
    """Decide which kernel runtime drives this session.

    Resolution order (highest priority first):

    1. ``request_runtime_id`` — user-supplied via the session-creation API.
       Must be one of ``claude_agent`` / ``codex`` / ``deepagents``;
       otherwise raises ``ProviderNotResolvable``. This is the path the
       picker in the UI uses to override provider defaults.
    2. ``derive_runtime_provider(provider_kind)`` — built-in providers are
       seeded with a platform-correct provider_kind so the user never had
       to pick before this feature.
    3. Final fallback: ``deepagents``. Reached only if a session is
       created against a deleted provider (resolve_model_provider would
       have already raised in normal flow).
    """
    from valuz_agent.modules.providers.service import derive_runtime_provider as _derive

    if request_runtime_id is not None:
        if request_runtime_id not in _VALID_RUNTIME_PROVIDERS:
            raise ProviderNotResolvable(
                f"unknown runtime {request_runtime_id!r}; expected one of "
                f"{sorted(_VALID_RUNTIME_PROVIDERS)}"
            )
        return request_runtime_id  # type: ignore[return-value]

    # System provider (ADR-007): the overlay descriptor pins the runtime
    # directly. Check the registry before the user table so a colliding
    # id resolves consistently with ``resolve_model_provider``.
    descriptor = get_llm_registry().get(provider_id)
    if descriptor is not None:
        if descriptor.runtime_provider not in _VALID_RUNTIME_PROVIDERS:
            raise ProviderNotResolvable(
                f"system provider {provider_id!r} declared unknown runtime "
                f"{descriptor.runtime_provider!r}"
            )
        return descriptor.runtime_provider  # type: ignore[return-value]

    provider = await providers.get_by_id(provider_id)
    if provider is None:
        return "deepagents"

    return _derive(provider.provider_kind)  # type: ignore[return-value]


__all__ = [
    "ApiProtocol",
    "ModelProvider",
    "ProviderNotResolvable",
    "RuntimeProvider",
    "resolve_model_provider",
    "resolve_runtime_provider",
]
