"""In-memory mailbox for v2 actor-style dispatch (M10 附录 B).

v1 dispatch is synchronous RPC: the lead's ``dispatch`` tool blocks until the
member session goes idle, then the manifest comes back as the tool_result. v2
upgrades both lead and member sessions to **persistent actors** — each runs a
loop (run turn → idle → wait for next message → run turn …). Messages flow
between actors through this registry instead of a tool return value.

Because valuz runs a single kernel process (unlike Claude Code's tmux multi-
process model, which needs a file mailbox + polling — see
docs/decisions/claude-code-agent-teams-analysis-2026-05.md §14), the channel is
just an ``asyncio.Queue`` per session: zero file IO, zero polling, event-driven.

Lifecycle: the actor loop ``register``s its session on start and ``unregister``s
on finalize. Senders ``put`` messages; the loop ``get``s the next one (blocking
up to an idle TTL) at each turn boundary. Delivery is therefore at turn
boundaries, never mid-turn — identical to Claude Code's semantics.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# kind of an inbound message delivered to an actor at its next turn boundary.
#   message      — free text from another actor (lead→member follow-up, etc.)
#   member_done  — a member finished a turn; payload carries its manifest
#   shutdown     — graceful stop request; the loop finalises after current turn
#   revise_goal  — the user revised task.goal on a running task; payload.goal
#                  carries the new goal. Delivered so the lead re-orients (the
#                  goal is its initial brief + goal-mode condition baked at
#                  spawn, so a bare task.goal write never reaches a running lead).
InboxKind = Literal["message", "member_done", "shutdown", "revise_goal"]


@dataclass(slots=True)
class InboxMsg:
    """One message waiting in an actor's inbox."""

    kind: InboxKind
    text: str = ""
    from_session: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class MailboxRegistry:
    """Process-wide registry of per-session async inboxes.

    Single event loop → an ``asyncio.Queue`` is sufficient and lock-free. A
    session must be ``register``ed (by its actor loop) before messages can be
    delivered; ``put`` to an unknown session is a no-op (the actor already
    finalised or never started) and returns ``False`` so callers can react.
    """

    def __init__(self) -> None:
        self._boxes: dict[str, asyncio.Queue[InboxMsg]] = {}

    def register(self, session_id: str) -> asyncio.Queue[InboxMsg]:
        """Create (or return existing) inbox for a session. Idempotent."""
        box = self._boxes.get(session_id)
        if box is None:
            box = asyncio.Queue()
            self._boxes[session_id] = box
            logger.debug("mailbox: registered %s", session_id)
        return box

    def unregister(self, session_id: str) -> None:
        """Drop a session's inbox. Idempotent."""
        if self._boxes.pop(session_id, None) is not None:
            logger.debug("mailbox: unregistered %s", session_id)

    def is_registered(self, session_id: str) -> bool:
        return session_id in self._boxes

    def has_pending(self, session_id: str) -> bool:
        """True if the session has at least one queued message (non-blocking).

        Lets the actor loop decide whether to keep waiting without consuming a
        message — e.g. a lead with no in-flight members can break early UNLESS a
        follow-up / member_done is already queued.
        """
        box = self._boxes.get(session_id)
        return box is not None and not box.empty()

    def put(self, session_id: str, msg: InboxMsg) -> bool:
        """Deliver a message. Returns False when no live inbox exists."""
        box = self._boxes.get(session_id)
        if box is None:
            logger.debug("mailbox: drop %s for unregistered session %s", msg.kind, session_id)
            return False
        box.put_nowait(msg)
        return True

    async def get(self, session_id: str, timeout: float | None = None) -> InboxMsg:
        """Await the next message for a session.

        Raises ``asyncio.TimeoutError`` when *timeout* elapses with no message
        (the actor loop treats this as an idle-TTL expiry and finalises).
        Raises ``KeyError`` when the session is not registered.
        """
        box = self._boxes.get(session_id)
        if box is None:
            raise KeyError(session_id)
        if timeout is None:
            return await box.get()
        return await asyncio.wait_for(box.get(), timeout=timeout)


# Module-level singleton — shared by the orchestrator actor loops and the
# dispatch_async / send MCP handlers.
mailbox_registry = MailboxRegistry()


__all__ = ["InboxKind", "InboxMsg", "MailboxRegistry", "mailbox_registry"]
