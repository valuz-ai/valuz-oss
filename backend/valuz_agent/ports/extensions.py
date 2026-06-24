"""Centralized extension-point container.

All replaceable ports live here as attributes on a single ``Extensions``
instance.  OSS boots with safe defaults (noop / allow-all / local); the
commercial overlay replaces individual attributes at startup::

    from valuz_agent.ports.extensions import ext

    ext.billing = BillingProvider(...)
    ext.auth_middleware = (CommercialAuthMiddleware, {...})

Read access is the same everywhere (routes, services, adapters, background
tasks) — no request object required::

    await ext.billing.check_budget(uid)
"""

from __future__ import annotations

from typing import Any

from valuz_agent.api.middleware import AuthMiddleware
from valuz_agent.infra.config import settings
from valuz_agent.infra.secret_store import FileSecretStore, SecretStorePort
from valuz_agent.ports.billing import BillingPort, NoopBillingProvider
from valuz_agent.ports.cache import CachePort, FileCache
from valuz_agent.ports.llm_provider import LLMProvider, NoopLLMProvider
from valuz_agent.ports.provider_policy import AllowAllProviderPolicy, ProviderPolicyPort
from valuz_agent.ports.resource_list_hook import NoopResourceListHook, ResourceListHook


class Extensions:
    """Singleton holding every replaceable port with its OSS default."""

    def __init__(self) -> None:
        self.billing: BillingPort = NoopBillingProvider()
        # ADR-011: an overlay's single LLMProvider — contributes provider
        # rows (list) and resolves their credentials (resolve). OSS default
        # contributes nothing.
        self.llm_provider: LLMProvider = NoopLLMProvider()
        self.policy: ProviderPolicyPort = AllowAllProviderPolicy()
        self.resource_list_hook: ResourceListHook = NoopResourceListHook()
        # Generic ephemeral cache (e.g. the connector OAuth PKCE handoff). OSS
        # default is a local file cache (single desktop process); the commercial
        # overlay swaps in a Redis-backed cache for the shared multi-process
        # backend.
        self.cache: CachePort = FileCache(settings.cache_dir)
        # The request auth middleware as a ``(cls, kwargs)`` tuple. Defaults to
        # the OSS ``AuthMiddleware``; the commercial overlay swaps in a subclass
        # (e.g. one that publishes extra per-request ContextVars with a reset
        # boundary). The app factory mounts ``cls`` — instantiated by Starlette
        # as ``cls(app, **kwargs)`` — so ``kwargs`` carries any constructor deps.
        self.auth_middleware: tuple[type, dict[str, Any]] = (AuthMiddleware, {})
        # API keys / OAuth tokens (BYOK provider creds, parser secrets, …). OSS
        # default is a local filesystem store (single desktop process). The
        # commercial overlay swaps in a shared store (e.g. Postgres-backed) so a
        # multi-process backend keeps every replica's view of a user's secrets
        # consistent — a local file store would strand BYOK keys on whichever
        # replica wrote them. Read access is uniform: ``ext.secret_store.get(ref)``.
        self.secret_store: SecretStorePort = FileSecretStore(settings.secrets_dir)


ext = Extensions()

__all__ = ["Extensions", "ext"]
