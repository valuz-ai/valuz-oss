"""Centralized extension-point container.

All replaceable ports live here as attributes on a single ``Extensions``
instance.  OSS boots with safe defaults (noop / allow-all / local); the
commercial overlay replaces individual attributes at startup::

    from valuz_agent.ports.extensions import ext

    ext.billing = BillingProvider(...)
    ext.identity = SaasIdentityResolver(...)

Read access is the same everywhere (routes, services, adapters, background
tasks) — no request object required::

    await ext.billing.check_budget(uid)
"""

from __future__ import annotations

from valuz_agent.integrations.identity_local import LocalIdentityResolver
from valuz_agent.ports.billing import BillingPort, NoopBillingProvider
from valuz_agent.ports.identity import AuthHook, IdentityResolver
from valuz_agent.ports.llm_provider import LLMProviderRegistry, _InMemoryRegistry
from valuz_agent.ports.provider_policy import AllowAllProviderPolicy, ProviderPolicyPort
from valuz_agent.ports.resource_enhancer import NoopResourceEnhancer, ResourceListEnhancer


class Extensions:
    """Singleton holding every replaceable port with its OSS default."""

    def __init__(self) -> None:
        self.billing: BillingPort = NoopBillingProvider()
        self.identity: IdentityResolver = LocalIdentityResolver() # set by boot or overlay
        self.llm_registry: LLMProviderRegistry = _InMemoryRegistry()
        self.policy: ProviderPolicyPort = AllowAllProviderPolicy()
        self.resource_enhancer: ResourceListEnhancer = NoopResourceEnhancer()
        self.auth_hook: AuthHook | None = None


ext = Extensions()

__all__ = ["Extensions", "ext"]
