"""``oss-tasks`` -- goal-driven multi-agent tasks: routes, recovery, the health
monitor and the orchestration / dispatch harness tools."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class TasksPlugin(OssPlugin):
    id = "oss-tasks"
    needs = ("oss.core", "oss.agents")
    provides = ("oss.tasks",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "tasks")
        ctx.toolkit.add(TOOL_GROUPS["tasks-orchestration"])
        ctx.toolkit.add(TOOL_GROUPS["tasks-dispatch"])
        startup(
            ctx,
            BootPhase.RECOVERY,
            step("purge_tasks_of_deleted_projects"),
            step("recover_active_tasks"),
        )
        # Task watchdog: a lead that died without finalizing is marked blocked.
        startup(ctx, BootPhase.RUNNERS, step("start_task_health_monitor", app=True))
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_task_health_monitor", app=True))
