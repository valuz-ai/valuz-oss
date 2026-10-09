"""``oss-memory`` -- project memory: routes, the harness memory tools and the idle /
task-finish extraction triggers."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class MemoryPlugin(OssPlugin):
    id = "oss-memory"
    provides = ("oss.memory",)

    def register(self, ctx: PluginContext) -> None:
        from valuz_agent.modules.memory.context import MemoryTurnContextProvider
        from valuz_agent.modules.memory.journal import review_journal

        ctx.ports.bind("memory_maintenance", review_journal)
        routes(ctx, "memory")
        ctx.toolkit.add(TOOL_GROUPS["memory"])
        ctx.ports.append("turn_context_providers", MemoryTurnContextProvider())
        startup(ctx, BootPhase.KERNEL, step("wire_memory_triggers"))
        startup(ctx, BootPhase.RECOVERY, step("start_memory_recovery"))
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_memory_recovery"))
