"""Owner-scoping regression for the kernel ``SQLAlchemyStore``.

Mirrors the host valuz_* owner-scoping: every user-facing read takes the
caller's ``user_id`` first and filters on it; writes stamp the owner. The
cross-owner sweep (``list_sessions(None)``) used by startup recovery is
asserted too. Exercised directly against a tmp SQLite store.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Message, Session, UserMessage


@pytest.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kernel.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


def _session(owner: str, tmp_path) -> Session:
    return Session(
        id=uuid.uuid4().hex,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=str(tmp_path),
    )


async def _seed_session_with_event(store, owner: str, tmp_path) -> tuple[str, str]:
    sess = _session(owner, tmp_path)
    await store.save_session(sess)
    msg = Message(
        id=uuid.uuid4().hex,
        session_id=sess.id,
        user_message=UserMessage(text="hi"),
        started_at=0,
        status="running",
    )
    await store.save_message(owner, msg)
    await store.append_event(owner, sess.id, msg.id, Event(type="user_message", data={}))
    return sess.id, msg.id


class TestKernelStoreOwnerScoping:
    async def test_load_and_list_scoped_by_owner(self, store, tmp_path) -> None:
        sid_a, _ = await _seed_session_with_event(store, "user-A", tmp_path)
        sid_b, _ = await _seed_session_with_event(store, "user-B", tmp_path)

        assert (await store.load_session("user-A", sid_a)) is not None
        assert (await store.load_session("user-B", sid_a)) is None  # cross-owner blocked
        assert {s.id for s in await store.list_sessions("user-A")} == {sid_a}
        assert {s.id for s in await store.list_sessions("user-B")} == {sid_b}

    async def test_events_and_messages_scoped_by_owner(self, store, tmp_path) -> None:
        sid_a, mid_a = await _seed_session_with_event(store, "user-A", tmp_path)

        assert len(await store.get_events("user-A", sid_a)) == 1
        assert await store.get_events("user-B", sid_a) == []  # cross-owner blocked
        assert (await store.load_message("user-A", mid_a)) is not None
        assert (await store.load_message("user-B", mid_a)) is None
        assert {m.id for m in await store.list_messages_for_session("user-A", sid_a)} == {mid_a}
        assert await store.list_messages_for_session("user-B", sid_a) == []

    async def test_delete_is_owner_scoped(self, store, tmp_path) -> None:
        sid_a, _ = await _seed_session_with_event(store, "user-A", tmp_path)
        assert (await store.delete_session("user-B", sid_a)) is False
        assert (await store.load_session("user-A", sid_a)) is not None
        assert (await store.delete_session("user-A", sid_a)) is True

    async def test_list_sessions_none_is_cross_owner(self, store, tmp_path) -> None:
        # Startup recovery sweep: user_id=None spans every owner.
        await _seed_session_with_event(store, "user-A", tmp_path)
        await _seed_session_with_event(store, "user-B", tmp_path)
        owners = {s.user_id for s in await store.list_sessions(None)}
        assert owners == {"user-A", "user-B"}


class TestGetEventsAfterForUser:
    """Cross-session cursor read backing the user-level control-plane stream."""

    async def _seed_run(self, store, owner: str, tmp_path) -> str:
        """One session + a lifecycle-shaped run: user_message → session_idle."""
        sess = _session(owner, tmp_path)
        await store.save_session(sess)
        msg = Message(
            id=uuid.uuid4().hex,
            session_id=sess.id,
            user_message=UserMessage(text="hi"),
            started_at=0,
            status="running",
        )
        await store.save_message(owner, msg)
        await store.append_event(owner, sess.id, msg.id, Event(type="user_message", data={}))
        await store.append_event(owner, sess.id, msg.id, Event(type="text_delta", data={}))
        await store.append_event(owner, sess.id, msg.id, Event(type="session_idle", data={}))
        return sess.id

    async def test_aggregates_across_sessions_ordered_by_seq(self, store, tmp_path) -> None:
        sid1 = await self._seed_run(store, "user-A", tmp_path)
        sid2 = await self._seed_run(store, "user-A", tmp_path)

        rows = await store.get_events_after_for_user("user-A")
        # Both sessions' events, one owner, ascending by the global cursor.
        assert {r.session_id for r in rows} == {sid1, sid2}
        seqs = [r.seq for r in rows]
        assert seqs == sorted(seqs)

    async def test_owner_scoped(self, store, tmp_path) -> None:
        await self._seed_run(store, "user-A", tmp_path)
        await self._seed_run(store, "user-B", tmp_path)

        rows_a = await store.get_events_after_for_user("user-A")
        assert rows_a  # A sees its own
        assert all(r.session_id for r in rows_a)
        # None of A's rows leak to B and vice versa: disjoint session sets.
        a_sessions = {r.session_id for r in rows_a}
        b_sessions = {r.session_id for r in await store.get_events_after_for_user("user-B")}
        assert a_sessions.isdisjoint(b_sessions)

    async def test_after_seq_cursor(self, store, tmp_path) -> None:
        await self._seed_run(store, "user-A", tmp_path)
        rows = await store.get_events_after_for_user("user-A")
        assert len(rows) >= 3
        cursor = rows[0].seq
        after = await store.get_events_after_for_user("user-A", after_seq=cursor)
        assert all(r.seq > cursor for r in after)
        assert len(after) == len(rows) - 1

    async def test_types_filter_excludes_deltas(self, store, tmp_path) -> None:
        await self._seed_run(store, "user-A", tmp_path)
        lifecycle = ("user_message", "session_idle", "session_error", "session_update")
        rows = await store.get_events_after_for_user("user-A", types=lifecycle)
        types = {r.type for r in rows}
        assert "text_delta" not in types
        assert types == {"user_message", "session_idle"}
