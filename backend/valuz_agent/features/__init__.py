"""OSS backend features, composed by the plugin host.

Valuz OSS's own backend is a set of plugins: ``oss-core`` and ``oss-agents`` are
required, every other feature (``oss-tasks``, ``oss-automations``, ...) is optional and
can be switched off in the extension prefs (``<data root>/extensions.json``) --
unless a required plugin needs it (``PluginHost.locks``). A plugin only *registers*
what it owns -- routers, boot steps, internal mounts / always-on MCP servers,
harness tool groups; ``create_app`` and the lifespan assemble them, in the canonical
order of ``features/order.py`` and ``boot/phases.py``. A disabled plugin contributes
nothing.

``oss_plugins()`` is the list. A bare OSS app composes it itself
(``create_app(plugin_host=None)``); the commercial overlay builds one host with
``oss_plugins()`` first, then its own and the edition's, and hands it in.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from valuz_agent.plugin_host import BackendPlugin, PluginHost

#: What the OSS plugins ask of a composition (they ignore facets: an OSS host always
#: contributes its routes, steps and mounts).
OSS_FACETS = frozenset({"routes", "ports", "lifespan", "app"})


def oss_plugins() -> list[BackendPlugin]:
    """The OSS feature plugins, in canonical activation order (fresh instances)."""
    from valuz_agent.features.activity import ActivityPlugin
    from valuz_agent.features.agent_plugins import AgentPluginsPlugin
    from valuz_agent.features.agents import AgentsPlugin
    from valuz_agent.features.automations import AutomationsPlugin
    from valuz_agent.features.backup import BackupPlugin
    from valuz_agent.features.browser import BrowserPlugin
    from valuz_agent.features.channels import ChannelsPlugin
    from valuz_agent.features.citations import CitationsPlugin
    from valuz_agent.features.connectors import ConnectorsPlugin
    from valuz_agent.features.core import CorePlugin
    from valuz_agent.features.dsh_plugins import DshPluginsPlugin
    from valuz_agent.features.feedback import FeedbackPlugin
    from valuz_agent.features.knowledge import KnowledgePlugin
    from valuz_agent.features.marketplace import MarketplacePlugin
    from valuz_agent.features.memory import MemoryPlugin
    from valuz_agent.features.notifications import NotificationsPlugin
    from valuz_agent.features.skills import SkillsPlugin
    from valuz_agent.features.tasks import TasksPlugin
    from valuz_agent.features.third_party import ThirdPartyPlugin

    return [
        CorePlugin(),
        AgentsPlugin(),
        TasksPlugin(),
        AutomationsPlugin(),
        ActivityPlugin(),
        SkillsPlugin(),
        ConnectorsPlugin(),
        KnowledgePlugin(),
        MemoryPlugin(),
        BrowserPlugin(),
        ChannelsPlugin(),
        BackupPlugin(),
        MarketplacePlugin(),
        AgentPluginsPlugin(),
        DshPluginsPlugin(),
        NotificationsPlugin(),
        FeedbackPlugin(),
        CitationsPlugin(),  # needs oss.knowledge + oss.feedback: after both
        ThirdPartyPlugin(),
    ]


@cache
def oss_capabilities() -> frozenset[str]:
    """Every capability name (``oss.<feature>``) the OSS plugins provide.

    The plugin host counts these as provided by the base (``PluginHost._available``):
    a composition that does not include the OSS plugins (a Taskiq worker, a narrow
    compatibility entry point, an edition loaded on its own) still orders and loads
    plugins that ``need`` an OSS feature.
    """
    return frozenset(name for plugin in oss_plugins() for name in plugin.provides)


def compose_oss_host(*, settings: Any = None) -> PluginHost:
    """Build (not load) a host of just the OSS plugins -- the bare OSS composition."""
    return PluginHost(oss_plugins(), role="web", facets=OSS_FACETS, settings=settings)


__all__ = ["OSS_FACETS", "compose_oss_host", "oss_capabilities", "oss_plugins"]
