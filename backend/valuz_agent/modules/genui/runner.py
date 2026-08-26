"""Generative-UI completers — the LLM-call seam.

Official Claude/Codex subscription channels use a throwaway no-tools kernel
session, mirroring ``modules/memory/runner.py::_make_completer``, because their
credentials live in the CLI keychain and the runtime self-authenticates.
Explicit-credential channels skip session creation and call the chat model
directly, streaming chunks to the originating tool card. Best-effort by
contract — failures bubble to the tool handler, which converts them to an error
result without affecting the originating turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.genui.protocol import OUTPUT_FORMAT, a2ui_instructions

logger = logging.getLogger(__name__)

# Minimum gap between MCP progress heartbeats. Frequent enough that a long
# generation reads as alive in the caller's log, far enough off the token rate
# that it never becomes a notification flood. It does NOT extend any client's
# deadline (measured) — that is the server's ``tool_timeout_sec``.
_PROGRESS_INTERVAL_S = 15.0

Completer = Callable[[str], Awaitable[str]]
_LOG_PREVIEW_CHARS = 240
_DIRECT_GENUI_MAX_TOKENS = 16384
_DIRECT_GENUI_REASONING_EFFORT = "low"

# How many follow-up turns may continue a generation that stopped at the
# output-token cap before we deliver whatever complete lines we have. A page
# large enough to need more than this is pathological; the salvage path still
# stores the complete prefix.
_GENERATION_MAX_CONTINUATIONS = 3

# Ephemeral event type → live event type emitted on the CALLING session.
# Only the generated A2UI document is forwarded. The compiler's private
# reasoning is implementation detail, often uses a provider-selected language,
# and can be very long; the host already presents localized generation status.
_FORWARD_TYPES = {
    "text_delta": "tool_output_delta",
}


def _resolve_provider_id(source: Any) -> str | None:
    """Provider id for the ephemeral session: prefer the host-stamped
    ``valuz.locked_provider_id`` (chat/project), fall back to the embedded
    agent config's ``metadata.provider_id`` (task lead)."""
    valuz = (getattr(source, "metadata", None) or {}).get("valuz", {}) or {}
    pid = valuz.get("locked_provider_id")
    if pid:
        return str(pid)
    ac = getattr(source, "agent_config", None)
    meta = (getattr(ac, "metadata", None) or {}) if ac is not None else {}
    pid = meta.get("provider_id")
    return str(pid) if pid else None


def _uses_official_cli_auth(*, runtime_provider: Any, mp: Any) -> bool:
    """True for Claude/Codex subscription channels whose credentials live in
    the official CLI keychain. These must keep the kernel session path so the
    runtime can self-authenticate out-of-band."""
    return mp is None and str(runtime_provider) in {"claude_agent", "codex"}


def _is_deepseek_anthropic_channel(*, model: str, mp: Any) -> bool:
    base_url = str(getattr(mp, "base_url", "") or "").lower()
    return "deepseek" in base_url or model.lower().startswith("deepseek-")


def _is_valuz_lite_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized == "valuz-lite" or normalized.startswith("valuz-lite-")


def _with_direct_llm_final_output_requirement(
    prompt: str,
    *,
    output_format: str = OUTPUT_FORMAT,
) -> str:
    requirement = (
        "Direct LLM final-output requirement: if you perform any thinking or reasoning, "
        "you must continue after it and emit the final answer as normal text containing "
        f"ONLY valid {output_format}. Never stop after thinking, never return only "
        "thinking or reasoning blocks, and do not include prose or markdown fences."
    )
    return f"{prompt.rstrip()}\n\n{requirement}"


def _make_progress_heartbeat(
    on_progress: Callable[[str], Awaitable[None]] | None,
) -> Callable[[int], Awaitable[None]]:
    """Throttled progress heartbeat for a long, silent tool call.

    Returns an ``await``-able the delta loop can call on EVERY chunk: it emits
    at most one notification per ``_PROGRESS_INTERVAL_S`` (a token-rate stream
    would otherwise flood the client) and is a no-op when the caller supplied
    no reporter — the loop stays free of both concerns.
    """
    last_beat = 0.0

    async def heartbeat(count: int) -> None:
        nonlocal last_beat
        if on_progress is None:
            return
        now = time.monotonic()
        if last_beat and now - last_beat < _PROGRESS_INTERVAL_S:
            return
        last_beat = now
        await on_progress(f"generating UI ({count} chunks)")

    return heartbeat


def _make_completer(
    *,
    user_id: str,
    runtime_provider: Any,
    model: str,
    mp: Any,
    calling_session_id: str | None = None,
    tool_use_id: str | None = None,
    session_instructions: str = a2ui_instructions(),
    output_format: str = OUTPUT_FORMAT,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> Completer:
    """Build the ``complete`` seam backed by a throwaway no-tools kernel session
    cloning the source's runtime/provider/model. Each call is a fresh ephemeral
    session (deleted after), sharing ONE fixed scratch cwd
    (``FsRegistry.generative_ui_cwd``).

    When ``calling_session_id`` + ``tool_use_id`` are set, the ephemeral
    session's ``text_delta`` stream is forwarded to the CALLING session as
    ``tool_output_delta`` (keyed by ``tool_use_id``) via the existing
    ``kernel_client.emit_live_event`` live-injection channel, so the frontend
    ``<Renderer isStreaming>`` paints progressively. ``run_turn`` still
    returns the full text as the canonical ToolResult. When either is None,
    behaves as the synchronous (non-streaming) version."""

    if not _uses_official_cli_auth(runtime_provider=runtime_provider, mp=mp):
        return _make_direct_llm_completer(
            user_id=user_id,
            model=model,
            mp=mp,
            calling_session_id=calling_session_id,
            tool_use_id=tool_use_id,
            output_format=output_format,
        )

    heartbeat = _make_progress_heartbeat(on_progress)

    async def _forward_deltas(ephem_id: str) -> None:
        forwarded = 0
        try:
            async for ev in kernel_client.subscribe_session_events(user_id, ephem_id):
                live_type = _FORWARD_TYPES.get(getattr(ev, "type", None) or "")
                if live_type is None:
                    continue
                text = (getattr(ev, "data", None) or {}).get("text")
                if not text:
                    continue
                await kernel_client.emit_live_event(
                    user_id,
                    calling_session_id or "",
                    live_type,
                    {"id": tool_use_id, "text": text},
                )
                forwarded += 1
                # Heartbeat the MCP caller. The generation is one long silent
                # tool call from the client's point of view, and this is the
                # only thing that says otherwise — it does not extend the
                # client's deadline (that is ``tool_timeout_sec``), but it is
                # what distinguishes "still writing" from "hung" while it
                # runs. Throttled so a token-rate stream doesn't become a
                # notification flood.
                await heartbeat(forwarded)
                logger.debug(
                    "generate_ui: forwarded %s #%d (%d chars) tool_use_id=%s",
                    live_type,
                    forwarded,
                    len(text),
                    tool_use_id,
                )
        except asyncio.CancelledError:
            logger.info(
                "generate_ui: delta forwarding cancelled after %d deltas (tool_use_id=%s)",
                forwarded,
                tool_use_id,
            )
            raise
        except Exception:  # noqa: BLE001 — best-effort; canonical full text still wins
            logger.exception(
                "generate_ui: delta forwarding stopped after %d deltas (tool_use_id=%s)",
                forwarded,
                tool_use_id,
            )
        else:
            logger.info(
                "generate_ui: streamed %d deltas for tool_use_id=%s",
                forwarded,
                tool_use_id,
            )

    async def _complete(prompt: str) -> str:
        from app.schemas import (
            AgentConfigSchema,
            CreateSessionRequest,
            ModelProviderInputSchema,
            ModelSettingsSchema,
        )

        # OAuth/subscription channels (Codex/Claude login) resolve to mp=None and
        # carry no static key — create the session with model_provider=None so the
        # runtime self-authenticates, exactly like the source session.
        mp_schema = (
            ModelProviderInputSchema(
                base_url=mp.base_url, api_key=mp.api_key, api_protocol=mp.api_protocol
            )
            if (mp is not None and getattr(mp, "api_key", None))
            else None
        )
        ephem_id = uuid4().hex
        gen_cwd = fs_registry.generative_ui_cwd(user_id)
        # ``bare_completion`` is the kernel-recognized strip switch
        # (``src.core.types.is_bare_completion``): every runtime drops its
        # agentic scaffolding — built-in tools, preset/base system prompts,
        # settings/skills discovery — for this one-shot no-tool session.
        # This session is a constrained document compiler, not a research turn.
        # Citation projection, semantic/claim audit, and task-coverage review add
        # no value to an A2UI JSONL stream and may each schedule expensive post-
        # processing.  Stamp every capability off explicitly: their kernel
        # defaults are intentionally user-facing and therefore otherwise true.
        marker = {
            "bare_completion": True,
            "valuz": {
                "ephemeral_generative_ui": True,
                "citation_enabled": False,
                "citation_verification_enabled": False,
                "task_coverage_enabled": False,
            },
        }
        req = CreateSessionRequest(
            id=ephem_id,
            agent_config=AgentConfigSchema(
                name="generative-ui",
                model=model,
                runtime_provider=runtime_provider,
                instructions=session_instructions,
                metadata=marker,
                # A full workbench page is a large document; without an
                # explicit cap the runtime default truncates it mid-write,
                # and a half-written A2UI doc is rejected at storage (no
                # version, no card). Match the direct path's budget.
                model_settings=ModelSettingsSchema(
                    max_tokens=_DIRECT_GENUI_MAX_TOKENS
                ),
            ),
            cwd=str(gen_cwd),
            runtime_provider=runtime_provider,
            model=model,
            model_provider=mp_schema,
            instructions=session_instructions,
            permission_mode="default",
            metadata=marker,
            model_settings=ModelSettingsSchema(
                max_tokens=_DIRECT_GENUI_MAX_TOKENS
            ),
        )
        await kernel_client.create_session(user_id, req)
        stream_task: asyncio.Task[None] | None = None
        if calling_session_id and tool_use_id:
            # Subscribe before run_turn: text_delta is live-only and not
            # persisted, so the subscription must be attached before the turn
            # emits. ``sleep(0)`` lets the task begin attaching its tap.
            logger.info(
                "generate_ui: streaming ephem=%s -> calling=%s tool_use_id=%s",
                ephem_id,
                calling_session_id,
                tool_use_id,
            )
            stream_task = asyncio.create_task(_forward_deltas(ephem_id))
            await asyncio.sleep(0)
        try:
            # Continuation loop: no output-token cap is large enough for every
            # page, so a big generation can still stop mid-document. A2UI is
            # append-only JSONL — the break is always the last line — so we
            # keep the complete lines and ask the model (in this same session,
            # which holds its own truncated output in history) to keep writing
            # from where it stopped. Repeats until a turn ends WITHOUT a
            # truncated tail, or the continuation budget is spent. Each
            # continuation streams into the workbench like the first turn.
            from valuz_agent.modules.genui.protocol import (
                CONTINUATION_PROMPT,
                a2ui_message_lines,
            )

            accumulated = ""
            turn_prompt = prompt
            for attempt in range(_GENERATION_MAX_CONTINUATIONS + 1):
                msg = await kernel_client.run_turn(user_id, ephem_id, turn_prompt)
                text = msg.assistant_message or ""
                lines, truncated = a2ui_message_lines(text)
                if not truncated:
                    # Complete turn: append VERBATIM (so a non-truncated,
                    # non-A2UI, or prose-tailed output is byte-identical to
                    # the pre-continuation behaviour) and stop.
                    accumulated = f"{accumulated}\n{text}" if accumulated else text
                    break
                # Truncated: keep only the complete lines (drop the half-
                # written tail) so the next turn's continuation does not sit
                # behind a broken line, then ask the model to keep writing.
                trimmed = "\n".join(lines)
                accumulated = f"{accumulated}\n{trimmed}" if accumulated else trimmed
                if attempt < _GENERATION_MAX_CONTINUATIONS:
                    logger.info(
                        "generate_ui: output truncated; continuing (%d/%d)",
                        attempt + 1,
                        _GENERATION_MAX_CONTINUATIONS,
                    )
                    turn_prompt = CONTINUATION_PROMPT
                else:
                    logger.warning(
                        "generate_ui: still truncated after %d continuations; "
                        "delivering the complete prefix",
                        _GENERATION_MAX_CONTINUATIONS,
                    )
            return accumulated
        finally:
            if stream_task is not None:
                stream_task.cancel()
                with contextlib.suppress(BaseException):
                    await stream_task
            try:
                await kernel_client.delete_session(user_id, ephem_id)
            except Exception:  # noqa: BLE001
                logger.debug("generative-ui: ephemeral session cleanup failed")

    return _complete


def _make_direct_llm_completer(
    *,
    user_id: str,
    model: str,
    mp: Any,
    calling_session_id: str | None = None,
    tool_use_id: str | None = None,
    output_format: str = OUTPUT_FORMAT,
) -> Completer:
    """Build a no-session completer for API-key / non-official model channels.

    ``CreateSessionRequest`` is only needed for Claude/Codex official CLI
    subscription auth. When ``mp`` carries an explicit key, generating the UI is
    a one-shot text completion; stream the model chunks directly into the
    original tool card and return the assembled message stream.
    """
    if mp is None or not getattr(mp, "api_key", None):
        raise ValueError("generate_ui: direct LLM path requires model credentials")

    async def _emit_delta(text: str) -> None:
        if not (calling_session_id and tool_use_id and text):
            return
        await kernel_client.emit_live_event(
            user_id,
            calling_session_id,
            "tool_output_delta",
            {"id": tool_use_id, "text": text},
        )

    async def _complete(prompt: str) -> str:
        from langchain_core.messages import HumanMessage

        protocol = str(getattr(mp, "api_protocol", "") or "openai_completion")
        logger.info(
            "generate_ui: using direct LLM stream protocol=%s model=%s tool_use_id=%s",
            protocol,
            model,
            tool_use_id,
        )
        chat_model = _build_direct_chat_model(model=model, mp=mp)
        chunks: list[str] = []
        status = "ok"
        output = ""
        logged_first_raw_chunk = False
        logged_first_text_chunk = False
        try:
            messages = [
                HumanMessage(
                    content=_with_direct_llm_final_output_requirement(
                        prompt,
                        output_format=output_format,
                    )
                )
            ]
            async for chunk in chat_model.astream(messages):
                text = _extract_langchain_text(chunk)
                if not logged_first_raw_chunk:
                    logged_first_raw_chunk = True
                    logger.info(
                        "generate_ui: direct LLM first_token raw_content=%s "
                        "protocol=%s model=%s tool_use_id=%s",
                        _preview_for_log(getattr(chunk, "content", None)),
                        protocol,
                        model,
                        tool_use_id,
                    )
                if not text:
                    continue
                if not logged_first_text_chunk:
                    logged_first_text_chunk = True
                    logger.info(
                        "generate_ui: direct LLM first_token text=%s "
                        "protocol=%s model=%s tool_use_id=%s",
                        _preview_for_log(text),
                        protocol,
                        model,
                        tool_use_id,
                    )
                chunks.append(text)
                await _emit_delta(text)
            output = "".join(chunks)
            if not output.strip():
                logger.info(
                    "generate_ui: direct LLM stream produced no text; "
                    "trying non-stream fallback protocol=%s model=%s tool_use_id=%s",
                    protocol,
                    model,
                    tool_use_id,
                )
                fallback = _extract_langchain_text(await chat_model.ainvoke(messages))
                if fallback:
                    output = fallback
                    logger.info(
                        "generate_ui: direct LLM non-stream fallback text=%s "
                        "protocol=%s model=%s tool_use_id=%s",
                        _preview_for_log(fallback),
                        protocol,
                        model,
                        tool_use_id,
                    )
                    await _emit_delta(fallback)
            return output
        except asyncio.CancelledError:
            status = "cancelled"
            output = "".join(chunks)
            raise
        except Exception:
            status = "error"
            output = "".join(chunks)
            raise
        finally:
            logger.info(
                "generate_ui: direct LLM stream finished status=%s "
                "protocol=%s model=%s chunks=%d chars=%d tool_use_id=%s",
                status,
                protocol,
                model,
                len(chunks),
                len(output),
                tool_use_id,
            )

    return _complete


def _preview_for_log(value: Any, *, max_chars: int = _LOG_PREVIEW_CHARS) -> str:
    preview = repr(value)
    if len(preview) <= max_chars:
        return preview
    return preview[:max_chars] + "...<truncated>"


def _build_direct_chat_model(*, model: str, mp: Any) -> Any:
    """Construct the LangChain chat model for direct GenUI generation."""
    from pydantic import SecretStr

    protocol = str(getattr(mp, "api_protocol", "") or "openai_completion")
    api_key = SecretStr(str(mp.api_key))
    base_url = getattr(mp, "base_url", None)

    if protocol == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "model_name": model,
            "max_tokens_to_sample": _DIRECT_GENUI_MAX_TOKENS,
            "timeout": None,
            "stop": None,
        }
        if base_url is not None:
            kwargs["base_url"] = base_url
        if _is_deepseek_anthropic_channel(model=model, mp=mp):
            kwargs["thinking"] = {"type": "enabled"}
        elif _is_valuz_lite_model(model):
            # This is a constrained schema compiler, not a research turn.
            # Lite may otherwise spend most of its latency before the first
            # visible document token.
            kwargs["effort"] = _DIRECT_GENUI_REASONING_EFFORT
        return ChatAnthropic(**kwargs)

    if protocol == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = {"model": model, "google_api_key": api_key}
        if base_url:
            kwargs["client_options"] = {"api_endpoint": base_url}
        return ChatGoogleGenerativeAI(**kwargs)

    # ``openai_response`` is a Codex-runtime protocol at the session layer. For
    # this direct one-shot UI generator we only need plain text, so use the
    # OpenAI-compatible chat-completions client for both OpenAI wire variants.
    from langchain_openai import ChatOpenAI

    kwargs = {
        "api_key": api_key,
        "model": model,
        "max_tokens": _DIRECT_GENUI_MAX_TOKENS,
        "stream_usage": True,
    }
    if _is_valuz_lite_model(model):
        kwargs["reasoning_effort"] = _DIRECT_GENUI_REASONING_EFFORT
    if base_url is not None:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _extract_langchain_text(chunk: Any) -> str:
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in {"thinking", "reasoning"}:
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""
