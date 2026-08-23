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
from valuz_agent.ports.a2ui_components import A2UIComponentRegistry
from valuz_agent.ports.agent_lifecycle import AgentLifecycleHook, NoopAgentLifecycleHook
from valuz_agent.ports.automation_runtime import (
    AutomationRuntimePort,
    InProcessAutomationRuntime,
)
from valuz_agent.ports.billing import BillingPort, NoopBillingProvider
from valuz_agent.ports.cache import CachePort, FileCache
from valuz_agent.ports.capability_policy import HostCapabilityPolicyPort
from valuz_agent.ports.citation_documents import CitationDocumentResolverPort
from valuz_agent.ports.citation_quality import (
    CitationQualityPolicyRegistry,
)
from valuz_agent.ports.connector_lifecycle import (
    ConnectorLifecycleHook,
    NoopConnectorLifecycleHook,
)
from valuz_agent.ports.connector_oauth_refresh import (
    ConnectorOAuthRefreshPort,
    LocalConnectorOAuthRefreshProvider,
)
from valuz_agent.ports.docs_dispatch import (
    DocsReindexDispatcher,
    DocsScopeContributor,
    NoopReindexDispatcher,
    no_extra_documents,
)
from valuz_agent.ports.docs_runtime import DocsRuntimeFactory, default_docs_runtime
from valuz_agent.ports.document_research import DocumentResearchProviderPort
from valuz_agent.ports.file_address import FileAddressResolverPort, LocalFileAddressResolver
from valuz_agent.ports.instructions import (
    GlobalInstructionsPort,
    OSSGlobalInstructionsProvider,
)
from valuz_agent.ports.llm_provider import LLMProvider, NoopLLMProvider
from valuz_agent.ports.mcp_always_on import AlwaysOnMcpServerSpec
from valuz_agent.ports.message_context import MessageContextProviderPort
from valuz_agent.ports.model_defaults import ModelDefaultsPort, SettingsModelDefaults
from valuz_agent.ports.provider_policy import AllowAllProviderPolicy, ProviderPolicyPort
from valuz_agent.ports.resource_list_hook import NoopResourceListHook, ResourceListHook
from valuz_agent.ports.runtime_availability import RuntimeAvailabilityPort
from valuz_agent.ports.runtime_resource import (
    LocalManagedAgentMutationPort,
    LocalManagedConnectorMutationPort,
    LocalRuntimeResourceApplyPort,
    ManagedAgentMutationPort,
    ManagedConnectorMutationPort,
    RuntimeResourceApplyPort,
)
from valuz_agent.ports.runtime_turn_context import (
    NoopRuntimeTurnContextContributor,
    RuntimeTurnContextContributor,
)
from valuz_agent.ports.sandbox_allocator import BootSingletonAllocator, SandboxAllocatorPort
from valuz_agent.ports.sandbox_credential import SandboxCredentialVerifierPort
from valuz_agent.ports.sandbox_maintenance import (
    SandboxMaintenancePort,
    UnsupportedSandboxMaintenancePort,
)
from valuz_agent.ports.sandbox_policy import AllowAllSandboxPolicy, SandboxPolicyPort
from valuz_agent.ports.skill_lifecycle import NoopSkillLifecycleHook, SkillLifecycleHook
from valuz_agent.ports.skill_runtime import (
    DiscoverAndExecuteExternalSkills,
    ExternalSkillDiscoveryPolicy,
)
from valuz_agent.ports.ui_artifact import UiArtifactSinkPort


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
        # An overlay may attach opaque, non-persisted context to a runtime
        # turn. OSS never interprets its keys or values.
        self.runtime_turn_context: RuntimeTurnContextContributor = (
            NoopRuntimeTurnContextContributor()
        )
        self.resource_list_hook: ResourceListHook = NoopResourceListHook()
        self.skill_lifecycle: SkillLifecycleHook = NoopSkillLifecycleHook()
        self.agent_lifecycle: AgentLifecycleHook = NoopAgentLifecycleHook()
        self.connector_lifecycle: ConnectorLifecycleHook = NoopConnectorLifecycleHook()
        # Runtime Resource Control v10 seams. OSS remains local/pass-through;
        # commercial editions replace these attributes at startup.
        self.managed_agent_mutation: ManagedAgentMutationPort = LocalManagedAgentMutationPort()
        self.managed_connector_mutation: ManagedConnectorMutationPort = (
            LocalManagedConnectorMutationPort()
        )
        self.runtime_resource_apply: RuntimeResourceApplyPort = LocalRuntimeResourceApplyPort()
        self.external_skill_discovery_policy: ExternalSkillDiscoveryPolicy = (
            DiscoverAndExecuteExternalSkills()
        )
        self.sandbox_maintenance: SandboxMaintenancePort = UnsupportedSandboxMaintenancePort()
        self.connector_oauth_refresh: ConnectorOAuthRefreshPort = (
            LocalConnectorOAuthRefreshProvider()
        )
        # Resolve a citation's stable document identity after the route has
        # reloaded its canonical message under the current owner. ``None`` uses
        # the OSS document-library adapter; editions may bind a connector or
        # SaaS-aware resolver without changing the message/reader contract.
        self.citation_document_resolver: CitationDocumentResolverPort | None = None
        # Connector-owned documents can participate in the same summary and
        # locked Q&A workspace as local library documents. Editions resolve
        # their stable document identity and provider-native summary here.
        self.document_research_provider: DocumentResearchProviderPort | None = None
        # Fixed-order OSS + commercial + distribution policy layers. Overlays
        # register their own slot; no later edition can replace an earlier
        # provider. The effective declarative snapshot is re-stamped before
        # every turn so user-authored session metadata cannot weaken the gate.
        self.citation_quality_policies = CitationQualityPolicyRegistry()
        # A2UI component catalog layers (same fixed commercial → distribution
        # order). Editions register the catalog text their frontend build
        # generated; the generate_ui prompt assembles from it per call, so a
        # registration is live without a process restart. See
        # docs/design/a2ui-dynamic-components.md.
        self.a2ui_components = A2UIComponentRegistry()
        # Resolve a file's absolute path into a client-usable access address
        # (see docs/design/file-address-resolution.md). OSS default returns the
        # local absolute path (bundled desktop reads it directly); the commercial
        # overlay binds a storage-specific resolver (e.g. COS presigned URLs) for
        # the cloud deployment. The backend never proxies file bytes.
        self.file_address_resolver: FileAddressResolverPort = LocalFileAddressResolver()
        # Builds the document-retrieval runtime for one owner. OSS default is
        # ripgrep over that owner's preview markdown; a deployment whose
        # documents are indexed in an external service binds its own factory.
        # Per-owner rather than a singleton because the OSS default is scoped
        # to the owner's preview directory.
        self.docs_runtime_factory: DocsRuntimeFactory = default_docs_runtime
        # Who parses queued documents. OSS default declines, leaving the
        # in-process daemon thread; a scaled deployment binds a dispatcher that
        # hands them to a worker so the work survives a web restart.
        self.docs_reindex_dispatcher: DocsReindexDispatcher = NoopReindexDispatcher()
        # Documents a caller may read beyond their own — shared libraries.
        # Additive, and skipped for document-research sessions whose scope is
        # exact by construction.
        self.docs_scope_contributor: DocsScopeContributor = no_extra_documents
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
        # One complete, owner-aware product prompt for the active
        # distribution. Managed editions replace this provider; they do not
        # append to the OSS prompt.
        self.global_instructions: GlobalInstructionsPort = OSSGlobalInstructionsProvider()
        # Per-turn message context providers (list semantics — editions append,
        # they do not replace). Each provider turns the client-declared
        # ``host_ref`` of a message into one extra additional-context section
        # after resolving it server-side. OSS registers none; a failing
        # provider is skipped so it can never block a turn.
        self.message_context_providers: list[MessageContextProviderPort] = []
        # Host-scoped capability policies (list semantics — editions append).
        # A hosted turn asks each policy for capability overrides (e.g. task
        # coverage off on an edition's workbench conversations); the first
        # non-None answer wins and is stamped on the session so host_ref-less
        # turns (queue drains, resumes) keep the hosted decision.
        self.host_capability_policies: list[HostCapabilityPolicyPort] = []
        # Edition-owned always-on internal MCP servers (list semantics —
        # editions append). The capability resolver appends them to every
        # session after the five built-ins, with the same internal credential
        # headers and timeout; reserved built-in names are skipped.
        self.always_on_mcp_specs: list[AlwaysOnMcpServerSpec] = []
        # Generated-UI artifact sinks (list semantics — editions append).
        # ``generate_ui`` offers every successful generation to each sink in
        # order and appends the FIRST returned receipt to its tool result;
        # a failing sink is skipped and never breaks generation.
        self.ui_artifact_sinks: list[UiArtifactSinkPort] = []

    @property
    def instructions(self) -> GlobalInstructionsPort:
        """Deprecated attribute alias for overlays migrating to the new name."""
        return self.global_instructions

    @instructions.setter
    def instructions(self, provider: GlobalInstructionsPort) -> None:
        self.global_instructions = provider


ext = Extensions()

__all__ = ["Extensions", "ext"]
