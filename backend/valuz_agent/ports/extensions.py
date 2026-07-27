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
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.integrations.sandbox_credential_hmac import (
    PerOwnerHmacSandboxCredentialVerifier,
)
from valuz_agent.ports.agent_lifecycle import AgentLifecycleHook, NoopAgentLifecycleHook
from valuz_agent.ports.automation_runtime import (
    AutomationRuntimePort,
    InProcessAutomationRuntime,
)
from valuz_agent.ports.billing import BillingPort, NoopBillingProvider
from valuz_agent.ports.cache import CachePort, FileCache
from valuz_agent.ports.connector_lifecycle import (
    ConnectorLifecycleHook,
    NoopConnectorLifecycleHook,
)
from valuz_agent.ports.connector_oauth_refresh import (
    ConnectorOAuthRefreshPort,
    LocalConnectorOAuthRefreshProvider,
)
from valuz_agent.ports.file_address import FileAddressResolverPort, LocalFileAddressResolver
from valuz_agent.ports.instructions import InstructionsPort
from valuz_agent.ports.llm_provider import LLMProvider, NoopLLMProvider
from valuz_agent.ports.model_defaults import ModelDefaultsPort, SettingsModelDefaults
from valuz_agent.ports.provider_policy import AllowAllProviderPolicy, ProviderPolicyPort
from valuz_agent.ports.resource_list_hook import NoopResourceListHook, ResourceListHook
from valuz_agent.ports.runtime_availability import RuntimeAvailabilityPort
from valuz_agent.ports.sandbox_allocator import BootSingletonAllocator, SandboxAllocatorPort
from valuz_agent.ports.sandbox_credential import SandboxCredentialVerifierPort
from valuz_agent.ports.sandbox_policy import AllowAllSandboxPolicy, SandboxPolicyPort
from valuz_agent.ports.skill_lifecycle import NoopSkillLifecycleHook, SkillLifecycleHook


class Extensions:
    """Singleton holding every replaceable port with its OSS default."""

    def __init__(self) -> None:
        # Automation business data and execution stay host-owned; deployments
        # may replace only the lifecycle/enqueue transport. OSS defaults to the
        # existing single-process tick + FIFO runner and failure monitor.
        self.automation_runtime: AutomationRuntimePort = InProcessAutomationRuntime()
        self.billing: BillingPort = NoopBillingProvider()
        # ADR-011: an overlay's single LLMProvider — contributes provider
        # rows (list) and resolves their credentials (resolve). OSS default
        # contributes nothing.
        self.llm_provider: LLMProvider = NoopLLMProvider()
        # Factory model defaults (runtime / model / provider / effort) for
        # users who never explicitly chose. OSS reads the Settings factory
        # fields (env-overridable); the commercial overlay layers
        # cloud-delivered per-distribution defaults on top.
        self.model_defaults: ModelDefaultsPort = SettingsModelDefaults()
        self.policy: ProviderPolicyPort = AllowAllProviderPolicy()
        # Gate for kernel-sandbox provisioning (plan entitlement + org
        # concurrency caps on the shared cloud host). OSS default allows every
        # provision (single-user desktop); the commercial overlay binds a
        # fail-closed policy. Separate from ``billing`` on purpose — gating is
        # not metering (see commercial ADR-012).
        self.sandbox_policy: SandboxPolicyPort = AllowAllSandboxPolicy()
        # Resolve which kernel serves a given owner (② control face). OSS default
        # returns "use the process/global kernel client" for everyone — single
        # in-process / single boot-sandbox behavior unchanged. The commercial
        # overlay binds a per-user pool allocator (one sandbox per user_id).
        self.sandbox_allocator: SandboxAllocatorPort = BootSingletonAllocator()
        # One opaque credential authenticates an untrusted sandbox to every
        # trusted host surface (built-in MCP + Data Service). OSS preserves the
        # existing per-owner HMAC tokens; managed editions may bind an async
        # database/cache-backed verifier for their workload credential.
        self.sandbox_credential_verifier: SandboxCredentialVerifierPort = (
            PerOwnerHmacSandboxCredentialVerifier()
        )
        self.resource_list_hook: ResourceListHook = NoopResourceListHook()
        self.skill_lifecycle: SkillLifecycleHook = NoopSkillLifecycleHook()
        self.agent_lifecycle: AgentLifecycleHook = NoopAgentLifecycleHook()
        self.connector_lifecycle: ConnectorLifecycleHook = NoopConnectorLifecycleHook()
        self.connector_oauth_refresh: ConnectorOAuthRefreshPort = (
            LocalConnectorOAuthRefreshProvider()
        )
        # Resolve a file's absolute path into a client-usable access address
        # (see docs/design/file-address-resolution.md). OSS default returns the
        # local absolute path (bundled desktop reads it directly); the commercial
        # overlay binds a storage-specific resolver (e.g. COS presigned URLs) for
        # the cloud deployment. The backend never proxies file bytes.
        self.file_address_resolver: FileAddressResolverPort = LocalFileAddressResolver()
        # Generic ephemeral cache (e.g. the connector OAuth PKCE handoff). OSS
        # default is a local file cache (single desktop process); the commercial
        # overlay swaps in a Redis-backed cache for the shared multi-process
        # backend.
        self.cache: CachePort = FileCache(fs_registry.cache_dir())
        # Owner-scoped byte/blob store. The OSS desktop build keeps the
        # existing on-disk layout under the local data root; shared deployments
        # can swap this with an object-store-backed implementation.
        self.asset_store: AssetStore = LocalAssetStore(fs_registry.shared_root())
        # The request auth middleware as a ``(cls, kwargs)`` tuple. Defaults to
        # the OSS ``AuthMiddleware``; the commercial overlay swaps in a subclass
        # (e.g. one that publishes extra per-request ContextVars with a reset
        # boundary). The app factory mounts ``cls`` — instantiated by Starlette
        # as ``cls(app, **kwargs)`` — so ``kwargs`` carries any constructor deps.
        self.auth_middleware: tuple[type, dict[str, Any]] = (AuthMiddleware, {})
        # Optional runtime-availability override. OSS asks the kernel; managed
        # deployments may bind a provider for their controlled runtime image.
        self.runtime_availability: RuntimeAvailabilityPort | None = None
        # Optional deployment-level instruction extensions. OSS leaves this
        # unbound so sessions start with the agent's own instructions.
        self.instructions: InstructionsPort | None = None


ext = Extensions()

__all__ = ["Extensions", "ext"]
