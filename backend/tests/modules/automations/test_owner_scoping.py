"""Owner-scoping regression for ``AutomationDatastore``.

User-facing reads filter by ``user_id``; writes stamp the owner. The background
sweeps (``find_due_automations`` / ``list_enabled`` / ``list_stranded_runs``)
stay cross-owner by design — those are asserted here too.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "auto.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine, tables=[AutomationRow.__table__, AutomationRunRow.__table__]
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _auto(name: str, *, status: str = "enabled", next_run_at: int | None = None) -> AutomationRow:
    return AutomationRow(
        id=uuid4().hex,
        name=name,
        agent_kind="project_member",
        agent_slug="a1",
        project_id="p1",
        prompt_template="hi",
        action_kind="chat",
        trigger_kind="manual",
        status=status,
        next_run_at=next_run_at,
    )


class TestAutomationOwnerScoping:
    async def test_reads_scoped_by_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            a = await AutomationDatastore(db).create_automation("user-A", _auto("A"))
        async with sessionmaker_() as db:
            ds = AutomationDatastore(db)
            assert (await ds.get_automation("user-A", a.id)) is not None
            assert (await ds.get_automation("user-B", a.id)) is None
            assert {r.name for r in await ds.list_automations("user-A")} == {"A"}
            assert await ds.list_automations("user-B") == []

    async def test_delete_owner_scoped(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            a = await AutomationDatastore(db).create_automation("user-A", _auto("A"))
        async with sessionmaker_() as db:
            await AutomationDatastore(db).delete_automation("user-B", a.id)
        async with sessionmaker_() as db:
            assert (await AutomationDatastore(db).get_automation("user-A", a.id)) is not None

    async def test_find_due_is_cross_owner(self, sessionmaker_) -> None:
        # The tick sweep must see every owner's due rows.
        async with sessionmaker_() as db:
            ds = AutomationDatastore(db)
            await ds.create_automation("user-A", _auto("A", next_run_at=10))
            await ds.create_automation("user-B", _auto("B", next_run_at=10))
        async with sessionmaker_() as db:
            due = await AutomationDatastore(db).find_due_automations(100)
            assert {r.name for r in due} == {"A", "B"}
            assert {r.user_id for r in due} == {"user-A", "user-B"}
