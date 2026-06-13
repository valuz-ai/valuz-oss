"""Owner-scoping regression for ``DocumentDatastore`` (KB reads + write-stamp)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.models import (
    DocumentRecordRow,
    KbFolderRow,
    KnowledgeBaseRow,
    ProjectKbBindingRow,
)


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "docs.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            KnowledgeBaseRow.__table__,
            KbFolderRow.__table__,
            DocumentRecordRow.__table__,
            ProjectKbBindingRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _create_kb(sm, owner: str, name: str, root: str) -> str:
    async with sm() as db:
        row = KnowledgeBaseRow(user_id="local-test-owner", name=name, root_path=root)
        await DocumentDatastore(db).create_kb(owner, row)
        return row.id


class TestDocsOwnerScoping:
    async def test_create_stamps_owner(self, sessionmaker_) -> None:
        kid = await _create_kb(sessionmaker_, "user-A", "A", "/tmp/a")
        async with sessionmaker_() as db:
            row = await DocumentDatastore(db).get_kb("user-A", kid)
            assert row is not None and row.user_id == "user-A"

    async def test_get_kb_absent_for_other_owner(self, sessionmaker_) -> None:
        kid = await _create_kb(sessionmaker_, "user-A", "A", "/tmp/a")
        async with sessionmaker_() as db:
            ds = DocumentDatastore(db)
            assert await ds.get_kb("user-A", kid) is not None
            assert await ds.get_kb("user-B", kid) is None

    async def test_list_kbs_only_callers(self, sessionmaker_) -> None:
        await _create_kb(sessionmaker_, "user-A", "A", "/tmp/a")
        await _create_kb(sessionmaker_, "user-B", "B", "/tmp/b")
        async with sessionmaker_() as db:
            ds = DocumentDatastore(db)
            assert {r.name for r in await ds.list_kbs("user-A")} == {"A"}
            assert {r.name for r in await ds.list_kbs("user-B")} == {"B"}

    async def test_delete_kb_owner_scoped(self, sessionmaker_) -> None:
        kid = await _create_kb(sessionmaker_, "user-A", "A", "/tmp/a")
        async with sessionmaker_() as db:
            await DocumentDatastore(db).delete_kb("user-B", kid)
        async with sessionmaker_() as db:
            assert await DocumentDatastore(db).get_kb("user-A", kid) is not None
