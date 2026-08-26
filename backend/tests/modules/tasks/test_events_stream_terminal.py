"""Terminal-close behaviour of the task-event SSE stream.

A finished task's stream must end (final ``stream_end`` event) instead of
holding its connection forever — browsers cap HTTP/1.1 at 6 concurrent
connections per host, so one immortal stream per finished task starves every
other request the desktop client makes (all pages hang Pending).
``keep_alive=True`` opts out (the completed-task follow-up chat streams
``deliverable_updated`` events), and ``stopped`` tasks keep their stream
because chat/inject can revive them without the subscriber's involvement.

DB fixture mirrors ``test_queries`` — tmp SQLite + monkeypatched
``AsyncSessionLocal`` so ``async_unit_of_work`` binds to it.
"""

from __future__ import annotations

import asyncio

import pytest

from valuz_agent.api.routes import tasks as tasks_routes
from valuz_agent.modules.tasks.models import TaskEventRow

LOCAL_USER_ID = "local-test-owner"




@pytest.fixture(autouse=True)
def _fast_stream(monkeypatch):
    """Shrink the poll/linger cadence so the tests run in milliseconds."""
    monkeypatch.setattr(tasks_routes, "_TASK_EVENTS_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(tasks_routes, "_TASK_EVENTS_TERMINAL_LINGER_S", 0.05)


def _add_event(db_factory, *, sequence: int, type_: str, task_id: str = "t1") -> None:
    db = db_factory()
    try:
        db.add(
            TaskEventRow(
                user_id=LOCAL_USER_ID,
                project_id="w1",
                task_id=task_id,
                sequence=sequence,
                type=type_,
                actor="lead",
                payload={},
            )
        )
        db.commit()
    finally:
        db.close()


def _iter(initial_status: str, *, keep_alive: bool = False, after_seq: int = 0):
    return tasks_routes._iter_task_events_sse(
        task_id="t1",
        project_id="w1",
        after_seq=after_seq,
        user_id=LOCAL_USER_ID,
        initial_status=initial_status,
        keep_alive=keep_alive,
    )


async def _drain(gen, *, timeout: float = 2.0) -> list[dict[str, str]]:
    """Consume the generator until it returns on its own."""
    items: list[dict[str, str]] = []

    async def _run() -> None:
        async for item in gen:
            items.append(item)

    await asyncio.wait_for(_run(), timeout)
    return items


async def _collect_until_timeout(gen, *, timeout: float) -> list[dict[str, str]]:
    """Consume for ``timeout`` seconds, expecting the stream NOT to end."""
    items: list[dict[str, str]] = []

    async def _run() -> None:
        async for item in gen:
            items.append(item)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_run(), timeout)
    await gen.aclose()
    return items


@pytest.mark.asyncio
async def test_completed_task_stream_replays_then_ends(db_factory):
    _add_event(db_factory, sequence=1, type_="task_completed")

    items = await _drain(_iter("completed"))

    assert [i["event"] for i in items] == ["task_completed", "stream_end"]


@pytest.mark.asyncio
async def test_keep_alive_suppresses_terminal_close(db_factory):
    _add_event(db_factory, sequence=1, type_="task_completed")

    items = await _collect_until_timeout(_iter("completed", keep_alive=True), timeout=0.3)

    assert [i["event"] for i in items if i["event"] != "heartbeat"] == ["task_completed"]


@pytest.mark.asyncio
async def test_active_stream_closes_after_terminal_event_arrives(db_factory):
    _add_event(db_factory, sequence=1, type_="task_planned")
    gen = _iter("active")
    items: list[dict[str, str]] = []

    async def _run() -> None:
        items.append(await anext(gen))  # replayed task_planned
        _add_event(db_factory, sequence=2, type_="task_completed")
        async for item in gen:
            items.append(item)

    await asyncio.wait_for(_run(), 2.0)

    assert [i["event"] for i in items] == ["task_planned", "task_completed", "stream_end"]


@pytest.mark.asyncio
async def test_stopped_task_stream_stays_open(db_factory):
    # ``stopped`` is revivable via chat/inject — the stream must survive so a
    # subscriber sees the eventual ``resumed`` without any action of its own.
    _add_event(db_factory, sequence=1, type_="task_stopped")

    items = await _collect_until_timeout(_iter("stopped"), timeout=0.3)

    assert all(i["event"] != "stream_end" for i in items)
