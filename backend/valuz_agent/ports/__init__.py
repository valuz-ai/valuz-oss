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

from valuz_agent.ports.billing import (
    Balance,
    BillingPort,
    BudgetStatus,
    MeterEvent,
    NoopBillingProvider,
    get_billing_port,
    set_billing_port,
)
from valuz_agent.ports.docs_runtime import DocsRuntimePort
from valuz_agent.ports.llm_provider import (
    LLMProviderRegistry,
    SystemLLMProvider,
    SystemProviderImmutable,
    get_llm_registry,
    set_llm_registry,
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
from valuz_agent.ports.skill_registry import SkillRegistryPort
from valuz_agent.ports.tool_provider import ToolProvider

__all__ = [
    "Balance",
    "BillingPort",
    "BudgetStatus",
    "CapabilityStatus",
    "ConfigField",
    "DocsRuntimePort",
    "LLMProviderRegistry",
    "McpCatalogPort",
    "MeterEvent",
    "NoopBillingProvider",
    "ParserBackend",
    "ParserCapabilityNotReady",
    "ParserPlugin",
    "ParserPluginConfig",
    "ParserPluginDescriptor",
    "ParserPluginMode",
    "PluginCapability",
    "PolicyDecision",
    "AllowAllProviderPolicy",
    "ProviderPolicyPort",
    "ProviderWriteContext",
    "SecretResolver",
    "SetupRequirement",
    "SkillRegistryPort",
    "SplitPolicy",
    "SystemLLMProvider",
    "SystemProviderImmutable",
    "ToolProvider",
    "get_billing_port",
    "get_llm_registry",
    "get_provider_policy",
    "set_billing_port",
    "set_provider_policy",
    "set_llm_registry",
]
