"""``valuz_feedback`` write invariant: one row per (user, message, action, block_ref)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.feedback.datastore import FeedbackDatastore
from valuz_agent.modules.feedback.models import FeedbackRow  # noqa: F401 — registers the table

USER = "owner-1"


@pytest_asyncio.fixture
async def db(tmp_path) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'feedback.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_repeat_bumps_occurrences_and_overwrites_value(db: AsyncSession) -> None:
    ds = FeedbackDatastore(db)
    first = await ds.upsert(
        USER, session_id="s1", message_id="m1", action="rating", value="up", source="ui"
    )
    assert first.occurrences == 1 and first.value == "up"

    second = await ds.upsert(
        USER,
        session_id="s1",
        message_id="m1",
        action="rating",
        value="down",
        reason_code="inaccurate_or_incomplete",
        source="ui",
        metadata={"k": 1},
    )
    assert second.id == first.id
    assert second.occurrences == 2
    assert second.value == "down"
    assert second.reason_code == "inaccurate_or_incomplete"
    assert second.metadata_ == {"k": 1}
    assert second.updated_at >= first.created_at

    rows = await ds.list_by_session(USER, "s1")
    assert [r.id for r in rows] == [first.id]


@pytest.mark.asyncio
async def test_actions_and_block_refs_are_separate_rows(db: AsyncSession) -> None:
    ds = FeedbackDatastore(db)
    await ds.upsert(
        USER, session_id="s1", message_id="m1", action="rating", value="up", source="ui"
    )
    await ds.upsert(USER, session_id="s1", message_id="m1", action="copy", source="ui")
    await ds.upsert(USER, session_id="s1", message_id="m1", action="copy", source="ui")
    await ds.upsert(
        USER, session_id="s1", message_id="m1", action="copy", block_ref="code:0", source="ui"
    )
    rows = await ds.list_by_session(USER, "s1")
    by_key = {(r.action, r.block_ref): r.occurrences for r in rows}
    assert by_key == {("rating", ""): 1, ("copy", ""): 2, ("copy", "code:0"): 1}


@pytest.mark.asyncio
async def test_owner_scoping_and_delete(db: AsyncSession) -> None:
    ds = FeedbackDatastore(db)
    await ds.upsert(
        USER, session_id="s1", message_id="m1", action="rating", value="up", source="ui"
    )
    await ds.upsert(
        "other", session_id="s1", message_id="m1", action="rating", value="down", source="ui"
    )
    assert len(await ds.list_by_session(USER, "s1")) == 1
    assert len(await ds.list_by_session("other", "s1")) == 1

    assert await ds.delete_by_subject(USER, "m1", "rating") is True
    assert await ds.delete_by_subject(USER, "m1", "rating") is False
    assert await ds.list_by_session(USER, "s1") == []
    # The other owner's row is untouched.
    assert len(await ds.list_by_session("other", "s1")) == 1


@pytest.mark.asyncio
async def test_server_actions_store_targets(db: AsyncSession) -> None:
    ds = FeedbackDatastore(db)
    row = await ds.upsert(
        USER,
        session_id="s1",
        message_id="m1",
        action="fork",
        target_type="session",
        target_id="s2",
        source="server",
    )
    assert (row.target_type, row.target_id, row.source) == ("session", "s2", "server")
    again = await ds.upsert(
        USER,
        session_id="s1",
        message_id="m1",
        action="fork",
        target_type="session",
        target_id="s3",
        source="server",
    )
    assert again.occurrences == 2 and again.target_id == "s3"
