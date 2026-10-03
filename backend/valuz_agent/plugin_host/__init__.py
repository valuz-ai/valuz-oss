"""Backend plugin host (docs/design/plugin-architecture §7).

Named ``plugin_host``, not ``plugins``: ``valuz_agent.modules.plugins`` is the
Agent Plugins installer, a different concept.
"""

from valuz_agent.plugin_host.context import AppApi, PluginContext
from valuz_agent.plugin_host.discovery import (
    BUNDLE_ENTRY_POINT_GROUP,
    DiscoveryResult,
    coerce_plugins,
    collect_plugins,
    discover_bundles,
)
from valuz_agent.plugin_host.errors import (
    DuplicatePluginError,
    InvalidPluginError,
    MissingNeedError,
    PluginCycleError,
    PluginGraphError,
    PluginHostError,
    PluginStartupError,
    UnknownPortError,
)
from valuz_agent.plugin_host.host import PluginHost, PluginInfo, PluginRecord
from valuz_agent.plugin_host.plugin import (
    BackendPlugin,
    BackendPluginBase,
    ChangeResult,
    PluginStatus,
)

__all__ = [
    "BUNDLE_ENTRY_POINT_GROUP",
    "AppApi",
    "BackendPlugin",
    "BackendPluginBase",
    "ChangeResult",
    "DiscoveryResult",
    "DuplicatePluginError",
    "InvalidPluginError",
    "MissingNeedError",
    "PluginContext",
    "PluginCycleError",
    "PluginGraphError",
    "PluginHost",
    "PluginHostError",
    "PluginInfo",
    "PluginRecord",
    "PluginStartupError",
    "PluginStatus",
    "UnknownPortError",
    "coerce_plugins",
    "collect_plugins",
    "discover_bundles",
]
