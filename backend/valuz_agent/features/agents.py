"""``oss-agents`` -- the agent library and agent templates (required)."""

from __future__ import annotations

from valuz_agent.features._base import OssPlugin, routes
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class AgentsPlugin(OssPlugin):
    id = "oss-agents"
    required = True
    provides = ("oss.agents",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "agents", "agent_templates")
        ctx.toolkit.add(TOOL_GROUPS["agent-proposal"])
