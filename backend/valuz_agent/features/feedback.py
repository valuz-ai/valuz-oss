"""``oss-feedback`` -- user outcome signals (rating / copy / regenerate / fork /
share)."""

from __future__ import annotations

from valuz_agent.features._base import OssPlugin, routes
from valuz_agent.plugin_host import PluginContext


class FeedbackPlugin(OssPlugin):
    id = "oss-feedback"
    provides = ("oss.feedback",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "feedback")
