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
from sqlalchemy import select
from valuz_agent.adapters import kernel_client as kernel_client_mod
from valuz_agent.modules.tasks import launcher as launcher_mod
from valuz_agent.modules.tasks import mailbox_store, member_probe
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

OWNER = "local-test-owner"


def _as_async(fn):
    """Wrap a sync callable as a coroutine fn for monkeypatching the async
    ``kernel_client`` facade (its methods are awaited by the code under test)."""

    async def _f(*args, **kwargs):
        return fn(*args, **kwargs)

    return _f




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


def _event_payload(db_factory, ev_type, project_id="w1", task_id="t1") -> dict:
    """Sync read of the newest event payload of ``ev_type`` for a task."""
    db = db_factory()
    try:
        rows = (
            db.execute(
                select(TaskEventRow)
                .filter_by(project_id=project_id, task_id=task_id, type=ev_type)
                .order_by(TaskEventRow.sequence)
            )
            .scalars()
            .all()
        )
        return dict(rows[-1].payload or {}) if rows else {}
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
            user_id=OWNER,
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
    # The plan snapshot the frontend Todo panel consumes stamps each node's
    # member display name at emit time, so the panel renders it directly instead
    # of joining the ``agent`` slug against an async members list. With no member
    # rows seeded here, resolution falls back to the slug — but the key is always
    # present on every node with an agent.
    panel = _event_payload(db_factory, "task_plan_update")["subtasks"]
    assert {n["key"]: n.get("agent_name") for n in panel} == {
        "a": "researcher",
        "b": "writer",
    }


def test_plan_task_rejects_when_progress_exists(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x", "status": "in_progress"}],
        )
    )
    # Second plan_task must refuse (there is progress).
    res = asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    res = asyncio.run(planning.get_plan(task_id="t1", project_id="w1", user_id=OWNER))
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
            user_id=OWNER,
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
    res = asyncio.run(planning.get_plan(task_id="t1", project_id="w1", user_id=OWNER))
    node = next(n for n in res["subtasks"] if n["key"] == "a")
    assert node["review_criteria"] == "covers price + %chg + 1-line takeaway"


def test_modify_plan_adds_and_revalidates(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    res = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A"}, {"key": "b", "title": "B", "depends_on": ["a"]}],
        )
    )
    res = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            update=[{"key": "a", "depends_on": ["b"]}],
        )
    )
    assert "error" in res and "cycle" in res["error"]


def test_dispatch_rejects_unknown_subtask_key(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    res = asyncio.run(
        orch.dispatcher.dispatch_async(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtask_key="ghost",
            user_id=OWNER,
        )
    )
    assert res["status"] == "failed" and "plan_task first" in res["error"]


def test_dispatch_rejects_blocked_subtask(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x"},
                {"key": "b", "title": "B", "agent": "y", "depends_on": ["a"]},
            ],
        )
    )
    res = asyncio.run(
        orch.dispatcher.dispatch_async(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            subtask_key="b",
            user_id=OWNER,
        )
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
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x", "goal": "build X", "status": "in_review"}
            ],
        )
    )
    # node in_review → reject (no live member) → rework
    asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x", "status": "in_review"},
                {"key": "b", "title": "B", "agent": "y", "depends_on": ["a"]},
            ],
        )
    )
    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            decision="approve",
            subtask_key="a",
        )
    )
    assert res["decision"] == "approve"
    assert res["ready"] == ["b"]  # b unlocked now that a is done
    types = _events(db_factory)
    assert "subtask_reviewed" in types and "subtask_completed" in types
    # The member's display name is stamped into the event payload at emit time
    # so the frontend renders it directly instead of joining the ``actor`` slug
    # against an async members list ("成员智能体名称查询不到"). With no member
    # rows seeded here, resolution falls back to the slug — but the key is always
    # present, which is what frees the frontend from the racy join.
    completed_payload = _event_payload(db_factory, "subtask_completed")
    assert completed_payload.get("agent_name") == "x"


def test_review_rework_no_live_member_sets_rework(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x", "status": "in_review"}],
        )
    )
    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            decision="rework",
            subtask_key="a",
            feedback="redo it",
        )
    )
    assert res["decision"] == "rework"
    assert res["delivered_to_live_member"] is False
    plan = asyncio.run(planning.get_plan(task_id="t1", project_id="w1", user_id=OWNER))
    node = next(n for n in plan["subtasks"] if n["key"] == "a")
    assert node["status"] == "active"  # rework maps to panel 'active'


def test_finish_task_stopped_emits_task_stopped(db_factory, tmp_path) -> None:
    """status='stopped' is the user-requested-terminate / unreachable path
    after the task_state.py rework (task-level 'failed' was removed)."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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


def test_update_task_status_enforces_state_machine(db_factory, tmp_path) -> None:
    """``update_task_status`` is the enforcement point for ``task_state.py``:
    out-of-enum targets and illegal transitions raise; legal ones pass; a
    legacy/unknown *source* row is tolerated so it can still be recovered."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.datastore import TaskDatastore
    from valuz_agent.modules.tasks.task_state import TaskStateError

    _make_task(db_factory, tmp_path)  # status="active"

    async def _run() -> None:
        # 1) out-of-enum target → raise (the old ``"failed"`` corruption).
        async with async_unit_of_work() as db:
            with pytest.raises(TaskStateError):
                await TaskDatastore(db).update_task_status(OWNER, "t1", "failed")
        # 2) illegal transition from a valid source → raise (active↛draft).
        async with async_unit_of_work() as db:
            with pytest.raises(TaskStateError):
                await TaskDatastore(db).update_task_status(OWNER, "t1", "draft")
        # 3) legal transition → succeeds.
        async with async_unit_of_work() as db:
            assert await TaskDatastore(db).update_task_status(OWNER, "t1", "blocked") is True
        assert _task_status(db_factory) == "blocked"

    asyncio.run(_run())

    # 4) legacy/unknown source status is tolerated (logged, not raised) so a
    # pre-enforcement ``"failed"`` row can still be recovered to ``active``.
    db = db_factory()
    try:
        db.get(TaskRow, "t1").status = "failed"  # simulate a legacy row
        db.commit()
    finally:
        db.close()

    async def _recover_legacy() -> None:
        async with async_unit_of_work() as db:
            assert await TaskDatastore(db).update_task_status(OWNER, "t1", "active") is True

    asyncio.run(_recover_legacy())
    assert _task_status(db_factory) == "active"


def test_finish_task_rejected_when_plan_has_unresolved_nodes(db_factory, tmp_path) -> None:
    """v0.14 guard: a 'completed' finish is rejected while planned nodes remain."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[
                {"key": "a", "title": "A", "agent": "x"},
                {"key": "sum", "title": "Summary", "depends_on": ["a"]},
            ],
        )
    )
    res = asyncio.run(
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            summary="done",
            user_id=OWNER,
        )
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
            user_id=OWNER,
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
        for st in ("in_progress", "in_review", "done"):  # legal chain
            plan.update_node("a", status=st)
        row.plan = plan.to_dict()
        db.commit()
    finally:
        db.close()
    res = asyncio.run(
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            summary="done",
            user_id=OWNER,
        )
    )
    assert res["ok"] is True
    assert "task_completed" in _events(db_factory)


def test_render_plan_md_writes_file(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
            user_id=OWNER,
        )
    )
    assert _task_status(db_factory) == "completed"
    assert "task_completed" in _events(db_factory)


def test_finalize_actor_threads_user_id_to_auto_finalize(db_factory, tmp_path) -> None:
    """Regression: ``_finalize_actor`` must forward ``user_id`` to
    ``_auto_finalize_lead_task``. The task lookup there is owner-scoped, so a
    dropped owner silently misses and orphans the task ``active`` forever — the
    exact live bug (lead self-completed inline, empty plan, task stuck active
    while the session sat ``idle``/end_turn). Drive the full finalize seam (not
    just ``_auto_finalize_lead_task`` directly) so the threading is covered."""
    _make_task(db_factory, tmp_path)
    _make_lead_run(db_factory)
    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization.finalize_actor(
            session_id="lead-sess",
            last_content="done inline",
            final_status="idle",
            role="lead",
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
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
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],  # status defaults to planned
        )
    )
    asyncio.run(
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
            user_id=OWNER,
        )
    )
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _events(db_factory)


def test_auto_finalize_blocks_on_terminated_with_empty_plan(db_factory, tmp_path) -> None:
    """Lead turn ended ``terminated`` (driver flagged a hard failure — e.g. a
    raised exception, or ``_resolve_turn_status`` elevating an idle-but-errored
    turn) with no plan nodes. This is a genuine failure the user must see and be
    able to retry, so it locks to ``blocked`` (not silently ``active``).
    ``resume_task`` rebuilds the lead from the detail page's retry/继续 entry."""
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="terminated",
            user_id=OWNER,
        )
    )
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _events(db_factory)


def test_auto_finalize_blocks_on_stop_reason_error_with_empty_plan(
    db_factory, tmp_path, monkeypatch
) -> None:
    """An API/exec error that surfaces via ``stop_reason`` (status stayed
    ``idle``) with an empty plan → ``blocked``. This is the confirmed bug: a
    socket-drop / ECONNRESET arrives as ``ResultMessage(is_error=True)``, the
    kernel records ``stop_reason.type=='error'``; auto-finalize must mark the
    task failed-but-resumable instead of ``completed``."""
    from types import SimpleNamespace


    _make_task(db_factory, tmp_path)
    fake_sess = SimpleNamespace(
        stop_reason={
            "type": "error",
            "category": "api_error",
            "message": "API Error: socket closed (ECONNRESET)",
        }
    )
    monkeypatch.setattr(
        kernel_client_mod, "get_session", _as_async(lambda _uid, _sid: fake_sess)
    )
    from valuz_agent.modules.tasks import events as events_mod

    published: list[tuple] = []
    monkeypatch.setattr(
        events_mod, "publish_task_finalized", lambda tid, uid, st: published.append((tid, st))
    )
    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
            user_id=OWNER,
        )
    )
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _events(db_factory)
    # Terminal contract (tasks/events.py): every terminal write announces
    # task.finalized — the lead-turn-error → blocked path used to skip it.
    assert ("t1", "blocked") in published


def test_auto_finalize_cancel_with_empty_plan_parks_paused(
    db_factory, tmp_path, monkeypatch
) -> None:
    """Carve-out from the 2026-05-29 EnterPlanMode-hang bug, resolved honestly:
    a user cancellation (``category='user_interrupt'``) BEFORE any plan node
    exists has no in-flight work to protect, so ``blocked`` (with its failure
    notification) would be a lie. But the old "stay active" was a lie too —
    an active task with no lead loop is a dead zone (inject on it returns
    LEAD_OFFLINE and drops the message; only halted states auto-revive), and
    the health watchdog then flipped it blocked anyway with a misleading
    "lead stopped" alert. ``paused`` is the honest resting state: inject /
    resume revive it immediately, the watchdog ignores it."""
    from types import SimpleNamespace


    _make_task(db_factory, tmp_path)
    fake_sess = SimpleNamespace(
        stop_reason={"type": "error", "category": "user_interrupt", "message": "cancelled"}
    )
    monkeypatch.setattr(
        kernel_client_mod, "get_session", _as_async(lambda _uid, _sid: fake_sess)
    )
    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
            user_id=OWNER,
        )
    )
    assert _task_status(db_factory) == "paused"
    assert "task_blocked" not in _events(db_factory)
    assert "paused" in _events(db_factory)

    # Composed with the watchdog: the parked task is OUT of the sweep set —
    # no more "lead stopped without finishing" notification 2 minutes after a
    # deliberate cancel (the contradiction this change resolves).
    from valuz_agent.modules.tasks.recovery import TaskHealthConfig, TaskHealthMonitor

    mon = TaskHealthMonitor(TaskHealthConfig(confirm_sweeps=1))
    assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "paused"


def _make_member_run(db_factory, *, session_id="mem-1", subtask_key="a") -> None:
    db = db_factory()
    try:
        db.add(
            TaskSessionRow(
                user_id="local-test-owner",
                id="run-mem",
                project_id="w1",
                task_id="t1",
                session_id=session_id,
                agent_slug="researcher",
                sequence=1,
                kind="subtask",
                subtask_key=subtask_key,
                status="active",
                dispatched_by="lead-sess",
            )
        )
        db.commit()
    finally:
        db.close()


def test_finalize_actor_member_error_sets_rework_not_failed(
    db_factory, tmp_path, monkeypatch
) -> None:
    """A member run ending terminated/error (incl. an API/socket drop that
    ``_resolve_turn_status`` elevates to 'terminated') must land its plan node on
    ``rework`` — recoverable + dispatchable — NOT ``failed`` (which the dispatch
    gate refuses and a blocked→resume can't relaunch). Mirrors reconcile()/
    stop_member; the error rides along as ``review_feedback`` and the timeline
    still records a ``subtask_failed`` event."""
    from valuz_agent.modules.sessions import run_orchestrator as run_orch
    from valuz_agent.modules.tasks.plan import TaskPlan

    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "researcher", "status": "in_progress"}],
        )
    )
    _make_member_run(db_factory)

    async def _noop(*_a: object, **_k: object) -> None: ...

    async def _fake_manifest(*_a: object, **_k: object) -> dict[str, str]:
        return {"session_id": "mem-1", "status": "terminated", "summary": "API Error: ECONNRESET"}

    from valuz_agent.modules.tasks import manifest as manifest_mod

    monkeypatch.setattr(run_orch, "_finalize_session", _noop)
    # collect_manifest_safe wraps the source module's collect_manifest —
    # patching the source covers every consumer.
    monkeypatch.setattr(manifest_mod, "collect_manifest", _fake_manifest)

    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization.finalize_actor(
            session_id="mem-1",
            last_content="",
            final_status="terminated",
            role="subtask",
            task_id="t1",
            project_id="w1",
            via_shutdown=False,  # type: ignore[call-arg]
            user_id=OWNER,
        )
    )

    db = db_factory()
    try:
        node = TaskPlan.from_dict(db.query(TaskRow).filter_by(id="t1").one().plan).get("a")
        assert node is not None
        assert node.status == "rework"  # NOT "failed" — re-dispatchable on resume
        assert node.review_feedback  # the error is folded into the retry brief
    finally:
        db.close()
    # The failed attempt is still recorded on the timeline.
    assert "subtask_failed" in _events(db_factory)


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
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    asyncio.run(
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="terminated",
            user_id=OWNER,
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
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
            user_id=OWNER,
        )
    )
    assert _events(db_factory) == []  # no duplicate terminal event appended


def test_auto_finalize_noop_when_members_in_flight(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"m1"})  # a member is still running
    asyncio.run(
        orch.finalization._auto_finalize_lead_task(
            lead_session_id="lead-sess",
            task_id="t1",
            project_id="w1",
            final_status="idle",
            user_id=OWNER,
        )
    )
    assert _task_status(db_factory) == "active"  # left open for the member to finish


def test_lead_idle_with_no_pending_true_when_clean(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    # No members, no plan → nothing to wait for → break the loop immediately.
    idle = orch.coordination.lead_idle_with_no_pending("t1", "w1", user_id=OWNER)
    assert asyncio.run(idle) is True


def test_lead_idle_with_no_pending_false_when_member_in_flight(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"m1"})
    idle = orch.coordination.lead_idle_with_no_pending("t1", "w1", user_id=OWNER)
    assert asyncio.run(idle) is False


def test_lead_idle_with_no_pending_false_when_plan_unresolved(db_factory, tmp_path) -> None:
    _make_task(db_factory, tmp_path)
    orch = TaskOrchestrator()
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    idle = orch.coordination.lead_idle_with_no_pending("t1", "w1", user_id=OWNER)
    assert asyncio.run(idle) is False


# ---------------------------------------------------------------------------
# VALUZ-RESUME S2 — _recover_one_task: reconcile members + re-drive lead
# ---------------------------------------------------------------------------


def test_recover_one_task_reconciles_members_and_redrives_lead(
    db_factory, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import mailbox_store
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
        kernel_client_mod, "get_session", _as_async(lambda _uid, sid: sessions.get(sid))
    )

    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> None:
        await orch.recovery._recover_one_task("t1", "w1", user_id=OWNER)
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
        # Recovery re-seeds the completed member's result into the lead's DURABLE
        # inbox: the lead's loop may come up in a different process than this.
        assert asyncio.run(mailbox_store.has_pending("lead-s"))
    finally:
        pass


# ---------------------------------------------------------------------------
# S4 — Layer 2: user stop / resume (stop_task / resume_task / stop_member)
# S3 — online heartbeat (member_probe.heartbeat_pending)
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

    async def _fake_interrupt(sid: str, user_id: str | None = None) -> None:
        assert user_id == OWNER
        interrupted.append(sid)

    orch._recovery._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]

    assert asyncio.run(orch.recovery.stop_task("t1", "w1", user_id=OWNER)) is True

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

    orch._recovery._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]
    assert asyncio.run(orch.recovery.stop_task("t1", "w1", user_id=OWNER)) is False


def test_resume_task_only_paused_flips_active_and_redrives(
    db_factory, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace


    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress")],
        task_status="paused",
        run_status="paused",
    )
    # paused member kernel session was interrupted (idle + UserInterrupt-ish) → resume.
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(
            lambda _uid, sid: SimpleNamespace(status="idle", stop_reason={"type": "user_interrupt"})
        ),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> dict:
        result = await orch.recovery.resume_task("t1", "w1", user_id=OWNER)
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
        pass


def test_resume_task_noop_when_active(db_factory, tmp_path) -> None:
    """An active task cannot be 'resumed' — it never paused. Caller gets a
    clear error string back so the LLM can surface it to the user."""
    _seed_lead_and_members(
        db_factory, tmp_path, members=[("A", "backend", "sA", "in_progress")], task_status="active"
    )
    orch = TaskOrchestrator()
    result = asyncio.run(orch.recovery.resume_task("t1", "w1", user_id=OWNER))
    assert result["ok"] is False
    assert result["prior_status"] == "active"
    assert "paused" in result["error"] or "blocked" in result["error"]


def test_resume_task_accepts_blocked(db_factory, tmp_path, monkeypatch) -> None:
    """blocked → active is a legal transition per task_state.ALLOWED_TRANSITIONS.
    The lead-turn-error auto-finalize path leaves tasks blocked; users should
    be able to revive them by calling resume_task."""
    from types import SimpleNamespace


    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="blocked", run_status="rejected"
    )
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(lambda _uid, sid: SimpleNamespace(status="idle", stop_reason={"type": "error"})),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    result = asyncio.run(orch.recovery.resume_task("t1", "w1", user_id=OWNER))
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


    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="stopped", run_status="completed"
    )
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(lambda _uid, sid: SimpleNamespace(status="idle", stop_reason=None)),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    result = asyncio.run(orch.recovery.resume_task("t1", "w1", user_id=OWNER))
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


    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="completed", run_status="completed"
    )
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(lambda _uid, sid: SimpleNamespace(status="idle", stop_reason=None)),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    result = asyncio.run(orch.recovery.resume_task("t1", "w1", user_id=OWNER))
    assert result["ok"] is True
    assert result["prior_status"] == "completed"
    assert _task_row(db_factory).status == "active"
    assert ("lead-s", "lead") in spawned


def test_resume_task_with_instruction_embeds_brief_and_logs_event(
    db_factory, tmp_path, monkeypatch
) -> None:
    """resume(text=…) — the instruction must (a) land in the respawned lead's
    recovery brief inside a <user-instruction source="resume"> envelope and
    (b) leave a ``user_inject`` timeline event, so "回复并恢复" is one atomic
    step (":intervene action=resume text=…" / chat inject on a halted task)."""
    from types import SimpleNamespace


    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="stopped", run_status="completed"
    )
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(lambda _uid, sid: SimpleNamespace(status="idle", stop_reason=None)),
    )
    orch = TaskOrchestrator()
    prompts: dict[str, str] = {}

    async def _fake_loop(*, session_id, role, initial_prompt, **_kw) -> None:
        prompts[role] = initial_prompt

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    try:
        result = asyncio.run(
            orch.recovery.resume_task("t1", "w1", user_id=OWNER, instruction="先核对数据再继续")
        )
        assert result["ok"] is True
        assert '<user-instruction source="resume">' in prompts["lead"]
        assert "先核对数据再继续" in prompts["lead"]
        assert "user_inject" in _events(db_factory)
        payload = _event_payload(db_factory, "user_inject")
        assert payload == {"text": "先核对数据再继续", "via": "resume"}
    finally:
        pass


def test_resume_task_accepts_legacy_failed(db_factory, tmp_path, monkeypatch) -> None:
    """Legacy rows written before task-failure folded into ``blocked`` still
    carry status='failed' (outside the enum). They used to be stranded —
    resume rejected them and the detail page showed no action bar. They now
    resume exactly like blocked (datastore tolerates the unknown source)."""
    from types import SimpleNamespace


    _seed_lead_and_members(
        db_factory, tmp_path, members=[], task_status="failed", run_status="archived"
    )
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(lambda _uid, sid: SimpleNamespace(status="idle", stop_reason=None)),
    )
    orch = TaskOrchestrator()
    spawned: list[tuple[str, str]] = []

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]
    try:
        result = asyncio.run(orch.recovery.resume_task("t1", "w1", user_id=OWNER))
        assert result["ok"] is True
        assert result["prior_status"] == "failed"
        assert _task_row(db_factory).status == "active"
        assert ("lead-s", "lead") in spawned
    finally:
        pass


def test_resume_task_rejects_abandoned(db_factory, tmp_path) -> None:
    """abandoned stays hard-terminal — a discarded draft has no plan to
    revive; the user must draft afresh."""
    _seed_lead_and_members(db_factory, tmp_path, members=[], task_status="abandoned")
    orch = TaskOrchestrator()
    result = asyncio.run(orch.recovery.resume_task("t1", "w1", user_id=OWNER))
    assert result["ok"] is False
    assert result["prior_status"] == "abandoned"
    assert _task_row(db_factory).status == "abandoned"


def test_stop_member_rejects_run_reworks_node_and_notifies_lead(
    db_factory, tmp_path, monkeypatch
) -> None:
    from valuz_agent.modules.tasks import mailbox_store
    from valuz_agent.modules.tasks.plan import TaskPlan

    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")])
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"sB"})

    async def _fake_interrupt(sid: str, user_id: str | None = None) -> None:
        assert user_id == OWNER
        pass

    orch._recovery._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]
    try:
        assert asyncio.run(orch.recovery.stop_member("sB", user_id=OWNER)) is True
        runs = _runs(db_factory)
        assert runs["sB"] == "rejected"
        plan = TaskPlan.from_dict(db_factory().query(TaskRow).filter_by(id="t1").one().plan)
        assert plan.get("B").status == "rework"
        assert "sB" not in orch._members.live_members("t1")
        assert asyncio.run(mailbox_store.has_pending("lead-s"))
        # The cancellation reaches the lead through its durable inbox: the
        # lead's loop may well be in another process than the one that served
        # the stop request.
        drained = asyncio.run(mailbox_store.drain("lead-s"))
        assert len(drained) == 1
        msg = drained[0]
        assert msg.kind == "member_done" and msg.payload["status"] == "cancelled"
    finally:
        pass


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
        kernel_client_mod, "get_session", _as_async(lambda _uid, sid: sessions.get(sid))
    )
    from valuz_agent.modules.tasks import manifest as manifest_mod

    monkeypatch.setattr(
        manifest_mod,
        "collect_manifest",
        _as_async(lambda *a, **k: {"status": "completed", "summary": "ok"}),
    )
    orch = TaskOrchestrator()

    out = asyncio.run(
        member_probe.heartbeat_pending(
            task_id="t1", project_id="w1", pending_keys={"B", "C"}, user_id=OWNER
        )
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

    async def _fake_interrupt(sid: str, user_id: str | None = None) -> None:
        assert user_id == OWNER
        interrupted.append(sid)

    async def _fake_loop(*, session_id, role, **_kw) -> None:
        spawned.append((session_id, role))

    from valuz_agent.modules.tasks import events as events_mod

    published: list[tuple] = []
    monkeypatch.setattr(
        events_mod, "publish_task_finalized", lambda tid, uid, st: published.append((tid, st))
    )
    monkeypatch.setattr(orch._recovery, "_interrupt_kernel_session", _fake_interrupt)
    monkeypatch.setattr(orch._actor, "run_actor_loop", _fake_loop)
    # On resume, paused members read as interrupted-idle → resumable.
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(
            lambda _uid, sid: SimpleNamespace(status="idle", stop_reason={"type": "user_interrupt"})
        ),
    )

    async def _run() -> None:
        # 1) Stop → stopped (cascade interrupt lead + members). ``stopped`` is
        # UI-terminal but still revivable (resume below proves the closed loop).
        async with async_unit_of_work() as db:
            resp = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="stop"), db, "local-test-owner"
            )
        assert resp.status == "stopped"
        # Terminal contract: a user stop is a terminal write → task.finalized
        # must be announced (sandbox TTL clamp). The old direct update_task
        # write in stop_task skipped both the announce and the state guard.
        assert ("t1", "stopped") in published
        assert set(interrupted) == {"sA", "sB", "lead-s"}
        runs = _runs(db_factory)
        assert runs["sA"] == "paused" and runs["sB"] == "paused"
        # In-flight plan nodes are parked → ``paused`` (panel stops spinning).
        parked = TaskPlan.from_dict(_task_row(db_factory).plan)
        assert {n.status for n in parked.nodes} == {"paused"}

        # 2) Resume → active (reconcile + respawn + re-drive lead).
        async with async_unit_of_work() as db:
            resp2 = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="resume"), db, "local-test-owner"
            )
        assert resp2.status == "active"
        await asyncio.sleep(0.05)  # let create_task'd loops run
        assert ("lead-s", "lead") in spawned
        assert ("sA", "subtask") in spawned and ("sB", "subtask") in spawned

    try:
        asyncio.run(_run())
        assert _task_row(db_factory).status == "active"
    finally:
        orch._members.set_members("t1", set())


def test_pause_distinct_from_stop_and_parks_nodes(db_factory, tmp_path, monkeypatch) -> None:
    """``pause`` → ``paused`` (resumable); ``stop`` on the now-paused task →
    ``stopped`` (the screenshot bug: stop on a paused task used to no-op because
    ``stop_task`` only accepted ``active``). Both park the in-flight plan node
    (``in_progress`` → ``paused``) so the right-rail panel stops spinning."""
    from valuz_agent.api.routes import tasks as tasks_route
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.plan import TaskPlan

    _seed_lead_and_members(db_factory, tmp_path, members=[("A", "backend", "sA", "in_progress")])
    orch = tasks_route.task_orchestrator
    orch._members.set_members("t1", {"sA"})

    async def _noop_interrupt(_sid: str, user_id: str | None = None) -> None:
        assert user_id == OWNER

    monkeypatch.setattr(orch._recovery, "_interrupt_kernel_session", _noop_interrupt)

    async def _run() -> None:
        # pause → paused; node parked; member run paused.
        async with async_unit_of_work() as db:
            r1 = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="pause"), db, "local-test-owner"
            )
        assert r1.status == "paused"
        assert TaskPlan.from_dict(_task_row(db_factory).plan).get("A").status == "paused"
        assert _runs(db_factory)["sA"] == "paused"

        # stop on the already-paused task → stopped (no longer a no-op).
        async with async_unit_of_work() as db:
            r2 = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="stop"), db, "local-test-owner"
            )
        assert r2.status == "stopped"

    try:
        asyncio.run(_run())
    finally:
        orch._members.set_members("t1", set())


def test_intervene_noop_raises_409_instead_of_false_success(
    db_factory, tmp_path, monkeypatch
) -> None:
    """A stop/resume the state machine rejects must surface a 409, not a silent
    200. Regression for the "点停止后状态有误" report: the route used to swallow
    ``stop_task``'s ``False`` / ``resume_task``'s ``{ok: False}``, so the client
    toasted "已停止/已恢复" on a no-op while the badge kept the old status.

    Both no-op branches are exercised on one task via a real sequence:
      - ``resume`` on an ``active`` task — ``active`` is not a resumable source.
      - ``stop`` on an already-``stopped`` task — only ``active`` / ``paused``
        can be stopped, so a second stop is a no-op."""
    from fastapi import HTTPException

    from valuz_agent.api.routes import tasks as tasks_route
    from valuz_agent.infra.db import async_unit_of_work

    _seed_lead_and_members(db_factory, tmp_path, members=[], task_status="active")
    orch = tasks_route.task_orchestrator

    async def _noop_interrupt(_sid: str, user_id: str | None = None) -> None:
        assert user_id == OWNER

    monkeypatch.setattr(orch._recovery, "_interrupt_kernel_session", _noop_interrupt)

    async def _run() -> None:
        # 1) resume on an ACTIVE task → not a resumable source → 409, no mutation.
        async with async_unit_of_work() as db:
            with pytest.raises(HTTPException) as ei:
                await tasks_route.intervene(
                    "t1", tasks_route.InterveneRequest(action="resume"), db, "local-test-owner"
                )
            assert ei.value.status_code == 409
        assert _task_status(db_factory) == "active"

        # 2) stop on the ACTIVE task is legal → 200, status flips to stopped.
        async with async_unit_of_work() as db:
            resp = await tasks_route.intervene(
                "t1", tasks_route.InterveneRequest(action="stop"), db, "local-test-owner"
            )
        assert resp.status == "stopped"

        # 3) stop AGAIN on the now-stopped task → no-op → 409 (not a false 200).
        async with async_unit_of_work() as db:
            with pytest.raises(HTTPException) as ei:
                await tasks_route.intervene(
                    "t1", tasks_route.InterveneRequest(action="stop"), db, "local-test-owner"
                )
            assert ei.value.status_code == 409
        assert _task_status(db_factory) == "stopped"

    try:
        asyncio.run(_run())
    finally:
        pass


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


    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("A", "backend", "sA", "in_progress")],
        task_status="paused",
        run_status="paused",
    )
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(
            lambda _uid, sid: SimpleNamespace(status="idle", stop_reason={"type": "user_interrupt"})
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

    orch._actor.run_actor_loop = _fake_loop  # type: ignore[method-assign]

    async def _run() -> None:
        await orch.recovery.resume_task("t1", "w1", user_id=OWNER)
        await asyncio.sleep(0.05)

    try:
        asyncio.run(_run())
        assert "lead-s" in evicted and "sA" in evicted  # both evicted on resume
        assert "lead-s" in spawned and "sA" in spawned
    finally:
        pass


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

    # _finalize_actor delegates to LifecycleService (ADR-023 Step 3c), whose
    # implementation calls its own _auto_finalize_lead_task — patch it there.
    monkeypatch.setattr(orch._finalization, "_auto_finalize_lead_task", _fake_auto)

    common = dict(
        session_id="L",
        last_content="",
        final_status="idle",
        role="lead",
        task_id="t1",
        project_id="w1",
        user_id=OWNER,
    )
    # shutdown exit → auto-finalize SKIPPED (no spurious block on resume)
    asyncio.run(orch.finalization.finalize_actor(via_shutdown=True, **common))  # type: ignore[arg-type]
    assert called == []
    # natural exit (idle-TTL / end_turn) → auto-finalize RUNS
    asyncio.run(orch.finalization.finalize_actor(via_shutdown=False, **common))  # type: ignore[arg-type]
    assert called == ["t1"]


# ---------------------------------------------------------------------------
# Lifecycle extraction regression pins — the two fossil-edit bugs (the fixes
# landed on the dead lifecycle copies while the live bodies sat on the
# orchestrator; the extraction merged them — keep them pinned).
# ---------------------------------------------------------------------------


def _mark_all_done(db_factory, task_id="t1") -> None:
    from valuz_agent.modules.tasks.plan import TaskPlan

    db = db_factory()
    try:
        row = db.execute(select(TaskRow).filter_by(id=task_id)).scalars().one()
        plan = TaskPlan.from_dict(row.plan)
        for n in plan.nodes:
            # Walk the legal chain — NODE_TRANSITIONS forbids planned → done.
            for st in ("in_progress", "in_review", "done"):
                plan.update_node(n.key, status=st)
        row.plan = plan.to_dict()
        db.commit()
    finally:
        db.close()


def test_finish_task_completed_publishes_finalized_and_notifies_memory(
    db_factory, tmp_path, monkeypatch
) -> None:
    """finish_task is a terminal write site of the task.finalized contract
    (tasks/events.py): the commercial allocator's TTL clamp listens on it.
    A completed finish must also graduate lessons into project memory."""
    from valuz_agent.modules.tasks import events as events_mod

    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    _mark_all_done(db_factory)

    calls: list[tuple] = []
    monkeypatch.setattr(
        events_mod, "publish_task_finalized", lambda tid, uid, st: calls.append(("pub", tid, st))
    )

    orch = TaskOrchestrator()
    res = asyncio.run(
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            summary="done",
            user_id=OWNER,
        )
    )
    assert res["ok"] is True
    # Memory graduation rides this announce (see the task.finalized wiring
    # test below) — the terminal write itself must publish it.
    assert ("pub", "t1", "completed") in calls


def test_finish_task_stopped_publishes_finalized_without_memory(
    db_factory, tmp_path, monkeypatch
) -> None:
    """A 'stopped' finish still announces task.finalized (sandbox reclaim)
    but must NOT graduate memory — that's reserved for real completions."""
    from valuz_agent.modules.tasks import events as events_mod

    _make_task(db_factory, tmp_path)

    calls: list[tuple] = []
    monkeypatch.setattr(
        events_mod, "publish_task_finalized", lambda tid, uid, st: calls.append(("pub", tid, st))
    )

    orch = TaskOrchestrator()
    res = asyncio.run(
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead",
            summary="stop",
            status="stopped",
            user_id=OWNER,
        )
    )
    assert res["ok"] is True
    assert ("pub", "t1", "stopped") in calls


def test_commit_task_creates_lead_session_with_task_scope(
    db_factory, tmp_path, monkeypatch
) -> None:
    """The committed lead must ride the task's sandbox scope
    (``SandboxScope(kind='task')``) exactly like the kickoff lead and every
    dispatched member — c81ab288 originally landed this on the dead copy."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import resolution as res_mod

    # Draft task with a plan (commit refuses empty plans).
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="chat",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    db = db_factory()
    try:
        row = db.execute(select(TaskRow).filter_by(id="t1")).scalars().one()
        row.status = "draft"
        db.commit()
    finally:
        db.close()

    class _FakeWsDs:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _uid, _wid):
            return SimpleNamespace(id="w1", kind="project", root_path=str(tmp_path), name="W1")

        async def get_context(self, _uid, _wid):
            return None

    class _FakeMemberDs:
        def __init__(self, _db):
            pass

        async def get(self, _uid, _wid, _slug):
            return SimpleNamespace(agent_slug="lead")

        async def list_by_project(self, _uid, _wid):
            return []  # empty roster → the commit/kickoff provider pre-flight passes

    created: dict = {}

    async def _capture_create_session(_uid, session, scope=None):
        created["session_id"] = session.id
        created["scope"] = scope

    monkeypatch.setattr(res_mod, "ProjectDatastore", _FakeWsDs)
    monkeypatch.setattr(res_mod, "ProjectMemberDatastore", _FakeMemberDs)
    monkeypatch.setattr(
        res_mod, "fs_registry", SimpleNamespace(project_cwd=lambda *a, **k: tmp_path)
    )
    monkeypatch.setattr(res_mod, "_member_agent_config", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(
        res_mod,
        "build_member_session",
        _as_async(lambda **_k: SimpleNamespace(id="lead-sess-1")),
    )
    monkeypatch.setattr(res_mod, "_credential_gap", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(res_mod, "_provider_resolver_deps", lambda _db: {})
    monkeypatch.setattr(launcher_mod.kernel_client, "create_session", _capture_create_session)
    monkeypatch.setattr(
        launcher_mod,
        "project_index",
        SimpleNamespace(record=_as_async(lambda *_a, **_k: None)),
    )

    async def _run() -> dict:
        orch = TaskOrchestrator()
        monkeypatch.setattr(orch._actor, "run_actor_loop", _as_async(lambda **_k: None))
        res = await orch.lifecycle.commit_task(
            task_id="t1",
            project_id="w1",
            caller_session_id="chat",
            user_id=OWNER,
        )
        await asyncio.sleep(0)  # let the spawned (stubbed) lead loop settle
        return res

    res = asyncio.run(_run())
    assert res.get("status") == "active", res
    assert created["session_id"] == "lead-sess-1"
    scope = created["scope"]
    assert scope is not None and scope.kind == "task" and scope.id == "t1"


# ---------------------------------------------------------------------------
# Event-trace golden — the frontend's stream contract in one test.
#
# Drives the full chat-plan lifecycle (draft → plan → commit → dispatch →
# member-in-review → approve → finish) with the kernel/actor layer stubbed,
# and pins the EXACT ordered event-type sequence plus the payload fields the
# frontend reads (agent_name, subtask_key). Any refactor that reorders,
# drops, renames, or de-stamps an event fails here first.
# ---------------------------------------------------------------------------


def test_task_lifecycle_event_trace_golden(db_factory, tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import lifecycle as lc_mod
    from valuz_agent.modules.tasks import resolution as res_mod

    class _FakeWsDs:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _uid, _wid):
            return SimpleNamespace(id="w1", kind="project", root_path=str(tmp_path), name="W1")

        async def get_context(self, _uid, _wid):
            return None

    class _FakeMemberDs:
        def __init__(self, _db):
            pass

        async def get(self, _uid, _wid, _slug):
            return SimpleNamespace(agent_slug="lead")

        async def list_by_project(self, _uid, _wid):
            return []  # empty roster → the commit/kickoff provider pre-flight passes

    _next_sid = iter(["lead-sess-1", "member-sess-1"])

    async def _fake_build(**_k):
        return SimpleNamespace(id=next(_next_sid))

    monkeypatch.setattr(res_mod, "ProjectDatastore", _FakeWsDs)
    monkeypatch.setattr(res_mod, "ProjectMemberDatastore", _FakeMemberDs)
    monkeypatch.setattr(
        res_mod, "fs_registry", SimpleNamespace(project_cwd=lambda *a, **k: tmp_path)
    )
    monkeypatch.setattr(res_mod, "_member_agent_config", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(res_mod, "build_member_session", _fake_build)
    monkeypatch.setattr(res_mod, "_credential_gap", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(res_mod, "_provider_resolver_deps", lambda _db: {})
    monkeypatch.setattr(
        res_mod, "resolve_agent_display_name", _as_async(lambda _w, slug, _u: f"Agent {slug}")
    )
    # planning resolves display names through its own imports (module-level
    # ``from ... import``), so stub those bindings too.
    monkeypatch.setattr(
        planning, "resolve_agent_display_name", _as_async(lambda _w, slug, _u: f"Agent {slug}")
    )
    monkeypatch.setattr(
        planning,
        "resolve_agent_display_names",
        _as_async(lambda _w, slugs, _u: {s: f"Agent {s}" for s in slugs}),
    )
    monkeypatch.setattr(
        launcher_mod.kernel_client, "create_session", _as_async(lambda *_a, **_k: None)
    )
    monkeypatch.setattr(kernel_client_mod, "get_session", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(kernel_client_mod, "set_mode", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(
        launcher_mod,
        "project_index",
        SimpleNamespace(record=_as_async(lambda *_a, **_k: None)),
    )
    # draft_task needs a project row + task file path via lifecycle namespace too.
    import valuz_agent.modules.projects.datastore as ws_src

    monkeypatch.setattr(ws_src, "ProjectDatastore", _FakeWsDs)
    monkeypatch.setattr(
        lc_mod,
        "fs_registry",
        SimpleNamespace(
            project_cwd=lambda *a, **k: tmp_path,
            task_path=lambda cwd, tid, slug: tmp_path / f"{tid}.md",
        ),
    )

    from valuz_agent.modules.sessions import project_index as pi_mod

    monkeypatch.setattr(pi_mod, "record", _as_async(lambda *_a, **_k: None))

    async def _run() -> None:
        orch = TaskOrchestrator()
        monkeypatch.setattr(orch._actor, "run_actor_loop", _as_async(lambda **_k: None))
        # session creation goes through the launcher now — same shared
        # kernel_client module object, patched once.
        from valuz_agent.modules.tasks.launcher import (
            kernel_client as launch_kernel,
        )

        monkeypatch.setattr(
            launch_kernel, "create_session", _as_async(lambda *_a, **_k: None)
        )

        # 1) draft → 2) plan → 3) commit
        row = await orch.lifecycle.draft_task(
            project_id="w1",
            goal="build the thing",
            lead_agent_slug="lead",
            originating_session_id="chat-1",
            user_id=OWNER,
        )
        task_id = row.id
        await planning.plan_task(
            task_id=task_id,
            project_id="w1",
            user_id=OWNER,
            lead_session_id="chat-1",
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
        res = await orch.lifecycle.commit_task(
            task_id=task_id, project_id="w1", caller_session_id="chat-1", user_id=OWNER
        )
        assert res.get("status") == "active", res

        # 4) dispatch the planned node (async spawn, actor stubbed)
        res = await orch.dispatcher.dispatch_async(
            task_id=task_id,
            project_id="w1",
            lead_session_id="lead-sess-1",
            subtask_key="a",
            user_id=OWNER,
        )
        assert res.get("status") == "dispatched", res

        # 5) member reports done → in_review, 6) lead approves
        await planning.mark_in_review(
            task_id=task_id,
            project_id="w1",
            member_session_id="member-sess-1",
            user_id=OWNER,
        )
        res = await planning.review_subtask(
            task_id=task_id,
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess-1",
            decision="approve",
            subtask_key="a",
        )
        assert res.get("decision") == "approve", res

        # 7) finish
        res = await orch.finalization.finish_task(
            task_id=task_id,
            project_id="w1",
            lead_session_id="lead-sess-1",
            summary="done",
            user_id=OWNER,
        )
        assert res.get("ok") is True, res
        await asyncio.sleep(0)  # drain the stubbed actor spawn

    asyncio.run(_run())

    # ---- the golden: exact ordered event-type sequence ----
    db = db_factory()
    try:
        rows = (
            db.execute(select(TaskEventRow).order_by(TaskEventRow.sequence)).scalars().all()
        )
    finally:
        db.close()
    assert [e.type for e in rows] == [
        "task_drafted",
        "task_planned",
        "task_plan_update",  # plan_task projects the fresh plan to the panel
        "committed",
        "subtask_spawned",
        "task_plan_update",  # node → in_progress (mark_node_dispatched)
        "task_plan_update",  # node → in_review (member idle)
        # approve persists the plan FIRST (CAS write + snapshot), then appends
        # the review events — an approval that loses the CAS race must not have
        # already announced itself.
        "task_plan_update",  # node → done (approve unlocks dependents)
        "subtask_reviewed",
        "subtask_completed",
        "task_completed",
    ], [e.type for e in rows]

    # ---- payload fields the frontend reads ----
    by_type = {}
    for e in rows:
        by_type.setdefault(e.type, e)
    spawned = by_type["subtask_spawned"].payload
    assert spawned["subtask_key"] == "a"
    assert spawned["agent_name"] == "Agent worker"
    completed_sub = by_type["subtask_completed"].payload
    assert completed_sub["subtask_key"] == "a"
    assert "agent_name" in completed_sub
    finished = by_type["task_completed"].payload
    assert finished["summary"] == "done" and finished["artifacts"] == []



def test_task_finalized_wiring_triggers_memory_on_completed(monkeypatch) -> None:
    """Event-first memory trigger: the scheduler subscribes to task.finalized
    (wired at boot) and graduates ONLY real completions — stopped/blocked
    terminals must not fire an extraction."""
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.memory import scheduler as sched_mod
    from valuz_agent.modules.tasks.events import TASK_FINALIZED

    sched_mod.wire_task_finalized_trigger()

    notified: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sched_mod.task_finish_scheduler,
        "notify_finished",
        lambda tid, uid: notified.append((tid, uid)),
    )
    event_bus.publish(TASK_FINALIZED, task_id="t-x", owner_user_id=OWNER, status="completed")
    event_bus.publish(TASK_FINALIZED, task_id="t-y", owner_user_id=OWNER, status="stopped")
    event_bus.publish(TASK_FINALIZED, task_id="t-z", owner_user_id=OWNER, status="blocked")
    assert notified == [("t-x", OWNER)]


# ---------------------------------------------------------------------------
# Credential-gap dispatch: no ghost session_id + commit roster pre-flight
# ---------------------------------------------------------------------------


def test_dispatch_credential_gap_event_carries_no_session_id(
    db_factory, tmp_path, monkeypatch
) -> None:
    """A dispatch failing the credential pre-flight appends ``subtask_failed``
    WITHOUT a session_id: the kernel session is never created on this path, so
    an id here becomes a clickable timeline link to a session that 404s
    ("Session not found.")."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import resolution as res_mod

    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )

    class _FakeWsDs:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _uid, _wid):
            return SimpleNamespace(id="w1", kind="project", root_path=str(tmp_path), name="W1")

        async def get_context(self, _uid, _wid):
            return None

    gap = "agent 'worker' has no usable model provider for model 'm1' (runtime 'claude_agent')."

    async def _gap(_session, agent_slug, **_k):
        return gap if agent_slug == "worker" else None

    monkeypatch.setattr(res_mod, "ProjectDatastore", _FakeWsDs)
    monkeypatch.setattr(
        res_mod, "fs_registry", SimpleNamespace(project_cwd=lambda *a, **k: tmp_path)
    )
    monkeypatch.setattr(
        res_mod,
        "build_member_session",
        _as_async(lambda **_k: SimpleNamespace(id="member-sess-1")),
    )
    monkeypatch.setattr(res_mod, "_credential_gap", _gap)
    monkeypatch.setattr(res_mod, "_provider_resolver_deps", lambda _db: {})
    monkeypatch.setattr(
        res_mod, "resolve_agent_display_name", _as_async(lambda _w, slug, _u: f"Agent {slug}")
    )

    orch = TaskOrchestrator()
    res = asyncio.run(
        orch.dispatcher.dispatch_async(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-sess",
            subtask_key="a",
            user_id=OWNER,
        )
    )
    assert res.get("status") == "failed", res
    assert res.get("error") == gap

    db = db_factory()
    try:
        rows = db.execute(select(TaskEventRow).filter_by(type="subtask_failed")).scalars().all()
    finally:
        db.close()
    assert len(rows) == 1, [r.type for r in rows]
    assert rows[0].session_id is None  # never-created session must not be linkable
    # The human line lives in ``summary`` for EVERY subtask_failed now — this
    # path used to be the odd one out, writing it as ``error`` while the other
    # two wrote ``summary``, so the timeline detail differed by internal path.
    payload = rows[0].payload or {}
    assert payload["summary"] == gap
    assert payload["reason"] == "dispatch_failed"
    assert payload["subtask_key"] == "a"


def test_commit_task_blocks_on_member_provider_preflight(db_factory, tmp_path, monkeypatch) -> None:
    """``commit_task`` runs the roster provider pre-flight after the lead's own
    credential check: an unconfigured member fails the commit with an
    actionable aggregated error instead of surfacing minutes later as a
    dispatch-time ``subtask_failed``."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import resolution as res_mod

    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="chat",
            subtasks=[{"key": "a", "title": "A", "agent": "worker"}],
        )
    )
    db = db_factory()
    try:
        row = db.execute(select(TaskRow).filter_by(id="t1")).scalars().one()
        row.status = "draft"
        db.commit()
    finally:
        db.close()

    class _FakeWsDs:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _uid, _wid):
            return SimpleNamespace(id="w1", kind="project", root_path=str(tmp_path), name="W1")

        async def get_context(self, _uid, _wid):
            return None

    class _FakeMemberDs:
        def __init__(self, _db):
            pass

        async def get(self, _uid, _wid, _slug):
            return SimpleNamespace(agent_slug="lead")

    member_gap = "agent 'worker' has no usable model provider for model 'm1' (runtime 'codex')."

    monkeypatch.setattr(res_mod, "ProjectDatastore", _FakeWsDs)
    monkeypatch.setattr(res_mod, "ProjectMemberDatastore", _FakeMemberDs)
    monkeypatch.setattr(
        res_mod, "fs_registry", SimpleNamespace(project_cwd=lambda *a, **k: tmp_path)
    )
    monkeypatch.setattr(res_mod, "_member_agent_config", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(
        res_mod,
        "build_member_session",
        _as_async(lambda **_k: SimpleNamespace(id="lead-sess-1")),
    )
    monkeypatch.setattr(res_mod, "_credential_gap", _as_async(lambda *_a, **_k: None))
    monkeypatch.setattr(res_mod, "_provider_resolver_deps", lambda _db: {})
    # The sweep is the unit under test's collaborator — stub the singleton the
    # lifecycle calls (instance attr shadows the bound method).
    monkeypatch.setattr(
        res_mod.task_session_resolver,
        "preflight_member_providers",
        _as_async(lambda _db, **_k: [member_gap]),
    )

    orch = TaskOrchestrator()
    res = asyncio.run(
        orch.lifecycle.commit_task(
            task_id="t1",
            project_id="w1",
            caller_session_id="chat",
            user_id=OWNER,
        )
    )
    assert "error" in res, res
    assert "model configuration check failed" in res["error"]
    assert member_gap in res["error"]
    # The draft must remain committable after the user fixes the provider.
    assert _task_row(db_factory).status == "draft"


# ---------------------------------------------------------------------------
# notify_lead_member_idle — the member -> lead timeline event
# ---------------------------------------------------------------------------


def test_member_report_emits_subtask_reported_with_agent_name(
    db_factory, tmp_path, monkeypatch
) -> None:
    """A member finishing a round writes ``subtask_reported``, not ``subtask_message``.

    The two directions shared one type (split only by ``payload.direction``)
    until 2026-07, so the timeline could not tell "the lead said something"
    from "a member finished a round" without reading the payload. This also
    pins the ``agent_name`` stamp: the frontend renders it straight from the
    payload rather than joining the slug against a racy async members list.
    """
    from valuz_agent.modules.tasks import coordination as coord_mod
    from valuz_agent.modules.tasks import manifest as manifest_mod

    _make_task(db_factory, tmp_path)
    db = db_factory()
    try:
        db.add(
            TaskSessionRow(
                user_id=OWNER,
                id="run-mem",
                project_id="w1",
                task_id="t1",
                session_id="mem-sess",
                agent_slug="researcher",
                sequence=1,
                kind="subtask",
                status="active",
                subtask_key="A",
                dispatched_by="lead-sess",
                run_dir=str(tmp_path),
            )
        )
        db.commit()
    finally:
        db.close()

    async def _fake_manifest(*_a, **_k):
        return {"session_id": "mem-sess", "status": "idle", "summary": "did the thing"}

    async def _fake_name(_ws, slug, _uid):
        return f"Name of {slug}"

    monkeypatch.setattr(manifest_mod, "collect_manifest", _fake_manifest)
    monkeypatch.setattr(coord_mod, "resolve_agent_display_name", _fake_name)

    orch = TaskOrchestrator()
    asyncio.run(orch.coordination.notify_lead_member_idle("mem-sess", "idle", user_id=OWNER))

    assert "subtask_reported" in _events(db_factory)
    assert "subtask_message" not in _events(db_factory)
    payload = _event_payload(db_factory, "subtask_reported")
    assert payload["agent_name"] == "Name of researcher"
    assert payload["summary"] == "did the thing"
    assert payload["status"] == "idle"


def test_review_subtask_rejects_when_task_vanishes_between_phases(
    db_factory, tmp_path, monkeypatch
) -> None:
    """``review_subtask`` reads in TWO units of work — guard both.

    Phase 1 resolves the node on a read-only UoW; phase 2 re-reads on a
    writable one. Only phase 1 checked for a missing task, so a task deleted in
    between crashed phase 2 with AttributeError on ``None.plan`` (a 500 out of
    the tool) instead of returning the same actionable "not found".
    """
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )

    # Delete the task after phase 1 has resolved the node: patch the datastore
    # read so the SECOND call (phase 2) reports the row gone.
    calls = {"n": 0}
    real = planning.TaskDatastore.get_task_by_project

    async def _vanish_after_first(self, user_id, project_id, task_id):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] > 1:
            return None
        return await real(self, user_id, project_id, task_id)

    monkeypatch.setattr(
        planning.TaskDatastore, "get_task_by_project", _vanish_after_first
    )

    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-sess",
            decision="approve",
            subtask_key="a",
            user_id=OWNER,
        )
    )
    assert "not found" in res["error"]


def test_list_tasks_counts_every_settled_run_as_done(db_factory, tmp_path) -> None:
    """``runs_done`` must count archived/rejected runs, not a status that
    doesn't exist.

    The run status enum is active | paused | completed | rejected | archived.
    This counted ``("completed", "failed")`` — and ``failed`` is not one of
    them — so an errored run (archived) or a user-stopped one (rejected) never
    counted, and the progress reported to the agent read low forever.
    """
    from valuz_agent.modules.tasks import service as queries

    _make_task(db_factory, tmp_path)
    db = db_factory()
    try:
        for i, status in enumerate(
            ["completed", "archived", "rejected", "active"], start=1
        ):
            db.add(
                TaskSessionRow(
                    user_id=OWNER,
                    id=f"run-{i}",
                    project_id="w1",
                    task_id="t1",
                    session_id=f"s-{i}",
                    agent_slug="x",
                    sequence=i,
                    kind="subtask",
                    status=status,
                    subtask_key=f"k{i}",
                )
            )
        db.commit()
    finally:
        db.close()

    rows = asyncio.run(queries.list_tasks("w1", user_id=OWNER))
    assert rows[0]["runs"] == 4
    # completed + archived + rejected are settled; only the active one isn't.
    assert rows[0]["runs_done"] == 3


def test_plan_update_payload_is_a_self_contained_snapshot(db_factory, tmp_path) -> None:
    """``task_plan_update`` must carry everything a consumer renders from it.

    Regression for a silent contract drift: the backend emitted
    ``{"subtasks": ...}`` alone while ``PlanCardFeed`` read four keys off the
    payload and guarded with

        if ((payload.plan_version ?? 0) <= card.planVersion) return

    With the version missing, every event evaluated ``0 <= n`` and was
    DISCARDED — the live plan feed only ever updated from its initial fetch,
    and nothing moved on screen as the plan progressed. Nothing failed; it just
    quietly stopped working.

    These events go out over SSE, where a consumer cannot assume it saw the
    previous one, so the payload has to stand alone. Lock the shape.
    """
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    payload = _event_payload(db_factory, "task_plan_update")

    assert set(payload) == {
        "subtasks",
        "plan_version",
        "structural",
        "task_status",
        "title",
    }
    assert payload["plan_version"] == 1, "the CAS token is the consumer's dedup key"
    assert payload["structural"] is True, "plan_task re-specifies the plan document"
    assert payload["task_status"] == "active"
    assert payload["title"] == "T"
    # Nodes carry the resolved display name so the panel never has to join the
    # slug against an async members list.
    assert payload["subtasks"][0]["key"] == "a"
    assert "agent_name" in payload["subtasks"][0]


def test_plan_update_version_advances_with_every_plan_write(db_factory, tmp_path) -> None:
    """Consecutive snapshots must be distinguishable, or the consumer's
    ``version <= seen`` guard drops the newer one."""
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            add=[{"key": "b", "title": "B", "agent": "x"}],
        )
    )
    db = db_factory()
    try:
        versions = [
            (e.payload or {}).get("plan_version")
            for e in db.query(TaskEventRow)
            .filter_by(type="task_plan_update")
            .order_by(TaskEventRow.sequence)
            .all()
        ]
    finally:
        db.close()
    assert versions == [1, 2]


def test_every_subtask_failure_path_writes_the_same_payload_shape(
    db_factory, tmp_path
) -> None:
    """One event type, one shape — whichever internal path detected the failure.

    ``subtask_failed`` used to be emitted from three places with three
    different payloads: the heartbeat backstop wrote {agent_name, subtask_key,
    status, summary, reason}, dispatch wrote {agent, agent_name, status, error}
    with no key and no summary, and the actor-loop finalize spread a whole
    manifest. The timeline renderer falls back to a ``text|summary|goal|error``
    lookup for this type, so the detail a user saw depended on which code path
    had failed — and no consumer could rely on any field being present.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.datastore import TaskEventDatastore
    from valuz_agent.modules.tasks.events import record_subtask_failed

    _make_task(db_factory, tmp_path)

    async def _emit(reason: str) -> None:
        async with async_unit_of_work() as db:
            await record_subtask_failed(
                TaskEventDatastore(db),
                user_id=OWNER,
                project_id="w1",
                task_id="t1",
                session_id="s-1",
                agent_slug="coder",
                agent_name="Coder",
                subtask_key="a",
                summary="it broke",
                reason=reason,
            )

    for reason in ("dispatch_failed", "heartbeat_detected", "run_error"):
        asyncio.run(_emit(reason))

    db = db_factory()
    try:
        rows = (
            db.execute(select(TaskEventRow).filter_by(type="subtask_failed"))
            .scalars()
            .all()
        )
    finally:
        db.close()

    assert len(rows) == 3
    expected_keys = {
        "agent",
        "agent_name",
        "subtask_key",
        "status",
        "summary",
        "reason",
        "artifacts",
    }
    for row in rows:
        assert set(row.payload) == expected_keys, "every path must fill every key"
        assert row.payload["status"] == "failed"
    # Only the machine-readable cause differs between paths.
    assert {r.payload["reason"] for r in rows} == {
        "dispatch_failed",
        "heartbeat_detected",
        "run_error",
    }


def test_finalize_actor_skips_parked_member_run(db_factory, tmp_path, monkeypatch) -> None:
    """stop_task parked this run (→paused) while its member sat idle; the
    member's shutdown loop-exit must NOT stamp it completed. Recovery only
    resumes active/paused runs, so the overwrite makes the session invisible
    on resume and the node is re-dispatched as a brand-new session — session
    continuity and record truth both lost."""
    from valuz_agent.modules.sessions import run_orchestrator as run_orch
    from valuz_agent.modules.tasks import manifest as manifest_mod

    _make_task(db_factory, tmp_path)
    _make_member_run(db_factory)
    db = db_factory()
    try:
        db.query(TaskSessionRow).filter_by(session_id="mem-1").update({"status": "paused"})
        db.commit()
    finally:
        db.close()

    async def _noop(*_a: object, **_k: object) -> None: ...

    async def _manifest(*_a: object, **_k: object) -> dict[str, str]:
        return {"session_id": "mem-1", "status": "idle", "summary": ""}

    monkeypatch.setattr(run_orch, "_finalize_session", _noop)
    monkeypatch.setattr(manifest_mod, "collect_manifest", _manifest)

    orch = TaskOrchestrator()
    asyncio.run(
        orch.finalization.finalize_actor(
            session_id="mem-1",
            last_content="",
            final_status="idle",
            role="subtask",
            task_id="t1",
            project_id="w1",
            via_shutdown=True,
            user_id=OWNER,
        )
    )

    db = db_factory()
    try:
        run = db.query(TaskSessionRow).filter_by(session_id="mem-1").one()
        assert run.status == "paused", "a parked run must survive the loop exit untouched"
        assert run.ended_at is None
    finally:
        db.close()


def test_stop_member_parks_the_run_the_member_reads(db_factory, tmp_path) -> None:
    """The kernel interrupt only reaches a member MID-TURN. An idle member
    (parked on its mailbox between turns) must still exit immediately on
    stop_member — without the shutdown message it sat out its full 10-minute
    idle TTL after the user already cancelled it."""
    from valuz_agent.modules.tasks import mailbox_store

    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")])
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"sB"})

    async def _fake_interrupt(sid: str, user_id: str | None = None) -> None: ...

    orch._recovery._interrupt_kernel_session = _fake_interrupt  # type: ignore[method-assign]
    try:
        assert asyncio.run(orch.recovery.stop_member("sB", user_id=OWNER)) is True
        # The member learns it was cancelled from its OWN run row, which this
        # call parks. A queued message could only have reached it while its
        # loop happened to share this process — which is why the stop that
        # ends a member was the one least able to cross one.
        assert not asyncio.run(mailbox_store.has_pending("sB")), "a stop is not a message"
        assert _runs(db_factory)["sB"] == "rejected", (
            "and the fact it reads must be written before we return"
        )
    finally:
        pass


def test_review_refuses_never_dispatched_node(db_factory, tmp_path) -> None:
    """Approving a ``planned`` node would skip its work entirely — the
    transition table refuses planned → done and the lead gets an actionable
    error instead of a silently 'done' node."""
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            decision="approve",
            subtask_key="a",
        )
    )
    assert "error" in res and "illegal subtask transition" in res["error"]
    from valuz_agent.modules.tasks.plan import TaskPlan

    db = db_factory()
    try:
        node = TaskPlan.from_dict(db.query(TaskRow).filter_by(id="t1").one().plan).get("a")
        assert node is not None and node.status == "planned"
    finally:
        db.close()


def test_review_refuses_halted_task(db_factory, tmp_path) -> None:
    """A paused/stopped task's plan must not move under review — same
    writable-status rationale as plan_commands."""
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x", "status": "in_review"}],
        )
    )
    db = db_factory()
    try:
        db.query(TaskRow).filter_by(id="t1").update({"status": "paused"})
        db.commit()
    finally:
        db.close()
    res = asyncio.run(
        planning.review_subtask(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            decision="approve",
            subtask_key="a",
        )
    )
    assert "error" in res and "review applies to an active task" in res["error"]


def test_modify_plan_cannot_stamp_failed(db_factory, tmp_path) -> None:
    """``failed`` is not in the unresolved set, so a failed-stamped node passes
    the finish_task(completed) guard — planned work skipped by relabeling.
    The table makes it unwritable through every door, modify_plan included."""
    _make_task(db_factory, tmp_path)
    asyncio.run(
        planning.plan_task(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            subtasks=[{"key": "a", "title": "A", "agent": "x"}],
        )
    )
    res = asyncio.run(
        planning.modify_plan(
            task_id="t1",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            update=[{"key": "a", "status": "failed"}],
        )
    )
    assert "error" in res and "illegal subtask transition" in res["error"]


def test_recover_crashed_members_resolves_pending_from_the_store(
    db_factory, tmp_path, monkeypatch
) -> None:
    """The between-turns backstop, end to end against the real store.

    This is the half that was missing. `_heartbeat_pending` only ever ran while
    the lead sat INSIDE `await_members`; parked on its mailbox between turns it
    consulted nothing. So a `member_done` posted into another process's queue —
    which is what happens whenever `dispatch` lands on a different host process
    than the lead loop — was simply never seen, the lead slept out its whole
    idle TTL, and auto-finalize blocked the task for "unresolved subtasks" with
    every member already finished.

    Here the mailbox is not involved at all: the plan says `in_progress`, the
    kernel says the session is done, and the lead must learn it from the store.
    """
    from types import SimpleNamespace

    _seed_lead_and_members(
        db_factory,
        tmp_path,
        members=[("B", "frontend", "sB", "in_progress"), ("C", "qa", "sC", "in_progress")],
    )
    sessions = {
        "sB": SimpleNamespace(status="idle", stop_reason={"type": "end_turn"}),
        "sC": SimpleNamespace(status="running", stop_reason=None),
    }
    monkeypatch.setattr(
        kernel_client_mod, "get_session", _as_async(lambda _uid, sid: sessions.get(sid))
    )
    from valuz_agent.modules.tasks import manifest as manifest_mod

    monkeypatch.setattr(
        manifest_mod,
        "collect_manifest",
        _as_async(lambda *a, **k: {"status": "completed", "summary": "ok"}),
    )
    orch = TaskOrchestrator()

    recovered = asyncio.run(
        orch.coordination.recover_crashed_members(
            task_id="t1", project_id="w1", user_id=OWNER
        )
    )
    assert recovered == 1, "one member had died; the other is still running"

    # It ENQUEUES rather than returning a batch, so the result travels the same
    # path as every other message and there is nothing for a caller to park.
    msgs = asyncio.run(mailbox_store.drain("lead-s", limit=10))
    assert [m.kind for m in msgs] == ["member_done"]
    assert msgs[0].from_session == "sB"
    assert msgs[0].payload["status"] == "completed"
    # The one still running is left alone — a backstop must not invent results.
    assert all(m.from_session != "sC" for m in msgs)


def test_recover_crashed_members_is_quiet_when_nothing_is_pending(
    db_factory, tmp_path, monkeypatch
) -> None:
    """A lead parked with no in-flight member must not pay for a kernel probe."""
    from types import SimpleNamespace

    _seed_lead_and_members(
        db_factory, tmp_path, members=[("B", "frontend", "sB", "done")]
    )
    probes: list[str] = []

    async def _get_session(_uid, sid):
        probes.append(sid)
        return SimpleNamespace(status="idle", stop_reason={"type": "end_turn"})

    monkeypatch.setattr(kernel_client_mod, "get_session", _get_session)
    orch = TaskOrchestrator()

    msgs = asyncio.run(
        orch.coordination.recover_crashed_members(
            task_id="t1", project_id="w1", user_id=OWNER
        )
    )
    assert msgs == []
    assert probes == []


# ---------------------------------------------------------------------------
# Duplicate member_done: the wasted-turn bug measured on qa.
# ---------------------------------------------------------------------------


def test_member_already_settled_says_yes_for_a_reviewed_member(
    db_factory, tmp_path
) -> None:
    """A member the lead already approved must not earn another turn.

    This is the qa case: the in-turn backstop synthesized the result while the
    member was still collecting its manifest, the lead reviewed and moved on,
    and the real ``member_done`` then arrived to wake it for work already done.
    """
    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "done")])
    orch = TaskOrchestrator()
    assert asyncio.run(
        orch.coordination.member_already_settled(
            task_id="t1", project_id="w1", member_session_id="sB", user_id=OWNER
        )
    )


def test_member_awaiting_review_is_not_settled(db_factory, tmp_path) -> None:
    """``in_review`` still owes the lead a review — waking it is the point."""
    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_review")])
    orch = TaskOrchestrator()
    assert not asyncio.run(
        orch.coordination.member_already_settled(
            task_id="t1", project_id="w1", member_session_id="sB", user_id=OWNER
        )
    )


def test_a_finished_task_settles_every_member(db_factory, tmp_path) -> None:
    """Both wasted turns on qa landed AFTER ``task_completed``.

    ``finish_task``'s shutdown was queued behind the stale results, and the
    mailbox is FIFO, so the loop burned a turn on each before reading it.
    """
    _seed_lead_and_members(
        db_factory, tmp_path, members=[("B", "frontend", "sB", "in_review")],
        task_status="completed",
    )
    orch = TaskOrchestrator()
    assert asyncio.run(
        orch.coordination.member_already_settled(
            task_id="t1", project_id="w1", member_session_id="sB", user_id=OWNER
        )
    )


def test_an_unknown_member_is_never_swallowed(db_factory, tmp_path) -> None:
    """No run row → surface it. Dropping is the dangerous direction here."""
    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")])
    orch = TaskOrchestrator()
    assert not asyncio.run(
        orch.coordination.member_already_settled(
            task_id="t1", project_id="w1", member_session_id="who-dis", user_id=OWNER
        )
    )


def test_the_backstop_lets_the_real_delivery_win_the_first_slice(
    db_factory, tmp_path, monkeypatch
) -> None:
    """Regression for the wasted turns: the backstop must not race the member.

    It reads durable state, so it sees a member as finished the moment its
    kernel session goes terminal — while that member is still inside
    ``notify_lead_member_idle`` collecting a manifest off the filesystem.
    Synthesizing there strands the real ``member_done`` in a mailbox nobody
    drains. One slice of grace hands the race to the real path; a member that
    truly died without delivering is still caught, one slice later.
    """
    from types import SimpleNamespace

    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")])
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(
            lambda _uid, sid: SimpleNamespace(status="idle", stop_reason={"type": "end_turn"})
        ),
    )
    from valuz_agent.modules.tasks import manifest as manifest_mod

    monkeypatch.setattr(
        manifest_mod, "collect_manifest",
        _as_async(lambda *a, **k: {"status": "completed", "summary": "ok"}),
    )
    monkeypatch.setattr("valuz_agent.modules.tasks.coordination._HEARTBEAT_S", 0.01)

    orch = TaskOrchestrator()
    hits: list[int] = []
    real = member_probe.heartbeat_pending

    async def _counting(**kw):
        hits.append(1)
        return await real(**kw)

    monkeypatch.setattr(member_probe, "heartbeat_pending", _counting)

    # A window of exactly one slice: the backstop must stay out of it.
    asyncio.run(
        orch.coordination.await_member_results(
            lead_session_id="lead-s", project_id="w1", task_id="t1",
            timeout_s=0.01, user_id=OWNER,
        )
    )
    assert hits == [], "the first slice belongs to the real delivery"



def test_the_backstop_still_fires_for_a_member_that_never_delivers(
    db_factory, tmp_path, monkeypatch
) -> None:
    """The grace above must not turn the backstop off — only make it second."""
    from types import SimpleNamespace

    _seed_lead_and_members(db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")])
    monkeypatch.setattr(
        kernel_client_mod,
        "get_session",
        _as_async(
            lambda _uid, sid: SimpleNamespace(status="idle", stop_reason={"type": "end_turn"})
        ),
    )
    from valuz_agent.modules.tasks import manifest as manifest_mod

    monkeypatch.setattr(
        manifest_mod, "collect_manifest",
        _as_async(lambda *a, **k: {"status": "completed", "summary": "ok"}),
    )
    monkeypatch.setattr("valuz_agent.modules.tasks.coordination._HEARTBEAT_S", 0.01)

    orch = TaskOrchestrator()
    out = asyncio.run(
        orch.coordination.await_member_results(
            lead_session_id="lead-s2", project_id="w1", task_id="t1",
            timeout_s=0.06, user_id=OWNER,
        )
    )
    assert out.get("collected"), "a member that never delivers must still be caught"


def test_finish_task_parks_members_that_are_still_running(db_factory, tmp_path) -> None:
    """A task cannot end while leaving its members running.

    A member reads its OWN run row to know it was stopped, and finish_task
    settles only the lead's. Without parking them here,
    ``finish_task(stopped, force=True)`` — the one path that deliberately ends
    a task while members are live — would leave every one of them running
    until its idle TTL.

    It used to queue a ``shutdown`` per member, which reached only the ones
    whose loops shared the process that ran finish_task.
    """
    _seed_lead_and_members(
        db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")]
    )
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"sB"})

    assert _runs(db_factory)["sB"] == "active", "precondition: the member is running"

    asyncio.run(
        orch.finalization.finish_task(
            task_id="t1",
            project_id="w1",
            lead_session_id="lead-s",
            summary="done enough",
            status="stopped",
            force=True,
            user_id=OWNER,
        )
    )

    assert _runs(db_factory)["sB"] == "paused", (
        "the member must be able to read that it was stopped — it has no other "
        "way to find out"
    )


def test_stop_task_parks_the_lead_run_as_well(db_factory, tmp_path) -> None:
    """A stopped task must not leave a run row claiming its lead is alive.

    The lead's loop leaves through the externally-managed exit, which skips
    finalize by design — the terminal state belongs to whoever stopped it — so
    nothing else ever settles this row. Observed on qa: a task `stopped` for
    twelve minutes, its lease correctly released, its lead run still `active`.
    Anything reading the run index for liveness rather than the lease saw a
    runner that had long since gone.
    """
    _seed_lead_and_members(
        db_factory, tmp_path, members=[("B", "frontend", "sB", "in_progress")]
    )
    orch = TaskOrchestrator()

    async def _no_interrupt(_sid: str, user_id: str | None = None) -> None: ...

    orch._recovery._interrupt_kernel_session = _no_interrupt  # type: ignore[method-assign]
    assert asyncio.run(orch.recovery.stop_task("t1", "w1", user_id=OWNER)) is True

    runs = _runs(db_factory)
    assert runs["sB"] == "paused", "members were already parked"
    assert runs["lead-s"] == "paused", (
        "and the lead must be too — nothing else will ever settle it"
    )
