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
from valuz_agent.ports.billing import BillingPort, NoopBillingProvider
from valuz_agent.ports.llm_provider import LLMProviderRegistry, _InMemoryRegistry
from valuz_agent.ports.provider_policy import AllowAllProviderPolicy, ProviderPolicyPort
from valuz_agent.ports.resource_enhancer import NoopResourceEnhancer, ResourceListEnhancer


class Extensions:
    """Singleton holding every replaceable port with its OSS default."""

    def __init__(self) -> None:
        self.billing: BillingPort = NoopBillingProvider()
        self.llm_registry: LLMProviderRegistry = _InMemoryRegistry()
        self.policy: ProviderPolicyPort = AllowAllProviderPolicy()
        self.resource_enhancer: ResourceListEnhancer = NoopResourceEnhancer()
        # The request auth middleware as a ``(cls, kwargs)`` tuple. Defaults to
        # the OSS ``AuthMiddleware``; the commercial overlay swaps in a subclass
        # (e.g. one that publishes extra per-request ContextVars with a reset
        # boundary). The app factory mounts ``cls`` — instantiated by Starlette
        # as ``cls(app, **kwargs)`` — so ``kwargs`` carries any constructor deps.
        self.auth_middleware: tuple[type, dict[str, Any]] = (AuthMiddleware, {})


ext = Extensions()

__all__ = ["Extensions", "ext"]
