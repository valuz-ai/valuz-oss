"""``oss-citations`` -- citations, document research and the citation calculation
tool."""

from __future__ import annotations

from valuz_agent.features._base import OssPlugin, routes
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext


class CitationsPlugin(OssPlugin):
    id = "oss-citations"
    # Claims are audited against knowledge-base documents and feedback signals.
    needs = ("oss.core", "oss.knowledge", "oss.feedback")
    provides = ("oss.citations",)

    def register(self, ctx: PluginContext) -> None:
        routes(ctx, "citations", "document_research")
        ctx.toolkit.add(TOOL_GROUPS["citation-calculation"])
