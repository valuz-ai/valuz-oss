"""Shared member/lead session-build helpers (ADR-023).

These are pure, stateless helpers used by the dispatcher + lifecycle services
when they build kernel sessions: resolving the provider/secret deps that
``build_member_session`` needs, and a credential pre-flight that fails fast
when a built session has no usable model provider.

Kept as module-level functions (not service methods) to avoid a service-on-
service edge — both DispatcherService and LifecycleService import them
directly. ``orchestrator.py`` re-exports ``_credential_gap`` so existing test
imports (``from ...orchestrator import _credential_gap``) keep working.
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from typing import Any

from valuz_agent.adapters import kernel_store

logger = logging.getLogger(__name__)


def _provider_resolver_deps(db: Any) -> dict[str, Any]:
    """Build the (providers, secrets) deps so build_member_session
    can resolve a per-agent pinned provider into the run's model_provider."""
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.secret_store import FileSecretStore
    from valuz_agent.modules.providers.datastore import ProviderDatastore

    return {
        "providers": ProviderDatastore(db),
        "secrets": FileSecretStore(settings.secrets_dir),
    }


# ---------------------------------------------------------------------------
# Credential pre-flight
# ---------------------------------------------------------------------------


async def _credential_gap(session: Any, agent_slug: str, *, db: Any | None = None) -> str | None:
    """Return a clear reason when a built session has no usable credentials.

    Credentials are funnelled through the provider system: a session's
    resolved ``model_provider`` (base_url/api_key/protocol) is the single
    source of truth (see backend/CLAUDE.md — the host does not read LLM keys
    from process env). When ``model_provider`` is None the run would only fail
    mid-turn with a cryptic SDK "Not logged in · Please run /login", so we
    detect it up front and surface *why* (no model provider configured).

    Exception: OAuth subscription providers (``claude /login`` /
    ``codex /login``) deliberately resolve to ``model_provider=None`` —
    their credentials live in the CLI's keychain and the runtime SDK
    reads them out-of-band. The pinned provider's ``auth_type`` tells us
    which case we're in, so we don't false-positive on a perfectly valid
    subscription setup. When ``db`` is omitted (legacy callers) we fall
    back to the strict check.

    Returns ``None`` when a provider resolved (or is an OAuth
    subscription), else a human-readable reason.
    """
    if getattr(session, "model_provider", None) is not None:
        return None

    # session.model_provider is None — could be (a) no provider pinned,
    # or (b) pinned an OAuth subscription provider. Distinguish by
    # loading the agent and checking the pinned provider's auth_type.
    if db is not None:
        try:
            from valuz_agent.modules.providers.datastore import ProviderDatastore

            agent_id = getattr(session, "agent_id", None)
            if agent_id:
                agent = await kernel_store.load_agent(agent_id)
                provider_id = (
                    (getattr(agent, "metadata", None) or {}).get("provider_id")
                    if agent is not None
                    else None
                )
                if provider_id:
                    provider = await ProviderDatastore(db).get_by_id(provider_id)
                    if provider is not None and provider.auth_type == "oauth":
                        # CLI-managed credentials — model_provider=None is
                        # the expected resolver output, not a gap.
                        return None
        except Exception:
            logger.warning(
                "credential_gap: OAuth-provider lookup failed for agent %s — "
                "falling back to strict check.",
                agent_slug,
                exc_info=True,
            )

    runtime = getattr(session, "runtime_provider", "") or ""
    return (
        f"agent '{agent_slug}' has no model provider configured "
        f"(runtime '{runtime}'). Pin a model provider on the agent before "
        f"dispatching."
    )
