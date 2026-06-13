"""Orchestrator plan/review methods against a tmp DB (VALUZ-TASK S2/S4/S5).

These methods are DB-only (no kernel / member execution), so we bind a throwaway
SQLite engine and exercise plan_task / get_plan / modify_plan / review_subtask /
finish_task + the dispatch plan-first gate directly.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.tasks import orchestrator as orch_mod
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator


def _as_async(fn):
    """Wrap a sync callable as a coroutine fn for monkeypatching the async
    ``kernel_client`` facade (its methods are awaited by the code under test)."""

    async def _f(*args, **kwargs):
        return fn(*args, **kwargs)

    return _f


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """A tmp-SQLite async sessionmaker bound into ``infra.db.AsyncSessionLocal``.

    The host is now fully async (``async_unit_of_work`` / aiosqlite); we patch
    ``infra.db.AsyncSessionLocal`` so the orchestrator's units of work bind to
    this tmp engine. A parallel SYNC sessionmaker is returned for the test
    helpers to seed/read rows synchronously (simpler than awaiting in helpers).
    """
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "plan.db"
    # Sync engine for the test helpers (seed/read).
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine, tables=[TaskRow.__table__, TaskEventRow.__table__, TaskSessionRow.__table__]
    )
    # Async engine for the code-under-test.
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _make_task(db_factory, tmp_path, *, project_id="w1", task_id="t1") -> str:
    db = db_factory()
    try:
        db.add(
            TaskRow(
                user_id="local-test-owner",
                id=task_id,
                project_id=project_id,
                file_path=str(tmp_path / f"{task_id}.md"),
                title="T",
                goal="do it",
                status="active",
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        db.commit()
    finally:
        db.close()
    return task_id


def _events(db_factory, project_id="w1", task_id="t1") -> list[str]:
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


def _runs(db_factory, task_id="t1") -> dict[str, str]:
    """Sync read of {session_id: status} for a task's runs (datastores are async)."""
    db = db_factory()
    try:
        rows = db.execute(select(TaskSessionRow).filter_by(task_id=task_id)).scalars().all()
        return {r.session_id: r.status for r in rows}
    finally:
        db.close()


def _task_row(db_factory, task_id="t1") -> TaskRow:
    db = db_factory()
    try:
        return db.execute(select(TaskRow).filter_by(id=task_id)).scalars().one()
    finally:
        db.close()


def test_plan_task_persists_plan_and_emits_events(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    res = asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-sess",
            subtasks=[
                {"key": "a", "title": "A", "agent": "researcher"},
                {"key": "b", "title": "B", "agent": "writer", "depends_on": ["a"]},
            ],
        )
    )
    assert "error" not in res
    assert res["ready"] == ["a"]  # b is blocked on a
    types = _events(db_factory)
    assert "task_planned" in types and "task_plan_update" in types


def test_plan_task_rejects_when_progress_exists(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x", "status": "in_progress"}],
        )
    )
    # Second plan_task must refuse (there is progress).
    res = asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "z", "title": "Z"}],
        )
    )
    assert "error" in res and "modify_plan" in res["error"]


def test_get_plan_returns_ready_and_counts(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    res = asyncio.run(planning.get_plan(task_id="t1", project_id="w1"))
    assert res["ready"] == ["a"]
    assert res["counts"] == {"planned": 1}
    assert res["all_done"] is False


def test_plan_review_criteria_round_trips_and_surfaces_in_get_plan(db_factory, tmp_path) -> None:
    """The lead's per-subtask review_criteria persists and is shown in get_plan."""
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[
                {
                    "key": "a",
                    "title": "A",
                    "agent": "x",
                    "review_criteria": "covers price + %chg + 1-line takeaway",
                }
            ],
        )
    )
    res = asyncio.run(planning.get_plan(task_id="t1", project_id="w1"))
    node = next(n for n in res["subtasks"] if n["key"] == "a")
    assert node["review_criteria"] == "covers price + %chg + 1-line takeaway"


def test_modify_plan_adds_and_revalidates(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    res = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            add=[{"key": "b", "title": "B", "agent": "y", "depends_on": ["a"]}],
        )
    )
    assert "error" not in res
    assert {n["key"] for n in res["subtasks"]} == {"a", "b"}
    assert "plan_revised" in _events(db_factory)


def test_modify_plan_rejects_cycle(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A"}, {"key": "b", "title": "B", "depends_on": ["a"]}],
        )
    )
    res = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            update=[{"key": "a", "depends_on": ["b"]}],
        )
    )
    assert "error" in res and "cycle" in res["error"]


def test_dispatch_rejects_unknown_subtask_key(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    res = asyncio.run(
        orch.dispatch(task_id="t1", project_id="w1", lead_session_id="lead", subtask_key="ghost")
    )
    assert res["status"] == "failed" and "plan_task first" in res["error"]


def test_dispatch_rejects_blocked_subtask(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x"},
                {"key": "b", "title": "B", "agent": "y", "depends_on": ["a"]},
            ],
        )
    )
    res = asyncio.run(
        orch.dispatch(task_id="t1", project_id="w1", lead_session_id="lead", subtask_key="b")
    )
    assert res["status"] == "failed" and "blocked" in res["error"]


def test_rework_redispatch_folds_feedback_into_brief(db_factory, tmp_path) -> None:
    """Sync rework: re-dispatching a reworked node carries the lead's feedback
    into the member brief so it knows why it was sent back (VALUZ-TASK)."""
    from valuz_agent.modules.tasks.models import TaskRow
    from valuz_agent.modules.tasks.plan import TaskPlan

    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x", "goal": "build X"}],
        )
    )
    # node in_review → reject (no live member) → rework
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            update=[{"key": "a", "status": "in_review"}],
        )
    )
    asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            decision="rework",
            subtask_key="a",
            feedback="handle empty input",
        )
    )
    db = db_factory()
    try:
        plan = TaskPlan.from_dict(db.get(TaskRow, "t1").plan)
    finally:
        db.close()
    resolved = planning.resolve_dispatch_node(plan, "a", None, None)
    assert not isinstance(resolved, str)
    _agent, goal = resolved
    assert "handle empty input" in goal and "Rework feedback" in goal


def test_review_approve_marks_done_and_unlocks(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x"},
                {"key": "b", "title": "B", "agent": "y", "depends_on": ["a"]},
            ],
        )
    )
    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            decision="approve",
            subtask_key="a",
        )
    )
    assert res["decision"] == "approve"
    assert res["ready"] == ["b"]  # b unlocked now that a is done
    types = _events(db_factory)
    assert "subtask_reviewed" in types and "subtask_completed" in types


def test_review_rework_no_live_member_sets_rework(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x", "status": "in_review"}],
        )
    )
    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            decision="rework",
            subtask_key="a",
            feedback="redo it",
        )
    )
    assert res["decision"] == "rework"
    assert res["delivered_to_live_member"] is False
    plan = asyncio.run(planning.get_plan(task_id="t1", project_id="w1"))
    node = next(n for n in plan["subtasks"] if n["key"] == "a")
    assert node["status"] == "active"  # rework maps to panel 'active'


def test_finish_task_stopped_emits_task_stopped(db_factory, tmp_path) -> None:
    """status='stopped' is the user-requested-terminate / unreachable path
    after the task_state.py rework (task-level 'failed' was removed)."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        orch.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            summary="user asked to stop",
            status="stopped",
        )
    )
    assert "task_stopped" in _events(db_factory)
    db = db_factory()
    try:
        assert db.get(TaskRow, "t1").status == "stopped"
    finally:
        db.close()


def test_finish_task_rejects_legacy_failed_status(db_factory, tmp_path) -> None:
    """Stale prompts that still pass status='failed' must be rejected loudly,
    not silently aliased. See task_state.py — task-level 'failed' isn't in
    the enum anymore (use 'stopped')."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            summary="legacy call",
            status="failed",
        )
    )
    assert result.get("status") == "rejected"
    assert "failed" in result.get("error", "").lower()
    # Task row stays in its prior state — no illegal value written.
    db = db_factory()
    try:
        assert db.get(TaskRow, "t1").status != "failed"
    finally:
        db.close()


def test_finish_task_rejected_when_plan_has_unresolved_nodes(db_factory, tmp_path) -> None:
    """v0.14 guard: a 'completed' finish is rejected while planned nodes remain."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x"},
                {"key": "sum", "title": "Summary", "depends_on": ["a"]},
            ],
        )
    )
    res = asyncio.run(
        orch.finish_task(task_id="t1", project_id="w1", lead_session_id="lead", summary="done")
    )
    assert res["status"] == "rejected"
    assert set(res["pending_subtasks"]) == {"a", "sum"}
    assert "task_completed" not in _events(db_factory)
    db = db_factory()
    try:
        assert db.get(TaskRow, "t1").status == "active"  # NOT completed
    finally:
        db.close()


def test_finish_task_allows_completion_when_all_done(db_factory, tmp_path) -> None:
    """Once every node is done, a 'completed' finish goes through."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    # Mark the only node done directly via the plan (sync seed — datastores are async).
    from valuz_agent.modules.tasks.plan import TaskPlan

    db = db_factory()
    try:
        row = db.execute(select(TaskRow).filter_by(id="t1")).scalars().one()
        plan = TaskPlan.from_dict(row.plan)
        plan.update_node("a", status="done")
        row.plan = plan.to_dict()
        db.commit()
    finally:
        db.close()
    res = asyncio.run(
        orch.finish_task(task_id="t1", project_id="w1", lead_session_id="lead", summary="done")
    )
    assert res["ok"] is True
    assert "task_completed" in _events(db_factory)


def test_render_plan_md_writes_file(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    md = Path(tmp_path / "t1.md")
    assert md.exists() and "## Plan" in md.read_text() and "**a**" in md.read_text()


# ---------------------------------------------------------------------------
# _auto_finalize_lead_task — host-side terminal fallback (lead ends w/o finish_task)
# ---------------------------------------------------------------------------


def _make_lead_run(db_factory, *, task_id="t1", session_id="lead-sess") -> None:
    db = db_factory()
    try:
        db.add(
            TaskSessionRow(
                user_id="local-test-owner",
                id="run-lead",
                project_id="w1",
                task_id=task_id,
                session_id=session_id,
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()


def _task_status(db_factory, task_id="t1") -> str:
    db = db_factory()
    try:
        return db.query(TaskRow).filter_by(id=task_id).one().status
    finally:
        db.close()


def test_auto_finalize_completes_when_no_pending_subtasks(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    _make_lead_run(db_factory)
    orch = TaskOrchestrator()
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess", task_id="t1", project_id="w1", final_status="idle"
        )
    )
    assert _task_status(db_factory) == "completed"
    assert "task_completed" in _events(db_factory)


def test_auto_finalize_blocks_when_plan_has_unresolved_nodes(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],  # status defaults to planned
        )
    )
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess", task_id="t1", project_id="w1", final_status="idle"
        )
    )
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _events(db_factory)


def test_auto_finalize_stays_active_on_error_with_empty_plan(db_factory, tmp_path) -> None:
    """Lead turn errored BEFORE producing any plan nodes — task should stay
    ``active`` instead of locking into ``blocked``. Bug from 2026-05-29:
    an automation-fired lead session used Claude Agent SDK's EnterPlanMode +
    nested Agent that hung; SDK cancelled the turn after ~3.5min; plan was
    still empty. Old behaviour locked the task immediately, breaking the
    user's next plan_task call with "task is blocked; plan is read-only".
    New behaviour: leave task active, let the next driver (user message
    or next fire) retry from a fresh turn."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="terminated",
        )
    )
    assert _task_status(db_factory) == "active"
    assert "task_blocked" not in _events(db_factory)


def test_auto_finalize_stays_active_on_stop_reason_error_with_empty_plan(
    db_factory, tmp_path, monkeypatch
) -> None:
    """Same "empty plan + turn error" contract when the failure surfaces via
    ``stop_reason`` instead of ``final_status``. With no in-flight work to
    protect, the task stays active so the next driver can retry — only the
    log warning records the turn-level error for auditing."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import orchestrator as orch_mod

    _make_task(db_factory, tmp_path)
    fake_sess = SimpleNamespace(
        stop_reason={"type": "error", "category": "execution_error", "message": "boom: skill x"}
    )
    monkeypatch.setattr(orch_mod.kernel_client, "get_session", _as_async(lambda _sid: fake_sess))
    orch = TaskOrchestrator()
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
        )
    )
    assert _task_status(db_factory) == "active"
    assert "task_blocked" not in _events(db_factory)


def test_auto_finalize_blocks_on_error_when_plan_has_unresolved_nodes(db_factory, tmp_path) -> None:
    """Counterpart to the "empty plan stays active" tests: when the lead
    errors AFTER dispatching work that's still in flight (subtasks with
    status planned / in_progress / in_review / rework), ``blocked`` is
    still the right disposition — there's half-done orchestration that
    needs manual ``resume_task`` to recover."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="terminated",
        )
    )
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _events(db_factory)


def test_auto_finalize_noop_when_already_finalized(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    db = db_factory()
    try:
        row = db.query(TaskRow).filter_by(id="t1").one()
        row.status = "completed"  # simulate finish_task already won
        db.commit()
    finally:
        db.close()
    orch = TaskOrchestrator()
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess", task_id="t1", project_id="w1", final_status="idle"
        )
    )
    assert _events(db_factory) == []  # no duplicate terminal event appended


def test_auto_finalize_noop_when_members_in_flight(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"m1"})  # a member is still running
    asyncio.run(
        orch._auto_finalize_lead_task(
            lead_session_id="lead-sess", task_id="t1", project_id="w1", final_status="idle"
        )
    )
    assert _task_status(db_factory) == "active"  # left open for the member to finish


def test_lead_idle_with_no_pending_true_when_clean(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    # No members, no plan → nothing to wait for → break the loop immediately.
    assert asyncio.run(orch._lead_idle_with_no_pending("t1", "w1")) is True


def test_lead_idle_with_no_pending_false_when_member_in_flight(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"m1"})
    assert asyncio.run(orch._lead_idle_with_no_pending("t1", "w1")) is False


def test_lead_idle_with_no_pending_false_when_plan_unresolved(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    assert asyncio.run(orch._lead_idle_with_no_pending("t1", "w1")) is False


# ---------------------------------------------------------------------------
# VALUZ-RESUME S2 — _recover_one_task: reconcile members + re-drive lead
# ---------------------------------------------------------------------------


def test_recover_one_task_reconciles_members_and_redrives_lead(
    db_factory, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from valuz_agent.modules.tasks.mailbox import mailbox_registry
    from valuz_agent.modules.tasks.models import TaskSessionRow
    from valuz_agent.modules.tasks.plan import TaskPlan

    # Seed an active task: lead run + 3 active subtask runs (A done / B host_restart / C error).
    db = db_factory()
    try:
        plan = {
            "subtasks": [
                {
                    "key": "A",
                    "label": "A",
                    "agent": "backend",
                    "status": "in_progress",
                    "attempts": 0,
                },
                {
                    "key": "B",
                    "label": "B",
                    "agent": "frontend",
                    "status": "in_progress",
                    "attempts": 0,
                },
                {"key": "C", "label": "C", "agent": "qa", "status": "in_progress", "attempts": 0},
            ]
        }
        db.add(
            TaskRow(
                user_id="local-test-owner",
                id="t1",
                project_id="w1",
                file_path=str(tmp_path / "t1.md"),
                title="T",
                goal="g",
                status="active",
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan,
            )
        )
        db.add(
            TaskSessionRow(
                user_id="local-test-owner",
                project_id="w1",
                task_id="t1",
                session_id="lead-s",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        for i, (key, agent, sid) in enumerate(
            [("A", "backend", "sA"), ("B", "frontend", "sB"), ("C", "qa", "sC")], start=1
        ):
            db.add(
                TaskSessionRow(
                    user_id="local-test-owner",
                    project_id="w1",
                    task_id="t1",
                    session_id=sid,
                    agent_slug=agent,
                    sequence=i,
                    kind="subtask",
                    status="active",
                    subtask_key=key,
                    run_dir=str(tmp_path),
                )
            )
        db.commit()
    finally:
        db.close()

    sessions = {
        "lead-s": SimpleNamespace(status="idle", stop_reason=None),
        "sA": SimpleNamespace(status="idle", stop_reason={"type": "end_turn"}),
        "sB": SimpleNamespace(
            status="idle", stop_reason={"type": "error", "category": "host_restart"}
        ),
        "sC": SimpleNamespace(
            status="idle", stop_reason={"type": "error", "category": "exec", "message": "boom"}
        ),
    }
    monkeypatch.setattr(
        orch_mod.kernel_client, "get_session", _as_async(lambda sid: sessions.get(sid))
    )

    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> None:
        await orch._recover_one_task("t1", "w1")
        await asyncio.sleep(0.05)  # let create_task'd loops run

    try:
        asyncio.run(_run())

        runs = _runs(db_factory)
        assert runs["sA"] == "completed"  # end_turn → completed
        assert runs["sB"] == "active"  # host_restart → resumed
        assert runs["sC"] == "archived"  # real error → failed

        row = db_factory().query(TaskRow).filter_by(id="t1").one()
        plan2 = TaskPlan.from_dict(row.plan)
        assert plan2.get("A").status == "in_review"
        assert plan2.get("B").status == "in_progress" and plan2.get("B").attempts == 1
        assert plan2.get("C").status == "rework"

        assert ("sB", "subtask") in spawned  # resumable member respawned
        assert ("lead-s", "lead") in spawned  # lead re-driven
        assert ("sA", "subtask") not in spawned and ("sC", "subtask") not in spawned
        assert mailbox_registry.has_pending("lead-s")  # completed A's member_done queued
    finally:
        mailbox_registry.unregister("lead-s")


# ---------------------------------------------------------------------------
# S4 — Layer 2: user stop / resume (stop_task / resume_task / stop_member)
# S3 — online heartbeat (_heartbeat_pending)
# ---------------------------------------------------------------------------


def _seed_lead_and_members(
    db_factory,
    tmp_path,
    *,
    members: list[tuple[str, str, str, str]],  # (key, agent, session_id, node_status)
    task_status: str = "active",
    run_status: str = "active",
) -> None:
    """Seed a task with a lead run + member runs/plan nodes."""
    from valuz_agent.modules.tasks.models import TaskSessionRow

    db = db_factory()
    try:
        plan = {
            "subtasks": [
                {"key": k, "label": k, "agent": a, "status": ns, "attempts": 0}
                for (k, a, _sid, ns) in members
            ]
        }
        db.add(
            TaskRow(
                user_id="local-test-owner",
                id="t1",
                project_id="w1",
                file_path=str(tmp_path / "t1.md"),
                title="T",
                goal="g",
                status=task_status,
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan,
            )
        )
        db.add(
            TaskSessionRow(
                user_id="local-test-owner",
                project_id="w1",
                task_id="t1",
                session_id="lead-s",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        for i, (key, agent, sid, _ns) in enumerate(members, start=1):
            db.add(
                TaskSessionRow(
                    user_id="local-test-owner",
                    project_id="w1",
                    task_id="t1",
                    session_id=sid,
                    agent_slug=agent,
                    sequence=i,
                    kind="subtask",
                    status=run_status,
                    subtask_key=key,
                    run_dir=str(tmp_path),
                    dispatched_by="lead-s",
                )
            )
        db.commit()
    finally:
        db.close()


def test_stop_task_pauses_members_and_cascade_interrupts(db_factory, tmp_path, monkeypatch) -> None:
    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress"), ("B", "frontend", "sB", "in_progress")],
    )
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"sA", "sB"})
    interrupted: list[str] = []

    async def _fake_interrupt(sid: str) -> None:
        interrupted.append(sid)

    orch._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]

    assert asyncio.run(orch.stop_task("t1", "w1")) is True

    task = _task_row(db_factory)
    assert task.status == "paused"
    runs = _runs(db_factory)
    assert runs["sA"] == "paused" and runs["sB"] == "paused"
    assert set(interrupted) == {"sA", "sB", "lead-s"}  # members + lead all interrupted


def test_stop_task_noop_when_not_active(db_factory, tmp_path, monkeypatch) -> None:
    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress")],
        task_status="paused",
    )
    orch = TaskOrchestrator()

    async def _fake_interrupt(sid: str) -> None:
        raise AssertionError("should not interrupt a non-active task")

    orch._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]
    assert asyncio.run(orch.stop_task("t1", "w1")) is False


def test_resume_task_only_paused_flips_active_and_redrives(
    db_factory, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from valuz_agent.modules.tasks.mailbox import mailbox_registry

    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress")],
        task_status="paused",
        run_status="paused",
    )
    # paused member kernel session was interrupted (idle + UserInterrupt-ish) → resume.
    monkeypatch.setattr(
        orch_mod.kernel_client,
        "get_session",
        _as_async(
            lambda sid: SimpleNamespace(status="idle", stop_reason={"type": "user_interrupt"})
        ),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> dict:
        result = await orch.resume_task("t1", "w1")
        await asyncio.sleep(0.05)
        return result

    try:
        result = asyncio.run(_run())
        assert result["ok"] is True
        assert result["resumed"] is True
        assert result["prior_status"] == "paused"
        assert _task_row(db_factory).status == "active"
        assert ("lead-s", "lead") in spawned
        assert ("sA", "subtask") in spawned  # paused member resumed
    finally:
        mailbox_registry.unregister("lead-s")


def test_resume_task_noop_when_active(db_factory, tmp_path) -> None:
    """An active task cannot be 'resumed' — it never paused. Caller gets a
    clear error string back so the LLM can surface it to the user."""
    _seed_lead_and_members(
        db_factory, tmp_path, members=[("A", "backend", "sA", "in_progress")], task_status="active"
    )
    orch = TaskOrchestrator()
    result = asyncio.run(orch.resume_task("t1", "w1"))
    assert result["ok"] is False
    assert result["prior_status"] == "active"
    assert "paused" in result["error"] or "blocked" in result["error"]


def test_resume_task_accepts_blocked(db_factory, tmp_path, monkeypatch) -> None:
    """blocked → active is a legal transition per task_state.ALLOWED_TRANSITIONS.
    The lead-turn-error auto-finalize path leaves tasks blocked; users should
    be able to revive them by calling resume_task."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import orchestrator as orch_mod

    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="blocked", run_status="rejected"
    )
    monkeypatch.setattr(
        orch_mod.kernel_client,
        "get_session",
        _as_async(lambda sid: SimpleNamespace(status="idle", stop_reason={"type": "error"})),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    result = asyncio.run(orch.resume_task("t1", "w1"))
    assert result["ok"] is True
    assert result["prior_status"] == "blocked"
    assert _task_row(db_factory).status == "active"
    assert ("lead-s", "lead") in spawned


def test_resume_task_accepts_stopped(db_factory, tmp_path, monkeypatch) -> None:
    """stopped → active is allowed (soft terminal). user-driven stop is
    reversible — finish_task previously marked the lead run 'completed';
    resume_task flips it back to 'active' so the recovery view stays
    consistent, then _recover_one_task respawns a fresh lead."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import orchestrator as orch_mod

    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="stopped", run_status="completed"
    )
    monkeypatch.setattr(
        orch_mod.kernel_client,
        "get_session",
        _as_async(lambda sid: SimpleNamespace(status="idle", stop_reason=None)),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    result = asyncio.run(orch.resume_task("t1", "w1"))
    assert result["ok"] is True
    assert result["prior_status"] == "stopped"
    assert _task_row(db_factory).status == "active"
    assert ("lead-s", "lead") in spawned


def test_resume_task_accepts_completed(db_factory, tmp_path, monkeypatch) -> None:
    """completed → active is now allowed (soft terminal): a finished task can
    be REOPENED to supplement/adjust subtasks from a second chat-plan
    (区分场景). finish_task marked the lead run 'completed'; resume flips it
    back to 'active' and _recover_one_task respawns a fresh lead."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import orchestrator as orch_mod

    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="completed", run_status="completed"
    )
    monkeypatch.setattr(
        orch_mod.kernel_client,
        "get_session",
        _as_async(lambda sid: SimpleNamespace(status="idle", stop_reason=None)),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    result = asyncio.run(orch.resume_task("t1", "w1"))
    assert result["ok"] is True
    assert result["prior_status"] == "completed"
    assert _task_row(db_factory).status == "active"
    assert ("lead-s", "lead") in spawned


def test_resume_task_rejects_abandoned(db_factory, tmp_path) -> None:
    """abandoned stays hard-terminal — a discarded draft has no plan to
    revive; the user must draft afresh."""
    _seed_lead_and_members(db_factory, tmp_path, members=[], task_status="abandoned")
    orch = TaskOrchestrator()
    result = asyncio.run(orch.resume_task("t1", "w1"))
    assert result["ok"] is False
    assert result["prior_status"] == "abandoned"
    assert _task_row(db_factory).status == "abandoned"


def test_stop_member_rejects_run_reworks_node_and_notifies_lead(
    db_factory, tmp_path, monkeypatch
) -> None:
    from valuz_agent.modules.tasks.mailbox import mailbox_registry
    from valuz_agent.modules.tasks.plan import TaskPlan

    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")])
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"sB"})

    async def _fake_interrupt(sid: str) -> None:
        pass

    orch._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]
    mailbox_registry.register("lead-s")
    try:
        assert asyncio.run(orch.stop_member("sB")) is True
        runs = _runs(db_factory)
        assert runs["sB"] == "rejected"
        plan = TaskPlan.from_dict(db_factory().query(TaskRow).filter_by(id="t1").one().plan)
        assert plan.get("B").status == "rework"
        assert "sB" not in orch._members.live_members("t1")
        assert mailbox_registry.has_pending("lead-s")
        msg = mailbox_registry._boxes["lead-s"].get_nowait()
        assert msg.kind == "member_done" and msg.payload["status"] == "cancelled"
    finally:
        mailbox_registry.unregister("lead-s")


def test_heartbeat_pending_synthesizes_terminal_completed(
    db_factory, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from valuz_agent.modules.tasks.plan import TaskPlan

    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("B", "frontend", "sB", "in_progress"), ("C", "qa", "sC", "in_progress")],
    )
    sessions = {
        "sB": SimpleNamespace(status="idle", stop_reason={"type": "end_turn"}),  # done
        "sC": SimpleNamespace(status="running", stop_reason=None),  # still in flight
    }
    monkeypatch.setattr(
        orch_mod.kernel_client, "get_session", _as_async(lambda sid: sessions.get(sid))
    )
    # ``_heartbeat_pending`` lives in tasks/coordination.py (ADR-023 Step 3b);
    # the orchestrator delegates to it, so stub the coordination module's
    # ``collect_manifest`` binding.
    from valuz_agent.modules.tasks import coordination as coord_mod

    monkeypatch.setattr(
        coord_mod, "collect_manifest", lambda *a, **k: {"status": "completed", "summary": "ok"}
    )
    orch = TaskOrchestrator()

    out = asyncio.run(
        orch._heartbeat_pending(task_id="t1", project_id="w1", pending_keys={"B", "C"})
    )

    assert set(out.keys()) == {"B"}  # only the terminal member synthesized
    assert out["B"]["status"] == "completed"
    runs = _runs(db_factory)
    assert runs["sB"] == "completed"
    plan = TaskPlan.from_dict(db_factory().query(TaskRow).filter_by(id="t1").one().plan)
    assert plan.get("B").status == "in_review"
    assert plan.get("C").status == "in_progress"  # in-flight untouched


def test_e2e_stop_resume_closed_loop_through_routes(db_factory, tmp_path, monkeypatch) -> None:
    """Closed-loop E2E: HTTP intervene stop → stopped (cascade), then resume →
    active (reconcile + respawn members + re-drive lead). Drives the real route
    handlers + orchestrator + datastores against a tmp DB; only the kernel
    runtime (interrupt / session-load / actor-loop spawn) is stubbed."""
    from types import SimpleNamespace

    from valuz_agent.api.routes import tasks as tasks_route
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.mailbox import mailbox_registry
    from valuz_agent.modules.tasks.plan import TaskPlan

    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress"), ("B", "frontend", "sB", "in_progress")],
    )
    orch = tasks_route.task_orchestrator
    orch._members.set_members("t1", {"sA", "sB"})

    interrupted: list[str] = []
    spawned: list[tuple[str, str]] = []

    async def _fake_interrupt(sid: str) -> None:
        interrupted.append(sid)

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    monkeypatch.setattr(orch, "_interrupt_kernel_session", _fake_interrupt)
    monkeypatch.setattr(orch, "run_actor_loop", _fake_loop)
    # On resume, paused members read as interrupted-idle → resumable.
    monkeypatch.setattr(
        orch_mod.kernel_client,
        "get_session",
        _as_async(
            lambda sid: SimpleNamespace(status="idle", stop_reason={"type": "user_interrupt"})
        ),
    )

    async def _run() -> None:
        # 1) Stop → stopped (cascade interrupt lead + members). ``stopped`` is
        # UI-terminal but still revivable (resume below proves the closed loop).
        async with async_unit_of_work() as db:
            resp = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="stop"), db
            )
        assert resp.status == "stopped"
        assert set(interrupted) == {"sA", "sB", "lead-s"}
        runs = _runs(db_factory)
        assert runs["sA"] == "paused" and runs["sB"] == "paused"
        # In-flight plan nodes are parked → ``paused`` (panel stops spinning).
        parked = TaskPlan.from_dict(_task_row(db_factory).plan)
        assert {n.status for n in parked.nodes} == {"paused"}

        # 2) Resume → active (reconcile + respawn + re-drive lead).
        async with async_unit_of_work() as db:
            resp2 = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="resume"), db
            )
        assert resp2.status == "active"
        await asyncio.sleep(0.05)  # let create_task'd loops run
        assert ("lead-s", "lead") in spawned
        assert ("sA", "subtask") in spawned and ("sB", "subtask") in spawned

    try:
        asyncio.run(_run())
        assert _task_row(db_factory).status == "active"
    finally:
        mailbox_registry.unregister("lead-s")
        orch._members.set_members("t1", set())


def test_pause_distinct_from_stop_and_parks_nodes(db_factory, tmp_path, monkeypatch) -> None:
    """``pause`` → ``paused`` (resumable); ``stop`` on the now-paused task →
    ``stopped`` (the screenshot bug: stop on a paused task used to no-op because
    ``stop_task`` only accepted ``active``). Both park the in-flight plan node
    (``in_progress`` → ``paused``) so the right-rail panel stops spinning."""
    from valuz_agent.api.routes import tasks as tasks_route
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.mailbox import mailbox_registry
    from valuz_agent.modules.tasks.plan import TaskPlan

    _seed_lead_and_members(db_factory, tmp_path, members=[("A", "backend", "sA", "in_progress")])
    orch = tasks_route.task_orchestrator
    orch._members.set_members("t1", {"sA"})

    async def _noop_interrupt(_sid: str) -> None: ...

    monkeypatch.setattr(orch, "_interrupt_kernel_session", _noop_interrupt)

    async def _run() -> None:
        # pause → paused; node parked; member run paused.
        async with async_unit_of_work() as db:
            r1 = await tasks_route.intervene("t1", tasks_route.InterveneRequest(action="pause"), db)
        assert r1.status == "paused"
        assert TaskPlan.from_dict(_task_row(db_factory).plan).get("A").status == "paused"
        assert _runs(db_factory)["sA"] == "paused"

        # stop on the already-paused task → stopped (no longer a no-op).
        async with async_unit_of_work() as db:
            r2 = await tasks_route.intervene("t1", tasks_route.InterveneRequest(action="stop"), db)
        assert r2.status == "stopped"

    try:
        asyncio.run(_run())
    finally:
        mailbox_registry.unregister("lead-s")
        orch._members.set_members("t1", set())


def test_resume_evicts_kernel_runtime_before_respawn(db_factory, tmp_path, monkeypatch) -> None:
    """Resume must evict the kernel runtime of the lead + every resumed member
    BEFORE respawning their actor loops. Load-bearing for pause→resume: the
    pause ``interrupt`` leaves the runtime's SDK client cancelled but cached in
    ``_runtimes``; reusing it makes the first resume turn cancel (null output) →
    the lead ends with an errored stop_reason → ``_auto_finalize`` blocks the
    task. Eviction-before-respawn (not in the old loop's async finalize) is
    race-free and forces a fresh runtime."""
    from types import SimpleNamespace

    import app.dependencies as appdeps

    from valuz_agent.modules.tasks.mailbox import mailbox_registry

    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress")],
        task_status="paused",
        run_status="paused",
    )
    monkeypatch.setattr(
        orch_mod.kernel_client,
        "get_session",
        _as_async(
            lambda sid: SimpleNamespace(status="idle", stop_reason={"type": "user_interrupt"})
        ),
    )

    evicted: list[str] = []

    class _FakeOrch:
        async def cleanup(self, sid: str) -> None:
            evicted.append(sid)

    monkeypatch.setattr(appdeps, "get_orchestrator", lambda: _FakeOrch())

    orch = TaskOrchestrator()
    spawned: list[str] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        # The runtime MUST have been evicted before the loop (would build a turn).
        assert session_id in evicted, f"{session_id} respawned without runtime eviction"
        spawned.append(session_id)

    orch.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> None:
        await orch.resume_task("t1", "w1")
        await asyncio.sleep(0.05)

    try:
        asyncio.run(_run())
        assert "lead-s" in evicted and "sA" in evicted  # both evicted on resume
        assert "lead-s" in spawned and "sA" in spawned
    finally:
        mailbox_registry.unregister("lead-s")


def test_lead_shutdown_exit_skips_auto_finalize(monkeypatch) -> None:
    """A lead loop that exits via ``shutdown`` (pause / stop / finish_task
    broadcast) must NOT run ``_auto_finalize_lead_task``. Otherwise a rapid
    pause→resume races: the OLD loop's finalize runs after resume flips the task
    back to ``active`` and wrongly ``blocked``s the freshly-resumed task (the
    observed VALUZ pause/resume bug). Natural exits still auto-finalize."""
    from valuz_agent.modules.sessions import run_orchestrator as run_orch

    orch = TaskOrchestrator()

    async def _noop(*_a: object, **_k: object) -> None: ...

    monkeypatch.setattr(run_orch, "_finalize_session", _noop)

    called: list[str] = []

    async def _fake_auto(**kw: object) -> None:
        called.append(str(kw["task_id"]))

    monkeypatch.setattr(orch, "_auto_finalize_lead_task", _fake_auto)

    common = dict(
        session_id="L",
        last_content="",
        final_status="idle",
        role="lead",
        task_id="t1",
        project_id="w1",
    )
    # shutdown exit → auto-finalize SKIPPED (no spurious block on resume)
    asyncio.run(orch._finalize_actor(via_shutdown=True, **common))  # type: ignore[arg-type]
    assert called == []
    # natural exit (idle-TTL / end_turn) → auto-finalize RUNS
    asyncio.run(orch._finalize_actor(via_shutdown=False, **common))  # type: ignore[arg-type]
    assert called == ["t1"]
