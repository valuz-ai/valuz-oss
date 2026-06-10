"""StorePort — persistence interface for Session, Message, and Event storage."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.core.events import Event
from src.core.types import Message, Session


class StorePort(Protocol):
    """Persistence interface — Session, Message, Event storage."""

    # -- Session CRUD --

    async def save_session(self, session: Session) -> None:
        """Create or update a Session."""
        ...

    async def load_session(self, session_id: str) -> Session | None:
        """Load a Session by ID, or None if not found."""
        ...

    async def list_sessions(
        self,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        """List sessions ordered by created_at descending.

        ``ids`` narrows to an explicit id set (bulk fetch for callers that
        resolve membership elsewhere); combines with the other filters.
        """
        ...

    async def delete_session(self, session_id: str) -> bool:
        """Delete a Session and its events. Returns True if found."""
        ...

    # -- Message CRUD --

    async def save_message(self, message: Message) -> None:
        """Create or update a Message (one run inside a Session)."""
        ...

    async def load_message(self, message_id: str) -> Message | None:
        """Load a Message by ID, or None if not found."""
        ...

    async def list_messages_for_session(
        self, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        """List a session's messages ordered by started_at descending."""
        ...

    # -- Event log --

    async def append_event(self, session_id: str, message_id: str, event: Event) -> None:
        """Append an event scoped to (session, message)."""
        ...

    async def get_events(
        self, session_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        """Get events for a Session, ordered by timestamp."""
        ...

    async def get_events_for_message(
        self, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        """Get events for a Message, ordered by timestamp."""
        ...
