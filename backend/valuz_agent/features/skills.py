"""``oss-skills`` -- the skill catalog: routes, the bundled-skill sync + index, the
auto-scan and file watcher, and the skill-authoring harness tools."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class SkillsPlugin(OssPlugin):
    id = "oss-skills"
    provides = ("oss.skills",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "skills")
        ctx.toolkit.add(TOOL_GROUPS["submit-skill"])
        ctx.toolkit.add(TOOL_GROUPS["skill-library"])
        startup(
            ctx,
            BootPhase.RUNNERS,
            step("start_skill_auto_scan", app=True),
            step("start_skills", app=True),
        )
        shutdown(
            ctx,
            BootPhase.STOP_RUNNERS,
            step("stop_skill_auto_scan", app=True),
            step("stop_skill_watcher", app=True),
        )
