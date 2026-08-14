"""Durable-mirror schema reconcile: stale sessions CHECK gains deepseek_harness.

Reproduces the field failure: a durable (valuz.db) seeded before kernel
revision 0004 keeps the three-value ``ck_sessions_runtime_provider``;
``create_all`` never ALTERs it, so deepseek_harness mirror writes were
silently rejected and every host read of the session 404'd. The reconcile in
``app.dependencies._ensure_durable_schema`` must widen the constraint in
place without touching existing rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.dependencies import _ensure_durable_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_LEGACY_SESSIONS_DDL = """
CREATE TABLE sessions (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    agent_config JSON NOT NULL,
    cwd TEXT NOT NULL,
    runtime_provider VARCHAR(20) NOT NULL,
    model VARCHAR(100) NOT NULL,
    instructions TEXT NOT NULL,
    skills JSON NOT NULL,
    mcp_servers JSON NOT NULL,
    model_provider JSON,
    model_settings JSON,
    permission_mode VARCHAR(20) NOT NULL,
    mode VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    stop_reason JSON,
    created_at BIGINT NOT NULL,
    metadata JSON NOT NULL,
    runtime_session_id VARCHAR(64),
    todos JSON,
    CONSTRAINT ck_sessions_mode CHECK (mode IN ('default', 'plan', 'goal')),
    CONSTRAINT ck_sessions_permission_mode
        CHECK (permission_mode IN ('default', 'auto_review', 'full_access')),
    CONSTRAINT ck_sessions_runtime_provider
        CHECK (runtime_provider IN ('claude_agent', 'codex', 'deepagents')),
    CONSTRAINT ck_sessions_status
        CHECK (status IN ('created', 'idle', 'running', 'terminated')),
    PRIMARY KEY (id)
)
"""

_INSERT = text(
    "INSERT INTO sessions (id, user_id, agent_config, cwd, runtime_provider, model, "
    "instructions, skills, mcp_servers, permission_mode, mode, status, created_at, metadata) "
    "VALUES (:id, 'u', '{}', '/tmp', :rt, 'm', '', '[]', '[]', "
    "'full_access', 'default', 'created', 0, '{}')"
)


@pytest.mark.asyncio
async def test_reconcile_widens_legacy_check_and_keeps_rows(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/durable.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_LEGACY_SESSIONS_DDL))
            await conn.execute(_INSERT, {"id": "old1", "rt": "claude_agent"})

        await _ensure_durable_schema(engine)

        async with engine.begin() as conn:
            ddl = (
                await conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE name='sessions'")
                )
            ).scalar_one()
            assert "deepseek_harness" in ddl
            rows = (
                await conn.execute(text("SELECT id, runtime_provider FROM sessions"))
            ).all()
            assert rows == [("old1", "claude_agent")]
            await conn.execute(_INSERT, {"id": "new1", "rt": "deepseek_harness"})

        # Idempotent: a second boot leaves the widened schema untouched.
        await _ensure_durable_schema(engine)
        async with engine.begin() as conn:
            rows = (
                await conn.execute(text("SELECT id FROM sessions ORDER BY id"))
            ).all()
            assert [r[0] for r in rows] == ["new1", "old1"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fresh_durable_gets_current_schema(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
    try:
        await _ensure_durable_schema(engine)
        async with engine.begin() as conn:
            ddl = (
                await conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE name='sessions'")
                )
            ).scalar_one()
            assert "deepseek_harness" in ddl
            await conn.execute(_INSERT, {"id": "s1", "rt": "deepseek_harness"})
    finally:
        await engine.dispose()
