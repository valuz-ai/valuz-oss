"""Bundled connectors are installed without silently starting OAuth."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.seeds.connectors import seed_builtin_connectors


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "builtin-connectors.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(
        sync_engine,
        tables=[
            ConnectorRow.__table__,
            ConnectorAttrRow.__table__,
            ConnectorOAuthRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def test_seed_installs_builtins_pending_without_credentials(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        await seed_builtin_connectors(db, user_id="user-1")
        await db.commit()

    async with sessionmaker_() as db:
        rows = (
            await db.execute(
                select(ConnectorRow)
                .where(ConnectorRow.user_id == "user-1")
                .order_by(ConnectorRow.slug)
            )
        ).scalars().all()
        oauth_count = len((await db.execute(select(ConnectorOAuthRow))).scalars().all())

    assert [row.slug for row in rows] == ["valuz-data", "valuz-search"]
    assert all(row.connector_type == "builtin" for row in rows)
    assert all(row.status == "pending_auth" and not row.enabled for row in rows)
    assert rows[0].url == "https://data.valuz.cn/mcp"
    assert rows[1].url == "https://data.valuz.cn/mcp/search"
    assert oauth_count == 0


async def test_seed_is_idempotent_and_owner_scoped(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        await seed_builtin_connectors(db, user_id="user-1")
        await seed_builtin_connectors(db, user_id="user-1")
        await seed_builtin_connectors(db, user_id="user-2")
        await db.commit()

    async with sessionmaker_() as db:
        rows = (await db.execute(select(ConnectorRow))).scalars().all()

    assert len(rows) == 4
    assert {(row.user_id, row.slug) for row in rows} == {
        ("user-1", "valuz-search"),
        ("user-1", "valuz-data"),
        ("user-2", "valuz-search"),
        ("user-2", "valuz-data"),
    }
