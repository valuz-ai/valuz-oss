"""``oss-notifications`` -- the notification ledger: routes and the boot sweep that
closes informational notifications older builds left open."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, startup, step
from valuz_agent.plugin_host import PluginContext


class NotificationsPlugin(OssPlugin):
    id = "oss-notifications"
    provides = ("oss.notifications",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "notifications")
        startup(ctx, BootPhase.RECOVERY, step("resolve_informational_notification_backlog"))
