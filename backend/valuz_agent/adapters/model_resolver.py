"""Resolve which model a new kernel session should use.

The kernel freezes ``session.model`` at creation; valuz is responsible for
deciding what that string is. Resolution order:

1. **Explicit override**: the request body specifies ``model_id``.
2. **Provider default**: a ``valuz_provider`` row was selected (provider id
   in the request) — its ``default_model`` wins.
3. **Workspace default**: forthcoming. ``valuz_workspace_extension`` will gain
   a ``default_model_id`` column when we expose per-workspace model selection.
4. **Default provider's default model**: REP-107 — when nothing above applies,
   read ``is_default=True`` provider's ``default_model`` so the user's
   "settings -> default provider" pick actually drives entry points (skill creator,
   schedule worker, etc.) that don't pass a provider_id.
5. **Global fallback**: ``claude-sonnet-4-6``.

Note (kernel V5 post-MODEL_CATALOG): the kernel no longer maintains a
curated list. The model id is a free-form string forwarded to the SDK
behind whichever ``api_protocol`` the session declares. Valuz therefore
no longer normalises against any catalog — whatever the user picks is
what gets stamped into ``session.model``.

The ``request_runtime_id`` parameter is informational. It is **not**
used to pick the model id (that decision is owned by the provider +
explicit user input). It is recorded on the resolution so the API layer
can log ``model_id not in provider.model_ids`` warnings for diagnostics
without losing the runtime context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.modules.providers.datastore import ProviderDatastore

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"


class SystemProviderPort(Protocol):
    """Resolve a system-managed model provider (e.g. Valuz hosted key pool).

    OSS mode: not set (None). Commercial version injects via
    ``set_system_provider()`` at startup.
    """

    def resolve_system_provider(self, model_id: str) -> str | None: ...


_system_provider: SystemProviderPort | None = None


def set_system_provider(provider: SystemProviderPort | None) -> None:
    """Replace the system provider (called by commercial app at startup)."""
    global _system_provider
    _system_provider = provider


@dataclass(frozen=True)
class ModelResolution:
    model: str
    source: str  # "request" | "channel" | "workspace" | "fallback"
    # Free-form hint carried through from the request. ``None`` for
    # legacy callers that haven't been updated to pass an explicit
    # runtime. The value is **not** validated here — ``provider_resolver
    # .resolve_runtime_provider`` is the canonical validation point.
    runtime_hint: str | None = None
    # ``True`` when the resolved model_id is not present in the bound
    # channel's ``model_options``. The API layer surfaces this to the
    # client as a non-blocking warning header (custom model id) so the
    # user knows we didn't recognise the value but forwarded it to the
    # SDK anyway.
    custom_model_id: bool = False


async def resolve_model(
    *,
    providers: ProviderDatastore,
    request_model_id: str | None = None,
    request_provider_id: str | None = None,
    request_runtime_id: str | None = None,
    workspace_default_model_id: str | None = None,
) -> ModelResolution:
    """Pick the kernel model name for a new session.

    Pure with respect to the caller's transactional state — does no writes,
    just reads from ``ProviderDatastore``.
    """
    custom = False
    if request_model_id:
        if request_provider_id:
            provider = await providers.get_by_id(request_provider_id)
            if provider is not None:
                options = _model_ids_of(provider)
                if options and request_model_id not in options:
                    custom = True
                    logger.info(
                        "session model %r not in provider %r options %s — "
                        "treating as custom model id",
                        request_model_id,
                        request_provider_id,
                        sorted(options),
                    )
        return ModelResolution(
            request_model_id,
            source="request",
            runtime_hint=request_runtime_id,
            custom_model_id=custom,
        )

    if request_provider_id:
        provider = await providers.get_by_id(request_provider_id)
        if provider and provider.default_model:
            return ModelResolution(
                provider.default_model,
                source="provider",
                runtime_hint=request_runtime_id,
            )

    if workspace_default_model_id:
        return ModelResolution(
            workspace_default_model_id,
            source="workspace",
            runtime_hint=request_runtime_id,
        )

    # REP-107: honour Settings -> default provider — fall through to the
    # ``is_default`` provider's ``default_model`` before resorting to
    # the hardcoded global fallback. Without this, entry points that
    # don't pass provider_id (skill creator, scheduled worker, programmatic
    # callers) silently bypassed the user's pick and 422'd against the
    # ANTHROPIC_API_KEY-less ch-anthropic seed.
    default_row = await providers.get_default()
    if default_row and default_row.default_model:
        return ModelResolution(
            default_row.default_model,
            source="provider",
            runtime_hint=request_runtime_id,
        )

    # System provider fallback — commercial version injects a hosted key
    # pool via ``set_system_provider()``. OSS: ``_system_provider`` is None.
    if _system_provider is not None:
        system_model = _system_provider.resolve_system_provider(
            request_model_id or DEFAULT_MODEL,
        )
        if system_model:
            return ModelResolution(
                system_model,
                source="system",
                runtime_hint=request_runtime_id,
            )

    return ModelResolution(
        DEFAULT_MODEL,
        source="fallback",
        runtime_hint=request_runtime_id,
    )


def _model_ids_of(provider: object) -> set[str]:
    """Read ``provider.model_ids`` into a set of model ids.

    Tolerates malformed JSON — returns an empty set so the caller falls
    through to "treat as custom" rather than raising. The provider row is
    the source of truth; we don't second-guess its content.
    """
    raw = getattr(provider, "model_ids", None)
    if not raw:
        return set()
    try:
        import json

        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {item for item in parsed if isinstance(item, str)}


__all__ = [
    "DEFAULT_MODEL",
    "ModelResolution",
    "SystemProviderPort",
    "resolve_model",
    "set_system_provider",
]
