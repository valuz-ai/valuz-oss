"""StorePort — persistence interface for Project, Agent, Session, and Event storage."""

from __future__ import annotations

from typing import Protocol

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.project import Project
from src.core.types import Message, Session


class StorePort(Protocol):
    """Persistence interface — Project, Agent, Session, Event storage."""

    # -- Project CRUD --

    async def save_project(self, project: Project) -> None:
        """Create or update a Project."""
        ...

    async def load_project(self, project_id: str) -> Project | None:
        """Load a Project by ID, or None if not found. Includes deleted projects."""
        ...

    async def list_projects(self, *, limit: int = 50, offset: int = 0) -> list[Project]:
        """List active projects ordered by creation time descending."""
        ...

    async def delete_project(self, project_id: str) -> bool:
        """Soft-delete a Project. Returns True if found."""
        ...

    # -- Agent CRUD --

    async def save_agent(self, agent: AgentConfig) -> None:
        """Create or update an Agent definition."""
        ...

    async def load_agent(self, agent_id: str) -> AgentConfig | None:
        """Load an Agent by ID, or None if not found."""
        ...

    async def list_agents(self, *, limit: int = 50, offset: int = 0) -> list[AgentConfig]:
        """List agents ordered by creation time descending."""
        ...

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an Agent. Returns True if found."""
        ...

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
        project_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        """List sessions ordered by created_at descending."""
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
