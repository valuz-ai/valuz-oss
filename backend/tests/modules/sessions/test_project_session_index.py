"""Project↔session index — record/query/cascade behaviour.

The index is the host's own mapping of kernel sessions to projects
(``valuz_project_session``); these tests pin the service facade other
modules build on (sidebar list filter, delete-project cascade, runs feed).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.models import ProjectSessionRow


@pytest.fixture(autouse=True)
def _index_db(tmp_path, monkeypatch):
    """Tmp SQLite with just the index table; async UoW bound to it."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "index.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine, tables=[ProjectSessionRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )


async def test_record_and_list_filters_by_project_and_kind() -> None:
    await project_index.record("proj-a", "sess-1", kind="chat", origin="user")
    await project_index.record("proj-a", "sess-2", kind="task_lead", origin="task")
    await project_index.record("proj-a", "sess-3", kind="task_subtask", origin="task")
    await project_index.record("proj-b", "sess-4", kind="chat", origin="automation")

    all_a = await project_index.list_session_ids("proj-a")
    assert set(all_a) == {"sess-1", "sess-2", "sess-3"}

    # user_only drops the task-internal kinds — the sidebar conversation
    # rail must not surface lead/subtask runs.
    user_a = await project_index.list_session_ids("proj-a", user_only=True)
    assert user_a == ["sess-1"]

    assert await project_index.project_of("sess-4") == "proj-b"
    assert await project_index.project_of("missing") is None
    assert await project_index.count_for_project("proj-a") == 3


async def test_record_is_idempotent_on_session_id() -> None:
    await project_index.record("proj-x", "sess-9", kind="chat")
    await project_index.record("proj-y", "sess-9", kind="task_lead", origin="task")

    assert await project_index.project_of("sess-9") == "proj-y"
    assert await project_index.count_for_project("proj-x") == 0
    assert await project_index.count_for_project("proj-y") == 1


async def test_remove_for_project_returns_cascade_ids() -> None:
    await project_index.record("proj-del", "sess-d1")
    await project_index.record("proj-del", "sess-d2", kind="task_lead", origin="task")
    await project_index.record("proj-keep", "sess-k1")

    removed = await project_index.remove_for_project("proj-del")
    assert set(removed) == {"sess-d1", "sess-d2"}
    assert await project_index.count_for_project("proj-del") == 0
    assert await project_index.count_for_project("proj-keep") == 1

    await project_index.remove("sess-k1")
    assert await project_index.project_of("sess-k1") is None


async def test_list_recent_feeds_runs_overview() -> None:
    await project_index.record("proj-r", "sess-r1")
    await project_index.record("proj-r", "sess-r2", kind="task_lead", origin="task")

    rows = await project_index.list_recent(limit=10)
    by_id = {r.session_id: r for r in rows}
    assert by_id["sess-r2"].kind == "task_lead"
    assert by_id["sess-r1"].project_id == "proj-r"
