"""A2UI prompt and payload assembly for the ``generate_ui`` tool.

A2UI v0.9.1 is the one wire protocol and the Valuz A2UI catalog is the one
component implementation.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Literal

from valuz_agent.ports.extensions import ext
from valuz_agent.ports.a2ui_components import A2UIComponentRegistry

OUTPUT_FORMAT = "A2UI v0.9.1 JSON message stream"

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
#: - ``atoms`` — the complete Valuz OSS A2UI catalog.
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
    "You generate user interfaces as an A2UI v0.9.1 JSON message stream. Output "
    "ONLY newline-delimited JSON objects, with no markdown fences, prose, or "
    "explanations. The first message must create a surface, and later messages "
    "may update its data model and components. Use concise component trees "
    "that fit inside an existing conversation pane; never generate an app shell, "
    "sidebar, top navigation, or fixed-width page chrome. Prefer compact, "
    "mobile-first layouts: KPI/detail rows may wrap, charts should occupy a "
    "readable full-width section, and tables may scroll horizontally only when "
    "their columns cannot stay readable. Use only component names and exact "
    "property shapes from the Valuz A2UI catalog below."
)

_A2UI_PREFER_EDITION_COMPONENTS = (
    " For financial market dashboards, prefer the Valuz "
    "semantic components in the edition catalog when they directly answer the "
    "research question; use base components to compose supporting layout."
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
    "all": "MetricGroup, ListBlock, DataTable, or Table",
    "atoms": "MetricGroup, ListBlock, DataTable, or Table",
    "edition": "a tile, list, or table component from the catalog above",
}


def _snapshot_fallbacks(scope: GenUIComponentScope) -> str:
    """The advice for one scope, after the registry has had its say.

    Under an edition holding ``replace`` this repository's names are gone from
    every scope, so the advice falls back to the ``edition`` wording — which
    names nothing, and is the only honest thing to say when the catalog's
    contents were chosen by a build this one cannot see.
    """

    if _component_registry().baseline_suppressed():
        return _A2UI_SNAPSHOT_FALLBACKS["edition"]
    return _A2UI_SNAPSHOT_FALLBACKS[scope]


_A2UI_MESSAGE_SHAPE = """\
Use official A2UI v0.9.1 component objects with component properties at the top
level, not nested under "props":
{"id":"title","component":"TextContent","text":"Revenue","variant":"h2"}
Use flat component ids for layout children:
{"id":"root","component":"Stack","children":["title","chart"],"direction":"vertical","gap":"md"}
Do not create placeholder charts or charts with empty series. If supplied data
does not include chart-ready arrays, show the raw values with {fallbacks}.

Live data slots (optional). By default, render supplied values directly into
component properties — that snapshot is complete and correct on its own. When
the answer names a host data source the edition catalog marks as pollable AND
freshness genuinely matters (a quote line, a watchlist), you may bind instead:

1. Seed a slot and declare its source in the data model:
{"version":"v0.9.1","updateDataModel":{"surfaceId":"s","path":"/data/quote","value":{"items":[...]}}}
{"version":"v0.9.1","updateDataModel":{"surfaceId":"s","path":"/refs/quote","value":{"source":"<source id>","params":{"symbol":"US:NVDA"},"refresh":{"interval":60}}}}
2. Bind the component property to the slot instead of inlining:
Bind the property named by the edition source notes to the matching slot path.
The binding IS the refresh: a property written as {"path": ...} re-renders
when the host updates the slot, an inlined copy of the same values never
does. Seeding the slot and then inlining the values anyway produces a board
that polls but visibly never moves — seed the SLOT, bind the PROPERTY.

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


def _load_component_catalog() -> str:
    """Catalog generated from ``@valuz/a2ui`` component schemas."""

    return (
        resources.files("valuz_agent.modules.genui")
        .joinpath("a2ui_component_catalog.txt")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )


_A2UI_EDITION_HEADING = "- Edition components:\n"

_A2UI_ROOT_ONLY_CATALOG = """Valuz A2UI root component:
  - Stack(children: array, direction?: \"vertical\"|\"horizontal\") — The document root.
"""


def _component_registry() -> A2UIComponentRegistry:
    """The process-wide A2UI component registry, with the OSS baseline bound.

    Binding is lazy rather than at import so an overlay that registers before
    importing this module still gets its collisions checked — the registry
    re-validates at bind and logs loudly on a drop.
    """

    registry = ext.a2ui_components
    if not registry.baseline_bound:
        catalog_text = _load_component_catalog()
        names = re.findall(r"^\s*-\s*([A-Za-z0-9]+)\(", catalog_text, re.MULTILINE)
        registry.bind_baseline(
            names=names,
            catalog_text=catalog_text,
        )
    return registry


A2UI_COMPONENT_CATALOG = _load_component_catalog()


def edition_catalog_text() -> str:
    """Components registered from outside this repository.

    The registry behind it is ``ext.a2ui_components``: an edition — a separate
    build that vendors this one — registers the catalog its own frontend
    generated, and this returns those layers and nothing else. Empty when
    nothing is installed, which is what makes ``resolve_component_scope``
    widen an ``edition`` scope back to ``all`` rather than offering a root
    with no components under it.

    Read per call, never cached: registration happens at edition startup, and
    a module constant would freeze the prompt at import — one process restart
    behind every edition, forever.
    """

    return _component_registry().catalog_text(baseline=False)


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

    The OSS half is generated from the same strict schemas the renderer uses;
    edition entries are appended by the distribution registry.
    """

    edition = edition_catalog_text()
    scope = resolve_component_scope(scope)

    own = f"Valuz A2UI component catalog:\n{_load_component_catalog()}\n"
    installed = f"{_A2UI_EDITION_HEADING}{edition}\n" if edition else ""
    if _component_registry().baseline_suppressed():
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
        if normalized in {"atoms", "atom", "components", "a2ui", "valuz", "base"}:
            return "atoms"
    return "all"


def a2ui_instructions(scope: GenUIComponentScope = "all") -> str:
    """The A2UI system instructions, saying only what this scope can back up."""

    scope = resolve_component_scope(scope)
    prefer_components = _A2UI_PREFER_EDITION_COMPONENTS if scope != "atoms" else ""
    tail = _A2UI_NO_PLACEHOLDER_CHARTS.replace("{fallbacks}", _snapshot_fallbacks(scope))
    return f"{_A2UI_INSTRUCTIONS_BASE}{prefer_components}{tail}"


A2UI_GENERATIVE_UI_INSTRUCTIONS = a2ui_instructions()


def build_a2ui_prompt(
    request: str,
    data: object | None = None,
    scope: GenUIComponentScope = "all",
) -> str:
    parts = [
        a2ui_instructions(scope),
        "",
        "A2UI v0.9.1 message contract:",
        '- createSurface: {"version":"v0.9.1","createSurface":{"surfaceId":"main","catalogId":"https://valuz.io/a2ui/catalogs/base/v1"}}',
        '- updateDataModel: {"version":"v0.9.1","updateDataModel":{"surfaceId":"main","path":"/","value":{...}}}',
        '- updateComponents: {"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":[...]}}',
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
