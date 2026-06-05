"""VALUZ-CHATPLAN S2 — draft / commit / abandon + plan-write CAS tests.

These tests exercise the orchestrator + datastore methods directly (no kernel
session bring-up — ``commit_task`` is end-to-end mocked at the kernel-session
boundary). Pattern mirrors ``test_plan_orchestrator.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """A tmp-SQLite async+sync sessionmaker pair (mirrors test_plan_orchestrator)."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "chatplan.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[TaskRow.__table__, TaskEventRow.__table__, TaskSessionRow.__table__],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _events(db_factory, workspace_id="w1", task_id="t1") -> list[str]:
    db = db_factory()
    try:
        return [
            e.type
            for e in db.execute(select(TaskEventRow).order_by(TaskEventRow.sequence))
            .scalars()
            .all()
        ]
    finally:
        db.close()


def _task_row(db_factory, task_id="t1") -> TaskRow:
    db = db_factory()
    try:
        return db.execute(select(TaskRow).filter_by(id=task_id)).scalars().one()
    finally:
        db.close()


def _make_draft(db_factory, tmp_path, *, task_id="t1", originator="chat-session-1") -> TaskRow:
    db = db_factory()
    try:
        row = TaskRow(
            id=task_id,
            workspace_id="w1",
            file_path=str(tmp_path / f"{task_id}.md"),
            title="T",
            goal="do it",
            status="draft",
            created_by="user",
            lead_agent_slug="lead-agent",
            current_holder="lead-agent",
            metadata_={"originating_session_id": originator},
            plan_version=0,
            committed_at=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    finally:
        db.close()
    return row


# ── plan_task on a draft bumps plan_version ─────────────────────────────


def test_plan_task_bumps_plan_version_from_zero_to_one(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    result = asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "Step A", "goal": "g"}],
        )
    )
    assert "error" not in result
    assert result["current_version"] == 1
    assert _task_row(db_factory).plan_version == 1


def test_plan_task_response_includes_current_version(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    result = asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    assert result["current_version"] == 1


# ── get_plan returns current_version ────────────────────────────────────


def test_get_plan_returns_current_version_zero_for_unwritten_plan(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    snap = asyncio.run(planning.get_plan(task_id="t1", workspace_id="w1"))
    assert snap["current_version"] == 0
    assert snap["subtasks"] == []


def test_get_plan_returns_current_version_after_plan_task(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    snap = asyncio.run(planning.get_plan(task_id="t1", workspace_id="w1"))
    assert snap["current_version"] == 1


# ── modify_plan CAS ──────────────────────────────────────────────────────


def test_modify_plan_with_matching_expected_version_succeeds(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    # Pass the version we just got back (1).
    result = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            add=[{"key": "b", "title": "B", "goal": "g"}],
            expected_version=1,
        )
    )
    assert "error" not in result
    assert result["current_version"] == 2


def test_modify_plan_with_stale_expected_version_returns_cas_conflict(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    # Pretend an out-of-band edit bumped to v2 then v3.
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            add=[{"key": "b", "title": "B", "goal": "g"}],
        )
    )
    result = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            update=[{"key": "a", "title": "A new"}],
            expected_version=1,  # stale — actual is 2
        )
    )
    assert result["error"] == "PLAN_VERSION_CONFLICT"
    assert result["current_version"] == 2
    assert result["you_passed"] == 1
    assert "hint" in result


def test_modify_plan_without_expected_version_skips_cas_check(db_factory, tmp_path):
    """Lead callers (single-actor) omit expected_version — read-modify-write."""
    _make_draft(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            add=[{"key": "b", "title": "B", "goal": "g"}],
        )
    )  # bumps to v2 silently
    result = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            update=[{"key": "a", "title": "A2"}],
            # No expected_version — should succeed regardless.
        )
    )
    assert "error" not in result
    assert result["current_version"] == 3


def test_modify_plan_bumps_plan_version_on_success(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            add=[{"key": "b", "title": "B", "goal": "g"}],
            expected_version=1,
        )
    )
    assert _task_row(db_factory).plan_version == 2


# ── abandon_task ─────────────────────────────────────────────────────────


def test_abandon_task_flips_draft_to_abandoned(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.abandon_task(
            task_id="t1",
            workspace_id="w1",
            caller_session_id="chat-session-1",
            reason="user changed their mind",
        )
    )
    assert result == {"task_id": "t1", "status": "abandoned"}
    assert _task_row(db_factory).status == "abandoned"


def test_abandon_task_appends_abandoned_event(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        orch.abandon_task(
            task_id="t1",
            workspace_id="w1",
            caller_session_id="chat-session-1",
        )
    )
    assert "abandoned" in _events(db_factory)


def test_abandon_task_rejects_non_draft(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    # Manually flip to active to simulate post-commit state.
    db = db_factory()
    row = db.execute(select(TaskRow).filter_by(id="t1")).scalars().one()
    row.status = "active"
    db.commit()
    db.close()

    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.abandon_task(
            task_id="t1",
            workspace_id="w1",
            caller_session_id="chat-session-1",
        )
    )
    assert "error" in result
    assert "draft" in result["error"]
    assert _task_row(db_factory).status == "active"  # unchanged


def test_abandon_task_returns_error_for_missing_task(db_factory, tmp_path):
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.abandon_task(
            task_id="nope",
            workspace_id="w1",
            caller_session_id="chat-session-1",
        )
    )
    assert "error" in result
    assert "not found" in result["error"]


# ── commit_task validation (full path needs kernel mocks; skip here) ─────


def test_commit_task_rejects_empty_plan(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)  # no plan written
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.commit_task(
            task_id="t1",
            workspace_id="w1",
            caller_session_id="chat-session-1",
        )
    )
    assert "error" in result
    assert "plan is empty" in result["error"]


def test_commit_task_rejects_non_draft(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    db = db_factory()
    row = db.execute(select(TaskRow).filter_by(id="t1")).scalars().one()
    row.status = "active"  # already committed
    db.commit()
    db.close()
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.commit_task(
            task_id="t1",
            workspace_id="w1",
            caller_session_id="chat-session-1",
        )
    )
    assert "error" in result
    assert "active" in result["error"]


def test_commit_task_returns_error_for_missing_task(db_factory, tmp_path):
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.commit_task(
            task_id="nope",
            workspace_id="w1",
            caller_session_id="chat-session-1",
        )
    )
    assert "error" in result
    assert "not found" in result["error"]


# ── plan_version invariants across mixed operations ─────────────────────


def test_plan_version_monotonic_across_plan_task_then_modify(db_factory, tmp_path):
    """plan_version always increases monotonically across plan_task and modify_plan."""
    _make_draft(db_factory, tmp_path)
    versions = []
    versions.append(_task_row(db_factory).plan_version)  # 0
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    versions.append(_task_row(db_factory).plan_version)  # 1
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            add=[{"key": "b", "title": "B", "goal": "g"}],
        )
    )
    versions.append(_task_row(db_factory).plan_version)  # 2
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            update=[{"key": "a", "title": "A2"}],
        )
    )
    versions.append(_task_row(db_factory).plan_version)  # 3
    assert versions == [0, 1, 2, 3]


def test_failed_cas_does_not_bump_plan_version(db_factory, tmp_path):
    _make_draft(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            subtasks=[{"key": "a", "title": "A", "goal": "g"}],
        )
    )
    # current=1; pass stale expected_version=99 → CAS fail, no version bump
    result = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            workspace_id="w1",
            lead_session_id="chat-session-1",
            add=[{"key": "b", "title": "B", "goal": "g"}],
            expected_version=99,
        )
    )
    assert result["error"] == "PLAN_VERSION_CONFLICT"
    assert _task_row(db_factory).plan_version == 1  # unchanged
