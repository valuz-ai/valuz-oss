"""generative-UI in-process MCP tool — the ``generate_ui`` tool.

Registered in the host toolkit MCP ``base`` toolset (runtime-agnostic). The
handler resolves the caller's runtime/provider/model from the calling session,
builds the OpenUI prompt (vendored genui-lib + request + optional data), and
returns the OpenUI Lang as the tool result — which the frontend renders with
OpenUI's ``<Renderer>``. Official Claude/Codex subscription channels still run
through an ephemeral no-tools kernel session so their CLI keychain auth works;
explicit-credential channels call the model directly and stream chunks back to
the originating tool card. Best-effort: every failure becomes an ``is_error``
result.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.config import settings
from valuz_agent.modules.genui.ids import resolve_tool_use_id
from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.genui.protocol import (
    build_prompt_for_protocol,
    normalize_genui_protocol,
    output_format_for_protocol,
    session_instructions_for_protocol,
    wrap_generated_ui,
)
from valuz_agent.modules.genui.runner import _make_completer, _resolve_provider_id
from valuz_agent.modules.providers.service import (
    resolve_model_provider_for_user as resolve_model_provider,
)

logger = logging.getLogger(__name__)

GENERATIVE_UI_TOOL_NAME = "generate_ui"
_GENERATION_MAX_ATTEMPTS = 2
_GENERATION_RETRY_DELAY_SECONDS = 0.5
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
    r"(?:可视化|图形化|图表|仪表盘|看板|数据面板|行情面板|图形"
    r"|(?:柱状|条形|折线|曲线|饼|饼状|散点|热力|雷达|走势|甘特|漏斗|气泡|K\s*线)图"
    r"|(?:画|绘|绘制|做|出|加|换|来|给|要|用|生成)(?:一)?[个张幅]?图(?!片|标)"
    r"|(?:做|画|生成|来|给)(?:一)?[个张]?(?:界面|页面)"
    r"|交互(?:式)?(?:界面|图)|生成式\s*UI"
    r"|\b(?:dashboard|chart|plot|graph|visuali[sz](?:e|ation)|visual|viz"
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
    },
    "required": ["request"],
}


async def _complete_with_retries(
    completer: Any,
    prompt: str,
    *,
    max_attempts: int = _GENERATION_MAX_ATTEMPTS,
) -> str:
    max_attempts = max(1, max_attempts)
    for attempt in range(1, max_attempts + 1):
        try:
            openui = await completer(prompt)
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
            if (openui or "").strip():
                if attempt > 1:
                    logger.info(
                        "generate_ui: generation succeeded on attempt %d/%d",
                        attempt,
                        max_attempts,
                    )
                return str(openui)
            if attempt >= max_attempts:
                return str(openui or "")
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
    protocol = normalize_genui_protocol(settings.genui_protocol)
    completer = _make_completer(
        user_id=user_id,
        runtime_provider=runtime_provider,
        model=model,
        mp=mp,
        calling_session_id=ctx.session_id if tool_use_id else None,
        tool_use_id=tool_use_id,
        session_instructions=session_instructions_for_protocol(protocol),
        output_format=output_format_for_protocol(protocol),
    )
    try:
        generated = await _complete_with_retries(
            completer,
            build_prompt_for_protocol(protocol, str(request), data),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: generation failed", exc_info=True)
        return ToolResult(content=f"generate_ui: generation failed ({exc})", is_error=True)

    generated = (generated or "").strip()
    if not generated:
        return ToolResult(
            content=f"generate_ui: model returned no {output_format_for_protocol(protocol)}",
            is_error=True,
        )
    return ToolResult(content=wrap_generated_ui(protocol, generated), is_error=False)


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
