"""Seeded tests for the kernel usage rollup (``StorePort.usage_rollup``).

PR #85 review follow-up: the analytics read path moved onto
``GET /api/v1/usage`` with only an empty-result smoke. These pin the
aggregation semantics: ``completed``-only filtering, half-open
``[start_ms, end_ms)`` window boundaries, per-(UTC day, model) grouping,
and exact request/token/cache sums.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore

DAY1 = 1717200000000  # 2024-06-01T00:00:00Z (epoch ms)
DAY2 = DAY1 + 86_400_000


@pytest.fixture
def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")

    async def _setup() -> SQLAlchemyStore:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))

    s = asyncio.run(_setup())
    s._engine_for_tests = engine  # keep a handle for raw seeding
    yield s
    asyncio.run(engine.dispose())


async def _seed_session(store: SQLAlchemyStore, session_id: str, model: str) -> None:
    async with store._engine_for_tests.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, user_id, agent_config, cwd, runtime_provider, "
                "model, instructions, skills, mcp_servers, permission_mode, mode, status, "
                "created_at, metadata, todos) "
                "VALUES (:id, 'u', '{\"name\": \"a\"}', '/tmp', 'claude_agent', :model, "
                "'', '[]', '[]', 'full_access', 'default', 'idle', :ts, '{}', 'null')"
            ),
            {"id": session_id, "model": model, "ts": DAY1},
        )


async def _seed_message(
    store: SQLAlchemyStore,
    *,
    message_id: str,
    session_id: str,
    status: str,
    started_at: int,
    turns: int,
    inp: int,
    out: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> None:
    async with store._engine_for_tests.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO messages (id, user_id, session_id, user_message, status, "
                "total_turns, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, started_at, metadata) "
                "VALUES (:id, 'u', :sid, '{\"text\": \"q\"}', :status, :turns, :inp, :out, "
                ":cr, :cw, :ts, '{}')"
            ),
            {
                "id": message_id,
                "sid": session_id,
                "status": status,
                "turns": turns,
                "inp": inp,
                "out": out,
                "cr": cache_read,
                "cw": cache_write,
                "ts": started_at,
            },
        )


def test_rollup_groups_by_day_and_model_with_exact_sums(store) -> None:
    async def _run():
        await _seed_session(store, "s-claude", "claude-sonnet-4-6")
        await _seed_session(store, "s-gpt", "gpt-5.5")
        # Two completed messages same day same model — sums add up.
        await _seed_message(
            store,
            message_id="m1",
            session_id="s-claude",
            status="completed",
            started_at=DAY1 + 1000,
            turns=2,
            inp=100,
            out=50,
            cache_read=10,
            cache_write=5,
        )
        await _seed_message(
            store,
            message_id="m2",
            session_id="s-claude",
            status="completed",
            started_at=DAY1 + 2000,
            turns=3,
            inp=200,
            out=75,
            cache_read=20,
            cache_write=15,
        )
        # Different model, same day — separate row.
        await _seed_message(
            store,
            message_id="m3",
            session_id="s-gpt",
            status="completed",
            started_at=DAY1 + 3000,
            turns=1,
            inp=40,
            out=20,
        )
        # Same model, NEXT UTC day — separate row.
        await _seed_message(
            store,
            message_id="m4",
            session_id="s-claude",
            status="completed",
            started_at=DAY2 + 1000,
            turns=1,
            inp=11,
            out=7,
        )
        return await store.usage_rollup("u", DAY1, DAY2 + 86_400_000)

    rows = asyncio.run(_run())
    by_key = {(r.day, r.model): r for r in rows}
    assert set(by_key) == {
        ("2024-06-01", "claude-sonnet-4-6"),
        ("2024-06-01", "gpt-5.5"),
        ("2024-06-02", "claude-sonnet-4-6"),
    }
    claude_day1 = by_key[("2024-06-01", "claude-sonnet-4-6")]
    assert claude_day1.request_count == 5  # 2 + 3 turns
    assert claude_day1.input_tokens == 300
    assert claude_day1.output_tokens == 125
    assert claude_day1.cache_read_tokens == 30
    assert claude_day1.cache_write_tokens == 20
    assert by_key[("2024-06-01", "gpt-5.5")].input_tokens == 40
    assert by_key[("2024-06-02", "claude-sonnet-4-6")].request_count == 1


def test_rollup_counts_only_completed_messages(store) -> None:
    async def _run():
        await _seed_session(store, "s1", "claude-sonnet-4-6")
        await _seed_message(
            store,
            message_id="ok",
            session_id="s1",
            status="completed",
            started_at=DAY1 + 1000,
            turns=1,
            inp=10,
            out=5,
        )
        for status in ("errored", "running", "cancelled"):
            await _seed_message(
                store,
                message_id=f"skip-{status}",
                session_id="s1",
                status=status,
                started_at=DAY1 + 2000,
                turns=9,
                inp=999,
                out=999,
            )
        return await store.usage_rollup("u", DAY1, DAY2)

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0].input_tokens == 10 and rows[0].request_count == 1


def test_rollup_window_is_half_open(store) -> None:
    async def _run():
        await _seed_session(store, "s1", "m")
        # Exactly at start → included; exactly at end → excluded.
        await _seed_message(
            store,
            message_id="at-start",
            session_id="s1",
            status="completed",
            started_at=DAY1,
            turns=1,
            inp=1,
            out=1,
        )
        await _seed_message(
            store,
            message_id="at-end",
            session_id="s1",
            status="completed",
            started_at=DAY2,
            turns=1,
            inp=100,
            out=100,
        )
        return await store.usage_rollup("u", DAY1, DAY2)

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0].input_tokens == 1
