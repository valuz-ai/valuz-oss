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
from valuz_agent.infra.asset_store import AssetStore, LocalAssetStore
from valuz_agent.infra.config import settings
from valuz_agent.infra.secret_store import AssetBackedSecretStore, SecretStorePort
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
        # Unified storage substrate for all host-domain "store it / read it"
        # data (credentials, uploaded files, derived blobs). OSS default is a
        # local filesystem store; an overlay swaps in an S3-backed one (keyed by
        # user_id + encryption) for a shared multi-process backend. It is an
        # object store with a file view — NOT a filesystem (no rename/seek);
        # POSIX workspaces are a separate concern (mounted into the sandbox).
        self.asset_store: AssetStore = LocalAssetStore(settings.data_dir)
        # API keys / OAuth tokens (BYOK creds, parser secrets, …) — the first
        # business built on the asset store, under the ``secrets/`` namespace.
        # A shared backend's asset store adds encryption at rest. Read access is
        # uniform: ``ext.secret_store.get(user_id, ref)``.
        self.secret_store: SecretStorePort = AssetBackedSecretStore(self.asset_store)


ext = Extensions()

__all__ = ["Extensions", "ext"]
