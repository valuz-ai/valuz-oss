"""``oss-memory`` -- project memory: routes, the harness memory tools and the idle /
task-finish extraction triggers."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, startup, step
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class MemoryPlugin(OssPlugin):
    id = "oss-memory"
    provides = ("oss.memory",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "memory")
        ctx.toolkit.add(TOOL_GROUPS["memory"])
        startup(ctx, BootPhase.KERNEL, step("wire_memory_triggers"))
