"""``oss-activity`` -- the activity overview."""

from __future__ import annotations

from valuz_agent.features._base import OssPlugin, routes
from valuz_agent.plugin_host import PluginContext


class ActivityPlugin(OssPlugin):
    id = "oss-activity"
    provides = ("oss.activity",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "activity")
