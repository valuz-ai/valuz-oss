"""Project↔session index — record/query/cascade behaviour.

The index is the host's own mapping of kernel sessions to projects
(``valuz_project_session``); these tests pin the service facade other
modules build on (sidebar list filter, delete-project cascade, runs feed).
"""

from types import SimpleNamespace

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
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProjectSessionRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )
    reconciled = getattr(project_index, "_reconciled_owners", None)
    if reconciled is not None:
        reconciled.clear()
    reconcile_locks = getattr(project_index, "_reconcile_locks", None)
    if reconcile_locks is not None:
        reconcile_locks.clear()


async def test_record_and_list_filters_by_project_and_kind() -> None:
    owner = "local-test-owner"
    await project_index.record("proj-a", "sess-1", kind="chat", origin="user", user_id=owner)
    await project_index.record(
        "proj-a", "sess-2", kind="task_lead", origin="task", user_id=owner
    )
    await project_index.record(
        "proj-a", "sess-3", kind="task_subtask", origin="task", user_id=owner
    )
    await project_index.record(
        "proj-b", "sess-4", kind="chat", origin="automation", user_id=owner
    )

    all_a = await project_index.list_session_ids("proj-a", user_id=owner)
    assert set(all_a) == {"sess-1", "sess-2", "sess-3"}

    # user_only drops the task-internal kinds — the sidebar conversation
    # rail must not surface lead/subtask runs.
    user_a = await project_index.list_session_ids("proj-a", user_only=True, user_id=owner)
    assert user_a == ["sess-1"]

    assert await project_index.project_of("sess-4") == "proj-b"
    assert await project_index.project_of("missing") is None
    assert await project_index.count_for_project("proj-a", user_id=owner) == 3


async def test_record_is_idempotent_on_session_id() -> None:
    owner = "local-test-owner"
    await project_index.record("proj-x", "sess-9", kind="chat", user_id=owner)
    await project_index.record(
        "proj-y", "sess-9", kind="task_lead", origin="task", user_id=owner
    )

    assert await project_index.project_of("sess-9") == "proj-y"
    assert await project_index.count_for_project("proj-x", user_id=owner) == 0
    assert await project_index.count_for_project("proj-y", user_id=owner) == 1


async def test_remove_for_project_returns_cascade_ids() -> None:
    owner = "local-test-owner"
    await project_index.record("proj-del", "sess-d1", user_id=owner)
    await project_index.record(
        "proj-del", "sess-d2", kind="task_lead", origin="task", user_id=owner
    )
    await project_index.record("proj-keep", "sess-k1", user_id=owner)

    removed = await project_index.remove_for_project("proj-del", user_id=owner)
    assert set(removed) == {"sess-d1", "sess-d2"}
    assert await project_index.count_for_project("proj-del", user_id=owner) == 0
    assert await project_index.count_for_project("proj-keep", user_id=owner) == 1

    await project_index.remove("sess-k1", user_id=owner)
    assert await project_index.project_of("sess-k1") is None


async def test_list_recent_feeds_runs_overview() -> None:
    owner = "local-test-owner"
    await project_index.record("proj-r", "sess-r1", user_id=owner)
    await project_index.record(
        "proj-r", "sess-r2", kind="task_lead", origin="task", user_id=owner
    )

    rows = await project_index.list_recent(limit=10, user_id=owner)
    by_id = {r.session_id: r for r in rows}
    assert by_id["sess-r2"].kind == "task_lead"
    assert by_id["sess-r1"].project_id == "proj-r"


async def test_reconcile_legacy_sessions_restores_missing_index_rows(monkeypatch) -> None:
    owner = "local-test-owner"
    await project_index.record("proj-existing", "sess-existing", user_id=owner)

    sessions = [
        SimpleNamespace(
            id="sess-existing",
            created_at=100,
            metadata={"valuz": {"project_id": "proj-existing", "origin": "user"}},
        ),
        SimpleNamespace(
            id="sess-chat",
            created_at=200,
            metadata={"valuz": {"project_id": "proj-chat", "origin": "automation"}},
        ),
        SimpleNamespace(
            id="sess-lead",
            created_at=300,
            metadata={
                "valuz": {
                    "project_id": "proj-task",
                    "task_id": "task-1",
                    "run_kind": "lead",
                }
            },
        ),
        SimpleNamespace(
            id="sess-unscoped",
            created_at=400,
            metadata={"valuz": {}},
        ),
    ]
    calls = 0

    async def _list_sessions(user_id, *, limit, offset):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        assert user_id == owner
        assert limit == 500
        assert offset == 0
        return sessions

    from valuz_agent.adapters import kernel_client

    monkeypatch.setattr(kernel_client, "list_sessions", _list_sessions)

    assert await project_index.ensure_legacy_session_index(owner) == 2
    rows = await project_index.list_recent(limit=10, user_id=owner)
    by_id = {row.session_id: row for row in rows}

    assert set(by_id) == {"sess-existing", "sess-chat", "sess-lead"}
    assert by_id["sess-chat"].kind == "chat"
    assert by_id["sess-chat"].origin == "automation"
    assert by_id["sess-chat"].created_at == 200
    assert by_id["sess-chat"].updated_at == 200
    assert by_id["sess-lead"].kind == "task_lead"
    assert by_id["sess-lead"].origin == "task"

    # The owner is reconciled at most once per process; activity polling must
    # not rescan the durable session store every few seconds.
    assert await project_index.ensure_legacy_session_index(owner) == 0
    assert calls == 1
