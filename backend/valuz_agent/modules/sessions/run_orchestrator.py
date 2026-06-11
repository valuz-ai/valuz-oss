"""Background agent-turn execution + post-turn finalization.

Drives one turn through the shared :func:`ActorRunner.run_session_to_idle`
runtime primitive (ADR-023 AC#5 — ONE turn-to-idle engine for both chat
sessions and task members/leads), adding only the chat-path billing meter via
an ``on_message`` hook. Post-turn finalization (``_finalize_session``) stays
here as the single finalize sink that the runtime primitive imports and calls.
Split out of ``service`` so the god module keeps only the SessionService
surface; the task orchestrator reuses ``_finalize_session`` directly.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# Side-effect: puts the kernel on sys.path so ``src.core`` / ``app.*``
# resolve at call time.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.tasks.actor_runner import run_session_to_idle

logger = logging.getLogger(__name__)


async def _run_agent_background(
    session_id: str,
    content: str,
    event_bus: EventBus,
) -> None:
    """Drive one agent turn in the background.

    Thin wrapper over the shared :func:`run_session_to_idle` runtime primitive
    (ADR-023): the primitive owns the attach-sink → build UserMessage →
    run_turn → read-back-status → finalize → consume-attachments →
    detach/cleanup → publish SESSION_FINISHED shape with the layered failure
    handling that guarantees a session never gets stranded in
    ``status="running"``. This wrapper adds only the chat-path billing meter
    (the task member/lead path leaves ``on_message=None`` so its behaviour is
    byte-identical).
    """

    async def _meter(message: Any, after_run: Any) -> None:
        if message.input_tokens is not None or message.output_tokens is not None:
            from valuz_agent.infra.owner_context import get_current_user_id
            from valuz_agent.ports.billing import MeterEvent
            from valuz_agent.ports.extensions import ext

            uid = (after_run.metadata if after_run else {}).get(
                "owner_user_id"
            ) or get_current_user_id()
            try:
                await ext.billing.meter(
                    MeterEvent(
                        user_id=uid,
                        event_type="llm_call",
                        cost_usd=0.0,
                        metadata={
                            "message_id": message.id,
                            "session_id": session_id,
                            "input_tokens": message.input_tokens or 0,
                            "output_tokens": message.output_tokens or 0,
                            "cache_read_tokens": message.cache_read_tokens or 0,
                            "cache_write_tokens": message.cache_write_tokens or 0,
                            "model_usage": message.model_usage,
                        },
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning("Billing meter failed for session %s", session_id)

    await run_session_to_idle(session_id, content, event_bus, on_message=_meter)


# Strip leading skill-trigger tokens (``/<slug>``) when deriving a
# session title from the user's first message. Composer ships these
# inline as part of the prompt, but they're routing metadata — using
# "/stock-screener 找股票" as the chat title leaks scaffolding into
# the sidebar. Only the *prefix* is stripped; ``/`` mentions later
# in the prose are kept verbatim because they're presumably part of
# the intent. (CN-IME ``、`` is normalized to ``/`` in the Composer
# before send, so we only need to handle the canonical form here.)
_SKILL_PREFIX_RE = re.compile(r"^\s*(?:/[a-zA-Z0-9_-]+\s+)+")


def _derive_session_name(content: str) -> str:
    cleaned = _SKILL_PREFIX_RE.sub("", content)
    return cleaned[:40].replace("\n", " ").strip()


async def _finalize_session(session_id: str, content: str, final_status: str) -> None:
    """Persist post-turn valuz metadata and the resolved kernel status.

    Split out so both the success and failure paths in ``_run_agent_background``
    can share it. Builds a fresh ``Session`` dataclass because the kernel's
    types are frozen.
    """
    session = await kernel_client.get_session(session_id)
    if session is None:
        return

    meta = dict(session.metadata)
    valuz = dict(meta.get("valuz") or {})
    valuz["last_user_message_text"] = content
    if not valuz.get("name"):
        valuz["name"] = content[:40].replace("\n", " ").strip()
    meta["valuz"] = valuz

    from app.schemas import FinalizeSessionRequest

    await kernel_client.finalize_session(
        session_id,
        FinalizeSessionRequest(status=final_status, metadata=meta),  # type: ignore[arg-type]
    )
