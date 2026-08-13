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
    extract_a2ui_document,
    normalize_component_names,
    normalize_component_scope,
    registered_data_source_contracts,
    registered_data_source_ids,
    registered_data_source_tool_guide,
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
    "market/document data in data_sources: do not query another tool to inline "
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
    r"|交互(?:式)?(?:界面|图)|生成式\s*UI"
    r"|\b(?:dashboard|workbench|workspace|chart|plot|graph|visuali[sz](?:e|ation)|visual|viz"
    r"|interactive\s+ui|render\s+(?:a\s+)?ui)\b)",
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
            "description": (
                "Optional existing research/narrative values. Do not query live "
                "data merely to fill this field; registered live values belong "
                "in data_sources and are loaded by the rendered binding."
            ),
            "additionalProperties": True,
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
        "data_sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string"},
                    "source": {"type": "string"},
                    "params": {"type": "object", "additionalProperties": True},
                    "shape": {"type": "string"},
                    "refresh_interval": {"type": "number"},
                },
                "required": ["slot", "source", "params"],
                "additionalProperties": False,
            },
            "description": (
                "Live bindings selected for the UI. Pass source ids and params, "
                "not pre-fetched values; the A2UI document declares refs and the "
                "host loads them immediately and keeps them refreshed."
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
    data_sources: object,
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...], str | None]:
    """Validate model-authored candidates without making semantic choices.

    The caller Agent owns the research judgment. This function only guarantees
    that the compiler receives exact registered component/source identifiers.
    """

    requested_components = normalize_component_names(component_names)
    allowed_components = frozenset(component_names_for_scope(scope))
    unknown_components = tuple(
        name for name in requested_components if name not in allowed_components
    )
    if unknown_components:
        return (), (), (
            "unknown component name(s): " + ", ".join(unknown_components)
        )

    if data_sources is None:
        return requested_components, (), None
    if not isinstance(data_sources, list):
        return (), (), "'data_sources' must be an array"

    known_sources = frozenset(registered_data_source_ids())
    contracts = registered_data_source_contracts()
    normalized_sources: list[dict[str, Any]] = []
    for index, raw in enumerate(data_sources):
        if not isinstance(raw, dict):
            return (), (), f"data_sources[{index}] must be an object"
        slot = str(raw.get("slot") or "").strip()
        source = str(raw.get("source") or "").strip()
        params = raw.get("params")
        if not slot or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", slot):
            return (), (), f"data_sources[{index}] has an invalid slot"
        if source not in known_sources:
            return (), (), f"data_sources[{index}] uses unknown source '{source}'"
        if not isinstance(params, dict):
            return (), (), f"data_sources[{index}].params must be an object"
        contract = contracts.get(source, {})
        param_specs = dict(contract.get("param_specs") or {})
        normalized_params = dict(params)
        unknown_params = tuple(
            name
            for name in normalized_params
            if param_specs and name not in param_specs
        )
        if unknown_params:
            return (), (), (
                f"data_sources[{index}] source '{source}' has unknown param(s): "
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
                        f"data_sources[{index}].params.{name} must reference "
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
                    f"data_sources[{index}].params.{name} must be a non-empty "
                    f"string ({spec.get('description')})"
                )
            if kind == "boolean" and not isinstance(value, bool):
                return (), (), f"data_sources[{index}].params.{name} must be boolean"
            if kind == "number" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                return (), (), f"data_sources[{index}].params.{name} must be a number"
            if kind == "number":
                minimum = spec.get("minimum")
                maximum = spec.get("maximum")
                if (minimum is not None and value < minimum) or (
                    maximum is not None and value > maximum
                ):
                    return (), (), (
                        f"data_sources[{index}].params.{name} must be between "
                        f"{minimum:g} and {maximum:g}"
                    )
            enum = tuple(spec.get("enum") or ())
            if enum and value not in enum:
                return (), (), (
                    f"data_sources[{index}].params.{name} must be one of: "
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
                f"data_sources[{index}] source '{source}' is missing required "
                f"param(s): {', '.join(missing_params)}"
            )
        accepted_components = tuple(contract.get("accepted_components") or ())
        if accepted_components and not any(
            name in requested_components for name in accepted_components
        ):
            return (), (), (
                f"data_sources[{index}] source '{source}' requires compatible "
                f"component(s): {', '.join(accepted_components)}"
            )
        normalized: dict[str, Any] = {
            "slot": slot,
            "source": source,
            "params": normalized_params,
        }
        shape = str(raw.get("shape") or "").strip()
        if shape:
            normalized["shape"] = shape
        refresh = raw.get("refresh_interval")
        if isinstance(refresh, (int, float)) and refresh > 0:
            normalized["refresh_interval"] = refresh
        normalized_sources.append(normalized)
    return requested_components, tuple(normalized_sources), None


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


def _compiled_document_error(
    document: str | None,
    *,
    component_names: tuple[str, ...],
    data_sources: tuple[dict[str, Any], ...],
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
    components = [
        component
        for message in messages
        for component in (message.get("updateComponents") or {}).get("components", ())
        if isinstance(component, dict)
    ]
    actual_names = tuple(
        str(component.get("component") or "") for component in components
    )
    if "Stack" not in actual_names or not any(component.get("id") == "root" for component in components):
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

    refs: dict[str, dict[str, Any]] = {}
    for message in messages:
        update = message.get("updateDataModel")
        if not isinstance(update, dict):
            continue
        path = str(update.get("path") or "")
        value = update.get("value")
        if path.startswith("/refs/") and isinstance(value, dict):
            refs[path.removeprefix("/refs/")] = value

    planned = {str(source["slot"]): source for source in data_sources}
    for slot, source in planned.items():
        actual = refs.get(slot)
        if actual is None:
            return f"compiler omitted planned live binding '{slot}'"
        if actual.get("source") != source.get("source") or actual.get("params") != source.get("params"):
            return f"compiler changed planned live binding '{slot}'"
        contract = registered_data_source_contracts().get(str(source.get("source")), {})
        accepted = frozenset(contract.get("accepted_components") or ())
        data_prefix = f"/data/{slot}"
        visible = False
        for component in components:
            if component.get("component") not in accepted:
                continue
            encoded = json.dumps(component, ensure_ascii=False, separators=(",", ":"))
            if data_prefix in encoded:
                visible = True
                break
        if accepted and not visible:
            return (
                f"planned live binding '{slot}' is not rendered by "
                f"{', '.join(sorted(accepted))}"
            )

    if generation_mode != "edit":
        unexpected_refs = tuple(slot for slot in refs if slot not in planned)
        if unexpected_refs:
            return "compiler added unplanned live binding(s): " + ", ".join(unexpected_refs)
    elif current_document:
        current_refs: dict[str, dict[str, Any]] = {}
        for line in current_document.splitlines():
            try:
                update = json.loads(line).get("updateDataModel")
            except (AttributeError, json.JSONDecodeError):
                continue
            if isinstance(update, dict):
                path = str(update.get("path") or "")
                if path.startswith("/refs/") and isinstance(update.get("value"), dict):
                    current_refs[path.removeprefix("/refs/")] = update["value"]
        for slot, value in current_refs.items():
            if slot not in planned and refs.get(slot) != value:
                return f"edit removed or changed existing live binding '{slot}'"
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
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.artifacts.datastore import ArtifactDatastore

        async with async_unit_of_work(commit=False) as db:
            ds = ArtifactDatastore(db)
            binding = await ds.get_binding(
                user_id,
                target_host.host_type,
                target_host.host_id,
                target_host.slot or "main",
            )
            if binding is None:
                return context
            revision = await ds.get_revision(user_id, binding.artifact_revision_id)
            artifact = await ds.get_artifact(user_id, binding.artifact_id)
            content = (
                await ds.get_content(user_id, revision.content_id)
                if revision is not None
                else None
            )
            document = content.content_inline if content is not None else None
            file_path = str(getattr(revision, "abs_path", "") or "")
        if document is None and file_path:
            document = await asyncio.to_thread(_read_revision_file, file_path)
        return _HostGenerationContext(
            target_host=target_host,
            expected_revision_id=binding.artifact_revision_id,
            bound_artifact=artifact,
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
                "visualization, or interactive UI. Answer in text instead; only call "
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
    component_names, data_sources, choice_error = _validate_generation_choices(
        scope=scope,
        component_names=args.get("component_names"),
        data_sources=args.get("data_sources"),
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
    user_text = _latest_user_text(messages)
    if generation_mode == "edit" and _STRICT_REPLACE_REQUEST_RE.search(user_text):
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
        data_sources=data_sources,
    )
    logger.info(
        "generate_ui: compile start model=%s lite=%s scope=%s candidates=%d "
        "sources=%d prompt_chars=%d mode=%s",
        compiler.model,
        compiler.is_lite,
        scope,
        len(component_names),
        len(data_sources),
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
    primary_document = extract_a2ui_document(generated) if generated else None
    has_generation_plan = bool(component_names or data_sources)
    primary_validation_error = (
        _compiled_document_error(
            primary_document,
            component_names=component_names,
            data_sources=data_sources,
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
    document = extract_a2ui_document(generated)
    validation_error = (
        _compiled_document_error(
            document,
            component_names=component_names,
            data_sources=data_sources,
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
    source_ids = registered_data_source_ids()
    if source_ids:
        parameters["properties"]["data_sources"]["items"]["properties"][
            "source"
        ]["enum"] = list(source_ids)
    td = ToolDef(
        name=GENERATIVE_UI_TOOL_NAME,
        description=TOOL_DESCRIPTION + registered_data_source_tool_guide(),
        parameters=parameters,
        handler=_generate_ui_handler,
        read_only=False,
    )
    logger.info("Built generative-ui tool def: %s", GENERATIVE_UI_TOOL_NAME)
    return (td,)
