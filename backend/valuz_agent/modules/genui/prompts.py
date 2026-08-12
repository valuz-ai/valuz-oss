"""How ``generate_ui`` describes itself to the calling agent.

The prompt the *generator* reads lives in ``protocol.py`` beside the catalog it
is assembled from. This is the other audience: the tool description is what
decides whether the tool gets called at all, and by an agent that has never
seen the catalog.
"""

from __future__ import annotations

TOOL_DESCRIPTION = (
    "Generate a rich, interactive UI — charts, forms, KPI cards, or a dashboard — "
    "only when the user has asked for a chart, dashboard, visualization, or "
    "interactive UI — in this message or recently in this conversation, so a "
    "follow-up refining a chart already on screen still counts even when it "
    "does not name one. Never infer this intent from data, and do "
    "not call it merely because the user asks to list items or show a table. Pass "
    "a natural-language `request` describing what to show, and optional `data`. "
    "Optionally narrow `components` to 'atoms' (the general vocabulary) or "
    "'edition' (a vertical edition's own components) when the shape of the "
    "answer is already clear; it generates faster from the smaller set. "
    "Describe the information hierarchy, research questions, data relationships, "
    "and interactions the UI must support — not raw colors, CSS, theme tokens, "
    "or pixel styling, which belong to the host's A2UI theme. "
    "When a target host already has a bound page, the tool automatically reads "
    "that exact A2UI revision and gives it to the UI compiler, so describe only "
    "the requested change rather than reconstructing the old page yourself. The "
    "client renders the returned A2UI payload inline; do not repeat the same "
    "content as text afterwards."
)
