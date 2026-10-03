"""``oss-connectors`` -- connector routes and the always-on connectors MCP server."""

from __future__ import annotations

from valuz_agent.features import mounts
from valuz_agent.features._base import OssPlugin, routes
from valuz_agent.plugin_host import PluginContext


class ConnectorsPlugin(OssPlugin):
    id = "oss-connectors"
    provides = ("oss.connectors",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "connectors")
        ctx.internal_mounts.mount(mounts.CONNECTORS)
