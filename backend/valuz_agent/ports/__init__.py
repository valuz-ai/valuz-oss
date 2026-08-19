"""Cross-cutting port protocols still in use after the V5 migration.

Surviving ports:
- ``BillingPort``: metering, budget checks, and balance queries.
- ``DocsRuntimePort``: read-only document index used by the docs domain.
- ``ParserBackend``: pluggable file parser the docs domain feeds.
- ``ToolProvider``: legacy tool-registration interface still used by the
  bundled CoreToolProvider; tool wiring into the kernel runtime happens via
  the kernel's own MCP/SDK plumbing now, but the providers package still
  exposes its tools through this protocol for inventory purposes.

Removed in Slice 4c (replaced by V5 kernel internals):
- ``runtime.RuntimePort`` — kernel ``src.core.runtime_port`` is the new contract.
- ``skill_source.SkillSource`` — was a thin wrapper that skills providers
  implemented; with the harness gone the skill providers are imported
  directly where needed.
"""

from valuz_agent.ports.agent_lifecycle import (
    AgentLifecycleHook,
    AgentSaveOrigin,
    NoopAgentLifecycleHook,
    get_agent_lifecycle_hook,
    set_agent_lifecycle_hook,
)
from valuz_agent.ports.billing import (
    Balance,
    BillingPort,
    BudgetStatus,
    MeterEvent,
    NoopBillingProvider,
    get_billing_port,
    set_billing_port,
)
from valuz_agent.ports.connector_lifecycle import (
    ConnectorLifecycleHook,
    ConnectorOAuthSnapshot,
    ConnectorSaveOrigin,
    ConnectorSecretSnapshot,
    NoopConnectorLifecycleHook,
    get_connector_lifecycle_hook,
    set_connector_lifecycle_hook,
)
from valuz_agent.ports.connector_oauth_refresh import (
    ConnectorOAuthRefreshPort,
    LocalConnectorOAuthRefreshProvider,
    NoopConnectorOAuthRefreshProvider,
    get_connector_oauth_refresh_port,
    set_connector_oauth_refresh_port,
)
from valuz_agent.ports.docs_runtime import DocsRuntimePort
from valuz_agent.ports.file_address import (
    FileAddressResolverPort,
    LocalFileAddressResolver,
    ResolvedAddress,
    get_file_address_resolver,
    set_file_address_resolver,
)
from valuz_agent.ports.llm_provider import (
    LLMProvider,
    NoopLLMProvider,
    ResolvedCredential,
    SystemProviderImmutable,
)
from valuz_agent.ports.mcp_catalog import McpCatalogPort
from valuz_agent.ports.parser_backend import ParserBackend
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ConfigField,
    ParserCapabilityNotReady,
    ParserPlugin,
    ParserPluginConfig,
    ParserPluginDescriptor,
    ParserPluginMode,
    PluginCapability,
    SecretResolver,
    SetupRequirement,
    SplitPolicy,
)
from valuz_agent.ports.provider_policy import (
    AllowAllProviderPolicy,
    PolicyDecision,
    ProviderPolicyPort,
    ProviderWriteContext,
    get_provider_policy,
    set_provider_policy,
)
from valuz_agent.ports.runtime_resource import (
    LocalManagedAgentMutationPort,
    LocalManagedConnectorMutationPort,
    LocalRuntimeResourceApplyPort,
    ManagedAgentMutationPort,
    ManagedConnectorMutationPort,
    ManagedMutationResult,
    RuntimeResourceApplyPort,
    RuntimeResourceContractError,
    ensure_managed_root_containment,
    require_sync_apply_origin,
    validate_skill_reference,
)
from valuz_agent.ports.runtime_turn_context import (
    NoopRuntimeTurnContextContributor,
    RuntimeTurnContextContributor,
    get_runtime_turn_context_contributor,
    set_runtime_turn_context_contributor,
)
from valuz_agent.ports.sandbox_maintenance import (
    SandboxMaintenanceLease,
    SandboxMaintenancePort,
    SandboxMaintenanceProbe,
    SandboxMaintenanceUnsupported,
    SandboxTerminalReceipt,
    UnsupportedSandboxMaintenancePort,
)
from valuz_agent.ports.sandbox_policy import (
    AllowAllSandboxPolicy,
    SandboxDecision,
    SandboxPolicyPort,
    SandboxProvisionContext,
    authorize_sandbox_provision,
    get_sandbox_policy,
    set_sandbox_policy,
)
from valuz_agent.ports.skill_lifecycle import (
    NoopSkillLifecycleHook,
    SkillLifecycleHook,
    SkillSaveOrigin,
    get_skill_lifecycle_hook,
    set_skill_lifecycle_hook,
)
from valuz_agent.ports.skill_registry import SkillRegistryPort
from valuz_agent.ports.skill_runtime import (
    CatalogOnlyUntilClaimed,
    DiscoverAndExecuteExternalSkills,
    DiscoveryDecision,
    ExecutionResourceGate,
    ExecutionResourceResolver,
    ExternalSkillClaimReservationGate,
    InMemoryExternalSkillClaimReservationGate,
    SkillTreeMutationPort,
    TurnBoundaryObservedHashHook,
    validate_managed_skill_path,
)
from valuz_agent.ports.tool_provider import ToolProvider

__all__ = [
    "AgentLifecycleHook",
    "AgentSaveOrigin",
    "Balance",
    "BillingPort",
    "BudgetStatus",
    "CapabilityStatus",
    "ConfigField",
    "ConnectorLifecycleHook",
    "ConnectorOAuthRefreshPort",
    "ConnectorOAuthSnapshot",
    "ConnectorSaveOrigin",
    "ConnectorSecretSnapshot",
    "DocsRuntimePort",
    "FileAddressResolverPort",
    "LocalFileAddressResolver",
    "LocalConnectorOAuthRefreshProvider",
    "McpCatalogPort",
    "MeterEvent",
    "NoopBillingProvider",
    "NoopAgentLifecycleHook",
    "NoopConnectorLifecycleHook",
    "NoopConnectorOAuthRefreshProvider",
    "NoopLLMProvider",
    "NoopSkillLifecycleHook",
    "ParserBackend",
    "ParserCapabilityNotReady",
    "ParserPlugin",
    "ParserPluginConfig",
    "ParserPluginDescriptor",
    "ParserPluginMode",
    "PluginCapability",
    "PolicyDecision",
    "AllowAllProviderPolicy",
    "AllowAllSandboxPolicy",
    "LLMProvider",
    "ProviderPolicyPort",
    "ProviderWriteContext",
    "ResolvedCredential",
    "SandboxDecision",
    "SandboxPolicyPort",
    "SandboxProvisionContext",
    "SecretResolver",
    "SetupRequirement",
    "SkillRegistryPort",
    "SkillLifecycleHook",
    "SkillSaveOrigin",
    "ResolvedAddress",
    "SplitPolicy",
    "SystemProviderImmutable",
    "ToolProvider",
    "CatalogOnlyUntilClaimed",
    "DiscoverAndExecuteExternalSkills",
    "DiscoveryDecision",
    "ExternalSkillClaimReservationGate",
    "InMemoryExternalSkillClaimReservationGate",
    "ExecutionResourceGate",
    "ExecutionResourceResolver",
    "LocalManagedAgentMutationPort",
    "LocalManagedConnectorMutationPort",
    "LocalRuntimeResourceApplyPort",
    "ManagedAgentMutationPort",
    "ManagedConnectorMutationPort",
    "ManagedMutationResult",
    "RuntimeResourceApplyPort",
    "RuntimeResourceContractError",
    "SandboxMaintenancePort",
    "SandboxMaintenanceLease",
    "SandboxMaintenanceProbe",
    "SandboxMaintenanceUnsupported",
    "SandboxTerminalReceipt",
    "SkillTreeMutationPort",
    "TurnBoundaryObservedHashHook",
    "UnsupportedSandboxMaintenancePort",
    "ensure_managed_root_containment",
    "require_sync_apply_origin",
    "validate_skill_reference",
    "validate_managed_skill_path",
    "authorize_sandbox_provision",
    "get_agent_lifecycle_hook",
    "get_billing_port",
    "get_connector_lifecycle_hook",
    "get_connector_oauth_refresh_port",
    "get_file_address_resolver",
    "get_provider_policy",
    "get_sandbox_policy",
    "get_skill_lifecycle_hook",
    "set_agent_lifecycle_hook",
    "set_billing_port",
    "set_connector_lifecycle_hook",
    "set_connector_oauth_refresh_port",
    "set_file_address_resolver",
    "NoopRuntimeTurnContextContributor",
    "RuntimeTurnContextContributor",
    "get_runtime_turn_context_contributor",
    "set_runtime_turn_context_contributor",
    "set_provider_policy",
    "set_sandbox_policy",
    "set_skill_lifecycle_hook",
]
