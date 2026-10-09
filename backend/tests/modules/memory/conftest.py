"""Opt-in isolated durable review database; never touches a host's user data."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.memory.journal import ReviewJobRow, ReviewOwnerRow


@pytest.fixture
def review_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'reviews.db'}", poolclass=NullPool
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda db: ReviewJobRow.metadata.create_all(
                    db,
                    tables=[
                        ReviewJobRow.__table__,
                        ReviewOwnerRow.__table__,
                        ExecutionLeaseRow.__table__,
                    ],
                )
            )

    asyncio.run(create())

    @asynccontextmanager
    async def uow(*, commit: bool = True) -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            try:
                yield db
                if commit:
                    await db.commit()
            except BaseException:
                await db.rollback()
                raise

    from valuz_agent.infra import execution_lease
    from valuz_agent.modules.memory import journal

    monkeypatch.setattr(journal, "async_unit_of_work", uow)
    monkeypatch.setattr(execution_lease, "async_unit_of_work", uow)
    monkeypatch.setattr(execution_lease, "_exclusive_by_construction", lambda: False)
    yield maker
    asyncio.run(engine.dispose())
