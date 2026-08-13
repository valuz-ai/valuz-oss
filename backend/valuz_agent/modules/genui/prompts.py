"""How ``generate_ui`` describes itself to the calling agent.

The prompt the *generator* reads lives in ``protocol.py`` beside the catalog it
is assembled from. This is the other audience: the tool description is what
decides whether the tool gets called at all, and by an agent that has never
seen the catalog.
"""

from __future__ import annotations

TOOL_DESCRIPTION = (
    "Generate a rich, interactive UI — charts, forms, KPI cards, or a dashboard — "
    "only when the user has asked for a chart, dashboard, workspace, page, "
    "research card, visualization, or interactive UI — in this message or "
    "recently in this conversation, so a "
    "follow-up refining a chart already on screen still counts even when it "
    "does not name one. Never infer this intent from data, and do "
    "not call it merely because the user asks to list items or show a table. Pass "
    "a natural-language `request` describing what to show. You own the business "
    "choice: pass the smallest exact `component_names` set that answers the "
    "request. Every component accepts its catalog-typed props. For a component "
    "listed with a bound data contract, pass `component_data` containing only "
    "the registered component name and its exact business params. Do not choose "
    "or pass a source id, slot, shape, refresh interval, binding path, or API URL: "
    "the component registry fixes those execution details and the tool completes "
    "the component-owned dataRef. Components without a bound contract receive "
    "typed revision-fixed inline props and do not refresh. Pass params with the documented primitive "
    "types; use a comma-separated string when a component says comma-separated "
    "symbols. The tool validates those choices and the compiler output, widens an overly "
    "narrow component scope when necessary, adds the structural root, and sends "
    "only the selected schemas to the UI compiler. Do not query a live "
    "live component merely to pre-fill `data`, including after a validation failure; "
    "correct the generate_ui call instead. The rendered binding loads immediately "
    "and stays current. Use `data` only for research values already in context, "
    "narrative analysis, or an explicitly frozen snapshot. "
    "Optionally narrow `components` to 'atoms' (the general vocabulary) or "
    "'edition' (a vertical edition's own components) when the shape of the "
    "answer is already clear; it generates faster from the smaller set. "
    "Describe the information hierarchy, research questions, data relationships, "
    "and interactions the UI must support — not raw colors, CSS, theme tokens, "
    "or pixel styling, which belong to the host's A2UI theme. "
    "When a target host already has a bound page, the tool automatically reads "
    "that exact A2UI revision and gives it to the UI compiler, so describe only "
    "the requested change rather than reconstructing the old page yourself. Set "
    "`generation_mode` to 'edit' only when the user explicitly asks to preserve "
    "the rest of the current page while changing or adding one local part. A "
    "request that says only what the new result should contain is not an edit, "
    "even when a page is already bound. Set it to 'replace' for a new page, a whole-page "
    "rebuild, or a request that says 'only' / '只要' / '只需' / '只包含'; otherwise old "
    "components can leak into a newly requested page. The "
    "client renders the returned A2UI payload inline; do not repeat the same "
    "content as text afterwards. One successful call completes this turn: do "
    "not call generate_ui again to restyle, expand, or regenerate the same "
    "request. When `target_host` was supplied and generation "
    "succeeds, reply with exactly one short sentence saying the preview is ready "
    "to inspect; do not recap values, components, data bindings, or refresh "
    "settings."
)
