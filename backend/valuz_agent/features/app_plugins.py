"""``oss-app-plugins`` -- third-party plugins on a local deployment (ADR-034).

Install / dev-link / lifecycle routes, the plugin asset server, per-user plugin
storage and config, plugin-declared automations, and the ``app_plugin_manager``
harness tool. Switching it off makes third-party plugins unavailable as a whole.
"""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features._base import OssPlugin, routes, startup, step
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.infra.middleware_registry import MiddlewareOrder
from valuz_agent.plugin_host import PluginContext


class AppPluginsPlugin(OssPlugin):
    id = "oss-app-plugins"
    provides = ("oss.app-plugins",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "app_plugins", "app_plugin_assets")
        ctx.toolkit.add(TOOL_GROUPS["app-plugin-manager"])
        # Requests a plugin makes (``X-Valuz-App-Plugin-Id``) are held to its manifest
        # permissions; every other request passes untouched.
        from valuz_agent.api.app_plugin_middleware import PluginPermissionMiddleware

        ctx.middleware.add(PluginPermissionMiddleware, MiddlewareOrder.RBAC)
        # A superseded version directory is kept until the next start, then dropped.
        startup(ctx, BootPhase.POST_BOOT, step("cleanup_superseded_app_plugin_versions"))


# Deprecated import aliases use the canonical implementation.
ThirdPartyPlugin = AppPluginsPlugin
