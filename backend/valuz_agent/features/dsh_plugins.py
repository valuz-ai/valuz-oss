"""``oss-dsh-plugins`` -- the dsh plugin manager: routes, the always-on tool bridge
(local workstations only) and the resident manager host's start / stop."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features import mounts
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.plugin_host import PluginContext


class DshPluginsPlugin(OssPlugin):
    id = "oss-dsh-plugins"
    provides = ("oss.dsh-plugins",)

    def register(self, ctx: PluginContext) -> None:
        from valuz_agent.modules.dsh_plugins.manager import manager_enabled

        routes(ctx, "dsh_plugins")
        if manager_enabled():
            mount = mounts.dsh_plugins_mount()
            # Advertised to sessions like any edition-registered always-on server
            # (``ext.always_on_mcp_specs``, undone with the plugin) and mounted by
            # ``create_app``'s spec loop; the mount entry carries its session manager.
            ctx.mcp_always_on(mount.spec, unique=True)
            ctx.internal_mounts.mount(mount)
        startup(ctx, BootPhase.POST_BOOT, step("start_dsh_manager_if_plugins_installed"))
        shutdown(ctx, BootPhase.STOP_RUNNERS, step("stop_dsh_manager"))
