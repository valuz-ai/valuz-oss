"""Tests for turn-aligned event-window pagination.

The window query lives in the kernel store (``get_events_window``); the
host adapter (``list_events_window``) is a translation shim over the
``KernelClient`` seam. These tests run the REAL kernel store + wire
serializer + host translation as one stack: a tmp-file SQLite is seeded
with hand-crafted ``events`` rows (kernel schema), the seam facade is
pointed at a store over that file, and assertions check the returned
window is whole-turn aligned.

A "turn" here = one ``user_message`` row plus every event that follows
until the next ``user_message`` (or session end). The pagination contract
is: each window starts on a ``user_message`` boundary, ``has_more=True``
iff at least one older ``user_message`` exists strictly before the
window's earliest seq, and the cursor is just ``items[0].seq``.

Tests wrap awaits with ``asyncio.run`` because this repo doesn't ship
pytest-asyncio / pytest-anyio — keeps the suite runnable end-to-end
without adding a plugin dep.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import EventWindowData
from app.serializers import stored_event_to_data
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore

from valuz_agent.adapters import event_sse_adapter
from valuz_agent.adapters.event_sse_adapter import list_events_window


@pytest.fixture
def seeded_engine(tmp_path, monkeypatch):
    """Kernel store over a tmp-file SQLite, wired behind the seam facade.

    The adapter calls ``kernel_client.get_events_window``; we substitute an
    implementation backed by a real ``SQLAlchemyStore`` on a private DB so
    every test exercises the genuine window SQL + wire projection.
    Returns the engine so tests can seed rows before calling the helper.
    """
    db_path = tmp_path / "events.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    store = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))

    async def _window(session_id: str, *, before_seq=None, turn_limit=20) -> EventWindowData:
        items, has_more = await store.get_events_window(
            session_id, before_seq=before_seq, turn_limit=turn_limit
        )
        return EventWindowData(items=[stored_event_to_data(e) for e in items], has_more=has_more)

    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events_window", _window)
    yield engine
    asyncio.run(engine.dispose())


async def _create_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_events(engine, session_id: str, rows: list[tuple[str, str | None]]) -> list[int]:
    """Insert ``rows`` into a freshly-created events table.

    Each row is ``(type, text)``: text is stored inside the JSON ``data``
    blob and can be ``None`` for non-message events. Returns the
    autoincrement ids in insertion order so tests can refer to specific
    rows by index.
    """
    await _create_tables(engine)
    async with engine.begin() as conn:
        ids: list[int] = []
        for idx, (event_type, text_payload) in enumerate(rows):
            data_blob = f'{{"text": "{text_payload}"}}' if text_payload is not None else "{}"
            result = await conn.execute(
                text(
                    "INSERT INTO events "
                    "(user_id, session_id, message_id, type, data, timestamp) "
                    "VALUES ('test-user', :sid, :mid, :type, :data, 1714600000000)"
                ),
                {
                    "sid": session_id,
                    "mid": f"msg-{idx}",
                    "type": event_type,
                    "data": data_blob,
                },
            )
            ids.append(int(result.lastrowid))
        return ids


def _build_turns(count: int) -> list[tuple[str, str | None]]:
    """Generate a synthetic turn sequence: ``count`` × (user, assistant)."""
    rows: list[tuple[str, str | None]] = []
    for i in range(count):
        rows.append(("user_message", f"q{i}"))
        rows.append(("assistant_message", f"a{i}"))
    return rows


# ── Empty / degenerate inputs ──────────────────────────────────────────


def test_should_return_empty_window_when_session_has_no_events(seeded_engine):
    asyncio.run(_create_tables(seeded_engine))

    window = asyncio.run(list_events_window("missing-session", turn_limit=10))

    assert window.items == []
    assert window.has_more is False


def test_should_return_empty_window_when_turn_limit_is_zero(seeded_engine):
    asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(3)))

    window = asyncio.run(list_events_window("sess-1", turn_limit=0))

    assert window.items == []
    assert window.has_more is False


def test_should_return_empty_window_when_session_has_no_user_message(
    seeded_engine,
):
    # Edge case: a session full of system / tool events with zero
    # ``user_message`` rows (e.g. a half-broken seed). The window query
    # depends on ``user_message`` boundaries and must degrade to empty
    # rather than returning every row.
    asyncio.run(
        _seed_events(
            seeded_engine,
            "sess-1",
            [("session_update", None), ("session_idle", None)],
        )
    )

    window = asyncio.run(list_events_window("sess-1", turn_limit=5))

    assert window.items == []
    assert window.has_more is False


# ── Whole-session fits in one window ───────────────────────────────────


def test_should_return_all_turns_when_session_smaller_than_turn_limit(
    seeded_engine,
):
    asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(3)))

    window = asyncio.run(list_events_window("sess-1", turn_limit=10))

    # All 3 user + 3 assistant events present, in seq order.
    assert [it.event_type for it in window.items] == [
        "message.user",
        "message.assistant.delta",
        "message.user",
        "message.assistant.delta",
        "message.user",
        "message.assistant.delta",
    ]
    assert window.has_more is False


def test_should_return_exactly_n_turns_when_session_matches_turn_limit(
    seeded_engine,
):
    asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(5)))

    window = asyncio.run(list_events_window("sess-1", turn_limit=5))

    user_count = sum(1 for it in window.items if it.event_type == "message.user")
    assert user_count == 5
    assert window.has_more is False


# ── Pagination ─────────────────────────────────────────────────────────


def test_should_truncate_to_latest_n_turns_when_session_exceeds_turn_limit(
    seeded_engine,
):
    # 10 turns total, ask for the latest 3 → window contains turns 7..9.
    ids = asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(10)))

    window = asyncio.run(list_events_window("sess-1", turn_limit=3))

    user_seqs = [it.seq for it in window.items if it.event_type == "message.user"]
    # User-message rows live at insertion indices 0,2,4,...,18; the
    # latest 3 are indices 14, 16, 18.
    assert user_seqs == [ids[14], ids[16], ids[18]]
    assert window.has_more is True


def test_should_walk_full_session_via_repeated_before_seq_calls(
    seeded_engine,
):
    # Three pages of 4 turns each over a 12-turn session, walking upward.
    # Concatenating the three responses in reverse order must reproduce
    # the full event list exactly — no gaps, no overlaps.
    asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(12)))

    page1 = asyncio.run(list_events_window("sess-1", turn_limit=4))
    assert page1.has_more is True
    assert sum(1 for it in page1.items if it.event_type == "message.user") == 4

    page2 = asyncio.run(list_events_window("sess-1", before_seq=page1.items[0].seq, turn_limit=4))
    assert page2.has_more is True
    assert sum(1 for it in page2.items if it.event_type == "message.user") == 4

    page3 = asyncio.run(list_events_window("sess-1", before_seq=page2.items[0].seq, turn_limit=4))
    assert page3.has_more is False
    assert sum(1 for it in page3.items if it.event_type == "message.user") == 4

    combined = page3.items + page2.items + page1.items
    seqs = [it.seq for it in combined]
    assert seqs == sorted(seqs)
    assert len(seqs) == 24  # 12 turns * 2 events


def test_should_signal_no_more_when_first_user_message_is_in_window(
    seeded_engine,
):
    # Session has exactly 5 turns, ask for 10 — every event returned and
    # ``has_more`` is false because no older user_message exists.
    asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(5)))

    window = asyncio.run(list_events_window("sess-1", turn_limit=10))

    assert window.has_more is False


# ── Tool-heavy turns ──────────────────────────────────────────────────


def test_should_return_all_events_when_a_single_turn_has_thousands(
    seeded_engine,
):
    # Tool-heavy skill sessions can produce thousands of events per turn.
    # The window endpoint must return them all — the previous event cap
    # silently dropped recent turns when one turn outgrew the cap.
    rows: list[tuple[str, str | None]] = [("user_message", "q0")]
    for tool_idx in range(2000):
        rows.append(("tool_use", f"t{tool_idx}"))
    rows.append(("assistant_message", "a0"))

    asyncio.run(_seed_events(seeded_engine, "sess-1", rows))

    window = asyncio.run(list_events_window("sess-1", turn_limit=1))

    assert len(window.items) == 2002


# ── Cursor edge cases ──────────────────────────────────────────────────


def test_should_isolate_session_when_other_sessions_exist_in_same_table(
    seeded_engine,
):
    # Two sessions side-by-side — the window query must scope to the
    # passed session_id only.
    ids_a = asyncio.run(_seed_events(seeded_engine, "sess-A", _build_turns(2)))

    async def _add_b():
        async with seeded_engine.begin() as conn:
            for i in range(3):
                await conn.execute(
                    text(
                        "INSERT INTO events "
                        "(user_id, session_id, message_id, type, data, timestamp) "
                        "VALUES ('test-user', 'sess-B', :mid, :type, '{}', 1714600000000)"
                    ),
                    {"mid": f"b-{i}", "type": "user_message"},
                )

    asyncio.run(_add_b())

    window = asyncio.run(list_events_window("sess-A", turn_limit=10))

    assert {it.seq for it in window.items} == set(ids_a)
    assert window.has_more is False


def test_should_exclude_before_seq_row_itself_from_window(seeded_engine):
    # ``before_seq`` is exclusive — the cursor row from the previous page
    # must not appear in the next page (would render a duplicate turn).
    ids = asyncio.run(_seed_events(seeded_engine, "sess-1", _build_turns(5)))

    cursor_seq = ids[6]  # 4th user_message
    window = asyncio.run(list_events_window("sess-1", before_seq=cursor_seq, turn_limit=10))

    assert all(it.seq < cursor_seq for it in window.items)
