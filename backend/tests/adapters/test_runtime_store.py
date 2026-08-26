"""RuntimeStore — sqlite runtime authority + inline best-effort DataService mirror.

Two real SQLAlchemyStores (distinct sqlite files) play runtime + mirror. Asserts
the ONE kernel store contract: reads and the returned event seq are the RUNTIME
store's; every write is dual-written to the mirror inline (visible immediately,
in commit order); a mirror outage never blocks or fails the runtime write;
redelivery is idempotent on ``event_uid``; the two stores' seqs stay independent.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adapters.runtime_store import RuntimeStore
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Message, Session, UserMessage


class FlakyMirror:
    """Wraps a real store; ``fail=True`` makes every WRITE raise (outage sim)."""

    def __init__(self, inner: SQLAlchemyStore) -> None:
        self._inner = inner
        self.fail = False

    def _guard(self) -> None:
        if self.fail:
            raise RuntimeError("mirror down")

    async def save_session(self, session):
        self._guard()
        return await self._inner.save_session(session)

    async def save_message(self, user_id, message):
        self._guard()
        return await self._inner.save_message(user_id, message)

    async def delete_session(self, user_id, session_id):
        self._guard()
        return await self._inner.delete_session(user_id, session_id)

    async def append_event(
        self, user_id, session_id, message_id, event, *, request_id=None, seq=None
    ):
        self._guard()
        return await self._inner.append_event(
            user_id, session_id, message_id, event, request_id=request_id, seq=seq
        )

    def __getattr__(self, name):  # reads pass straight through
        return getattr(self._inner, name)


async def _mk_store(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False)), engine


@pytest.fixture
async def rt(tmp_path):
    """The kernel store: runtime sqlite authority + inline dual-write mirror."""
    runtime, re_ = await _mk_store(tmp_path / "runtime.db")
    mirror_inner, me = await _mk_store(tmp_path / "mirror.db")
    mirror = FlakyMirror(mirror_inner)
    store = RuntimeStore(runtime, mirror)
    yield store, runtime, mirror, mirror_inner
    await re_.dispose()
    await me.dispose()


def _sess(sid: str, owner: str, cwd: str) -> Session:
    return Session(
        id=sid,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=cwd,
    )


async def _seed(store, owner, tmp_path):
    sid = uuid.uuid4().hex
    await store.save_session(_sess(sid, owner, str(tmp_path)))
    mid = uuid.uuid4().hex
    await store.save_message(
        owner,
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=0,
            status="running",
        ),
    )
    return sid, mid


async def test_writes_dual_write_to_mirror_inline(rt, tmp_path):
    store, runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    # Runtime is authoritative AND the mirror already holds the same rows — the
    # dual-write is inline, there is no flush/barrier to wait for.
    assert await runtime.load_session("u", sid) is not None
    assert await mirror_inner.load_session("u", sid) is not None
    assert await runtime.load_message("u", mid) is not None
    assert await mirror_inner.load_message("u", mid) is not None


async def test_event_seq_is_runtime_authoritative(rt, tmp_path):
    store, runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    s1 = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    s2 = await store.append_event(
        "u", sid, mid, Event(type="assistant_message", data={"text": "x"})
    )
    # The returned seq is the RUNTIME autoincrement (monotonic, == the local
    # read cursor). The mirror assigns its OWN independent seqs — the contract
    # is never "equal seq values"; cross-store identity is the event_uid.
    assert s1 is not None and s2 is not None and s2 > s1
    assert [e.seq for e in await runtime.get_events_after("u", sid, after_seq=0)] == [s1, s2]
    assert len(await mirror_inner.get_events("u", sid)) == 2


async def test_mirror_receives_runtime_commit_order(rt, tmp_path):
    store, _runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    for i in range(5):
        await store.append_event("u", sid, mid, Event(type="thinking", data={"i": i}))
    # Sequential-inline dual-write: mirror arrival order == runtime commit order.
    mirror_events = await mirror_inner.get_events_after("u", sid, after_seq=0)
    assert [e.data["i"] for e in mirror_events] == [0, 1, 2, 3, 4]


async def test_append_idempotent_across_both(rt, tmp_path):
    store, runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    rid = "rid-1"
    a = await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id=rid)
    b = await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id=rid)
    assert a == b  # runtime replay returns the original seq
    assert len(await runtime.get_events("u", sid)) == 1
    assert len(await mirror_inner.get_events("u", sid)) == 1  # uid-idempotent mirror


async def test_stored_events_carry_event_uid_in_both(rt, tmp_path):
    store, runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id="rid-9")
    (runtime_ev,) = await runtime.get_events_after("u", sid, after_seq=0)
    (mirror_ev,) = await mirror_inner.get_events_after("u", sid, after_seq=0)
    # Same identity in both stores — the cross-store merge key.
    assert runtime_ev.event_uid == mirror_ev.event_uid == "rid-9"


async def test_append_without_request_id_mints_shared_uid(rt, tmp_path):
    store, runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    (runtime_ev,) = await runtime.get_events_after("u", sid, after_seq=0)
    (mirror_ev,) = await mirror_inner.get_events_after("u", sid, after_seq=0)
    # RuntimeStore minted ONE uid and used it for both copies.
    assert runtime_ev.event_uid is not None
    assert runtime_ev.event_uid == mirror_ev.event_uid


async def test_reads_are_runtime_only(rt, tmp_path):
    store, _runtime, _mirror, mirror_inner = rt
    sid, mid = await _seed(store, "u", tmp_path)
    # An event that exists ONLY in the mirror is invisible to the kernel — the
    # kernel has no remote read path.
    await mirror_inner.append_event("u", sid, mid, Event(type="user_message", data={}))
    assert await store.get_events("u", sid) == []


async def test_mirror_outage_never_blocks_runtime_writes(rt, tmp_path):
    store, runtime, mirror, mirror_inner = rt
    mirror.fail = True
    sid, mid = await _seed(store, "u", tmp_path)  # would raise if not best-effort
    seq = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    # Runtime writes all succeeded despite the mirror outage…
    assert seq is not None
    assert await runtime.load_session("u", sid) is not None
    assert len(await runtime.get_events("u", sid)) == 1
    # …and the mirror simply missed them (a logged gap; reconciliation of a
    # lagging mirror is an explicit later step, not this class's job).
    assert await mirror_inner.load_session("u", sid) is None
    assert await mirror_inner.get_events("u", sid) == []


async def test_delete_returns_runtime_result_and_mirrors(rt, tmp_path):
    store, runtime, _mirror, mirror_inner = rt
    sid, _mid = await _seed(store, "u", tmp_path)
    assert await store.delete_session("u", sid) is True
    assert await runtime.load_session("u", sid) is None
    assert await mirror_inner.load_session("u", sid) is None
    # Deleting a session unknown to the runtime reports False (runtime verdict).
    assert await store.delete_session("u", uuid.uuid4().hex) is False


async def test_runtime_preexisting_ids_never_drop_events(tmp_path):
    """Regression: one store's seq must NOT be forced onto the other's PK.

    When the RUNTIME store already holds events at ids that overlap the
    mirror's (independent, lower) autoincrement, forcing ``mirror.id =
    runtime_seq`` collided and silently dropped rows. Seqs stay per-store.
    """
    runtime, re_ = await _mk_store(tmp_path / "runtime.db")
    mirror, me = await _mk_store(tmp_path / "mirror.db")
    try:
        sid, mid = await _seed(runtime, "u", tmp_path)  # seed DIRECTLY on runtime
        for _ in range(5):
            await runtime.append_event("u", sid, mid, Event(type="thinking", data={}))
        # Mirror is fresh (autoincrement at 1) — the exact overlap that used to
        # collide. Append through the RuntimeStore.
        store = RuntimeStore(runtime, mirror)
        s = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
        assert s is not None and s > 5  # runtime autoincrement continued
        assert len(await runtime.get_events("u", sid)) == 6
        assert len(await mirror.get_events("u", sid)) == 1
    finally:
        await re_.dispose()
        await me.dispose()


async def test_sqlalchemy_store_explicit_seq(tmp_path):
    """SQLAlchemyStore stores an explicit seq when given one (mirror push)."""
    store, engine = await _mk_store(tmp_path / "x.db")
    try:
        sid, mid = await _seed(store, "u", tmp_path)
        got = await store.append_event(
            "u", sid, mid, Event(type="user_message", data={}), request_id="r", seq=4242
        )
        assert got == 4242
        assert [e.seq for e in await store.get_events_after("u", sid, after_seq=0)] == [4242]
    finally:
        await engine.dispose()
