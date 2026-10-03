"""``oss-channels`` -- IM channels: routes and the WeCom / Feishu long connections
(started after the host finished booting)."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.plugin_host import PluginContext


class ChannelsPlugin(OssPlugin):
    id = "oss-channels"
    provides = ("oss.channels",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "channels")
        startup(ctx, BootPhase.POST_BOOT, step("start_post_boot_agent_channels", app=True))
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_agent_channels", app=True))
