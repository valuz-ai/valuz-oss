"""A2UI prompt and payload assembly for the ``generate_ui`` tool.

A2UI v0.9 is the one wire protocol. The tool used to be able to emit OpenUI
Lang instead, chosen by ``VALUZ_GENUI_PROTOCOL``; carrying two generation
formats meant two prompt vocabularies, two renderers and two sets of failure
modes for one feature, so the second was removed rather than maintained.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Literal

from valuz_agent.ports.extensions import ext
from valuz_agent.ports.genui_blocks import GenUIBlockRegistry

OUTPUT_FORMAT = "A2UI v0.9 JSON message stream"

#: Follow-up prompt when the previous turn's A2UI document was cut off at the
#: output-token cap. Sent in the SAME ephemeral session, so the model sees its
#: own truncated output in history and continues it. It must not repeat what it
#: already completed (components merge by id, so a repeat only bloats the doc)
#: and must not add prose — only the remaining A2UI message lines.
CONTINUATION_PROMPT = (
    "你上一条 A2UI 文档在输出上限处被截断了,还没写完。"
    "请从中断处继续,只补齐**尚未输出完整**的剩余消息——"
    "每条消息一行完整 JSON,不要重复已经完整输出过的组件,"
    "不要加任何解释文字,直接接着写。"
)

#: Which set of components one generation is offered.
#:
#: The split follows where a component comes from, not what it is made of:
#:
#: - ``atoms`` — everything this repository ships: OpenUI's primitives *and*
#:   the built-in blocks. The general vocabulary.
#: - ``edition`` — only what an edition registered from outside this repo.
#:   A vertical's own set, unmixed with the general one.
#: - ``all`` — both. The default, and right when the shape of the answer is not
#:   known up front.
#:
#: A shorter menu is an easier menu: the model chooses better from one, and the
#: catalog is the bulk of every ``generate_ui`` prompt.
#:
#: Narrowing is prompt-side only. The renderer keeps accepting every component
#: it ever accepted, so a narrowed prompt can never produce a payload the client
#: cannot draw — the failure direction runs the other way, and that one stays
#: closed.
GenUIComponentScope = Literal["all", "edition", "atoms"]

_A2UI_INSTRUCTIONS_BASE = (
    "You generate user interfaces as an A2UI v0.9 JSON message stream. Output "
    "ONLY newline-delimited JSON objects, with no markdown fences, prose, or "
    "explanations. The first message must create a surface, and later messages "
    "may update its data model and components. Use concise component trees "
    "that fit inside an existing conversation pane; never generate an app shell, "
    "sidebar, top navigation, or fixed-width page chrome. Prefer compact, "
    "mobile-first layouts: KPI/detail rows may wrap, charts should occupy a "
    "readable full-width section, and tables may scroll horizontally only when "
    "their columns cannot stay readable. Use OpenUI component names from the "
    "catalog below so the @a2ui/react renderer can map them to OpenUI React "
    "components one-for-one."
)

_A2UI_PREFER_BLOCKS = (
    " For financial market dashboards, prefer the Valuz "
    "semantic components in the catalog: they are rendered as OpenUI surfaces "
    "but avoid fragile Card/TextContent/Chart compositions."
)

_A2UI_NO_PLACEHOLDER_CHARTS = (
    " Do not create "
    "placeholder charts: only render chart components when the request or data "
    "contains real chart series, labels, slices, or points. When the data is a "
    "current snapshot rather than a time series, use {fallbacks} instead of an "
    "empty chart."
)

# What to fall back to when the data has no chart-ready series. Named per scope
# because a fallback the catalog does not offer is worse than no advice at all:
# the model is being told to reach for something it was never shown.
#
# The `edition` entry names nothing, and cannot: this repository does not know
# what an edition installed. Generic advice is the honest limit — better than
# naming components from a set that scope just excluded.
_A2UI_SNAPSHOT_FALLBACKS: dict[str, str] = {
    "all": "MarketIndexGrid, StatsCard, MarketBreadth, DataList, or Table",
    "atoms": "MarketIndexGrid, StatsCard, MarketBreadth, DataList, or Table",
    "edition": "a tile, list, or table component from the catalog above",
}


def _snapshot_fallbacks(scope: GenUIComponentScope) -> str:
    """The advice for one scope, after the registry has had its say.

    Under an edition holding ``replace`` this repository's names are gone from
    every scope, so the advice falls back to the ``edition`` wording — which
    names nothing, and is the only honest thing to say when the catalog's
    contents were chosen by a build this one cannot see.
    """

    if _block_registry().baseline_suppressed():
        return _A2UI_SNAPSHOT_FALLBACKS["edition"]
    return _A2UI_SNAPSHOT_FALLBACKS[scope]


A2UI_OPENUI_COMPONENT_CATALOG = """
OpenUI component catalog supported by the A2UI renderer. These sit alongside the
Valuz blocks below; where both could serve, the block is the better answer —
it is opinionated about the shape of the data, and these are not.

- Stack(children: array, direction?: "row"|"column", gap?: string) — The document root every payload opens with, and the only general-purpose
  container kept here: it stacks the answer's sections. Inline, Cluster, Split and DashboardGrid are the blocks for arranging things inside a section.
- LineChart(labels: array, series: array, variant?: string, xLabel?: string, yLabel?: string) — Values over an ordered axis, one line per series. labels is the axis and series is {category, values} with the nth value under the nth label. This is the only multi-series line chart; ComboChart draws bars with a single line over them, so reach for that one only when the second measure genuinely is a rate over a level.
- AreaChart(labels: array, series: array, variant?: string, xLabel?: string, yLabel?: string) — A LineChart with the area under each line filled. Use it when the quantity accumulates or when the total, not the path, is the point; a plain LineChart reads more precisely for levels that move both ways.
- RadarChart(labels: array, series: array, variant?: string) — One subject scored across several named axes, drawn as a closed shape. Best from about four axes up, and only when the axes share a scale — mixed units make the shape meaningless. Two or three series at most before the shapes obscure each other.
- ScatterChart(series: array, xLabel?: string, yLabel?: string) — Points in two dimensions, for the relationship between two measures rather than change over time.
- PieChart(labels: array, values: array) — Parts of one whole. Keep it under about six slices and only when the parts really do sum to the whole; anything ranked or compared is a GroupedBar.
- RadialChart(labels: array, values: array) — The same composition drawn as concentric arcs. Use it for a small number of shares where the ring reads better than a pie.

- Callout(text: string, title?: string, variant?: string) — A tinted panel raising one thing about the answer: a caveat, a coverage gap, a figure on a different basis. variant carries the severity, and stating it is the point — a warning drawn neutral reads as a footnote. ContextCard is the neighbouring explanation of method; this is a flag above the answer.
- Markdown(text: string) — Prose with markup: headings, bold, lists, links, inline code. The only component that parses markup — RichText renders its text literally. A real table is DataGrid or ComparisonTable, not a markdown table. (MarkDownRenderer is this component's former name and is still accepted.)
- CodeBlock(code: string, language?: string) — Code or a formula kept verbatim and highlighted. Use it as a receipt the reader can check or re-run — the query behind a table, the formula behind a screen — and never paraphrase it to save space. language only selects highlighting; omit it rather than guess.
- Tag(label: string, variant?: string) — A short classification label as a pill: a filing type, a category, a rating. IconTag is the sibling when the mark is an icon rather than a word.
- TagBlock(tags: array) — A wrapping row of Tags, for a set worth reading together. A single tag needs no block.

- Tabs(children: array) / TabItem(label: string, children: array) — Panels behind named tabs. Only one panel is visible, so never put the answer's main finding inside a tab the reader must discover. The selection is local to the page and reaches no agent.

- Form(fields: array, buttons?: array) / FormControl(children: array) / Label(label: string) — A field group. Every control below is a picture of an input: nothing is submitted, nothing reaches an agent, and no value you set comes back. Render one to show what was asked or what a reader would fill in — never to collect an answer you intend to act on, and never write text promising that pressing something will do anything.
- Input(name: string, type?: string, placeholder?: string, value?: string) — A single-line field.
- TextArea(name: string, rows?: number, placeholder?: string, value?: string) — A multi-line field.
- Select(name: string, children: array) / SelectItem(value: string, label?: string) — A dropdown and its options.
- CheckBoxGroup(items: array) / CheckBoxItem(name: string, label: string, description?: string, checked?: boolean) — Independent toggles. StatusList is the better answer when you are reporting what is done rather than offering choices.
- RadioGroup(name: string, items: array, defaultValue?: string) / RadioItem(value: string, label: string, description?: string) — One choice from a set. OptionCards reads better when each option needs a sentence.
- SwitchGroup(items: array) / SwitchItem(name: string, label: string, description?: string, checked?: boolean) — On/off settings.
- Slider(name: string, min?: number, max?: number, step?: number, value?: number, label?: string) — A value on a range.
- DatePicker(mode?: string, value?: string) — A date or range field.
- Button(label: string, variant?: string) / Buttons(buttons: array, direction?: string) — A button is drawn, not wired: clicking it does nothing. Use it only to depict an action that exists elsewhere, and say where — never as the way the reader is meant to proceed.
"""

_A2UI_MESSAGE_SHAPE = """\
Use official A2UI v0.9 component objects with component properties at the top
level, not nested under "props":
{"id":"title","component":"TextContent","text":"Revenue","size":"large-heavy"}
Use flat component ids for layout children:
{"id":"root","component":"Stack","children":["title","chart"],"direction":"column","gap":"m"}
Do not create placeholder charts or charts with empty series. If supplied data
does not include chart-ready arrays, show the raw values with {fallbacks}.

Live data slots (optional). By default, render supplied values directly into
component properties — that snapshot is complete and correct on its own. When
the answer names a host data source the edition catalog marks as pollable AND
freshness genuinely matters (a quote line, a watchlist), you may bind instead:

1. Seed a slot and declare its source in the data model:
{"version":"v0.9","updateDataModel":{"surfaceId":"s","path":"/data/quote","value":{"items":[...]}}}
{"version":"v0.9","updateDataModel":{"surfaceId":"s","path":"/refs/quote","value":{"source":"<source id>","params":{"symbol":"US:NVDA"},"refresh":{"interval":60}}}}
2. Bind the component property to the slot instead of inlining:
{"id":"q","component":"MetricStrip","items":{"path":"/data/quote/items"},"source":"Valuz","asOf":"..."}
The binding IS the refresh: a property written as {"path": ...} re-renders
when the host updates the slot, an inlined copy of the same values never
does. Seeding the slot and then inlining the values anyway produces a board
that polls but visibly never moves — seed the SLOT, bind the PROPERTY.

CHECK BEFORE YOU FINISH: every /refs/<slot> you declared must have at least
one component property written as {"path":"/data/<slot>/..."}. A ref with no
reader is dead weight — the host polls, the page never changes, and the
values you inlined instead are frozen at generation time. If you find a ref
with no binding, either bind it or drop the ref.

And bind the SLOT's shape, not the source data you were handed: the values
arriving after a refresh have the shape the edition notes state for that
source, which is rarely the shape of the tool output you read. Hand-copying
the tool's own records into a component (a chip row given {"label":...}
objects when it takes plain strings) prints raw JSON on the page — that is
the giveaway that a binding was replaced by a transcription.

The slot's shape is NOT yours to design. After every refresh the host
replaces the slot's whole value with the source's declared shape, exactly
as the edition notes state it (e.g. a metric source refreshes to
{"items":[{"label","value","delta?","trend?"}],"source","asOf"}). Seed
that same shape and bind inside it — items:{"path":"/data/quote/items"} —
never invent your own slot fields: a binding to an invented field renders
once from your seed and goes blank on the first refresh.

Always write the seed value — the binding must render correctly even if the
host never refreshes it. One slot per source; the slot path and the ref path
share the trailing name. Never invent a source id: only the ids the edition
notes list as pollable exist, and anything else leaves the slot permanently
stale. refresh.interval is seconds and must respect the source's stated
minimum.

When the edition notes list MORE THAN ONE shape for a source, the ref must
say which one it wants with a "shape" key (e.g. {"source":"...","shape":
"ChartData","params":{...}}); single-shape sources need no shape key.

A param value may be written as {"$host":"<key>"} instead of a literal when
the edition notes say the current page provides that key (e.g. a company
page providing "symbol") — the page then re-binds when its subject changes.
Only keys the notes name exist; on pages that provide none, always write
literal params."""


def _load_block_catalog() -> str:
    """The Valuz block section of the catalog.

    Generated from the block registry in ``@valuz/genui-blocks`` by
    ``frontend/packages/ui/scripts/gen_openui_prompt.mjs`` — the same registry
    ``A2UIRenderer`` builds its component list from, so the model is never told
    about a block that cannot render, nor left unaware of one that can. Hand-
    editing this asset re-opens exactly that drift.
    """

    return (
        resources.files("valuz_agent.modules.genui")
        .joinpath("a2ui_block_catalog.txt")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )


_A2UI_EDITION_HEADING = "- Edition components:\n"

_A2UI_ROOT_ONLY_CATALOG = """
OpenUI component catalog supported by the A2UI renderer:
- Layout: Stack — the document root, and the only component from the general
  vocabulary offered here. Everything else comes from the edition below.
"""

#: Every component the OpenUI library defines — the reserved set the block
#: registry refuses collisions against.
#:
#: Deliberately NOT derived from the catalog above. The two answer different
#: questions: the catalog decides what the model is *offered*, while this list
#: decides what a block may not be *named*. A name dropped from the catalog is
#: still defined by the OpenUI library, and a block taking it collides at JSON
#: Schema conversion — "Duplicate schema id" at render time, nowhere near the
#: registration that caused it. So this stays complete even as the catalog
#: narrows; add a name here whenever OpenUI grows a component.
_OPENUI_COMPONENT_NAMES: tuple[str, ...] = (
    "Accordion", "AccordionItem", "AreaChart", "BarChart", "Button", "Buttons",
    "Callout", "Card", "CardHeader", "Carousel", "CheckBoxGroup", "CheckBoxItem",
    "CodeBlock", "Col", "DatePicker", "Form", "FormControl", "Grid", "Heading",
    "HorizontalBarChart", "Image", "ImageBlock", "ImageGallery", "Input", "KPI",
    "Label", "LineChart", "MarkDownRenderer", "Markdown", "Modal", "Paragraph",
    "PieChart", "Point", "RadarChart", "RadialChart", "RadioGroup", "RadioItem",
    "Row", "ScatterChart", "ScatterSeries", "Section", "Select", "SelectItem",
    "Separator", "Series", "SingleStackedBarChart", "Slice", "Slider", "Stack",
    "Steps", "StepsItem", "SwitchGroup", "SwitchItem", "TabItem", "Table",
    "Tabs", "Tag", "TagBlock", "Text", "TextArea", "TextCallout", "TextContent",
    "Title",
)


def _block_registry() -> GenUIBlockRegistry:
    """The process-wide block registry, with the OSS baseline bound.

    Binding is lazy rather than at import so an overlay that registers before
    importing this module still gets its collisions checked — the registry
    re-validates at bind and logs loudly on a drop.
    """

    registry = ext.genui_blocks
    if not registry.baseline_bound:
        catalog_text = _load_block_catalog()
        names = re.findall(r"^\s*-\s*([A-Za-z0-9]+)\(", catalog_text, re.MULTILINE)
        registry.bind_baseline(
            names=[*names, *_OPENUI_COMPONENT_NAMES],
            catalog_text=catalog_text,
        )
    return registry


A2UI_COMPONENT_CATALOG = f"""{A2UI_OPENUI_COMPONENT_CATALOG}
- Valuz blocks (cards, citations, report pages, diagrams):
{_load_block_catalog()}
"""


def edition_catalog_text() -> str:
    """Components registered from outside this repository.

    The registry behind it is ``ext.genui_blocks``: an edition — a separate
    build that vendors this one — registers the catalog its own frontend
    generated, and this returns those layers and nothing else. Empty when
    nothing is installed, which is what makes ``resolve_component_scope``
    widen an ``edition`` scope back to ``all`` rather than offering a root
    with no components under it.

    Read per call, never cached: registration happens at edition startup, and
    a module constant would freeze the prompt at import — one process restart
    behind every edition, forever.
    """

    return _block_registry().catalog_text(baseline=False)


def resolve_component_scope(scope: GenUIComponentScope) -> GenUIComponentScope:
    """The scope actually available, which is not always the one asked for.

    An ``edition`` scope with no edition registered would offer the root and
    nothing else — that does not produce a smaller answer, it produces no
    answer. Widening is the only safe direction when a scope turns out empty;
    narrowing to nothing is the failure this whole seam guards against.

    Resolved in one place so the catalog and the instructions cannot disagree
    about which scope is live — instructions naming a fallback the catalog never
    showed is the exact drift the scope exists to prevent.
    """

    if scope == "edition" and not edition_catalog_text():
        return "all"
    return scope


def build_a2ui_catalog(scope: GenUIComponentScope = "all") -> str:
    """The A2UI catalog for one scope.

    Assembled rather than stored per scope because A2UI's primitive list is a
    hand-written blob (the renderer maps those names one-for-one) while the
    block half is generated — only the second half has a build step to hang a
    variant on.
    """

    edition = edition_catalog_text()
    scope = resolve_component_scope(scope)

    own = (
        f"{A2UI_OPENUI_COMPONENT_CATALOG}\n"
        "- Valuz blocks (cards, citations, report pages, diagrams):\n"
        f"{_load_block_catalog()}\n"
    )
    installed = f"{_A2UI_EDITION_HEADING}{edition}\n" if edition else ""
    if _block_registry().baseline_suppressed():
        # An edition holds `replace`: this repository's vocabulary is gone from
        # the renderer, so no scope may describe it — a described-but-
        # unrenderable component is the failure direction the seam keeps closed.
        # Every scope collapses to the root plus what the edition installed,
        # `atoms` included, since its whole content is what the edition removed.
        components = f"{_A2UI_ROOT_ONLY_CATALOG}{installed}"
    elif scope == "atoms":
        components = own
    elif scope == "edition":
        # The root comes from the general set even here: it is the one component
        # an edition cannot supply for itself, since every document is rooted in
        # it before any edition component appears.
        components = f"{_A2UI_ROOT_ONLY_CATALOG}{installed}"
    else:
        # `all` is the union, in this order — an edition's components read as an
        # addition to the general vocabulary, which is what they are.
        components = f"{own}{installed}"

    fallbacks = _snapshot_fallbacks(scope)
    # `.replace`, not `.format`: the message-shape text is JSON, and every brace
    # in it would be read as a format field.
    return f"{components}{_A2UI_MESSAGE_SHAPE.replace('{fallbacks}', fallbacks)}\n"


def normalize_component_scope(value: object) -> GenUIComponentScope:
    """Read a caller-supplied scope, defaulting to the whole vocabulary.

    Tolerant on purpose: this argument is written by a model, and an unusable
    value should cost the wider prompt rather than the whole generation.
    """

    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"all", "full", "everything"}:
            return "all"
        if normalized in {"edition", "vertical"}:
            return "edition"
        if normalized in {"atoms", "atom", "blocks", "openui", "valuz", "base"}:
            return "atoms"
    return "all"


def a2ui_instructions(scope: GenUIComponentScope = "all") -> str:
    """The A2UI system instructions, saying only what this scope can back up."""

    scope = resolve_component_scope(scope)
    prefer_blocks = _A2UI_PREFER_BLOCKS if scope != "atoms" else ""
    tail = _A2UI_NO_PLACEHOLDER_CHARTS.replace("{fallbacks}", _snapshot_fallbacks(scope))
    return f"{_A2UI_INSTRUCTIONS_BASE}{prefer_blocks}{tail}"


A2UI_GENERATIVE_UI_INSTRUCTIONS = a2ui_instructions()


def build_a2ui_prompt(
    request: str,
    data: object | None = None,
    scope: GenUIComponentScope = "all",
) -> str:
    parts = [
        a2ui_instructions(scope),
        "",
        "A2UI v0.9 message contract:",
        '- createSurface: {"version":"v0.9","createSurface":{"surfaceId":"main","catalogId":"openui"}}',
        '- updateDataModel: {"version":"v0.9","updateDataModel":{"surfaceId":"main","path":"/","value":{...}}}',
        '- updateComponents: {"version":"v0.9","updateComponents":{"surfaceId":"main","components":[...]}}',
        "- deleteSurface is allowed only when removing a surface.",
        '- every UI must include a component with id "root"; put the visible tree under root.children.',
        "",
        build_a2ui_catalog(scope).strip(),
        "",
        "REQUEST:",
        request.strip(),
    ]
    if data is not None:
        parts.append("")
        parts.append("DATA (render these values directly into the components):")
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)


def wrap_generated_ui(content: str) -> str:
    """Wrap the generated stream in the envelope the client dispatches on."""

    body = (content or "").strip()
    return json.dumps(
        {"protocol": "a2ui-json", "content": body},
        ensure_ascii=False,
        separators=(",", ":"),
    )


# A2UI message keys that carry document state. A line with none of these is
# not part of the document (models routinely close with a prose summary).
_A2UI_MESSAGE_KEYS = (
    "createSurface",
    "updateComponents",
    "updateDataModel",
    "deleteSurface",
)


def a2ui_message_lines(raw: str) -> tuple[list[str], bool]:
    """The complete JSON message lines in ``raw`` and whether its tail is cut.

    A2UI is append-only JSONL, so a generation stopped by an output cap leaves
    every line complete except a half-written last one. This returns the
    complete ``{``-opening lines in order and a ``truncated`` flag set when the
    stream broke mid-line — the two facts a continuation loop needs: what has
    arrived, and whether to ask the model to keep writing.
    """
    lines: list[str] = []
    truncated = False
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            truncated = True
            break
        lines.append(line)
    return lines, truncated


def extract_a2ui_document(raw: str) -> str | None:
    """The document inside a model's raw output, or ``None`` if unusable.

    Generations do not arrive clean. Two things happen routinely:

    * The model closes with prose — "界面已生成，包含以下模块…". Harmless, but it
      has no business being stored as part of the document: the renderer skips
      it, every guard downstream has to know to skip it, and the same text is
      already in the conversation.
    * The generation is cut off mid-write (an aborted tool call), leaving a
      half-written JSON line. Stored as-is, that becomes a bindable version
      whose page renders as whatever stray component survived — a blank
      workbench with no explanation.

    So: keep the message lines, drop everything else, and refuse the document
    outright when a line OPENS a JSON object and then fails to parse. That last
    rule is why "any non-JSON line is corruption" would be wrong — it would
    condemn every generation that ends with a sentence.
    """
    if not raw:
        return None
    kept: list[str] = []
    saw_components = False
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # A ``{``-opening line that will not parse is a TRUNCATED tail:
            # A2UI is append-only JSONL, so a generation cut off by an output
            # cap or an aborted stream leaves its last line half-written while
            # every earlier line is complete. Reject-the-whole-document threw
            # away a nearly-finished page (blank workbench, no version) for a
            # single missing closing brace. Instead, stop at the break and
            # keep the valid prefix — the page renders what completed, minus
            # the final unfinished section. (A break, not skip-and-continue:
            # nothing valid follows a truncation in an append-only stream, and
            # skipping into later lines could stitch across a genuinely
            # corrupt middle.)
            break
        if not isinstance(message, dict):
            continue
        if not any(key in message for key in _A2UI_MESSAGE_KEYS):
            continue
        if "updateComponents" in message:
            saw_components = True
        kept.append(line)
    # Still require a real page: a run truncated BEFORE its first complete
    # ``updateComponents`` has nothing to show and is rejected as before.
    if not saw_components:
        return None
    return "\n".join(_without_repeated_document(kept))


def _without_repeated_document(lines: list[str]) -> list[str]:
    """Drop a second, identical copy of the document.

    A turn's canonical assistant text is the join of every model-end segment,
    so a run that emits the document in two segments hands back both — the same
    page, twice, byte for byte. Storing that doubles every revision and makes
    the stored document disagree with itself about how many surfaces it has.

    Narrow on purpose: only an exact repeat of the whole line sequence is
    dropped. A generation that legitimately restarts with DIFFERENT content is
    left alone — that is a real second declaration, and deciding which of two
    differing versions wins is not this function's call.
    """
    count = len(lines)
    if count < 2 or count % 2 != 0:
        return lines
    half = count // 2
    return lines[:half] if lines[:half] == lines[half:] else lines
