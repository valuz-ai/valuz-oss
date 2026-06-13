"""Owner-scoping + composite-PK regression for ``SkillDatastore``."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillIndexRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "skills.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[SkillIndexRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _row(skill_id: str) -> SkillIndexRow:
    return SkillIndexRow(
        user_id="local-test-owner",
        id=skill_id,
        slug=skill_id,
        name=skill_id,
        scope="user",
        source="valuz",
        source_path=f"/tmp/{skill_id}",
    )


class TestSkillOwnerScoping:
    async def test_create_stamps_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await SkillDatastore(db).create("user-A", _row("s1"))
        async with sessionmaker_() as db:
            row = await SkillDatastore(db).get_by_id("user-A", "s1")
            assert row is not None and row.user_id == "user-A"

    async def test_reads_absent_for_other_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await SkillDatastore(db).create("user-A", _row("s1"))
        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            assert await ds.get_by_id("user-A", "s1") is not None
            assert await ds.get_by_id("user-B", "s1") is None
            assert {r.id for r in await ds.list_skills("user-A")} == {"s1"}
            assert await ds.list_skills("user-B") == []

    async def test_delete_is_owner_scoped(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await SkillDatastore(db).create("user-A", _row("s1"))
        async with sessionmaker_() as db:
            await SkillDatastore(db).delete("user-B", "s1")
        async with sessionmaker_() as db:
            assert await SkillDatastore(db).get_by_id("user-A", "s1") is not None
