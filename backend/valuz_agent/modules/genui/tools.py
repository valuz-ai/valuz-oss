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
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.genui.ids import resolve_tool_use_id
from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.genui.protocol import (
    OUTPUT_FORMAT,
    a2ui_instructions,
    build_a2ui_prompt,
    extract_a2ui_document,
    normalize_component_scope,
    wrap_generated_ui,
)
from valuz_agent.modules.genui.runner import _make_completer, _resolve_provider_id
from valuz_agent.modules.providers.service import (
    resolve_model_provider_for_user as resolve_model_provider,
)
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

_EXPLICIT_VISUAL_REQUEST_RE = re.compile(
    # Chart-ish nouns, then "<verb> 图/界面/页面" for the many ways a request is
    # phrased without naming a chart type. The negative lookaheads are the
    # load-bearing part: 图 alone lives inside 图片, 图标, 地图 and 试图, and
    # matching those is what would let an ordinary request become a dashboard.
    r"(?:可视化|图形化|图表|仪表盘|看板|数据面板|行情面板|工作台|图形"
    r"|(?:柱状|条形|折线|曲线|饼|饼状|散点|热力|雷达|走势|甘特|漏斗|气泡|K\s*线)图"
    r"|(?:画|绘|绘制|做|出|加|换|来|给|要|用|生成)(?:一)?[个张幅]?图(?!片|标)"
    r"|(?:做|画|生成|来|给)(?:一)?[个张]?(?:界面|页面)"
    r"|交互(?:式)?(?:界面|图)|生成式\s*UI"
    r"|\b(?:dashboard|workbench|chart|plot|graph|visuali[sz](?:e|ation)|visual|viz"
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
            "description": "Optional structured values to render directly into the components.",
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
    UI_ARTIFACT_RECEIPT_CLOSE,
    UI_ARTIFACT_RECEIPT_OPEN,
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
    model = source.model
    runtime_provider = source.runtime_provider
    if not provider_id or not model:
        return ToolResult(
            content="generate_ui: could not resolve a model channel for this session",
            is_error=True,
        )

    try:
        mp = await resolve_model_provider(
            user_id=user_id,
            provider_id=str(provider_id),
            model_id=model,
            runtime_provider=runtime_provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: provider resolve failed", exc_info=True)
        return ToolResult(content=f"generate_ui: model channel unavailable ({exc})", is_error=True)

    tool_use_id = await resolve_tool_use_id(
        user_id=user_id, session_id=ctx.session_id, arguments=args
    )
    scope = normalize_component_scope(args.get("components"))
    target_host = _parse_target_host(args, source)
    host_context = await _load_host_generation_context(user_id, target_host)
    completer = _make_completer(
        user_id=user_id,
        runtime_provider=runtime_provider,
        model=model,
        mp=mp,
        calling_session_id=ctx.session_id if tool_use_id else None,
        tool_use_id=tool_use_id,
        session_instructions=a2ui_instructions(scope),
        output_format=OUTPUT_FORMAT,
        # Keep the MCP caller's idle timer alive while the model writes; the
        # toolkit server supplies this only when the client asked for
        # progress (see ``HostExecContext.report_progress``).
        on_progress=getattr(ctx, "report_progress", None),
    )
    try:
        generated = await _complete_with_retries(
            completer,
            build_a2ui_prompt(
                str(request),
                data,
                scope,
                current_document=(
                    host_context.current_document if host_context is not None else None
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: generation failed", exc_info=True)
        return ToolResult(content=f"generate_ui: generation failed ({exc})", is_error=True)

    generated = (generated or "").strip()
    if not generated:
        return ToolResult(
            content=f"generate_ui: model returned no {OUTPUT_FORMAT}",
            is_error=True,
        )
    # Extract before recording. The raw output routinely carries a closing
    # prose summary, and an aborted generation carries a half-written line —
    # neither belongs in a version somebody can bind a page to. The CARD still
    # shows the raw output: the renderer skips what it cannot parse, and a
    # partial render in the conversation beats an empty one.
    document = extract_a2ui_document(generated)
    receipt_trailer = ""
    if document is None:
        logger.warning(
            "generate_ui: output is not a usable document; not recorded (%d chars)",
            len(generated),
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
    return ToolResult(
        content=wrap_generated_ui(generated) + receipt_trailer,
        is_error=False,
    )


def build_generative_ui_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``generate_ui`` tool def (live handler) for the host toolkit MCP server."""
    td = ToolDef(
        name=GENERATIVE_UI_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_generate_ui_handler,
        read_only=False,
    )
    logger.info("Built generative-ui tool def: %s", GENERATIVE_UI_TOOL_NAME)
    return (td,)
