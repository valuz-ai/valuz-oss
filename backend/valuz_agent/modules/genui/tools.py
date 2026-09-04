"""generative-UI in-process MCP tool — the ``generate_ui`` tool.

Registered in the host toolkit MCP ``base`` toolset (runtime-agnostic). The
handler resolves the caller's runtime/provider/model from the calling session,
builds the A2UI prompt (component catalog + request + optional data), and
returns the A2UI v0.9.1 message stream as the tool result — which the frontend
renders with ``A2UIRenderer``. Official Claude/Codex subscription channels still run
through an ephemeral no-tools kernel session so their CLI keychain auth works;
explicit-credential channels call the model directly and stream chunks back to
the originating tool card. Best-effort: every failure becomes an ``is_error``
result.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.modules.genui.ids import resolve_tool_use_id
from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.genui.protocol import (
    OUTPUT_FORMAT,
    a2ui_instructions,
    build_a2ui_prompt,
    component_names_for_scope,
    component_property_names,
    extract_a2ui_document,
    normalize_component_names,
    normalize_component_scope,
    registered_component_data_contracts,
    registered_component_data_names,
    registered_component_data_tool_guide,
    wrap_generated_ui,
)
from valuz_agent.modules.genui.runner import _make_completer, _resolve_provider_id
from valuz_agent.modules.providers.service import (
    resolve_model_provider_for_user as resolve_model_provider,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.ui_artifact import UiArtifactTargetHost

logger = logging.getLogger(__name__)

GENERATIVE_UI_TOOL_NAME = "generate_ui"
_GENERATION_MAX_ATTEMPTS = 2
_GENERATION_RETRY_DELAY_SECONDS = 0.5
_CURRENT_DOCUMENT_MAX_BYTES = 4 * 1024 * 1024
_SUPPORTED_CATALOG_ID = "https://valuz.io/a2ui/catalogs/base/v1"
_COMPOSITION_COMPONENTS = ("Card", "Grid", "TextContent", "Separator")
# How many recent turns may carry the visual intent forward. A refinement
# ("换成柱状图", "把刚才的图加上成交额") rarely restates the request, so a
# conversation that is plainly about a chart has to keep working without the
# user re-saying the magic word.
#
# Five turns is chosen against the cost of being wrong in each direction. A
# false negative breaks a feature in front of the user; a false positive costs
# one unnecessary generation, and the model still has the tool description
# telling it not to. It is bounded rather than session-wide because intent from
# far earlier in a long conversation is not intent now.
_INTENT_LOOKBACK_TURNS = 5

_GENERATION_RETRY_GUIDANCE = (
    " Correct the generate_ui choices and call generate_ui again. Keep live "
    "market/document data in component_data: do not query another tool to inline "
    "a static substitute, and do not omit a requested live binding."
)

_STRICT_REPLACE_REQUEST_RE = re.compile(
    r"(?:只要|只需|只需要|只保留|只包含|仅展示|仅保留|不要(?:加入|添加|生成).{0,12}(?:额外|其他))"
    r"|(?:\bonly\b|nothing\s+else|no\s+(?:extra|additional)\b)",
    re.IGNORECASE,
)

_EXPLICIT_VISUAL_REQUEST_RE = re.compile(
    # Chart-ish nouns, then "<verb> 图/界面/页面" for the many ways a request is
    # phrased without naming a chart type. The negative lookaheads are the
    # load-bearing part: 图 alone lives inside 图片, 图标, 地图 and 试图, and
    # matching those is what would let an ordinary request become a dashboard.
    r"(?:可视化|图形化|图表|仪表盘|看板|数据面板|行情面板|工作台|图形"
    r"|(?:柱状|条形|折线|曲线|饼|饼状|散点|热力|雷达|走势|甘特|漏斗|气泡|K\s*线)图"
    r"|(?:画|绘|绘制|做|出|加|换|来|给|要|用|生成)(?:一)?[个张幅]?图(?!片|标)"
    r"|(?:做|画|生成|创建|制作|设计|构建|搭建|来|给)(?:一)?[个张套]?"
    r"[^，。！？\n]{0,24}(?:界面|页面)"
    # Editing a home page is explicit UI intent too. Keep the authoring verb:
    # merely reading a company's homepage must not authorize UI generation.
    r"|(?:生成|创建|设计|构建|更新|修改|调整|写入|放到)[^，。！？\n]{0,24}(?:首页|主页|页面|界面)"
    r"|\b(?:build|create|make|update|edit|modify|add|put)\b[^.!?\n]{0,60}\bhome\s?page\b"
    r"|(?:做|生成|创建|制作|设计|构建|加)(?:一)?[个张]?[^，。！？\n]{0,16}"
    r"(?:研究卡(?:片)?|信息卡(?:片)?|指标卡(?:片)?|KPI\s*卡(?:片)?|组件)"
    r"|交互(?:式)?(?:界面|图)|生成式\s*UI"
    r"|\b(?:dashboard|workbench|workspace|chart|plot|graph|visuali[sz](?:e|ation)|visual|viz"
    r"|interactive\s+ui|render\s+(?:a\s+)?ui|(?:build|create|make|render|turn)\s+"
    r"(?:(?:this|it)\s+(?:as|into)\s+)?(?:a\s+)?"
    r"(?:research|information|metric|kpi)\s+card)\b)",
    re.IGNORECASE,
)


def _requested_visual_output(messages: object) -> bool:
    """True when any of the recent turns explicitly asked for a visual.

    Bare 图 is deliberately not a keyword: it would match 地图, 图片 and 试图.
    Refinements that say only "把刚才的图…" are covered by the lookback window
    rather than by loosening the pattern, because loosening it is what would
    let an ordinary request become a dashboard.
    """

    for message in list(messages or [])[:_INTENT_LOOKBACK_TURNS]:
        user_message = getattr(message, "user_message", None)
        text = getattr(user_message, "text", "")
        if isinstance(text, str) and _EXPLICIT_VISUAL_REQUEST_RE.search(text):
            return True
    return False


def _latest_user_language_reference(messages: object) -> str | None:
    """Return the latest real user text that the tool call is acting on.

    ``request`` is written by an Agent and may translate a Chinese request into
    English before calling ``generate_ui``. The persisted user message is the
    reliable language signal, so pass it to the compiler prompt separately.
    ``list_messages`` is newest-first, matching the visual-intent lookback.
    """

    for message in list(messages or []):
        user_message = getattr(message, "user_message", None)
        text = getattr(user_message, "text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _latest_user_text(messages: object) -> str:
    return _latest_user_language_reference(messages) or ""

_PARAMS = {
    "type": "object",
    "properties": {
        "request": {
            "type": "string",
            "description": (
                "Natural-language description of the UI to generate — intent, "
                "layout, and what to show."
            ),
        },
        "data": {
            "type": "object",
            "additionalProperties": True,
            "description": (
                "Optional existing research/narrative values. Do not query live "
                "data merely to fill this field; registered live values belong "
                "in component_data and are loaded by the rendered component. Always "
                "pass a nested JSON object, never a JSON-encoded string."
            ),
        },
        "components": {
            "type": "string",
            "enum": ["all", "atoms", "edition"],
            "default": "all",
            "description": (
                "Which set of components to offer this generation. 'all' "
                "(default) is everything. 'atoms' is the general vocabulary — "
                "the complete OSS A2UI component catalog. 'edition' is "
                "only the components a vertical edition installed, for an "
                "answer that should stay in that edition's house style. A "
                "shorter menu is an easier one, so narrow whenever the shape of "
                "the answer is already clear."
            ),
        },
        "component_names": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
            "description": (
                "Exact candidate components selected for this UI. Choose the "
                "smallest set that answers the user's research intent; the "
                "tool validates the names, adds the required Stack root, and "
                "sends only these component schemas to the compiler."
            ),
        },
        "component_data": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "params": {"type": "object", "additionalProperties": True},
                },
                "required": ["component", "params"],
                "additionalProperties": False,
            },
            "description": (
                "Live components selected for the UI. Pass only a registered "
                "component name and its business params, never source ids, slots, "
                "shapes, refresh settings, paths, URLs, or pre-fetched values."
            ),
        },
        "generation_mode": {
            "type": "string",
            "enum": ["edit", "replace"],
            "description": (
                "For a bound host, use 'edit' to preserve and modify the "
                "current document, or 'replace' when the user asked to rebuild "
                "the whole page. Replace avoids sending the old document to "
                "the compiler. Omit for a new or conversation-only UI."
            ),
        },
        "target_host": {
            "type": "object",
            "description": (
                "Where the generated UI should live, when the conversation is "
                "anchored to a product host (e.g. a workbench page). Copy "
                "host_type/host_id verbatim from the host context provided to "
                "you; omit for a one-off in-conversation visual."
            ),
            "properties": {
                "host_type": {"type": "string"},
                "host_id": {"type": "string"},
                "slot": {"type": "string"},
            },
            "required": ["host_type", "host_id"],
            "additionalProperties": False,
        },
    },
    "required": ["request"],
}


def _component_param_schema(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema for one registered business param plus its Host reference.

    The old tool schema declared ``params`` as an arbitrary object and relied
    on prose for the actual contract.  Tool-calling models could therefore
    submit plausible but invalid fields and discover them only after a failed
    call.  The source registry already has the exact primitive contract, so
    expose it directly to the caller.
    """

    kind = str(spec.get("kind") or "string")
    primitive: dict[str, Any] = {"type": kind}
    if spec.get("enum"):
        primitive["enum"] = list(spec["enum"])
    if spec.get("minimum") is not None:
        primitive["minimum"] = spec["minimum"]
    if spec.get("maximum") is not None:
        primitive["maximum"] = spec["maximum"]
    description = str(spec.get("description") or "").strip()
    if description:
        primitive["description"] = description

    host_keys = [name]
    if name.endswith("s") and "comma-separated" in description:
        host_keys.append(name.removesuffix("s"))
    host_ref = {
        "type": "object",
        "properties": {"$host": {"type": "string", "enum": host_keys}},
        "required": ["$host"],
        "additionalProperties": False,
    }
    return {"oneOf": [primitive, host_ref]}


def _registered_component_data_item_schemas() -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for component, contract in registered_component_data_contracts().items():
        param_specs = dict(contract.get("param_specs") or {})
        schemas.append(
            {
                "type": "object",
                "properties": {
                    "component": {"type": "string", "enum": [component]},
                    "params": {
                        "type": "object",
                        "properties": {
                            name: _component_param_schema(name, spec)
                            for name, spec in param_specs.items()
                        },
                        "required": list(contract.get("required_params") or ()),
                        "additionalProperties": False,
                    },
                },
                "required": ["component", "params"],
                "additionalProperties": False,
            }
        )
    return schemas


def _host_from_mapping(raw: object) -> UiArtifactTargetHost | None:
    if not isinstance(raw, dict):
        return None
    host_type = str(raw.get("host_type") or "").strip()
    host_id = str(raw.get("host_id") or "").strip()
    if not host_type or not host_id:
        return None
    return UiArtifactTargetHost(
        host_type=host_type,
        host_id=host_id,
        slot=str(raw.get("slot") or "main").strip() or "main",
    )


def _parse_target_host(args: dict[str, Any], session: Any = None) -> UiArtifactTargetHost | None:
    """Where the generated UI belongs: the tool argument when the model
    supplied one, otherwise the host the TURN declared.

    The argument is an override, not the source of truth. Asking the model to
    copy the host out of its context into an argument is probabilistic — it
    forgets, and the generation then silently becomes a conversation-only
    visual for a user who is looking at that very workbench. The turn's own
    ``host_ref`` (stamped by ``turn_driver``) is the deterministic answer.
    """
    explicit = _host_from_mapping(args.get("target_host"))
    if explicit is not None:
        return explicit
    if session is None:
        return None
    valuz = ((getattr(session, "metadata", None) or {}).get("valuz") or {})
    return _host_from_mapping(valuz.get("host_ref"))


def _validate_generation_choices(
    *,
    scope: str,
    component_names: object,
    component_data: object,
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...], str | None]:
    """Validate model-authored candidates without making semantic choices.

    The caller Agent owns the research judgment. This function only guarantees
    that query component names and business params match the fixed registry.
    """

    requested_components = list(normalize_component_names(component_names))
    allowed_components = frozenset(component_names_for_scope(scope))
    unknown_components = tuple(
        name for name in requested_components if name not in allowed_components
    )
    if unknown_components:
        return (), (), (
            "unknown component name(s): " + ", ".join(unknown_components)
        )

    # These are general composition glue, not business-semantic choices. The
    # compiler routinely needs one heading, card boundary, grid, or separator
    # around the exact components the Agent selected. Offer the small shared
    # set up front so a valid page does not need a second Agent/tool round-trip
    # merely to add a structural name the compiler already chose correctly.
    if requested_components:
        requested_components.extend(
            name
            for name in _COMPOSITION_COMPONENTS
            if name in allowed_components and name not in requested_components
        )

    if component_data is None:
        return tuple(requested_components), (), None
    if not isinstance(component_data, list):
        return (), (), "'component_data' must be an array"

    known_query_components = frozenset(registered_component_data_names())
    contracts = registered_component_data_contracts()
    normalized_plans: list[dict[str, Any]] = []
    for index, raw in enumerate(component_data):
        if not isinstance(raw, dict):
            return (), (), f"component_data[{index}] must be an object"
        unknown_fields = tuple(name for name in raw if name not in {"component", "params"})
        if unknown_fields:
            return (), (), (
                f"component_data[{index}] has unsupported field(s): "
                f"{', '.join(unknown_fields)}; pass only component and params"
            )
        component = str(raw.get("component") or "").strip()
        params = raw.get("params")
        if component not in known_query_components:
            return (), (), (
                f"component_data[{index}] uses component '{component}' without "
                "a registered bound-data contract"
            )
        if component not in allowed_components:
            return (), (), (
                f"component_data[{index}] component '{component}' is outside "
                f"the '{scope}' component scope"
            )
        if not isinstance(params, dict):
            return (), (), f"component_data[{index}].params must be an object"
        contract = contracts.get(component, {})
        param_specs = dict(contract.get("param_specs") or {})
        normalized_params = dict(params)
        # Models occasionally pluralize a single-symbol parameter (or the
        # inverse) even though the registered contract is exact.  Normalize
        # only the unambiguous one-name alias; this does not invent a source or
        # change a multi-symbol literal into a single symbol.
        for name in tuple(normalized_params):
            if name in param_specs:
                continue
            singular = name.removesuffix("s") if name.endswith("s") else None
            plural = f"{name}s"
            target = (
                singular
                if singular and singular in param_specs
                else plural
                if plural in param_specs
                and "comma-separated"
                in str(param_specs[plural].get("description") or "")
                else None
            )
            if target and target not in normalized_params:
                value = normalized_params[name]
                if not (
                    target == singular
                    and isinstance(value, str)
                    and "," in value
                ):
                    normalized_params[target] = normalized_params.pop(name)
        unknown_params = tuple(
            name
            for name in normalized_params
            if param_specs and name not in param_specs
        )
        if unknown_params:
            return (), (), (
                f"component_data[{index}] component '{component}' has unknown param(s): "
                f"{', '.join(unknown_params)}; allowed params: "
                f"{', '.join(param_specs)}"
            )
        for name, spec in param_specs.items():
            if name not in normalized_params:
                continue
            value = normalized_params[name]
            if isinstance(value, dict) and set(value) == {"$host"}:
                host_key = value.get("$host")
                # A company Host owns one canonical ``symbol``.  Sources that
                # accept a comma-separated comparison universe call that wire
                # parameter ``symbols``; requiring the Host key to have the
                # same plural spelling makes even a one-company live chart
                # impossible to re-bind when the page subject changes.
                allowed_host_keys = {name}
                if name.endswith("s") and "comma-separated" in str(
                    spec.get("description") or ""
                ):
                    allowed_host_keys.add(name.removesuffix("s"))
                if host_key not in allowed_host_keys:
                    return (), (), (
                        f"component_data[{index}].params.{name} must reference "
                        "one of the compatible host keys: "
                        + ", ".join(f"'{key}'" for key in sorted(allowed_host_keys))
                    )
                continue
            kind = spec.get("kind")
            if kind == "string" and isinstance(value, (list, tuple)):
                if value and all(isinstance(item, str) and item.strip() for item in value):
                    value = ",".join(item.strip() for item in value)
                    normalized_params[name] = value
            if kind == "string" and (not isinstance(value, str) or not value.strip()):
                return (), (), (
                    f"component_data[{index}].params.{name} must be a non-empty "
                    f"string ({spec.get('description')})"
                )
            if kind == "boolean" and not isinstance(value, bool):
                return (), (), f"component_data[{index}].params.{name} must be boolean"
            if kind == "number" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                return (), (), f"component_data[{index}].params.{name} must be a number"
            if kind == "number":
                minimum = spec.get("minimum")
                maximum = spec.get("maximum")
                if (minimum is not None and value < minimum) or (
                    maximum is not None and value > maximum
                ):
                    return (), (), (
                        f"component_data[{index}].params.{name} must be between "
                        f"{minimum:g} and {maximum:g}"
                    )
            enum = tuple(spec.get("enum") or ())
            if enum and value not in enum:
                return (), (), (
                    f"component_data[{index}].params.{name} must be one of: "
                    f"{', '.join(enum)}"
                )
        required_params = tuple(contract.get("required_params") or ())
        missing_params = tuple(
            name
            for name in required_params
            if name not in normalized_params
        )
        if missing_params:
            return (), (), (
                f"component_data[{index}] component '{component}' is missing required "
                f"param(s): {', '.join(missing_params)}"
            )
        if component not in requested_components:
            requested_components.append(component)
        normalized: dict[str, Any] = {
            "component": component,
            "params": normalized_params,
            "inputs": tuple(
                {
                    **input_contract,
                    "params": {
                        **(
                            normalized_params
                            if not input_contract.get("param_map")
                            else {}
                        ),
                        **{
                            source_name: normalized_params[component_name]
                            for source_name, component_name in dict(
                                input_contract.get("param_map") or {}
                            ).items()
                            if component_name in normalized_params
                        },
                        # Developer-owned constants are authoritative even if
                        # a future component param happens to reuse the name.
                        **dict(input_contract.get("fixed_params") or {}),
                    },
                }
                for input_contract in (contract.get("inputs") or ())
            ),
            "fixed_props": dict(contract.get("fixed_props") or {}),
        }
        normalized_plans.append(normalized)
    return tuple(requested_components), tuple(normalized_plans), None


def _document_component_names(document: str | None) -> tuple[str, ...]:
    if not document:
        return ()
    names: list[str] = []
    for line in document.splitlines():
        try:
            message = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        update = message.get("updateComponents")
        if not isinstance(update, dict):
            continue
        for component in update.get("components") or ():
            if isinstance(component, dict) and isinstance(component.get("component"), str):
                names.append(component["component"])
    return tuple(dict.fromkeys(names))


def _ensure_planned_component_data_refs(
    document: str | None,
    component_data: tuple[dict[str, Any], ...],
) -> str | None:
    """Complete component-owned data metadata from the fixed registry plan."""

    if not document or not component_data:
        return document
    messages: list[dict[str, Any]] = []
    for line in document.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return document
        if not isinstance(parsed, dict):
            return document
        messages.append(parsed)

    components = [
        component
        for message in messages
        if isinstance((update := message.get("updateComponents")), dict)
        for component in (update.get("components") or ())
        if isinstance(component, dict)
    ]
    claimed_ids: set[str] = set()
    for plan in component_data:
        target = str(plan.get("component") or "")
        candidates = [
            component
            for component in components
            if component.get("component") == target
            and isinstance(component.get("id"), str)
            and component["id"] not in claimed_ids
        ]
        if not candidates:
            continue
        expected_refs = {
            str(input_contract["key"]): {
                "source": input_contract["source"],
                "params": input_contract.get("params") or {},
                **({"shape": input_contract["shape"]} if input_contract.get("shape") else {}),
                **(
                    {"refresh": {"interval": input_contract["refresh_interval"]}}
                    if input_contract.get("refresh_interval")
                    else {}
                ),
            }
            for input_contract in (plan.get("inputs") or ())
        }
        # During edit, retain instance identity by preferring an exact existing
        # ref; for new components prefer one without any compiler-authored ref.
        component = next(
            (candidate for candidate in candidates if candidate.get("dataRefs") == expected_refs),
            next(
                (candidate for candidate in candidates if "dataRefs" not in candidate),
                candidates[0],
            ),
        )
        component_id = str(component["id"])
        claimed_ids.add(component_id)
        component["dataRefs"] = expected_refs
        declared = frozenset(component_property_names(target))
        for prop, value in dict(plan.get("fixed_props") or {}).items():
            if prop in declared:
                component[prop] = value
        for input_contract in (plan.get("inputs") or ()):
            input_key = str(input_contract.get("key") or "")
            data_prefix = f"/data/{component_id}/{input_key}"
            for prop, field in dict(input_contract.get("bindings") or {}).items():
                if prop in declared:
                    component[prop] = {"path": f"{data_prefix}/{field}"}
    return "\n".join(
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for message in messages
    )


def _ensure_supported_catalog_id(document: str | None) -> str | None:
    """Pin every generated surface to the catalog the renderer registers.

    Edition components extend Valuz's base catalog at runtime; they do not
    create a second ``finance`` catalog. A compiler occasionally infers a
    plausible-looking edition URL even though the prompt gives the one valid
    id. That document passes component validation but the renderer rejects the
    entire surface. Catalog selection is host plumbing, not an Agent business
    decision, so canonicalize it deterministically before validation/storage.
    """

    if not document:
        return document
    messages: list[dict[str, Any]] = []
    for line in document.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return document
        if not isinstance(parsed, dict):
            return document
        created = parsed.get("createSurface")
        if isinstance(created, dict):
            created["catalogId"] = _SUPPORTED_CATALOG_ID
        messages.append(parsed)
    return "\n".join(
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for message in messages
    )


def _compiled_document_error(
    document: str | None,
    *,
    component_names: tuple[str, ...],
    component_data: tuple[dict[str, Any], ...],
    current_document: str | None,
    generation_mode: str,
) -> str | None:
    """Validate the compiler stayed inside the Agent's registered plan."""

    if document is None:
        return f"model returned no {OUTPUT_FORMAT}"
    messages: list[dict[str, Any]] = []
    for line in document.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return "compiler returned invalid A2UI JSONL"
        if isinstance(parsed, dict):
            messages.append(parsed)
    components: list[dict[str, Any]] = []
    for message in messages:
        update = message.get("updateDataModel")
        if isinstance(update, dict):
            path = str(update.get("path") or "")
            if path == "/refs" or path.startswith("/refs/"):
                return "surface-global refs are not supported; dataRef belongs to its component"
        component_update = message.get("updateComponents")
        if not isinstance(component_update, dict):
            continue
        components.extend(
            component
            for component in (component_update.get("components") or ())
            if isinstance(component, dict)
        )
    actual_names = tuple(
        str(component.get("component") or "") for component in components
    )
    if "Stack" not in actual_names or not any(
        component.get("id") == "root" for component in components
    ):
        return "compiler omitted the required root Stack"
    allowed_names = frozenset(
        (
            "Stack",
            *component_names,
            *(_document_component_names(current_document) if generation_mode == "edit" else ()),
        )
    )
    unexpected_names = tuple(
        dict.fromkeys(name for name in actual_names if name and name not in allowed_names)
    )
    if unexpected_names:
        return "compiler used unselected component(s): " + ", ".join(unexpected_names)

    contracts = registered_component_data_contracts()
    current_refs: dict[str, tuple[str, Any]] = {}
    if generation_mode == "edit" and current_document:
        for line in current_document.splitlines():
            try:
                current_message = json.loads(line)
            except json.JSONDecodeError:
                continue
            current_update = current_message.get("updateComponents")
            if not isinstance(current_update, dict):
                continue
            for current in current_update.get("components") or ():
                if (
                    isinstance(current, dict)
                    and isinstance(current.get("id"), str)
                    and "dataRefs" in current
                ):
                    current_refs[current["id"]] = (
                        str(current.get("component") or ""),
                        current.get("dataRefs"),
                    )

    claimed_ids: set[str] = set()
    for plan in component_data:
        target = str(plan.get("component") or "")
        expected_refs = {
            str(input_contract["key"]): {
                "source": input_contract.get("source"),
                "params": input_contract.get("params") or {},
                **({"shape": input_contract["shape"]} if input_contract.get("shape") else {}),
                **(
                    {"refresh": {"interval": input_contract["refresh_interval"]}}
                    if input_contract.get("refresh_interval")
                    else {}
                ),
            }
            for input_contract in (plan.get("inputs") or ())
        }
        matching = [
            component
            for component in components
            if component.get("component") == target
            and isinstance(component.get("id"), str)
            and component["id"] not in claimed_ids
            and component.get("dataRefs") == expected_refs
        ]
        if not matching:
            return f"planned query component '{target}' is missing its canonical dataRefs"
        component = matching[0]
        component_id = str(component["id"])
        claimed_ids.add(component_id)
        for input_contract in (plan.get("inputs") or ()):
            input_key = str(input_contract.get("key") or "")
            data_prefix = f"/data/{component_id}/{input_key}"
            for prop, field in dict(input_contract.get("bindings") or {}).items():
                if component.get(prop) != {"path": f"{data_prefix}/{field}"}:
                    return f"planned query component '{component_id}' has invalid binding '{prop}'"
        for prop, value in dict(plan.get("fixed_props") or {}).items():
            if component.get(prop) != value:
                return f"planned query component '{component_id}' changed fixed prop '{prop}'"

    for component in components:
        if "dataRef" in component:
            component_id = component.get("id") or ""
            return f"component '{component_id}' uses the unsupported single dataRef form"
        if "dataRefs" not in component:
            continue
        component_id = str(component.get("id") or "")
        component_name = str(component.get("component") or "")
        refs = component.get("dataRefs")
        contract = contracts.get(component_name)
        if not isinstance(refs, dict) or contract is None:
            return f"component '{component_id}' has unregistered dataRefs"
        canonical_inputs = {
            str(value.get("key")): str(value.get("source"))
            for value in (contract.get("inputs") or ())
        }
        actual_inputs = {
            str(key): str(value.get("source"))
            for key, value in refs.items()
            if isinstance(value, dict)
        }
        if actual_inputs != canonical_inputs:
            return f"component '{component_id}' dataRefs do not match its fixed inputs"
        if component_id in claimed_ids:
            continue
        if generation_mode != "edit":
            return f"component '{component_id}' has unplanned dataRefs"
        if current_refs.get(component_id) != (component_name, refs):
            return f"edit added or changed unplanned dataRefs on component '{component_id}'"

    if generation_mode == "edit":
        actual_ids = {str(component.get("id") or "") for component in components}
        for component_id in current_refs:
            if component_id not in claimed_ids and component_id not in actual_ids:
                return f"edit removed existing query component '{component_id}'"
    return None


@dataclass(frozen=True)
class _HostGenerationContext:
    """The exact host state one generation started from.

    Captured before the compiler runs so its input document and the later
    adoption receipt describe the same revision even if another tab moves the
    binding while the model is writing.
    """

    target_host: UiArtifactTargetHost
    expected_revision_id: str | None = None
    bound_artifact: Any = None
    current_document: str | None = None


@dataclass(frozen=True)
class _CompilerModel:
    """The isolated model channel used for A2UI compilation.

    Research stays on the caller's model.  A contributed Valuz Lite channel is
    preferred only for the constrained JSON compiler call; when the current
    owner cannot use Lite we preserve the caller's exact channel.
    """

    provider_id: str
    model: str
    runtime_provider: str
    model_provider: Any
    is_lite: bool = False


def _is_valuz_lite_model(model: Any) -> bool:
    model_id = str(getattr(model, "id", "") or "").strip().lower()
    label = str(getattr(model, "label", "") or "").strip().lower()
    return label == "valuz lite" or model_id == "valuz-lite" or model_id.startswith(
        "valuz-lite-"
    )


def _lite_channel_rank(channel: Any, model: Any) -> tuple[int, int, int, str]:
    """Prefer the system-managed Anthropic Lite route, then other Lite wires."""

    protocols = tuple(
        str(value)
        for value in (getattr(channel, "compatible_protocols", None) or ())
    )
    protocol_rank = (
        0
        if "anthropic" in protocols
        else 1
        if "openai-completion" in protocols
        else 2
    )
    source_rank = 0 if getattr(channel, "source", None) == "system" else 1
    exact_label_rank = 0 if str(getattr(model, "label", "") or "").strip() == "Valuz Lite" else 1
    return source_rank, protocol_rank, exact_label_rank, str(getattr(model, "id", ""))


async def _resolve_compiler_model(
    *,
    user_id: str,
    source_provider_id: str,
    source_model: str,
    source_runtime_provider: str,
) -> _CompilerModel:
    """Resolve Valuz Lite for GenUI, falling back to the caller's channel.

    Availability comes from the same owner-scoped provider catalog used by the
    model picker.  No model id or credential is assumed across distributions.
    """

    try:
        candidates: list[tuple[Any, Any]] = []
        for channel in await ext.llm_provider.list(user_id=user_id):
            if not getattr(channel, "enabled", True):
                continue
            for model in getattr(channel, "models", ()):
                if _is_valuz_lite_model(model):
                    candidates.append((channel, model))
        candidates.sort(key=lambda pair: _lite_channel_rank(*pair))
        for channel, model in candidates:
            protocols = tuple(
                str(value) for value in (getattr(channel, "compatible_protocols", None) or ())
            )
            declared = tuple(getattr(model, "runtimes", None) or ())
            if declared:
                runtime_provider = "deepagents" if "deepagents" in declared else str(declared[0])
            elif "anthropic" in protocols or "openai-completion" in protocols:
                runtime_provider = "deepagents"
            elif "openai-response" in protocols:
                runtime_provider = "codex"
            else:
                runtime_provider = "deepagents"
            try:
                mp = await resolve_model_provider(
                    user_id=user_id,
                    provider_id=str(channel.id),
                    model_id=str(model.id),
                    runtime_provider=runtime_provider,
                )
            except Exception:  # noqa: BLE001 — try the next Lite wire/fallback
                logger.debug(
                    "generate_ui: Valuz Lite candidate unavailable provider=%s model=%s",
                    getattr(channel, "id", None),
                    getattr(model, "id", None),
                    exc_info=True,
                )
                continue
            logger.info(
                "generate_ui: compiler model=Valuz Lite provider=%s model=%s runtime=%s",
                channel.id,
                model.id,
                runtime_provider,
            )
            return _CompilerModel(
                provider_id=str(channel.id),
                model=str(model.id),
                runtime_provider=runtime_provider,
                model_provider=mp,
                is_lite=True,
            )
    except Exception:  # noqa: BLE001 — catalog failure must not break GenUI
        logger.debug("generate_ui: Valuz Lite catalog lookup failed", exc_info=True)

    mp = await resolve_model_provider(
        user_id=user_id,
        provider_id=source_provider_id,
        model_id=source_model,
        runtime_provider=source_runtime_provider,
    )
    logger.info(
        "generate_ui: compiler falling back to caller model provider=%s model=%s runtime=%s",
        source_provider_id,
        source_model,
        source_runtime_provider,
    )
    return _CompilerModel(
        provider_id=source_provider_id,
        model=source_model,
        runtime_provider=source_runtime_provider,
        model_provider=mp,
    )


async def _resolve_source_compiler_model(
    *,
    user_id: str,
    provider_id: str,
    model: str,
    runtime_provider: str,
) -> _CompilerModel:
    """Resolve the caller model without attempting the Lite preference."""

    mp = await resolve_model_provider(
        user_id=user_id,
        provider_id=provider_id,
        model_id=model,
        runtime_provider=runtime_provider,
    )
    return _CompilerModel(
        provider_id=provider_id,
        model=model,
        runtime_provider=runtime_provider,
        model_provider=mp,
    )


def _read_revision_file(path: str) -> str | None:
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_size > _CURRENT_DOCUMENT_MAX_BYTES:
            return None
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


async def _load_host_generation_context(
    user_id: str,
    target_host: UiArtifactTargetHost | None,
) -> _HostGenerationContext | None:
    """Load the complete A2UI revision currently shown by ``target_host``.

    Hosted edits should not depend on the calling Agent remembering a
    read-before-write ceremony. Missing or unreadable bytes leave generation
    usable as a new page, while the captured binding still protects adoption.
    """

    if target_host is None:
        return None
    context = _HostGenerationContext(target_host=target_host)
    try:
        from valuz_agent.modules.artifacts.service import load_bound_host_revision

        bound = await load_bound_host_revision(
            user_id,
            host_type=target_host.host_type,
            host_id=target_host.host_id,
            slot=target_host.slot or "main",
        )
        if bound is None:
            return context
        document = bound.document_inline
        if document is None and bound.file_path:
            document = await asyncio.to_thread(_read_revision_file, bound.file_path)
        return _HostGenerationContext(
            target_host=target_host,
            expected_revision_id=bound.artifact_revision_id,
            bound_artifact=bound.artifact,
            current_document=document,
        )
    except Exception:  # noqa: BLE001 — generation remains useful without a base
        logger.exception("generate_ui: could not load the host's current A2UI revision")
        return context


# Marker wrapping the sink receipt appended to a successful tool result. The
# frontend extracts + strips it before handing the payload to the renderer;
# it rides IN the persisted tool result so the adopt affordance survives
# history replay (per the conversation-to-ui-artifact contract §7).
from valuz_agent.ports.ui_artifact import (  # noqa: E402 — canonical home
    ui_artifact_receipt_trailer,
)

_HOST_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _document_file_name(target_host: UiArtifactTargetHost | None) -> str:
    """Stable name per host slot, so regenerating a page appends a version.

    Identity in the artifact layer is the scope-relative path, so this name IS
    what makes "generate this page again" land on the deliverable the host is
    already showing rather than starting a parallel one. Host-free visuals get
    no stable name — each is its own one-off (see ``as_new_artifact`` below).
    """
    if target_host is None:
        return "generated-ui.a2ui.jsonl"
    parts = (target_host.host_type, target_host.host_id, target_host.slot or "main")
    stem = _HOST_NAME_SAFE.sub("-", ".".join(p for p in parts if p)).strip("-")
    return f"{stem}.a2ui.jsonl"


def host_document_file_name(host_type: str, host_id: str, slot: str = "main") -> str:
    """The stable document name a host slot's generations are recorded under.

    Public counterpart of ``_document_file_name`` for readers: the name is the
    cross-scope identity of a host's pages (a regeneration from another
    conversation may land in another scope but keeps this name), so listing a
    host's FULL version history — bound lineage and historical forks alike —
    means querying revisions by this name.
    """

    return _document_file_name(
        UiArtifactTargetHost(host_type=host_type, host_id=host_id, slot=slot or "main")
    )


async def _deliver_generated_ui(
    *,
    user_id: str,
    session_id: str | None,
    tool_use_id: str | None,
    target_host: UiArtifactTargetHost | None,
    request: str,
    document: str,
    host_context: _HostGenerationContext | None = None,
) -> str:
    """Record the document as an artifact revision; return a receipt trailer.

    The receipt is what lets the conversation offer "apply to the workbench"
    later — it survives history replay because it rides IN the tool result.
    Adoption is deliberately NOT done here: a generation creates a version, the
    user adopts it.

    Best-effort by contract: a delivery that fails must not fail the tool call,
    because the generated page is still useful in the conversation.
    """
    if not session_id:
        return ""
    try:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.artifacts.models import ArtifactKind
        from valuz_agent.modules.artifacts.scope import (
            ScopeUnavailableError,
            resolve_artifact_scope,
            resolve_delivery_scope,
        )
        from valuz_agent.modules.artifacts.service import DeliveryRequest, deliver_artifact

        try:
            delivery = await resolve_delivery_scope(user_id, session_id)
        except ScopeUnavailableError:
            logger.debug("generate_ui: no delivery scope; not recorded", exc_info=True)
            return ""

        file_name = _document_file_name(target_host)
        # Use the binding captured BEFORE generation. Re-reading here could
        # produce a receipt claiming the compiler edited v6 when it saw v5.
        if target_host is not None and host_context is None:
            host_context = await _load_host_generation_context(user_id, target_host)
        expected_revision_id = (
            host_context.expected_revision_id if host_context is not None else None
        )
        bound_artifact = host_context.bound_artifact if host_context is not None else None

        # A hosted regeneration appends to the lineage the host is showing —
        # the binding names it, and it may live in ANOTHER conversation's
        # scope (each panel chat is its own project). The stable per-host file
        # name was always meant to make "generate this page again" append a
        # version; identity is scoped, so without this the new conversation
        # quietly forked a parallel artifact starting over at v1. Deliver in
        # the bound artifact's own scope; if that scope no longer resolves,
        # fall back to this session's (a fork, but a recorded page beats a
        # refusal).
        target_artifact_id: str | None = None
        if bound_artifact is not None:
            own_scope = await resolve_artifact_scope(user_id, bound_artifact)
            if own_scope is not None:
                delivery = own_scope
                target_artifact_id = bound_artifact.id
            else:
                logger.warning(
                    "generate_ui: bound artifact %s scope no longer resolves; "
                    "recording a new lineage in the session's scope",
                    bound_artifact.id,
                )

        async with async_unit_of_work(commit=True) as db:
            result = await deliver_artifact(
                db,
                scope=delivery.scope,
                scope_cwd=delivery.cwd,
                owner_roots=[],  # unused by the content form — nothing to police
                request=DeliveryRequest(
                    content=document,
                    file_name=file_name,
                    display_name=file_name,
                    kind=ArtifactKind.UI,
                    artifact_id=target_artifact_id,
                    as_new_artifact=target_host is None,
                ),
                source_session_id=session_id,
            )
        if not result.ok or not result.revision_id:
            logger.warning("generate_ui: not recorded (%s)", result.status)
            return ""
    except Exception:  # noqa: BLE001 — recording is never load-bearing
        logger.exception("generate_ui: recording the document failed; skipping")
        return ""

    del tool_use_id, request  # audit context lives on the revision row itself
    return ui_artifact_receipt_trailer(
        artifact_id=result.artifact_id or "",
        revision_id=result.revision_id,
        version_no=result.version_no or 0,
        host_type=target_host.host_type if target_host else None,
        host_id=target_host.host_id if target_host else None,
        slot=(target_host.slot or "main") if target_host else "main",
        expected_revision_id=expected_revision_id,
    )


async def _complete_with_retries(
    completer: Any,
    prompt: str,
    *,
    max_attempts: int = _GENERATION_MAX_ATTEMPTS,
) -> str:
    max_attempts = max(1, max_attempts)
    for attempt in range(1, max_attempts + 1):
        try:
            document = await completer(prompt)
        except Exception:  # noqa: BLE001
            if attempt >= max_attempts:
                raise
            logger.info(
                "generate_ui: generation attempt %d/%d failed; retrying",
                attempt,
                max_attempts,
                exc_info=True,
            )
        else:
            if (document or "").strip():
                if attempt > 1:
                    logger.info(
                        "generate_ui: generation succeeded on attempt %d/%d",
                        attempt,
                        max_attempts,
                    )
                return str(document)
            if attempt >= max_attempts:
                return str(document or "")
            logger.info(
                "generate_ui: generation returned blank output on attempt %d/%d; retrying",
                attempt,
                max_attempts,
            )

        await asyncio.sleep(_GENERATION_RETRY_DELAY_SECONDS * attempt)

    return ""


async def _generate_ui_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    started_at = time.monotonic()
    user_id = ctx.user_id
    request = args.get("request")
    data = args.get("data")
    if not request or not str(request).strip():
        return ToolResult(content="generate_ui: 'request' is required", is_error=True)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return ToolResult(
                content="generate_ui: 'data' string must contain a JSON object",
                is_error=True,
            )
        if not isinstance(data, dict):
            return ToolResult(
                content="generate_ui: 'data' must be an object",
                is_error=True,
            )

    # The tool request is authored by the agent and therefore cannot grant its
    # own permission to expand the user's scope. Gate against the persisted
    # current user message so "列出..." cannot silently become a dashboard.
    try:
        messages = (
            await kernel_client.list_messages(
                user_id, ctx.session_id, limit=_INTENT_LOOKBACK_TURNS
            )
            if ctx.session_id
            else []
        )
    except Exception:  # noqa: BLE001
        logger.debug("generate_ui: user-intent lookup failed", exc_info=True)
        messages = []
    if not _requested_visual_output(messages):
        return ToolResult(
            content=(
                "generate_ui: no recent user message asked for a chart, dashboard, "
                "workspace, page, research card, visualization, or interactive UI. "
                "Answer in text instead; only call "
                "this tool once the user has asked for one."
            ),
            is_error=True,
        )

    source = await kernel_client.get_session(user_id, ctx.session_id) if ctx.session_id else None
    if source is None:
        return ToolResult(
            content="generate_ui: no active session to resolve a model from",
            is_error=True,
        )

    provider_id = _resolve_provider_id(source)
    source_model = source.model
    source_runtime_provider = source.runtime_provider
    if not provider_id or not source_model:
        return ToolResult(
            content="generate_ui: could not resolve a model channel for this session",
            is_error=True,
        )

    scope = normalize_component_scope(args.get("components"))
    requested_components = normalize_component_names(args.get("component_names"))
    scoped_names = frozenset(component_names_for_scope(scope))
    all_names = frozenset(component_names_for_scope("all"))
    # Scope is only a prompt-size hint. If the Agent selected registered
    # components outside that hint, widen the compiler catalog instead of
    # rejecting an otherwise valid business choice.
    if any(name not in scoped_names for name in requested_components) and all(
        name in all_names for name in requested_components
    ):
        scope = "all"
    component_names, component_data, choice_error = _validate_generation_choices(
        scope=scope,
        component_names=args.get("component_names"),
        component_data=args.get("component_data"),
    )
    if choice_error:
        return ToolResult(
            content=(
                f"generate_ui: invalid generation choice ({choice_error})."
                f"{_GENERATION_RETRY_GUIDANCE}"
            ),
            is_error=True,
        )

    try:
        compiler = await _resolve_compiler_model(
            user_id=user_id,
            source_provider_id=str(provider_id),
            source_model=source_model,
            source_runtime_provider=source_runtime_provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: provider resolve failed", exc_info=True)
        return ToolResult(content=f"generate_ui: model channel unavailable ({exc})", is_error=True)

    tool_use_id = await resolve_tool_use_id(
        user_id=user_id, session_id=ctx.session_id, arguments=args
    )
    target_host = _parse_target_host(args, source)
    host_context = await _load_host_generation_context(user_id, target_host)
    generation_mode = str(args.get("generation_mode") or "").strip().lower()
    # A hosted generation is a replacement unless the caller explicitly chose
    # edit. Feeding an old page into an omitted/default mode makes the compiler
    # leak unrelated components into a newly requested page, and then forces
    # repeated calls just to enumerate those accidental components.
    if target_host is not None and generation_mode != "edit":
        generation_mode = "replace"
    user_text = _latest_user_text(messages)
    if _STRICT_REPLACE_REQUEST_RE.search(user_text):
        logger.info(
            "generate_ui: overriding edit to replace for strict user scope session=%s",
            ctx.session_id,
        )
        generation_mode = "replace"
    current_document = (
        None
        if generation_mode == "replace"
        else host_context.current_document
        if host_context is not None
        else None
    )
    def _completer_for(
        selected: _CompilerModel,
        *,
        stream_to_tool: bool,
    ) -> Any:
        return _make_completer(
            user_id=user_id,
            runtime_provider=selected.runtime_provider,
            model=selected.model,
            mp=selected.model_provider,
            calling_session_id=(
                ctx.session_id if stream_to_tool and tool_use_id else None
            ),
            tool_use_id=tool_use_id if stream_to_tool else None,
            session_instructions=a2ui_instructions(scope),
            output_format=OUTPUT_FORMAT,
            # Keep the MCP caller's idle timer alive while the model writes;
            # the toolkit server supplies this only when the client asked for
            # progress (see ``HostExecContext.report_progress``).
            on_progress=getattr(ctx, "report_progress", None),
        )

    completer = _completer_for(compiler, stream_to_tool=True)
    prompt = build_a2ui_prompt(
        str(request),
        data,
        scope,
        current_document=current_document,
        language_reference=_latest_user_language_reference(messages),
        component_names=component_names,
        component_data=component_data,
    )
    logger.info(
        "generate_ui: compile start model=%s lite=%s scope=%s candidates=%d "
        "live_components=%d prompt_chars=%d mode=%s",
        compiler.model,
        compiler.is_lite,
        scope,
        len(component_names),
        len(component_data),
        len(prompt),
        generation_mode or "new",
    )
    primary_error: Exception | None = None
    try:
        generated = await _complete_with_retries(
            completer,
            prompt,
            # A small compiler should either work promptly or yield to the
            # caller's stronger model; retrying Lite first doubles the exact
            # latency this path exists to remove.
            max_attempts=1 if compiler.is_lite else _GENERATION_MAX_ATTEMPTS,
        )
    except Exception as exc:  # noqa: BLE001
        primary_error = exc
        generated = ""

    generated = (generated or "").strip()
    primary_document = _ensure_planned_component_data_refs(
        _ensure_supported_catalog_id(
            extract_a2ui_document(generated) if generated else None
        ),
        component_data,
    )
    has_generation_plan = bool(component_names or component_data)
    primary_validation_error = (
        _compiled_document_error(
            primary_document,
            component_names=component_names,
            component_data=component_data,
            current_document=current_document,
            generation_mode=generation_mode,
        )
        if has_generation_plan
        else None
    )
    if compiler.is_lite and (
        primary_document is None or primary_validation_error is not None
    ):
        logger.warning(
            "generate_ui: Valuz Lite produced an invalid planned document (%s); "
            "falling back to caller model=%s",
            primary_validation_error or "no usable A2UI document",
            source_model,
            exc_info=primary_error is not None,
        )
        try:
            compiler = await _resolve_source_compiler_model(
                user_id=user_id,
                provider_id=str(provider_id),
                model=source_model,
                runtime_provider=source_runtime_provider,
            )
            # The Lite stream may already have painted a partial preview. Do
            # not concatenate a second document into that delta stream; the
            # canonical final ToolResult replaces it when fallback completes.
            generated = await _complete_with_retries(
                _completer_for(compiler, stream_to_tool=False),
                prompt,
            )
            generated = (generated or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug("generate_ui: fallback generation failed", exc_info=True)
            return ToolResult(
                content=f"generate_ui: generation failed ({exc})",
                is_error=True,
            )
    elif primary_error is not None:
        logger.debug("generate_ui: generation failed", exc_info=primary_error)
        return ToolResult(
            content=f"generate_ui: generation failed ({primary_error})",
            is_error=True,
        )

    if not generated:
        return ToolResult(
            content=f"generate_ui: model returned no {OUTPUT_FORMAT}",
            is_error=True,
        )
    # Extract before recording. The raw output routinely carries a closing
    # prose summary, an aborted half-written line, or a second declaration of
    # the same surface. None belongs in a bindable version. When extraction
    # succeeds, preview exactly the canonical document that was recorded so
    # the confirmation card can never disagree with the revision it applies.
    document = _ensure_planned_component_data_refs(
        _ensure_supported_catalog_id(extract_a2ui_document(generated)),
        component_data,
    )
    validation_error = (
        _compiled_document_error(
            document,
            component_names=component_names,
            component_data=component_data,
            current_document=current_document,
            generation_mode=generation_mode,
        )
        if has_generation_plan
        else None
    )
    receipt_trailer = ""
    if validation_error is not None:
        logger.warning(
            "generate_ui: output is not a valid planned document; not recorded "
            "(%d chars): %s",
            len(generated),
            validation_error,
        )
        return ToolResult(
            content=(
                "generate_ui: generated document failed validation "
                f"({validation_error}).{_GENERATION_RETRY_GUIDANCE}"
            ),
            is_error=True,
        )
    else:
        receipt_trailer = await _deliver_generated_ui(
            user_id=user_id,
            session_id=ctx.session_id,
            tool_use_id=tool_use_id,
            target_host=target_host,
            request=str(request),
            document=document,
            host_context=host_context,
        )
    logger.info(
        "generate_ui: compile finished model=%s lite=%s elapsed_ms=%d "
        "generated_chars=%d usable=%s",
        compiler.model,
        compiler.is_lite,
        int((time.monotonic() - started_at) * 1000),
        len(generated),
        document is not None,
    )
    return ToolResult(
        content=wrap_generated_ui(
            document if document is not None else generated,
            hosted=target_host is not None,
        )
        + receipt_trailer,
        is_error=False,
    )


def build_generative_ui_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``generate_ui`` tool def (live handler) for the host toolkit MCP server."""
    parameters = copy.deepcopy(_PARAMS)
    parameters["properties"]["component_names"]["items"]["enum"] = list(
        component_names_for_scope("all")
    )
    component_schemas = _registered_component_data_item_schemas()
    if component_schemas:
        parameters["properties"]["component_data"]["items"] = {
            "oneOf": component_schemas
        }
    td = ToolDef(
        name=GENERATIVE_UI_TOOL_NAME,
        description=TOOL_DESCRIPTION + registered_component_data_tool_guide(),
        parameters=parameters,
        handler=_generate_ui_handler,
        read_only=False,
    )
    logger.info("Built generative-ui tool def: %s", GENERATIVE_UI_TOOL_NAME)
    return (td,)
