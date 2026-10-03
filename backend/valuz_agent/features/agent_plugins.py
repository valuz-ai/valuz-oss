"""``oss-agent-plugins`` -- the ``/v1/plugins`` Agent Plugins installer (not the dsh
plugin manager) and its harness tools."""

from __future__ import annotations

from valuz_agent.features._base import OssPlugin, routes
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class AgentPluginsPlugin(OssPlugin):
    id = "oss-agent-plugins"
    # Installing a plugin lands its skills in the skill catalog.
    needs = ("oss.core", "oss.skills")
    provides = ("oss.agent-plugins",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "plugins")
        ctx.toolkit.add(TOOL_GROUPS["plugin"])
