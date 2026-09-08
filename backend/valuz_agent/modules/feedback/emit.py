"""Best-effort server-side emit for regenerate / fork / share.

Called from the sessions and research services after the primary action
succeeded. A failing port is logged and swallowed — recording a signal must
never fail the action that produced it.
"""

from __future__ import annotations

import logging
from typing import Any

from valuz_agent.ports.feedback import (
    FeedbackActor,
    FeedbackSubject,
    FeedbackTarget,
    get_feedback_port,
)

logger = logging.getLogger(__name__)


async def record_server_action(
    user_id: str,
    *,
    session_id: str,
    message_id: str,
    action: str,
    target: FeedbackTarget | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        await get_feedback_port().record(
            FeedbackActor(user_id),
            FeedbackSubject(session_id, message_id),
            action,
            target=target,
            source="server",
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "feedback: server-side %s on %s/%s not recorded",
            action,
            session_id,
            message_id,
            exc_info=True,
        )
