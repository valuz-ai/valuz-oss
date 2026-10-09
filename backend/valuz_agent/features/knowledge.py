"""``oss-knowledge`` -- the knowledge base: docs + parser routes, the docs MCP server,
the auto-discovery scanner, the parser polling scheduler and parse pool, and the
project-binding listener that keeps open sessions' docs capabilities fresh."""

from __future__ import annotations

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features import mounts
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.plugin_host import PluginContext


class KnowledgePlugin(OssPlugin):
    id = "oss-knowledge"
    provides = ("oss.knowledge",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "docs", "parser_system", "parser_settings")
        ctx.internal_mounts.mount(mounts.DOCS)
        startup(ctx, BootPhase.KERNEL, step("install_binding_change_listener"))
        startup(
            ctx,
            BootPhase.RUNNERS,
            step("start_docs_auto_discovery", app=True),
            step("start_polling_scheduler"),
            step("warm_parse_pool"),
        )
        shutdown(
            ctx,
            BootPhase.STOP_RUNNERS,
            step("stop_docs_auto_discovery", app=True),
            step("shutdown_parse_pool"),
            step("stop_polling_scheduler"),
        )
