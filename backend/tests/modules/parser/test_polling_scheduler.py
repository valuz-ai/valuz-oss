"""Coverage for the on-loop ``PollingScheduler`` happy-path + failure flows.

A ``FakePollingHandler`` lets us exercise the scheduler without making HTTP
calls. The scheduler is an on-loop asyncio task: its tick coroutine, the DB
I/O, and the ``await_task`` futures all live on the test's event loop, so the
test simply ``startup()``s it, enqueues, and awaits.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from valuz_agent.infra.database import Base
from valuz_agent.modules.parser.polling import (
    PollFailed,
    PollingScheduler,
    PollOutcome,
    PollPending,
    PollSucceeded,
)
from valuz_agent.ports.parser_backend import ParseResult


class _FakeHandler:
    """Scripted handler: a per-instance queue of outcomes the test enqueues
    before the run. ``submit`` always returns ``ext-1``.

    ``max_attempts`` is intentionally large so the "timed out" terminal state
    never trips during cancel/race tests."""

    kind: str = "parser.fake"
    initial_delay_s: float = 0.01
    max_delay_s: float = 0.05
    max_attempts: int = 500

    def __init__(self) -> None:
        self.outcomes: list[PollOutcome] = []
        self.submit_calls = 0
        self.fetch_calls = 0

    def submit(self, payload):  # type: ignore[no-untyped-def]
        self.submit_calls += 1
        return "ext-1"

    def poll(self, external_task_id, payload) -> PollOutcome:
        return self.outcomes.pop(0)

    def fetch_result(self, external_task_id, payload, raw) -> ParseResult:
        self.fetch_calls += 1
        return ParseResult(
            markdown="**done**",
            page_count=1,
            metadata={"engine": "fake", **dict(raw)},
        )


@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Point ``infra.db.AsyncSessionLocal`` at a file-backed (WAL) async engine.

    Everything runs on the one test loop, so a default pool would be fine, but
    ``NullPool`` + WAL keeps the fixture robust and matches how the app engine
    is configured.
    """
    import valuz_agent.infra.db as db_mod
    import valuz_agent.modules.parser  # noqa: F401 — register PollingTaskRow

    db_file = tmp_path / "polling.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)

    @event.listens_for(async_engine.sync_engine, "connect")
    def _pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    monkeypatch.setattr(
        db_mod, "AsyncSessionLocal", async_sessionmaker(bind=async_engine, expire_on_commit=False)
    )
    yield


@pytest_asyncio.fixture
async def scheduler(_db):
    handler = _FakeHandler()
    sched = PollingScheduler(handlers=[handler])
    # Tighten the tick so the test runs in well under a second.
    sched._TICK_INTERVAL_S = 0.01  # type: ignore[attr-defined]
    await sched.startup()
    yield sched, handler
    await sched.shutdown()


async def test_pending_then_succeeds(scheduler):
    sched, handler = scheduler
    handler.outcomes = [PollPending(next_in_s=0.01), PollSucceeded(raw={"k": "v"})]
    task_id = await sched.enqueue("parser.fake", {"file": "x"})
    result = await asyncio.wait_for(sched.await_task(task_id), timeout=15.0)
    assert result.markdown == "**done**"
    assert result.metadata["engine"] == "fake"
    assert result.metadata["k"] == "v"
    assert handler.fetch_calls == 1


async def test_failure_propagates_to_awaiter(scheduler):
    sched, handler = scheduler
    handler.outcomes = [PollFailed(error="upstream broke")]
    task_id = await sched.enqueue("parser.fake", {})
    with pytest.raises(RuntimeError, match="upstream broke"):
        await asyncio.wait_for(sched.await_task(task_id), timeout=15.0)


async def test_unknown_kind_at_enqueue_raises(scheduler):
    sched, _ = scheduler
    with pytest.raises(KeyError):
        await sched.enqueue("parser.does_not_exist", {})


async def test_cancel_resolves_awaiter_with_error(scheduler):
    sched, handler = scheduler
    # Make poll always pending so the cancel-vs-completion race goes to cancel.
    handler.outcomes = [PollPending(next_in_s=0.05)] * 100

    task_id = await sched.enqueue("parser.fake", {})

    async def _cancel_soon():
        await asyncio.sleep(0.1)
        await sched.cancel(task_id)

    cancel_task = asyncio.create_task(_cancel_soon())
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(sched.await_task(task_id), timeout=15.0)
    await cancel_task


def test_duplicate_handler_kind_rejected():
    h1 = _FakeHandler()
    h2 = _FakeHandler()
    with pytest.raises(ValueError, match="duplicate"):
        PollingScheduler(handlers=[h1, h2])


async def test_startup_shutdown_is_idempotent(_db):
    sched = PollingScheduler(handlers=[_FakeHandler()])
    await sched.startup()
    await sched.startup()  # second startup is a no-op while running
    await sched.shutdown()
    await sched.shutdown()  # second shutdown is harmless
