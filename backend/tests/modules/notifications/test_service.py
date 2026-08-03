"""NotificationService — durable-only ledger (docs/design/notifications.md).

Delivery is by re-reading the persisted table (the SSE stream polls it), NOT an
in-process broadcast — so these tests exercise the write→snapshot path and prove
a second service instance (a second pod sharing the DB) sees the same state.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.service import NotificationService

OWNER = "local-test-owner"


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "notif.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[NotificationRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _q(**kw):
    base = dict(
        dedup_key="q:p1",
        kind="question",
        title="architect 需要你确认",
        body="选哪种布局？",
        route="/tasks/t1",
        action="answer",
        task_id="t1",
        pending_id="p1",
    )
    base.update(kw)
    return base


def test_ingest_is_idempotent_by_dedup(db_factory) -> None:
    svc = NotificationService()

    async def run():
        e1 = await svc.ingest(OWNER, **_q())
        e2 = await svc.ingest(OWNER, **_q())  # re-fire — same subject
        entries, unread = await svc.snapshot(OWNER)
        return e1, e2, entries, unread

    e1, e2, entries, unread = asyncio.run(run())
    assert e1 is not None and e2 is not None
    assert e1.id == e2.id  # upsert returned the same row
    assert len(entries) == 1
    assert unread == 1


def test_ingest_publishes_created_event_only_for_new_row(
    db_factory, monkeypatch
) -> None:
    from valuz_agent.infra.eventbus import event_bus

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        event_bus,
        "publish",
        lambda topic, **payload: published.append((topic, payload)),
    )
    svc = NotificationService()

    async def run() -> None:
        await svc.ingest(OWNER, **_q())
        await svc.ingest(OWNER, **_q())

    asyncio.run(run())

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "notification.created"
    assert payload["owner_user_id"] == OWNER
    assert payload["unread"] == 1
    assert payload["notification"]["kind"] == "question"
    assert payload["notification"]["pending_id"] == "p1"


def test_resolve_clears_from_open_set(db_factory) -> None:
    svc = NotificationService()

    async def run():
        await svc.ingest(OWNER, **_q())
        await svc.resolve(OWNER, "q:p1")
        return await svc.snapshot(OWNER)

    entries, unread = asyncio.run(run())
    assert entries == []
    assert unread == 0


def test_mark_read_drops_unread_but_keeps_open(db_factory) -> None:
    svc = NotificationService()

    async def run():
        e = await svc.ingest(OWNER, **_q())
        await svc.mark_read(OWNER, e.id)
        return await svc.snapshot(OWNER)

    entries, unread = asyncio.run(run())
    assert len(entries) == 1  # still open (unresolved)
    assert entries[0].read_at is not None
    assert unread == 0


def test_resolve_pending_clears_without_owner(db_factory) -> None:
    """``resolve_pending`` finds the row by the globally-unique ``pending_id``
    alone — no owner needed. Used for conversation questions, whose owner isn't
    held in memory at resolve time."""
    svc = NotificationService()

    async def run():
        await svc.ingest(OWNER, **_q(route="/conversation/sess-1", task_id=None))
        await svc.resolve_pending("p1")  # no owner supplied
        return await svc.snapshot(OWNER)

    entries, unread = asyncio.run(run())
    assert entries == []
    assert unread == 0


def test_resolve_pending_missing_is_noop(db_factory) -> None:
    svc = NotificationService()
    # No matching row → best-effort no-op, no raise.
    asyncio.run(svc.resolve_pending("does-not-exist"))


def test_owner_scoped(db_factory) -> None:
    svc = NotificationService()

    async def run():
        await svc.ingest(OWNER, **_q())
        await svc.ingest("other", **_q(dedup_key="q:p2", pending_id="p2"))
        mine, _ = await svc.snapshot(OWNER)
        theirs, _ = await svc.snapshot("other")
        return mine, theirs

    mine, theirs = asyncio.run(run())
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0].pending_id == "p1"


def test_second_pod_sees_the_same_ledger(db_factory) -> None:
    """Multi-pod safety: two independent ``NotificationService`` instances
    (simulating two pods sharing one DB) observe each other's writes through the
    persisted table — no in-process fan-out involved. This is exactly what the
    DB-poll SSE stream relies on."""
    pod_a = NotificationService()
    pod_b = NotificationService()

    async def run():
        # Written on pod A, read on pod B.
        await pod_a.ingest(OWNER, **_q())
        seen_by_b, unread_b = await pod_b.snapshot(OWNER)
        # Resolved on pod B, gone on pod A.
        await pod_b.resolve(OWNER, "q:p1")
        seen_by_a, unread_a = await pod_a.snapshot(OWNER)
        return seen_by_b, unread_b, seen_by_a, unread_a

    seen_by_b, unread_b, seen_by_a, unread_a = asyncio.run(run())
    assert len(seen_by_b) == 1 and unread_b == 1
    assert seen_by_b[0].pending_id == "p1"
    assert seen_by_a == [] and unread_a == 0


def test_snapshot_signature_tracks_add_read_resolve(db_factory) -> None:
    """The stream's change key flips on add / read / resolve and is stable when
    nothing changed — so the poll emits a fresh snapshot exactly when needed."""
    from valuz_agent.api.routes.notifications import _snapshot_signature

    svc = NotificationService()

    async def run():
        empty = _snapshot_signature(*await svc.snapshot(OWNER))
        e = await svc.ingest(OWNER, **_q())
        after_add = _snapshot_signature(*await svc.snapshot(OWNER))
        after_add_again = _snapshot_signature(*await svc.snapshot(OWNER))
        await svc.mark_read(OWNER, e.id)
        after_read = _snapshot_signature(*await svc.snapshot(OWNER))
        await svc.resolve(OWNER, "q:p1")
        after_resolve = _snapshot_signature(*await svc.snapshot(OWNER))
        return empty, after_add, after_add_again, after_read, after_resolve

    empty, after_add, after_add_again, after_read, after_resolve = asyncio.run(run())
    assert after_add != empty  # add changed it
    assert after_add_again == after_add  # no change → stable (poll stays quiet)
    assert after_read != after_add  # read-state flip changed it
    assert after_resolve == empty  # back to empty open set
