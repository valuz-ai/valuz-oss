"""``oss-backup`` -- local backups: routes, the staged-restore apply at boot and the
backup scheduler."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.plugin_host import PluginContext


class BackupPlugin(OssPlugin):
    id = "oss-backup"
    provides = ("oss.backup",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "backup")
        startup(ctx, BootPhase.GUARD, step("apply_backup_restore"))
        startup(ctx, BootPhase.RUNNERS, step("start_backup_scheduler", app=True))
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_backup_scheduler", app=True))
