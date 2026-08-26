"""TDD: orchestrator.update_deliverable — append deliverable_updated event.

Reuses the same db_factory / _make_task / _events fixtures pattern from
test_plan_orchestrator.py. The conftest sets current_user_id to
"local-test-owner" for all tests.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

OWNER = "local-test-owner"

# ---------------------------------------------------------------------------
# Shared db_factory / helpers (mirrors test_plan_orchestrator.py)
# ---------------------------------------------------------------------------




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


def _events(db_factory, project_id="w1", task_id="t1") -> list[TaskEventRow]:
    """Return all TaskEventRow objects in sequence order."""
    db = db_factory()
    try:
        return list(
            db.execute(
                select(TaskEventRow)
                .where(
                    TaskEventRow.project_id == project_id,
                    TaskEventRow.task_id == task_id,
                )
                .order_by(TaskEventRow.sequence)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()


def _event_types(db_factory, project_id="w1", task_id="t1") -> list[str]:
    return [e.type for e in _events(db_factory, project_id, task_id)]


# ---------------------------------------------------------------------------
# Fixtures: completed_task and active_task
# ---------------------------------------------------------------------------


@pytest.fixture()
def completed_task(db_factory, tmp_path):
    """Task with status='completed', seeded directly in the DB (no plan needed)."""
    _make_task(db_factory, tmp_path)
    # Set status to completed directly — avoids plan-completeness guard in finish_task.
    db = db_factory()
    try:
        row = db.execute(select(TaskRow).filter_by(id="t1")).scalars().one()
        row.status = "completed"
        db.commit()
    finally:
        db.close()
    return {"task_id": "t1", "project_id": "w1", "lead_session_id": "lead-sess"}


@pytest.fixture()
def active_task(db_factory, tmp_path):
    """Task created but NOT finished — status stays 'active'."""
    _make_task(db_factory, tmp_path)
    return {"task_id": "t1", "project_id": "w1", "lead_session_id": "lead-sess"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_update_deliverable_appends_event_on_completed_task(
    db_factory, completed_task
) -> None:
    """update_deliverable on a completed task appends a deliverable_updated event."""
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.finalization.update_deliverable(
            task_id=completed_task["task_id"],
            project_id=completed_task["project_id"],
            user_id=OWNER,
            lead_session_id=completed_task["lead_session_id"],
            summary="revised deliverable",
            artifacts=["report.md", "summary.pdf"],
        )
    )

    assert result == {"ok": True, "status": "updated"}

    rows = _events(db_factory)
    deliverable_events = [e for e in rows if e.type == "deliverable_updated"]
    assert len(deliverable_events) == 1, (
        f"Expected exactly 1 deliverable_updated event, got: {[e.type for e in rows]}"
    )

    evt = deliverable_events[0]
    assert evt.payload["summary"] == "revised deliverable"
    assert evt.payload["artifacts"] == ["report.md", "summary.pdf"]
    assert evt.actor == completed_task["lead_session_id"]
    assert evt.session_id == completed_task["lead_session_id"]


def test_update_deliverable_rejected_on_active_task(db_factory, active_task) -> None:
    """update_deliverable is rejected when the task is not yet completed."""
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.finalization.update_deliverable(
            task_id=active_task["task_id"],
            project_id=active_task["project_id"],
            user_id=OWNER,
            lead_session_id=active_task["lead_session_id"],
            summary="premature update",
        )
    )

    assert result.get("status") == "rejected"
    assert "completed" in result.get("error", "").lower(), (
        f"Expected 'completed' in error message, got: {result.get('error')}"
    )
    # No deliverable_updated event should be appended.
    assert "deliverable_updated" not in _event_types(db_factory)


def test_update_deliverable_rejected_when_task_not_found(db_factory) -> None:
    """update_deliverable is rejected when no task matches the id."""
    orch = TaskOrchestrator()
    result = asyncio.run(
        orch.finalization.update_deliverable(
            task_id="does-not-exist",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead-sess",
            summary="x",
        )
    )

    assert result.get("status") == "rejected"
    assert "not found" in result.get("error", "").lower(), (
        f"Expected 'not found' in error message, got: {result.get('error')}"
    )


# ---------------------------------------------------------------------------
# MCP tool declaration tests
# ---------------------------------------------------------------------------
# The handler closures live inside build_task_tool_defs() and are not directly
# accessible. The lead-gate path (_check_lead_gate) is shared by all sibling
# lead-only tools (finish_task, dispatch, review_subtask, etc.) and is covered
# by those tools' tests. Here we verify the static declaration surface instead.


def test_update_deliverable_tool_is_declared() -> None:
    """update_deliverable ships in the lead toolset."""
    from valuz_agent.modules.tasks.tools.declarations import (
        DISPATCH_TOOL_DECLARATIONS,
        UPDATE_DELIVERABLE_TOOL_NAME,
    )

    decl_names = {d.name for d in DISPATCH_TOOL_DECLARATIONS}
    assert UPDATE_DELIVERABLE_TOOL_NAME in decl_names, (
        "update_deliverable ToolDef must appear in DISPATCH_TOOL_DECLARATIONS"
    )


def test_update_deliverable_declaration_has_required_summary() -> None:
    """The update_deliverable ToolDef schema requires 'summary'."""
    from valuz_agent.modules.tasks.tools.declarations import (
        DISPATCH_TOOL_DECLARATIONS,
        UPDATE_DELIVERABLE_TOOL_NAME,
    )

    decl = next(d for d in DISPATCH_TOOL_DECLARATIONS if d.name == UPDATE_DELIVERABLE_TOOL_NAME)
    required = decl.parameters.get("required", [])
    assert "summary" in required, (
        f"update_deliverable schema must have 'summary' in required; got: {required}"
    )


def test_update_deliverable_is_lead_only() -> None:
    """update_deliverable must never reach a chat session's toolset.

    It rewrites a finished task's deliverable card, so only that task's lead
    may call it. Audience is decided by tuple membership (``boot/steps.py``
    partitions the toolkit MCP server by these two lists).
    """
    from valuz_agent.modules.tasks.tools.declarations import (
        ORCHESTRATION_TOOL_DECLARATIONS,
        UPDATE_DELIVERABLE_TOOL_NAME,
    )

    assert UPDATE_DELIVERABLE_TOOL_NAME not in {
        d.name for d in ORCHESTRATION_TOOL_DECLARATIONS
    }, "update_deliverable must not be advertised to chat sessions"
