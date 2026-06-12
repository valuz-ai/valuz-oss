"""SQLAlchemyStore — StorePort implementation for SQLite, PostgreSQL, MySQL."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
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
from src.core.store_port import StoredEvent, UsageRollupRow
from src.core.types import Message, Session


def _model_to_stored_event(model: EventModel) -> StoredEvent:
    data: Any = model.data
    if not isinstance(data, dict):
        data = {"raw": data}
    return StoredEvent(
        seq=int(model.id),
        session_id=model.session_id,
        message_id=model.message_id,
        type=model.type,
        data=data,
        timestamp=int(model.timestamp),
    )


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

    async def append_event(self, session_id: str, message_id: str, event: Event) -> int | None:
        async with self._session_factory() as db:
            model = event_to_model(session_id, message_id, event)
            db.add(model)
            await db.commit()
            return int(model.id) if model.id is not None else None

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

    async def get_events_after(
        self, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        async with self._session_factory() as db:
            stmt = (
                select(EventModel)
                .where(EventModel.session_id == session_id, EventModel.id > after_seq)
                .order_by(EventModel.id)
                .limit(limit)
            )
            result = await db.execute(stmt)
            return [_model_to_stored_event(m) for m in result.scalars()]

    async def get_events_window(
        self, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> tuple[list[StoredEvent], bool]:
        if turn_limit <= 0:
            return [], False
        async with self._session_factory() as db:
            # Step 1: the most recent ``turn_limit`` user_message row ids
            # under the cursor — each marks the start of a turn.
            anchor_stmt = (
                select(EventModel.id)
                .where(
                    EventModel.session_id == session_id,
                    EventModel.type == "user_message",
                )
                .order_by(EventModel.id.desc())
                .limit(turn_limit)
            )
            if before_seq is not None:
                anchor_stmt = anchor_stmt.where(EventModel.id < before_seq)
            anchor_ids = [int(row[0]) for row in (await db.execute(anchor_stmt)).all()]
            if not anchor_ids:
                return [], False

            floor_id = min(anchor_ids)
            # Step 2: every event in [floor, before_seq), ascending. No
            # per-event cap — ``turn_limit`` is the pagination knob; a cap
            # here would silently drop the tail of tool-heavy turns.
            range_stmt = (
                select(EventModel)
                .where(EventModel.session_id == session_id, EventModel.id >= floor_id)
                .order_by(EventModel.id)
            )
            if before_seq is not None:
                range_stmt = range_stmt.where(EventModel.id < before_seq)
            rows = list((await db.execute(range_stmt)).scalars())

            # Step 3: does at least one older turn exist before the window?
            has_more = False
            if rows:
                probe_stmt = (
                    select(EventModel.id)
                    .where(
                        EventModel.session_id == session_id,
                        EventModel.type == "user_message",
                        EventModel.id < int(rows[0].id),
                    )
                    .limit(1)
                )
                has_more = (await db.execute(probe_stmt)).first() is not None
        return [_model_to_stored_event(m) for m in rows], has_more

    # -- Aggregates --

    async def usage_rollup(self, start_ms: int, end_ms: int) -> list[UsageRollupRow]:
        async with self._session_factory() as db:
            # ``started_at`` is epoch ms (BIGINT): /1000 → seconds for
            # SQLite's 'unixepoch' modifier so the UTC day bucket is correct.
            day_col = func.strftime(
                "%Y-%m-%d", MessageModel.started_at / 1000, "unixepoch"
            ).label("day")
            model_col = SessionModel.model.label("model")
            stmt = (
                select(
                    day_col,
                    model_col,
                    func.coalesce(func.sum(MessageModel.total_turns), 0).label("request_count"),
                    func.coalesce(func.sum(MessageModel.input_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(MessageModel.output_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(MessageModel.cache_read_tokens), 0).label(
                        "cache_read_tokens"
                    ),
                    func.coalesce(func.sum(MessageModel.cache_write_tokens), 0).label(
                        "cache_write_tokens"
                    ),
                )
                .join(SessionModel, MessageModel.session_id == SessionModel.id)
                .where(
                    MessageModel.started_at >= start_ms,
                    MessageModel.started_at < end_ms,
                    MessageModel.status == "completed",
                )
                .group_by(day_col, model_col)
                .order_by(day_col)
            )
            rows = (await db.execute(stmt)).all()
        return [
            UsageRollupRow(
                day=str(r.day),
                model=str(r.model or ""),
                request_count=int(r.request_count or 0),
                input_tokens=int(r.input_tokens or 0),
                output_tokens=int(r.output_tokens or 0),
                cache_read_tokens=int(r.cache_read_tokens or 0),
                cache_write_tokens=int(r.cache_write_tokens or 0),
            )
            for r in rows
        ]
