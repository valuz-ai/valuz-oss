"""SQLAlchemyStore — StorePort implementation for SQLite, PostgreSQL, MySQL."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.sqlalchemy_store.converters import (
    agent_to_model,
    event_to_model,
    message_to_model,
    model_to_agent,
    model_to_event,
    model_to_message,
    model_to_project,
    model_to_session,
    project_to_model,
    session_to_model,
)
from src.adapters.sqlalchemy_store.models import (
    AgentModel,
    EventModel,
    MessageModel,
    ProjectModel,
    SessionModel,
)
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.project import Project
from src.core.types import Message, Session


class SQLAlchemyStore:
    """StorePort implementation — works with SQLite, PostgreSQL, MySQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- Project CRUD --

    async def save_project(self, project: Project) -> None:
        async with self._session_factory() as db:
            await db.merge(project_to_model(project))
            await db.commit()

    async def load_project(self, project_id: str) -> Project | None:
        async with self._session_factory() as db:
            model = await db.get(ProjectModel, project_id)
            return model_to_project(model) if model else None

    async def list_projects(self, *, limit: int = 50, offset: int = 0) -> list[Project]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(ProjectModel)
                .where(ProjectModel.status == "active")
                .order_by(ProjectModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [model_to_project(m) for m in result.scalars()]

    async def delete_project(self, project_id: str) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                update(ProjectModel)
                .where(ProjectModel.id == project_id, ProjectModel.status == "active")
                .values(status="deleted")
            )
            await db.commit()
            return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    # -- Agent CRUD --

    async def save_agent(self, agent: AgentConfig) -> None:
        async with self._session_factory() as db:
            model = agent_to_model(agent)
            await db.merge(model)
            await db.commit()

    async def load_agent(self, agent_id: str) -> AgentConfig | None:
        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status == "active")
            )
            model = result.scalar_one_or_none()
            return model_to_agent(model) if model else None

    async def list_agents(self, *, limit: int = 50, offset: int = 0) -> list[AgentConfig]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentModel)
                .where(AgentModel.status == "active")
                .order_by(AgentModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [model_to_agent(m) for m in result.scalars()]

    async def delete_agent(self, agent_id: str) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                update(AgentModel)
                .where(AgentModel.id == agent_id, AgentModel.status == "active")
                .values(status="deleted")
            )
            await db.commit()
            return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    # -- Session CRUD --

    async def save_session(self, session: Session) -> None:
        async with self._session_factory() as db:
            model = session_to_model(session)
            await db.merge(model)
            await db.commit()

    async def load_session(self, session_id: str) -> Session | None:
        async with self._session_factory() as db:
            model = await db.get(SessionModel, session_id)
            return model_to_session(model) if model else None

    async def list_sessions(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        async with self._session_factory() as db:
            stmt = select(SessionModel).order_by(SessionModel.created_at.desc())
            if ids is not None:
                if not ids:
                    return []
                stmt = stmt.where(SessionModel.id.in_(list(ids)))
            if project_id is not None:
                stmt = stmt.where(SessionModel.project_id == project_id)
            if agent_id is not None:
                stmt = stmt.where(SessionModel.agent_id == agent_id)
            if status is not None:
                stmt = stmt.where(SessionModel.status == status)
            stmt = stmt.offset(offset).limit(limit)
            result = await db.execute(stmt)
            return [model_to_session(m) for m in result.scalars()]

    async def delete_session(self, session_id: str) -> bool:
        async with self._session_factory() as db:
            await db.execute(delete(EventModel).where(EventModel.session_id == session_id))
            await db.execute(delete(MessageModel).where(MessageModel.session_id == session_id))
            result = await db.execute(delete(SessionModel).where(SessionModel.id == session_id))
            await db.commit()
            return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    # -- Message CRUD --

    async def save_message(self, message: Message) -> None:
        async with self._session_factory() as db:
            await db.merge(message_to_model(message))
            await db.commit()

    async def load_message(self, message_id: str) -> Message | None:
        async with self._session_factory() as db:
            model = await db.get(MessageModel, message_id)
            return model_to_message(model) if model else None

    async def list_messages_for_session(
        self, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        async with self._session_factory() as db:
            stmt = (
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.started_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await db.execute(stmt)
            return [model_to_message(m) for m in result.scalars()]

    # -- Event log --

    async def append_event(self, session_id: str, message_id: str, event: Event) -> None:
        async with self._session_factory() as db:
            db.add(event_to_model(session_id, message_id, event))
            await db.commit()

    async def get_events(
        self, session_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        async with self._session_factory() as db:
            stmt = (
                select(EventModel)
                .where(EventModel.session_id == session_id)
                .order_by(EventModel.timestamp)
                .offset(offset)
                .limit(limit)
            )
            result = await db.execute(stmt)
            return [model_to_event(m) for m in result.scalars()]

    async def get_events_for_message(
        self, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        async with self._session_factory() as db:
            stmt = (
                select(EventModel)
                .where(EventModel.message_id == message_id)
                .order_by(EventModel.timestamp)
                .offset(offset)
                .limit(limit)
            )
            result = await db.execute(stmt)
            return [model_to_event(m) for m in result.scalars()]
