"""Langfuse tracing bootstrap — optional, env-gated, no-op by default.

Enables LLM observability for agent turns when the operator points the
process at a (self-hosted) Langfuse deployment. Activation requires BOTH:

* env config: ``LANGFUSE_BASE_URL`` + ``LANGFUSE_PUBLIC_KEY`` +
  ``LANGFUSE_SECRET_KEY`` all set (``LANGFUSE_TRACING_ENVIRONMENT`` is
  honored natively by the SDK for the environment tag), and
* the optional ``tracing`` extra installed (``valuz-oss-backend[tracing]``).

Anything missing → every function here degrades to a cheap no-op, so the
default local-first deployment carries zero overhead and zero new imports.

Two tracing flavors, chosen per runtime:

* **Event-driven** (claude_agent, codex — see ``EVENT_TRACED_RUNTIMES``):
  the orchestrator opens one :class:`TurnTrace` per turn and slots a
  :class:`TurnTracingSink` into the session's sink chain. Spans are built
  from the kernel's OWN cross-runtime event contract (``tool_use`` /
  ``tool_result`` / ``assistant_message`` / ``usage_update`` /
  ``session_error``) — NOT from SDK instrumentation — so runtimes need
  zero tracing code and any runtime or future feature that speaks the
  contract is traced automatically. (SDK-level instrumentation was
  evaluated and rejected: openinference's claude instrumentor only spans
  ``receive_response()``, which the claude runtime's wake-up bracket
  logic never calls, and no codex SDK instrumentation exists at all.)
* **Native LangChain handler** (deepagents): richer than events can offer
  (per-LLM-call generations, langgraph structure), via
  :func:`langchain_config_overlay` merged into the call-time config.

Trace→user/session attribution is applied at the orchestrator with
:func:`turn_trace_context` (Langfuse ``propagate_attributes``): every span
created inside the turn is stamped with ``user_id`` / ``session_id`` /
``message_id``, so both flavors group identically.

Init runs once per process and is idempotent; the host lifespan
(``valuz_agent/boot``), the standalone kernel app (``kernel/app``) and the
headless facade all call it — whichever boots first wins. ``auth_check()``
is deliberately NOT called (the SDK documents it as blocking / not for
production paths) — credential problems surface as export errors in the
Langfuse SDK's own logging.
"""

from __future__ import annotations

import logging
import os
from contextlib import AbstractContextManager, nullcontext
from typing import Any

logger = logging.getLogger(__name__)

_client: Any | None = None
_langchain_handler: Any | None = None
_initialized = False

# Runtimes traced from the kernel event stream. deepagents is deliberately
# absent: it carries the native LangChain callback handler (see module
# docstring). A NEW runtime that emits the standard events needs only its
# provider key added here.
EVENT_TRACED_RUNTIMES = frozenset({"claude_agent", "codex", "deepseek_harness"})

# Tool outputs can be huge (a build log from codex ``aggregated_output``
# easily reaches MBs). Cap what we ship per observation field.
_MAX_TOOL_OUTPUT_CHARS = 40_000


def tracing_configured() -> bool:
    """True when the env carries a complete Langfuse endpoint config."""
    return bool(
        os.environ.get("LANGFUSE_BASE_URL")
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def tracing_active() -> bool:
    """True when :func:`init_tracing` ran and produced a live client."""
    return _client is not None


def init_tracing() -> bool:
    """Process-level init. Idempotent. Returns True when tracing is active.

    Never raises: a partially configured env or a missing optional
    dependency logs a warning and leaves tracing disabled.
    """
    global _client, _langchain_handler, _initialized
    if _initialized:
        return _client is not None
    _initialized = True

    if not tracing_configured():
        if os.environ.get("LANGFUSE_BASE_URL"):
            logger.warning(
                "LANGFUSE_BASE_URL is set but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY "
                "is missing — Langfuse tracing stays disabled"
            )
        return False

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning(
            "Langfuse env is configured but the 'langfuse' package is not installed "
            "(install the 'tracing' extra) — tracing stays disabled"
        )
        return False

    try:
        # Credentials/base_url/environment are read from the LANGFUSE_* env by
        # the SDK itself; constructing the client also installs its OTel span
        # processor on the (possibly newly created) global tracer provider.
        _client = Langfuse()
    except Exception:  # noqa: BLE001 — observability must never break boot
        logger.exception("Langfuse client construction failed — tracing stays disabled")
        _client = None
        return False

    try:
        from langfuse.langchain import CallbackHandler

        # One shared handler per process (official guidance): stateless per
        # run — concurrent runs are keyed by LangChain run_id internally.
        _langchain_handler = CallbackHandler()
    except ImportError:
        _langchain_handler = None
    except Exception:  # noqa: BLE001 — observability must never break boot
        logger.exception("LangChain CallbackHandler construction failed; continuing without it")
        _langchain_handler = None

    logger.info("Langfuse tracing enabled (base_url=%s)", os.environ.get("LANGFUSE_BASE_URL"))
    return True


def shutdown_tracing() -> None:
    """Flush buffered spans and stop the SDK's background threads."""
    global _client, _langchain_handler
    if _client is None:
        return
    try:
        _client.shutdown()
    except Exception:  # noqa: BLE001 — shutdown must not raise
        logger.exception("Langfuse shutdown failed")
    _client = None
    _langchain_handler = None


def reset_tracing_for_tests() -> None:
    """Drop all module state so a test can exercise init from scratch."""
    global _client, _langchain_handler, _initialized
    _client = None
    _langchain_handler = None
    _initialized = False


def turn_trace_context(
    *,
    user_id: str | None,
    session_id: str,
    message_id: str | None = None,
) -> AbstractContextManager[Any]:
    """Attribution context for one turn — a no-op unless tracing is active.

    Wrap the whole turn with this so every span created inside (the
    :class:`TurnTrace` observations, LangChain handler spans) carries the
    Langfuse ``user_id`` / ``session_id`` and the kernel ``message_id``.
    Only spans STARTED inside the context are stamped — enter it before
    creating any observation, never after.
    """
    if _client is None:
        return nullcontext()
    try:
        from langfuse import propagate_attributes
    except ImportError:  # pragma: no cover — client import already succeeded
        return nullcontext()
    metadata: dict[str, str] = {}
    if message_id:
        metadata["message_id"] = message_id
    # Deploy-time service identity (set uniformly on cloud pods; the
    # commercial allocator forwards it into the sandbox kernel env so the
    # process that actually creates the spans carries it too).
    service_name = os.environ.get("SERVICE_NAME")
    if service_name:
        metadata["service_name"] = service_name
    try:
        return propagate_attributes(
            user_id=user_id or None,
            session_id=session_id,
            metadata=metadata or None,
        )
    except Exception:  # noqa: BLE001 — observability must never break a turn
        logger.exception("propagate_attributes failed; running turn untraced")
        return nullcontext()


class TurnTrace:
    """One agent turn as a Langfuse observation tree, built from kernel events.

    Structure: a root AGENT observation (input = the user's message, output =
    the assistant text) with TOOL children opened on ``tool_use`` and closed
    on the matching ``tool_result``; subagent activity nests via
    ``parent_tool_use_id``. The last ``usage_update`` payload (the
    cross-runtime contract: four flat token fields + ``model_usage`` +
    optional ``cost_usd``) lands as a child GENERATION at ``end()`` — the
    shape Langfuse aggregates tokens/cost from.

    Every method is exception-proof and a post-``end()`` event is ignored —
    the between-turns idle drainer may still emit through a stale sink chain.
    """

    def __init__(self, observation: Any) -> None:
        self._observation = observation
        self._open_tools: dict[str, Any] = {}
        self._output_parts: list[str] = []
        self._usage: dict[str, Any] | None = None
        self._observed_error: str | None = None
        self._ended = False

    def observe(self, event: Any) -> None:
        """Feed one kernel event. Unknown event types are ignored."""
        if self._ended:
            return
        try:
            handler = self._HANDLERS.get(getattr(event, "type", None))
            if handler is not None:
                handler(self, dict(getattr(event, "data", None) or {}))
        except Exception:  # noqa: BLE001 — observability must never break a turn
            logger.exception("turn trace failed to observe event")

    def _on_tool_call(self, data: dict[str, Any]) -> None:
        tool_id = data.get("id")
        if not tool_id or tool_id in self._open_tools:
            return
        # A subagent's tools carry the spawning Task tool's id — nest them
        # under that (still-open) TOOL observation.
        parent = self._open_tools.get(data.get("parent_tool_use_id") or "", self._observation)
        self._open_tools[tool_id] = parent.start_observation(
            name=str(data.get("name") or "tool"),
            as_type="tool",
            input=data.get("input"),
        )

    def _on_tool_result(self, data: dict[str, Any]) -> None:
        observation = self._open_tools.pop(data.get("id") or "", None)
        if observation is None:
            return
        content = data.get("content")
        if isinstance(content, str) and len(content) > _MAX_TOOL_OUTPUT_CHARS:
            content = content[:_MAX_TOOL_OUTPUT_CHARS] + "\n… [truncated]"
        update: dict[str, Any] = {"output": content}
        if data.get("is_error"):
            update["level"] = "ERROR"
        observation.update(**update)
        observation.end()

    def _on_assistant_message(self, data: dict[str, Any]) -> None:
        # Subagent-internal text (parented) is visible in its tool span's
        # result; only top-level text is the turn's answer.
        text = data.get("text")
        if text and not data.get("parent_tool_use_id"):
            self._output_parts.append(str(text))

    def _on_usage_update(self, data: dict[str, Any]) -> None:
        # Last-wins: claude re-emits cumulative usage per bracket close; the
        # final one covers the turn. codex emits exactly once at turn end.
        self._usage = data

    def _on_session_error(self, data: dict[str, Any]) -> None:
        self._observed_error = str(data.get("message") or "session_error")

    _HANDLERS = {
        "tool_use": _on_tool_call,
        "tool_result": _on_tool_result,
        "assistant_message": _on_assistant_message,
        "usage_update": _on_usage_update,
        "session_error": _on_session_error,
    }

    def _flush_usage(self) -> None:
        usage = self._usage
        if usage is None:
            return
        usage_details = {
            key: usage.get(source)
            for key, source in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("cache_read", "cache_read_tokens"),
                ("cache_write", "cache_write_tokens"),
            )
            if isinstance(usage.get(source), int)
        }
        cost = usage.get("cost_usd")
        model_usage = usage.get("model_usage")
        models = list(model_usage) if isinstance(model_usage, dict) else []
        if not usage_details and cost is None:
            return
        generation = self._observation.start_observation(
            name="usage",
            as_type="generation",
            metadata={"models": models} if len(models) > 1 else None,
        )
        generation.update(
            model=models[0] if len(models) == 1 else None,
            usage_details=usage_details or None,
            cost_details={"total": float(cost)} if cost is not None else None,
        )
        generation.end()

    def end(self, *, error: str | None = None) -> None:
        """Close the turn: in-flight tools, usage generation, root. Idempotent."""
        if self._ended:
            return
        self._ended = True
        try:
            for observation in self._open_tools.values():
                observation.end()
            self._open_tools.clear()
            self._flush_usage()
            update: dict[str, Any] = {}
            if self._output_parts:
                update["output"] = "\n\n".join(self._output_parts)
            final_error = error or self._observed_error
            if final_error:
                update["level"] = "ERROR"
                update["status_message"] = final_error
            if update:
                self._observation.update(**update)
            self._observation.end()
        except Exception:  # noqa: BLE001 — observability must never break a turn
            logger.exception("turn trace end failed")


class TurnTracingSink:
    """EventSink decorator: feeds each event to the turn trace, then forwards.

    Observation failures are already swallowed inside ``TurnTrace.observe``;
    forwarding is never skipped.
    """

    def __init__(self, inner: Any, trace: TurnTrace) -> None:
        self._inner = inner
        self._trace = trace

    async def emit(self, event: Any) -> None:
        self._trace.observe(event)
        await self._inner.emit(event)


def start_turn_trace(*, runtime_provider: str, prompt: str) -> TurnTrace | None:
    """Open the per-turn observation tree for an event-traced runtime.

    Returns ``None`` when tracing is inactive OR the runtime is not event-
    traced (deepagents: native LangChain handler instead) — callers treat
    ``None`` as "no tracing this turn".
    """
    if _client is None or runtime_provider not in EVENT_TRACED_RUNTIMES:
        return None
    try:
        observation = _client.start_observation(
            name=f"{runtime_provider}.turn",
            as_type="agent",
            input=prompt,
            metadata={"runtime": runtime_provider},
        )
        return TurnTrace(observation)
    except Exception:  # noqa: BLE001 — observability must never break a turn
        logger.exception("turn trace start failed; running turn untraced")
        return None


def langchain_config_overlay(*, session_id: str, user_id: str | None) -> dict[str, Any]:
    """Callbacks + metadata to merge into a langgraph/LangChain call config.

    Returns ``{}`` when tracing is inactive so callers can unconditionally
    ``config.update(...)``. The ``langfuse_*`` metadata keys duplicate what
    :func:`turn_trace_context` propagates — belt and braces, and they keep
    attribution correct even for a call site that isn't wrapped in a turn
    context.
    """
    if _langchain_handler is None:
        return {}
    metadata: dict[str, Any] = {"langfuse_session_id": session_id}
    if user_id:
        metadata["langfuse_user_id"] = user_id
    return {"callbacks": [_langchain_handler], "metadata": metadata}
