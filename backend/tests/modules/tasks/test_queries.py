"""Characterization tests for ``tasks/queries.py`` (the read-side functions
extracted from ``TaskOrchestrator`` in the T1.1 split).

Pins the dict shapes the dispatch MCP tools depend on — list_tasks
filtering/limit/run-counts, get_task projection + project scoping, and the
list_members degraded path when the kernel agent can't be loaded.

DB fixture mirrors ``test_chatplan_s4`` — tmp SQLite + monkeypatched
``AsyncSessionLocal`` so ``async_unit_of_work`` binds to it; no kernel bring-up.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import ProjectMemberRow
from valuz_agent.modules.tasks import queries
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """A tmp-SQLite sync sessionmaker; async UoW bound to the same file."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "queries.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            TaskRow.__table__,
            TaskEventRow.__table__,
            TaskSessionRow.__table__,
            ProjectMemberRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod, "AsyncSessionLocal", async_sessionmaker(bind=async_engine, expire_on_commit=False)
    )
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _add_task(
    db_factory,
    tmp_path,
    *,
    task_id: str,
    project_id: str = "w1",
    status: str = "active",
    originator: str = "chat-1",
    title: str = "T",
) -> None:
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id=task_id,
                project_id=project_id,
                file_path=str(tmp_path / f"{task_id}.md"),
                title=title,
                goal="do it",
                status=status,
                created_by="user",
                lead_agent_slug="lead-agent",
                current_holder="lead-agent",
                metadata_={"originating_session_id": originator, "dispatch_mode": "async"},
            )
        )
        db.commit()
    finally:
        db.close()


def _add_run(db_factory, tmp_path, *, task_id, project_id="w1", session_id, status="active"):
    db = db_factory()
    try:
        db.add(
            TaskSessionRow(
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                agent_slug="member-a",
                sequence=0,
                kind="member",
                status=status,
                label="L",
                goal="g",
                subtask_key="s1",
                project_mode="shared",
                run_dir=str(tmp_path),
            )
        )
        db.commit()
    finally:
        db.close()


# ── list_tasks ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tasks_returns_summaries_with_run_counts(db_factory, tmp_path):
    _add_task(db_factory, tmp_path, task_id="t1")
    _add_run(db_factory, tmp_path, task_id="t1", session_id="s-a", status="completed")
    _add_run(db_factory, tmp_path, task_id="t1", session_id="s-b", status="active")

    out = await queries.list_tasks("w1")

    assert len(out) == 1
    item = out[0]
    assert item["task_id"] == "t1"
    assert item["lead_agent"] == "lead-agent"
    assert item["dispatch_mode"] == "async"
    assert item["runs"] == 2
    assert item["runs_done"] == 1


@pytest.mark.asyncio
async def test_list_tasks_filters_by_status(db_factory, tmp_path):
    _add_task(db_factory, tmp_path, task_id="t1", status="active")
    _add_task(db_factory, tmp_path, task_id="t2", status="completed")

    out = await queries.list_tasks("w1", status="completed")

    assert [t["task_id"] for t in out] == ["t2"]


@pytest.mark.asyncio
async def test_list_tasks_mine_only_scopes_to_originator(db_factory, tmp_path):
    _add_task(db_factory, tmp_path, task_id="t1", originator="me")
    _add_task(db_factory, tmp_path, task_id="t2", originator="someone-else")

    out = await queries.list_tasks("w1", mine_session_id="me")

    assert [t["task_id"] for t in out] == ["t1"]
    assert out[0]["originated_by_me"] is True


@pytest.mark.asyncio
async def test_list_tasks_honours_limit(db_factory, tmp_path):
    for i in range(5):
        _add_task(db_factory, tmp_path, task_id=f"t{i}")

    out = await queries.list_tasks("w1", limit=2)

    assert len(out) == 2


# ── get_task ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task_projects_detail_and_runs(db_factory, tmp_path):
    _add_task(db_factory, tmp_path, task_id="t1", title="Build it")
    _add_run(db_factory, tmp_path, task_id="t1", session_id="s-a")

    detail = await queries.get_task("t1", "w1")

    assert detail is not None
    assert detail["task_id"] == "t1"
    assert detail["title"] == "Build it"
    assert detail["status"] == "active"
    assert "plan" in detail and "ready" in detail
    assert len(detail["runs"]) == 1
    assert detail["runs"][0]["session_id"] == "s-a"


@pytest.mark.asyncio
async def test_get_task_returns_none_for_other_project(db_factory, tmp_path):
    _add_task(db_factory, tmp_path, task_id="t1", project_id="w1")

    assert await queries.get_task("t1", "w2") is None


@pytest.mark.asyncio
async def test_get_task_surfaces_latest_event_summary(db_factory, tmp_path):
    _add_task(db_factory, tmp_path, task_id="t1")
    db = db_factory()
    try:
        for seq, summary in enumerate(["first", "latest"]):
            db.add(
                TaskEventRow(
                    project_id="w1",
                    task_id="t1",
                    sequence=seq,
                    type="subtask_completed",
                    actor="member-a",
                    payload={"summary": summary},
                )
            )
        db.commit()
    finally:
        db.close()

    detail = await queries.get_task("t1", "w1")

    assert detail is not None
    assert detail["latest_summary"] == "latest"


# ── list_members ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_members_degrades_when_agent_not_loadable(db_factory, tmp_path, monkeypatch):
    db = db_factory()
    try:
        db.add(
            ProjectMemberRow(
                project_id="w1",
                agent_slug="researcher",
                kernel_agent_id="ka-1",
                source_agent_slug="researcher-template",
            )
        )
        db.commit()
    finally:
        db.close()

    async def _no_agent(_member, _ds):
        return None

    monkeypatch.setattr(queries, "_member_agent_config", _no_agent)

    out = await queries.list_members("w1")

    assert len(out) == 1
    member = out[0]
    assert member["slug"] == "researcher"
    assert member["name"] == "researcher"  # falls back to agent_slug
    assert member["runtime"] == "unknown"
    assert member["role_summary"] == ""
    assert member["source_agent_slug"] == "researcher-template"
