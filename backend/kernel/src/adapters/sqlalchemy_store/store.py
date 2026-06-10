"""SQLAlchemyStore — StorePort implementation for SQLite, PostgreSQL, MySQL."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.sqlalchemy_store.converters import (
    event_to_model,
    message_to_model,
    model_to_event,
    model_to_message,
    model_to_session,
    session_to_model,
)
from src.adapters.sqlalchemy_store.models import (
    EventModel,
    MessageModel,
    SessionModel,
)
from src.core.events import Event
from src.core.types import Message, Session


class SQLAlchemyStore:
    """StorePort implementation — works with SQLite, PostgreSQL, MySQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
