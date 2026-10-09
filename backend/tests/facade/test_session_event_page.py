"""Real kernel SQLite + its route/client/data-reader chain, without executing a turn."""

from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — initializes the kernel import path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Message, Session, UserMessage
from valuz_agent.adapters import data_reader, kernel_client
from valuz_agent.facade.sessions import SessionLibrary


@pytest.fixture
async def kernel_store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
    client = kernel_client.InProcessKernelClient(store_getter=lambda: store)
    monkeypatch.setattr(kernel_client, "_data_plane", lambda: client)
    monkeypatch.setattr(data_reader, "_reader", None)
    session = Session(
        id="session",
        user_id="owner",
        agent_config=AgentConfig(id="agent", name="Agent"),
        cwd=str(tmp_path),
    )
    await store.save_session(session)
    message = Message(
        id="message",
        session_id=session.id,
        user_message=UserMessage(text="Hi"),
        started_at=1,
    )
    await store.save_message("owner", message)
    for i in range(7):
        await store.append_event(
            "owner",
            session.id,
            message.id,
            Event(type="assistant_message", data={"text": f"event-{i}"}, timestamp=1000 + i),
            request_id=f"uid-{i}" if i else None,
        )
    yield store
    await engine.dispose()


async def test_event_page_reads_first_and_middle_rows_without_tail_truncation(kernel_store):
    library = SessionLibrary("owner")
    first = await library.event_page("owner", "session", limit=2)
    assert [event.data["text"] for event in first.items] == ["event-0", "event-1"]
    assert first.next_cursor == first.items[-1].seq
    events = list(first.items)
    cursor = first.next_cursor
    while cursor is not None:
        page = await library.event_page("owner", "session", after_seq=cursor, limit=2)
        events.extend(page.items)
        cursor = page.next_cursor
    assert [event.data["text"] for event in events] == [f"event-{i}" for i in range(7)]
    assert [event.timestamp for event in events] == list(range(1000, 1007))
    assert events[0].legacy_identity and events[0].event_id == f"session:{events[0].seq}"
    assert not events[1].legacy_identity and events[1].event_id == "uid-1"


async def test_event_page_identity_and_empty_end_are_explicit(kernel_store):
    assert await SessionLibrary("other").event_page("other", "session") is None
    with pytest.raises(PermissionError):
        await SessionLibrary("owner").event_page("other", "session")
    full = await SessionLibrary("owner").event_page("owner", "session", limit=7)
    assert full.next_cursor is None and len(full.items) == 7
    empty = await SessionLibrary("owner").event_page(
        "owner", "session", after_seq=full.items[-1].seq
    )
    assert empty.items == () and empty.next_cursor is None
    with pytest.raises(ValueError):
        await SessionLibrary("owner").event_page("owner", "session", limit=501)
