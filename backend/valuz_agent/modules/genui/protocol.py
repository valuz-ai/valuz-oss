"""A2UI prompt and payload assembly for the ``generate_ui`` tool.

A2UI v0.9.1 is the one wire protocol and the Valuz A2UI catalog is the one
component implementation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from importlib import resources
from typing import Any, Literal

from valuz_agent.ports.a2ui_components import A2UIComponentRegistry
from valuz_agent.ports.extensions import ext

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
    "property shapes from the Valuz A2UI catalog below. Emit createSurface and "
    "a renderable root shell first, then add the requested sections in reading "
    "order so streaming can paint a useful first screen before the document is "
    "complete. Write every natural-"
    "language UI label, explanation, and any visible reasoning or progress in "
    "the request's language unless the request explicitly asks for another "
    "language. Once you have emitted a complete page with its root component, "
    "stop immediately; do not plan, restart, or generate the same page again."
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

_A2UI_THEME_AND_VISUALIZATION_CONTRACT = """\
Theme and analytical visualization contract:
- The host already supplies the A2UI theme, light/dark mode, density, locale,
  accessibility preferences, and responsive container. Do not encode those
  environment choices in the document and do not imitate the host with custom
  CSS, raw colors, theme tokens, gradients, shadows, radii, typography, or
  pixel-positioned layout. Use component variants and semantic properties only.
- Choose a chart only when the data relationship requires it: ordered trend,
  categorical comparison, part-to-whole, distribution, range/target, bridge,
  flow, hierarchy, correlation, or network. Prefer a semantic edition component
  when it answers the research question; do not wrap every fact in a chart.
- A chart may select ONE registered palette for its data relationship. Use
  ocean/orchid/emerald/steel/amber for ordered single-hue data, vivid for
  distinct categories, spectrum for values around a meaningful midpoint, and
  sunset for ordered intensity, risk, probability, or stage. Omit palette when
  the deterministic default is sufficient. Never invent a palette or color.
- Use series.role when a series has stable meaning: actual, estimate, benchmark,
  target, positive, negative, total, or neutral. Semantic roles override the
  palette. Mathematical positive/negative is not market up/down, and neither is
  application success/danger. Market direction and its color convention belong
  to the host theme; retain labels, signs, line styles, or markers so color is
  never the only carrier of meaning.
- The analytical theme owns chart geometry, opacity, line treatment, bar width,
  grids, cursors, tooltips, legend styling, and light/dark contrast. The A2UI
  document owns data, relationships, semantic roles, and necessary interaction —
  not final pixels.
"""

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
For time-indexed market performance, normalization, or a reference baseline,
use TimeSeriesChart, not LineChart. Example:
{"id":"returns","component":"TimeSeriesChart","data":{"path":"/data/returns"},"xKey":"date","series":[{"key":"nvda","label":"NVDA","role":"actual"},{"key":"benchmark","label":"Benchmark","role":"benchmark"}],"normalize":true,"referenceValue":100}
All chart series entries use "label", never "name".

Generate only the requested surface content. Do not put preview/save/apply
status, confirmation instructions, or host lifecycle chrome inside the A2UI
surface; the host renders those controls around the Artifact. Treat phrases in
the user's request such as "preview first", "let me confirm", "do not save",
or "apply after confirmation" as instructions to the host, never as content to
render. Do not add a preview banner or tell the user how to save inside the
surface.

Query component data is planned and completed by generate_ui. When the prompt
lists a planned query component, create exactly one matching component instance
for each listed item and give it a stable unique id. Do not inline current
market/document values into its registered data-bearing properties. Do not
author source ids, API URLs, dataRefs metadata, refresh settings, data slots, or
binding paths: those are reserved host metadata that the tool adds after this
compiler returns. A component not listed in the query plan receives its
catalog-typed inline props. Those values are fixed in this Artifact revision
and do not refresh; they may be research narrative, a controlled calculation,
or an explicitly frozen snapshot.

When editing a current document, preserve existing component dataRefs metadata
and property bindings on components the request does not change. Never create
surface-global /refs data-model entries. Do not append an empty root data-model
update after writing /data/* values because path "/" replaces the entire data
model. Once the requested components are complete, stop."""


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


def edition_catalog_text(
    names: Sequence[str] | None = None,
    *,
    include_notes: bool = True,
    include_notes_without_entries: bool = False,
    component_data_names: Sequence[str] | None = None,
) -> str:
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

    return _component_registry().catalog_text(
        baseline=False,
        names=names,
        include_notes=include_notes,
        include_notes_without_entries=include_notes_without_entries,
        note_keys=component_data_names,
    )


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


def component_names_for_scope(
    scope: GenUIComponentScope = "all",
) -> tuple[str, ...]:
    """All exact component names available to one generation scope."""

    scope = resolve_component_scope(scope)
    registry = _component_registry()
    names: list[str] = []
    if not registry.baseline_suppressed() and scope != "edition":
        names.extend(
            re.findall(
                r"^\s*-\s*([A-Za-z0-9]+)\(",
                _load_component_catalog(),
                re.MULTILINE,
            )
        )
    if scope != "atoms":
        names.extend(registry.registered_names())
    # Stack is the mandatory root even for an edition-only vocabulary.
    return tuple(dict.fromkeys(("Stack", *names)))


def component_property_names(name: str) -> tuple[str, ...]:
    """Return generated public properties for one registered component.

    The catalog is the cross-runtime contract shown to the UI compiler. Reuse
    it for deterministic document completion so the OSS tool can recognize
    edition properties without hard-coding an edition component list.
    """

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
        return ()
    catalogs = (_load_component_catalog(), edition_catalog_text(include_notes=False))
    for catalog in catalogs:
        match = re.search(
            rf"(?m)^\s*-\s*{re.escape(name)}\(([^)]*)\)",
            catalog,
        )
        if match is None:
            continue
        properties: list[str] = []
        for raw in match.group(1).split(","):
            candidate = raw.strip().split(":", 1)[0].strip().removesuffix("?")
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", candidate):
                properties.append(candidate)
        return tuple(dict.fromkeys(properties))
    return ()


def normalize_component_names(value: object) -> tuple[str, ...]:
    """Model-authored exact candidates, de-duplicated and syntax checked."""

    if not isinstance(value, (list, tuple)):
        return ()
    names: list[str] = []
    for raw in value:
        name = str(raw or "").strip()
        if name and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
            names.append(name)
    return tuple(dict.fromkeys(names))


_COMPONENT_DATA_CONTRACT_PREFIX = "COMPONENT_DATA_CONTRACT "


def _parse_param_specs(params_doc: str) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    """Parse the compact business-param grammar generated by an edition."""

    inner = params_doc.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    required: list[str] = []
    param_specs: dict[str, dict[str, Any]] = {}
    for raw_field in inner.split(","):
        name_part, separator, raw_description = raw_field.partition(":")
        field = name_part.strip()
        if not separator or not field:
            continue
        optional = field.endswith("?")
        name = field.removesuffix("?")
        description = raw_description.strip()
        spec: dict[str, Any] = {
            "required": not optional,
            "description": description,
            "kind": "string",
        }
        if description == "boolean":
            spec["kind"] = "boolean"
        else:
            range_match = re.fullmatch(r"(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)", description)
            if range_match:
                spec.update(
                    kind="number",
                    minimum=float(range_match.group(1)),
                    maximum=float(range_match.group(2)),
                )
            elif "|" in description:
                spec["enum"] = tuple(
                    value.strip() for value in description.split("|") if value.strip()
                )
        param_specs[name] = spec
        if not optional:
            required.append(name)
    return tuple(dict.fromkeys(required)), param_specs


def registered_component_data_contracts() -> dict[str, dict[str, Any]]:
    """Fixed component → named-input contracts from generated edition notes.

    A component may combine several sources. The edition owns every input's
    source, shape, bindings and parameter projection; the Agent still supplies
    one component-level business-parameter object.
    """

    notes = edition_catalog_text(include_notes_without_entries=True)
    contracts: dict[str, dict[str, Any]] = {}
    for raw_line in notes.splitlines():
        line = raw_line.strip()
        if not line.startswith(_COMPONENT_DATA_CONTRACT_PREFIX):
            continue
        try:
            payload = json.loads(line.removeprefix(_COMPONENT_DATA_CONTRACT_PREFIX))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        component = str(payload.get("component") or "").strip()
        params_doc = str(payload.get("params") or "{}").strip()
        raw_inputs = payload.get("inputs")
        if not isinstance(raw_inputs, list):
            continue
        inputs: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for raw_input in raw_inputs:
            if not isinstance(raw_input, dict):
                continue
            key = str(raw_input.get("key") or "").strip()
            source = str(raw_input.get("source") or "").strip()
            raw_bindings = raw_input.get("bindings")
            bindings = (
                {
                    str(prop): str(field)
                    for prop, field in raw_bindings.items()
                    if isinstance(prop, str) and prop and isinstance(field, str) and field
                }
                if isinstance(raw_bindings, dict)
                else {}
            )
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key) or key in seen_keys:
                continue
            if not source or not bindings:
                continue
            seen_keys.add(key)
            fixed_params = raw_input.get("fixedParams")
            param_map = raw_input.get("paramMap")
            inputs.append({
                "key": key,
                "source": source,
                "shape": str(raw_input.get("shape") or "").strip(),
                "bindings": bindings,
                "fixed_params": dict(fixed_params) if isinstance(fixed_params, dict) else {},
                "param_map": {
                    str(source_name): str(component_name)
                    for source_name, component_name in param_map.items()
                    if isinstance(source_name, str) and source_name
                    and isinstance(component_name, str) and component_name
                } if isinstance(param_map, dict) else {},
                "refresh_interval": raw_input.get("refreshInterval"),
            })
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", component) or not inputs:
            continue
        required, param_specs = _parse_param_specs(params_doc)
        fixed_props = payload.get("fixedProps")
        contracts[component] = {
            "component": component,
            "required_params": required,
            "param_specs": param_specs,
            "inputs": tuple(inputs),
            "fixed_props": dict(fixed_props) if isinstance(fixed_props, dict) else {},
        }
    return contracts


def registered_component_data_names() -> tuple[str, ...]:
    return tuple(registered_component_data_contracts())


def registered_component_data_tool_guide() -> str:
    """Compact business-parameter menu for the calling Agent."""

    lines: list[str] = []
    for component, contract in registered_component_data_contracts().items():
        params: list[str] = []
        for name, spec in dict(contract.get("param_specs") or {}).items():
            suffix = "" if spec.get("required") else "?"
            description = str(spec.get("description") or spec.get("kind") or "value")
            params.append(f"{name}{suffix}: {description}")
        lines.append(f"- {component} {{{', '.join(params)}}}")
    if not lines:
        return ""
    return (
        "\nRegistered query components (one component may combine several fixed named "
        "data inputs; pass only exact component-level business params; all other "
        "catalog components use typed revision-fixed inline props):\n"
        + "\n".join(lines)
        + '\nUse component_data entries shaped as {"component":"Name","params":{...}}. '
        'On a Host that provides a param, write its value as {"$host":"param"}. '
        "The registry projects those params into every required input and loads them "
        "in parallel. Do not pass source ids, input keys, slots, shapes, refresh "
        "settings, paths, or URLs."
    )


def build_a2ui_catalog(
    scope: GenUIComponentScope = "all",
    component_names: Sequence[str] | None = None,
    *,
    include_edition_data_notes: bool = False,
    component_data_names: Sequence[str] | None = None,
) -> str:
    """The A2UI catalog for one scope.

    The OSS half is generated from the same strict schemas the renderer uses;
    edition entries are appended by the distribution registry.
    """

    scope = resolve_component_scope(scope)
    requested = tuple(component_names or ())
    allowed = frozenset(component_names_for_scope(scope))
    selected = tuple(name for name in requested if name in allowed)
    # Stack is structural, not a business choice, and every valid surface needs
    # it. If no requested candidate is valid, widen safely instead of handing
    # the compiler an unusable empty vocabulary.
    if requested and selected:
        selected = tuple(dict.fromkeys(("Stack", *selected)))
        selection: tuple[str, ...] | None = selected
    else:
        selection = None

    edition = edition_catalog_text(
        selection,
        include_notes=include_edition_data_notes or selection is None,
        include_notes_without_entries=include_edition_data_notes,
        component_data_names=component_data_names,
    )

    # Take only the generated baseline lines so edition entries stay in their
    # own titled section below.
    if selection is not None:
        own_catalog = "\n".join(
            line
            for line in _load_component_catalog().splitlines()
            if any(line.lstrip().startswith(f"- {name}(") for name in selection)
        )
    else:
        own_catalog = _load_component_catalog()
    own = f"Valuz A2UI component catalog:\n{own_catalog}\n"
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
    return (
        f"{components}{_A2UI_MESSAGE_SHAPE.replace('{fallbacks}', fallbacks)}\n"
        f"{_A2UI_THEME_AND_VISUALIZATION_CONTRACT}"
    )


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
    current_document: str | None = None,
    language_reference: str | None = None,
    component_names: Sequence[str] | None = None,
    component_data: Sequence[object] | None = None,
) -> str:
    parts = [
        a2ui_instructions(scope),
        "",
        "A2UI v0.9.1 message contract:",
        '- createSurface: {"version":"v0.9.1","createSurface":{"surfaceId":"main","catalogId":"https://valuz.io/a2ui/catalogs/base/v1"}}',
        '- updateDataModel: {"version":"v0.9.1","updateDataModel":'
        '{"surfaceId":"main","path":"/","value":{...}}}',
        '- updateComponents: {"version":"v0.9.1","updateComponents":'
        '{"surfaceId":"main","components":[...]}}',
        '- every UI must include a component with id "root"; put the visible '
        'tree under root.children.',
        "",
        build_a2ui_catalog(
            scope,
            component_names,
            include_edition_data_notes=False,
        ).strip(),
    ]
    if current_document:
        parts.extend(
            [
                "",
                "CURRENT HOST DOCUMENT (the complete A2UI document currently bound "
                "to the target host):",
                current_document.strip(),
                "",
                "EDIT CONTRACT:",
                "Return a complete replacement A2UI document, not a patch. Preserve every current "
                "component, component dataRefs metadata, data binding, and layout choice "
                "that the request does not change. "
                "Apply the requested change to this document; replace the whole page only when the "
                "request explicitly asks for a replacement.",
            ]
        )
    if language_reference:
        parts.extend(
            [
                "",
                "OUTPUT LANGUAGE:",
                "Match the language of the user's original message below for every "
                "natural-language UI label and every visible reasoning or progress "
                "message. The Agent-authored REQUEST may be a translation or paraphrase "
                "and must not change the output language.",
                language_reference.strip(),
            ]
        )
    parts.extend(["", "REQUEST:", request.strip()])
    if component_data:
        compiler_plan = []
        for raw in component_data:
            if not isinstance(raw, dict):
                continue
            compiler_plan.append(
                {
                    "component": raw.get("component"),
                    "params": raw.get("params") or {},
                    "inputs": [
                        {
                            "key": input_contract.get("key"),
                            "bindings": dict(input_contract.get("bindings") or {}),
                        }
                        for input_contract in (raw.get("inputs") or ())
                    ],
                    **(
                        {"fixedProps": raw.get("fixed_props")}
                        if raw.get("fixed_props")
                        else {}
                    ),
                }
            )
        parts.extend(
            [
                "",
                "PLANNED QUERY COMPONENTS (create one matching component per item; "
                "do not query or inline their current values):",
                json.dumps(compiler_plan, ensure_ascii=False),
                "generate_ui will add the component-owned named dataRefs, fixed "
                "sources, refresh policies, and /data/<componentId>/<inputKey> "
                "bindings after compilation. "
                "Do not author any of those fields yourself.",
            ]
        )
    if data is not None:
        parts.append("")
        parts.append(
            "EXISTING RESEARCH DATA (use as narrative/frozen content; never treat "
            "it as a replacement for a registered query component):"
        )
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)


def wrap_generated_ui(content: str, *, hosted: bool = False) -> str:
    """Wrap the generated stream in the envelope the client dispatches on."""

    body = (content or "").strip()
    payload = {"protocol": "a2ui-json", "content": body}
    if hosted:
        # The renderer reads only protocol/content. This field is deliberately
        # adjacent to the successful result so the calling Agent sees the
        # post-tool contract after a potentially multi-minute compilation.
        payload["agent_instruction"] = (
            "Reply with exactly one short sentence saying the workspace preview "
            "is ready to inspect. Do not recap values, components, sources, "
            "bindings, or refresh settings."
        )
    return json.dumps(
        payload,
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
    """The complete JSON messages in ``raw`` and whether its tail is cut.

    The wire format is JSONL, but models sometimes pretty-print each top-level
    message across several lines. Decode top-level objects from the stream
    rather than assuming one physical line per object; otherwise a complete
    pretty-printed page looks truncated and the runner asks for three needless
    continuation turns after the UI is already visible.
    """
    decoder = json.JSONDecoder()
    lines: list[str] = []
    cursor = 0
    while True:
        match = re.search(r"(?m)^[ \t]*\{", raw[cursor:])
        if match is None:
            return lines, False
        start = cursor + match.start() + len(match.group(0)) - 1
        try:
            message, end = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            return lines, True
        cursor = end
        if not isinstance(message, dict):
            continue
        if not any(key in message for key in _A2UI_MESSAGE_KEYS):
            continue
        lines.append(
            json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        )


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
    kept, _truncated = a2ui_message_lines(raw)
    saw_components = any("updateComponents" in json.loads(line) for line in kept)
    # Still require a real page: a run truncated BEFORE its first complete
    # ``updateComponents`` has nothing to show and is rejected as before.
    if not saw_components:
        return None
    canonical = _latest_surface_declaration(kept)
    return "\n".join(_without_destructive_trailing_root_reset(canonical))


def _without_destructive_trailing_root_reset(lines: list[str]) -> list[str]:
    """Drop a model's final ``path=/, value={}`` after it populated data.

    A root data-model update is a full replacement in A2UI. Models sometimes
    finish an otherwise valid live document with an empty root update, as if it
    were a harmless terminator. It is not: it erases every ``/data/*`` value
    written above it, so all dynamic component props become unresolved and
    strict nested components can crash the host.

    Narrow by construction: only an *empty* root replacement is removed, only
    when the same surface already received a non-root data-model update, and
    only when no later non-root update repopulates it. A root seed used as the
    document's actual data payload remains untouched.
    """

    messages = [json.loads(line) for line in lines]
    populated_surfaces: set[str] = set()
    drop: set[int] = set()
    for index, message in enumerate(messages):
        update = message.get("updateDataModel")
        if not isinstance(update, dict):
            continue
        surface_id = update.get("surfaceId")
        path = update.get("path")
        if not isinstance(surface_id, str):
            continue
        if path != "/":
            populated_surfaces.add(surface_id)
            continue
        if update.get("value") == {} and surface_id in populated_surfaces:
            drop.add(index)
            populated_surfaces.discard(surface_id)
    return [line for index, line in enumerate(lines) if index not in drop]


def _latest_surface_declaration(lines: list[str]) -> list[str]:
    """Keep each surface's final declaration when a model restarts it.

    A runtime can join multiple model-end segments into one assistant message.
    Models also sometimes review their first page and emit a second, slightly
    revised full document in the same turn. Both cases produce a second
    ``createSurface`` for the same surface id. A2UI does not define that as two
    pages: the later declaration is the model's final replacement.

    Different surface ids are preserved. For every id, messages older than its
    final ``createSurface`` are discarded while other surfaces remain in their
    original order. This handles both a whole-document second pass and a model
    that revises only one surface inside a legitimate multi-surface document.
    """
    messages = [json.loads(line) for line in lines]
    last_declaration: dict[str, int] = {}
    message_surface_ids: list[str | None] = []
    for index, message in enumerate(messages):
        surface_id = _a2ui_surface_id(message)
        message_surface_ids.append(surface_id)
        declaration = message.get("createSurface")
        if surface_id is not None and isinstance(declaration, dict):
            last_declaration[surface_id] = index
    return [
        line
        for index, (line, surface_id) in enumerate(
            zip(lines, message_surface_ids, strict=True)
        )
        if surface_id is None or index >= last_declaration.get(surface_id, 0)
    ]


def _a2ui_surface_id(message: dict[str, Any]) -> str | None:
    """Surface id carried by one supported document-state message."""
    for key in _A2UI_MESSAGE_KEYS:
        payload = message.get(key)
        if not isinstance(payload, dict):
            continue
        surface_id = payload.get("surfaceId")
        if isinstance(surface_id, str) and surface_id:
            return surface_id
    return None
