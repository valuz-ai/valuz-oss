"""StorePort — persistence interface for Session, Message, and Event storage."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.core.events import Event
from src.core.types import Message, Session


@dataclass(frozen=True)
class StoredEvent:
    """One persisted event row, including its storage coordinates.

    Unlike the domain :class:`Event` (pure payload), this carries the
    autoincrement row id (``seq`` — the global ordering cursor clients
    page with) and the owning ``message_id``. Read-only projection for
    the events query API; writes still go through ``append_event``.
    """

    seq: int
    session_id: str
    message_id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: int = 0  # Unix epoch ms (UTC)


@dataclass(frozen=True)
class UsageRollupRow:
    """Per-(UTC day, model) usage aggregate over completed messages."""

    day: str  # "YYYY-MM-DD"
    model: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class StorePort(Protocol):
    """Persistence interface — Session, Message, Event storage."""

    # -- Session CRUD --
    #
    # Owner model (mirrors the host valuz_* tables): owner-scoped reads take the
    # caller's ``user_id`` FIRST and filter on it; writes stamp the owner
    # explicitly (``save_session`` from ``session.user_id``; ``save_message`` /
    # ``append_event`` from their ``user_id`` arg). ``list_sessions`` accepts
    # ``user_id=None`` for the two cross-owner startup sweeps (orphan scans) —
    # every other caller passes a concrete owner.

    async def save_session(self, session: Session) -> None:
        """Create or update a Session (owner stamped from ``session.user_id``)."""
        ...

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        """Load one of ``user_id``'s Sessions by ID, or None if not found / not owned."""
        ...

    async def list_sessions(
        self,
        user_id: str | None,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        """List sessions ordered by created_at descending, scoped to ``user_id``.

        ``user_id=None`` lists across every owner — reserved for the kernel's
        own startup sweeps (orphan reconciliation); callers serving a request
        always pass a concrete owner. ``ids`` narrows to an explicit id set
        (bulk fetch for callers that resolve membership elsewhere); combines
        with the other filters.
        """
        ...

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete one of ``user_id``'s Sessions and its events. Returns True if found."""
        ...

    # -- Message CRUD --

    async def save_message(self, user_id: str, message: Message) -> None:
        """Create or update a Message (one run inside a Session), owner-stamped."""
        ...

    async def load_message(self, user_id: str, message_id: str) -> Message | None:
        """Load one of ``user_id``'s Messages by ID, or None if not found / not owned."""
        ...

    async def list_messages_for_session(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        """List a session's messages (owner-scoped) ordered by started_at descending."""
        ...

    # -- Event log --

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event
    ) -> int | None:
        """Append an owner-stamped event scoped to (session, message).

        Returns the persisted row id (the client paging cursor ``seq``)
        when the backend can report it, else ``None``."""
        ...

    async def get_events(
        self, user_id: str, session_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        """Get a session's events (owner-scoped), ordered by timestamp."""
        ...

    async def get_events_for_message(
        self, user_id: str, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        """Get a message's events (owner-scoped), ordered by timestamp."""
        ...

    async def get_events_after(
        self, user_id: str, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        """Get a session's events (owner-scoped) with row id strictly greater
        than ``after_seq``, ordered by row id ascending.

        The row id doubles as the client paging cursor (``seq``)."""
        ...

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> tuple[list[StoredEvent], bool]:
        """Return a turn-aligned window of one owner's session events ending
        strictly before ``before_seq`` (or session end when ``None``).

        A "turn" starts at a ``user_message`` event. The window covers the
        most recent ``turn_limit`` turns in full, ordered ascending. The
        boolean is ``has_more`` — whether at least one older turn exists
        before the window."""
        ...

    # -- Aggregates --

    async def usage_rollup(self, user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupRow]:
        """Token/turn usage per (UTC day, model) for ``user_id``'s completed
        messages whose ``started_at`` falls in the half-open
        ``[start_ms, end_ms)`` window."""
        ...
