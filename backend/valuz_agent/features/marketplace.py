"""``oss-marketplace`` -- the skill / agent / plugin marketplace: routes and the
once-per-process index endpoint race."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, startup, step
from valuz_agent.plugin_host import PluginContext


class MarketplacePlugin(OssPlugin):
    id = "oss-marketplace"
    provides = ("oss.marketplace",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "marketplace")
        startup(ctx, BootPhase.RUNNERS, step("resolve_marketplace_index"))
