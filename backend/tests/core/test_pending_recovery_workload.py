"""Boot recovery must scale with pending actions, not historical conversations."""

# ruff: noqa: I001 -- establishes the kernel src/app import roots.
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401

import pytest
from sqlalchemy import event

from src.adapters.sqlalchemy_store.converters import session_to_model
from src.adapters.sqlalchemy_store.engine import create_engine, create_session_factory
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.adapters.runtime_store import RuntimeStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.orchestrator import SessionOrchestrator
from src.core.types import Message, Session, UserMessage


def session(sid: str, owner: str = "owner-a") -> Session:
    return Session(
        id=sid,
        user_id=owner,
        cwd="/tmp",
        agent_config=AgentConfig(id="agent-" + sid, name="test", model="m"),
    )


@pytest.fixture
async def store(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'kernel.db'}")
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    try:
        yield SQLAlchemyStore(factory), engine, factory
    finally:
        await engine.dispose()


async def pending(store, sid, owner="owner-a", pid="pending", timestamp=10):
    await store.save_session(session(sid, owner))
    msg = Message(
        id="message-" + sid,
        session_id=sid,
        status="completed",
        user_message=UserMessage(text="synthetic recovery test"),
        started_at=1,
    )
    await store.save_message(owner, msg)
    await store.append_event(
        owner,
        sid,
        msg.id,
        Event(type="requires_action", data={"pending_id": pid}, timestamp=timestamp),
    )
    return msg


@pytest.mark.asyncio
async def test_recovery_query_budget_does_not_grow_with_idle_history(store):
    db, engine, factory = store
    async with factory() as tx:
        tx.add_all(session_to_model(session(f"history-{n:04d}")) for n in range(600))
        await tx.commit()
    msg = await pending(db, "open")
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        sealed = await SessionOrchestrator(db).scan_orphan_pendings()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert sealed == 1
    assert len(queries) <= 12, f"Historical sessions caused {len(queries)} recovery reads"
    events = await db.get_events_for_message("owner-a", msg.id)
    assert events[-1].type == "action_resolved"
    assert events[-1].data["decision"] == "expired"


@pytest.mark.asyncio
async def test_resolved_reused_and_foreign_pending_ids(store):
    db, _, _ = store
    closed = await pending(db, "closed", timestamp=10)
    await db.append_event(
        "owner-a",
        "closed",
        closed.id,
        Event(type="action_resolved", data={"pending_id": "pending"}, timestamp=10),
    )
    reused = await pending(db, "reused", timestamp=10)
    await db.append_event(
        "owner-a",
        "reused",
        reused.id,
        Event(type="action_resolved", data={"pending_id": "pending"}, timestamp=11),
    )
    await db.append_event(
        "owner-a",
        "reused",
        reused.id,
        Event(type="requires_action", data={"pending_id": "pending"}, timestamp=12),
    )
    foreign = await pending(db, "foreign", timestamp=10)
    await db.append_event(
        "owner-b",
        "foreign",
        foreign.id,
        Event(type="action_resolved", data={"pending_id": "pending"}, timestamp=11),
    )
    assert await SessionOrchestrator(db).scan_orphan_pendings() == 2
    closed_events = await db.get_events_for_message("owner-a", closed.id)
    assert len(closed_events) == 2
    assert (await db.get_events_for_message("owner-a", reused.id))[-1].type == "action_resolved"
    assert (await db.get_events_for_message("owner-a", foreign.id))[-1].type == "action_resolved"


@pytest.mark.asyncio
async def test_candidate_cursor_survives_resolution_of_previous_page(store):
    db, _, _ = store
    for sid in ("a", "b", "c"):
        await pending(db, sid)
    first = await db.list_pending_action_session_keys(limit=1)
    assert first == [("owner-a", "a")]
    await db.append_event(
        "owner-a",
        "a",
        "message-a",
        Event(type="action_resolved", data={"pending_id": "pending"}, timestamp=20),
    )
    assert await db.list_pending_action_session_keys(after_session_id="a", limit=1) == [
        ("owner-a", "b")
    ]
    assert await db.list_pending_action_session_keys(after_session_id="b", limit=1) == [
        ("owner-a", "c")
    ]


@pytest.mark.asyncio
async def test_runtime_store_candidate_scan_does_not_read_durable_lineage(store, tmp_path):
    local, _, _ = store
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'mirror.db'}")
    async with engine.begin() as tx:
        await tx.run_sync(Base.metadata.create_all)
    mirror = SQLAlchemyStore(create_session_factory(engine))
    combined = RuntimeStore(local, mirror)
    try:
        await pending(combined, "local")
        foreign = await pending(mirror, "other-process", "owner-b")
        assert await SessionOrchestrator(combined).scan_orphan_pendings() == 1
        events = await mirror.get_events_for_message("owner-b", foreign.id)
        assert [e.type for e in events] == ["requires_action"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_store_without_candidate_capability_still_recovers(store):
    original, _, factory = store

    class LegacyStore(SQLAlchemyStore):
        list_pending_action_session_keys = None

    await pending(original, "legacy")
    assert await SessionOrchestrator(LegacyStore(factory)).scan_orphan_pendings() == 1


@pytest.mark.asyncio
async def test_host_retries_when_initial_boot_scan_could_not_finish(tmp_path):
    from app.config import AppConfig
    from app.dependencies import (
        boot_orphan_recovery_complete,
        get_store,
        init_dependencies,
        shutdown_dependencies,
    )
    from valuz_agent.boot import steps

    url = f"sqlite+aiosqlite:///{tmp_path / 'not-yet-migrated.db'}"
    await init_dependencies(
        AppConfig(database_url=url, kernel_store="local", durable_database_url=None)
    )
    engine = create_engine(url)
    try:
        assert not boot_orphan_recovery_complete()
        async with engine.begin() as tx:
            await tx.run_sync(Base.metadata.create_all)
        db = get_store()
        msg = await pending(db, "retry-after-schema")
        await steps.seal_orphan_pendings()
        assert (await db.get_events_for_message("owner-a", msg.id))[-1].type == "action_resolved"
    finally:
        await shutdown_dependencies()
        await engine.dispose()
    assert not boot_orphan_recovery_complete()


@pytest.mark.asyncio
async def test_shared_host_keeps_live_sandbox_approval_and_recovers_only_dead_one(
    store, monkeypatch
):
    from app.dependencies import shutdown_dependencies
    from valuz_agent.boot import steps
    from valuz_agent.infra.config import settings
    from valuz_agent.modules.sessions import recovery

    db, engine, _ = store
    live = await pending(db, "live", "owner-a")
    dead = await pending(db, "dead", "owner-b")
    active = await db.load_session("owner-a", "live")
    active.status = "running"
    await db.save_session(active)
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    monkeypatch.setattr(settings, "kernel_database_url", str(engine.url))
    monkeypatch.setenv("DATABASE_URL", str(engine.url))
    monkeypatch.setenv("VALUZ_DURABLE_DATABASE_URL", "")
    await valuz_agent.boot.kernel.init_kernel_dependencies()
    checked = []

    async def sandbox_alive(owner, sid):
        checked.append((owner, sid))
        return owner == "owner-a" and sid == "live"

    monkeypatch.setattr(recovery, "_sandbox_alive", sandbox_alive)
    monkeypatch.setattr(steps.settings, "deployment_type", "cloud")
    monkeypatch.setattr(steps.settings, "kernel_mode", "inprocess")
    try:
        # Shared-host initialization must neither expire approvals nor reset
        # another process's running session before checking its sandbox.
        assert (await db.load_session("owner-a", "live")).status == "running"
        await steps.seal_orphan_pendings()
        assert sorted(checked) == [("owner-a", "live"), ("owner-b", "dead")]
        assert [e.type for e in await db.get_events_for_message("owner-a", live.id)] == [
            "requires_action"
        ]
        assert (await db.get_events_for_message("owner-b", dead.id))[-1].type == "action_resolved"
    finally:
        await shutdown_dependencies()
