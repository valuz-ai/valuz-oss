"""``oss-browser`` -- the managed browser: routes, the ``browser_start`` /
``browser_stop`` harness tools and the engine shutdown."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, shutdown, step
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class BrowserPlugin(OssPlugin):
    id = "oss-browser"
    provides = ("oss.browser",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "browser")
        ctx.toolkit.add(TOOL_GROUPS["browser"])
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_managed_browser"))
