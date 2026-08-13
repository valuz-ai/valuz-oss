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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Four kinds in three semantic classes — the class decides how the loop
# consumes it:
#   FREE TEXT (becomes the next turn's prompt verbatim):
#     text         — from another actor or the user (follow-up / inject / rework)
#     revise_goal  — user revised task.goal; distinct so await_member_results
#                    preempts on it (a bare DB write never reaches a running
#                    lead — the goal is baked into the session at spawn)
#   STRUCTURED REPORT (rendered first): member_done — manifest, via
#     ActorRunner._format_member_done
#   CONTROL SIGNAL (no prompt): shutdown — finalize after the current turn
# Process-local only (in-memory asyncio.Queue per session; never persisted).
InboxKind = Literal["text", "member_done", "shutdown", "revise_goal"]


@dataclass(slots=True)
class InboxMsg:
    """One message waiting in an actor's inbox."""

    kind: InboxKind
    text: str = ""
    from_session: str = ""
    # Who produced this message, for the loop's per-turn log. Diagnostic ONLY —
    # nothing branches on it. A turn is otherwise invisible in the log, so when
    # one appears that nobody expected there is no way to ask where it came
    # from; that cost hours on a production re-run regression.
    origin: str = ""
    # ``Mapping`` not ``dict``: ``member_done`` carries a ``MemberManifest``
    # TypedDict, and dict is invariant — a dict[str, Any] annotation would
    # force every producer to erase the type it just built.
    payload: Mapping[str, Any] = field(default_factory=dict)


class MailboxRegistry:
    """Process-wide registry of per-session async inboxes.

    Single event loop → an ``asyncio.Queue`` is sufficient and lock-free. A
    session must be ``register``ed (by its actor loop) before messages can be
    delivered; ``put`` to an unknown session is a no-op (the actor already
    finalised or never started) and returns ``False`` so callers can react.
    """

    def __init__(self) -> None:
        self._boxes: dict[str, asyncio.Queue[InboxMsg]] = {}
        # session_id → current OWNER claim (see claim/release). Guards against
        # a stale actor loop's ``finally`` dropping the box a freshly resumed
        # loop is reading from.
        self._claims: dict[str, int] = {}
        self._claim_seq = 0

    def register(self, session_id: str) -> asyncio.Queue[InboxMsg]:
        """Create (or return existing) inbox for a session. Idempotent.

        NON-OWNING — for senders that need the box to exist before the owner's
        loop ticks (dispatch/await belt-and-suspenders, recovery pre-seeding).
        The actor loop itself uses :meth:`claim`.
        """
        box = self._boxes.get(session_id)
        if box is None:
            box = asyncio.Queue()
            self._boxes[session_id] = box
            logger.debug("mailbox: registered %s", session_id)
        return box

    def claim(self, session_id: str) -> int:
        """Register (idempotent) AND take ownership; returns the claim token.

        A later claim on the same session invalidates every earlier token, so
        a STALE loop's :meth:`release` becomes a no-op instead of stealing the
        new loop's box. The race this closes: stop_task interrupts the lead,
        the old loop is still unwinding SDK teardown (seconds) when a rapid
        resume — user click or inject's TASK_HALTED auto-revive — spawns a new
        loop on the same session id; the old ``finally`` then popped the
        SHARED box, recovery's queued ``member_done``s died with it, and the
        new loop's next ``get`` raised into a spurious auto-finalize→blocked.
        """
        self.register(session_id)
        self._claim_seq += 1
        self._claims[session_id] = self._claim_seq
        return self._claim_seq

    def release(self, session_id: str, token: int) -> None:
        """Drop the inbox — only if *token* is still the current claim."""
        if self._claims.get(session_id) != token:
            logger.debug("mailbox: stale release(%d) for %s ignored", token, session_id)
            return
        self._claims.pop(session_id, None)
        self.unregister(session_id)

    def unregister(self, session_id: str) -> None:
        """Drop a session's inbox unconditionally. Idempotent.

        Prefer :meth:`release` from actor loops — this bypasses the claim
        guard and exists for tests / non-loop teardown.

        Drops the CLAIM as well: an owner recorded for a box that no longer
        exists would report :meth:`is_owned` for a session nothing can be
        delivered to — the same lie in the other direction.
        """
        self._claims.pop(session_id, None)
        if self._boxes.pop(session_id, None) is not None:
            logger.debug("mailbox: unregistered %s", session_id)

    def is_registered(self, session_id: str) -> bool:
        """A box EXISTS — messages can be queued. Says nothing about a reader.

        Not a liveness signal: :meth:`register` is non-owning, so a box can
        outlive (or precede) any actor loop. Use :meth:`is_owned` to ask
        whether anyone is actually reading.
        """
        return session_id in self._boxes

    def is_owned(self, session_id: str) -> bool:
        """An actor loop HOLDS this session — the liveness oracle.

        A claim is taken by the loop itself (:meth:`claim`, from
        ``spawn_actor``) and dropped by its ``finally`` (:meth:`release`), so
        this tracks the loop, not the box. ``is_registered`` used to stand in
        for this and could not: a box pre-seeded by a sender for a loop that
        then failed to start stays registered for the life of the process
        (nothing calls ``unregister`` in production), so every reader of it
        saw a dead task as healthy — the watchdog never blocked it, and
        ``inject_into_task`` reported delivery into a queue nobody reads.
        """
        return session_id in self._claims

    def is_claim_current(self, session_id: str, token: int) -> bool:
        """Is *token* still the live claim on this session's inbox?

        The box is SHARED by every loop that ever claimed the session (``claim``
        reuses it), so a superseded loop must ask before posting: whatever it
        puts there is read by whichever loop owns the box NOW. Without this a
        stale loop's ``shutdown`` — the very message that tells it to stop —
        would be consumed by the live loop that replaced it, killing the wrong
        one.
        """
        return self._claims.get(session_id) == token

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
