"""``oss-automations`` -- automations, playbooks and operations, their always-on MCP
servers, and the automation runtime (scheduler tick + failure monitor)."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features import mounts
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.plugin_host import PluginContext


class AutomationsPlugin(OssPlugin):
    id = "oss-automations"
    provides = ("oss.automations",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "automations", "playbooks", "operations")
        ctx.internal_mounts.mount(mounts.AUTOMATIONS)
        ctx.internal_mounts.mount(mounts.PLAYBOOKS)
        startup(ctx, BootPhase.RUNNERS, step("start_automation_runtime", app=True))
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_automation_runtime", app=True))
