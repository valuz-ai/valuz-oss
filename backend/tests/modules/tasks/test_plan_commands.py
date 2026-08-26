"""Plan writes have ONE authorized door, and both transports go through it.

The MCP tool path used to resolve the caller's session and run
``gate.check_plan_writer_gate``; the REST path checked only that the task
belonged to the requesting user and called ``planning`` directly. Since
``planning`` carries no status guard of its own, that second door let a
*completed* or *paused* task's plan be rewritten, and let an *active* task's
plan be rewritten by someone who is not its lead.

These tests are the acceptance criteria for closing that: the rules, and the
two callers agreeing about them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from valuz_agent.modules.tasks import plan_commands
from valuz_agent.modules.tasks.models import TaskRow

OWNER = "local-test-owner"
OTHER = "someone-else"
SUBTASKS = [{"key": "a", "title": "A", "agent": "x"}]


def _seed(db_factory, *, status: str, task_id: str = "t1", plan: dict | None = None) -> None:
    db = db_factory()
    try:
        db.add(
            TaskRow(
                user_id=OWNER,
                id=task_id,
                project_id="w1",
                file_path=f"/tmp/{task_id}.md",
                title="T",
                goal="g",
                status=status,
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan or {},
                plan_version=1 if plan else 0,
                metadata_={"originating_session_id": "chat-1"},
            )
        )
        db.commit()
    finally:
        db.close()


def _agent_session(monkeypatch, **valuz: object) -> None:
    """Bind a fake caller session for AgentCaller authorization."""

    class _Reader:
        async def get_session(self, _uid: str, sid: str):
            return SimpleNamespace(id=sid, project_id="w1", metadata={"valuz": valuz})

    monkeypatch.setattr(plan_commands, "data_reader", lambda: _Reader())


# ---------------------------------------------------------------------------
# Status rules — identical for both callers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["paused", "stopped", "completed", "blocked", "abandoned"])
def test_a_settled_task_plan_is_read_only_for_the_owner(db_factory, status: str) -> None:
    """The REST hole: these all used to be writable straight through.

    A finished or halted task's plan is a record of what happened. Rewriting it
    would make the timeline describe work that never ran that way.
    """
    _seed(db_factory, status=status)
    res = asyncio.run(
        plan_commands.plan_task(
            plan_commands.OwnerCaller(OWNER), task_id="t1", subtasks=SUBTASKS
        )
    )
    assert status in res["error"] and "read-only" in res["error"]


@pytest.mark.parametrize("status", ["paused", "completed"])
def test_a_settled_task_plan_is_read_only_for_an_agent_too(
    db_factory, monkeypatch, status: str
) -> None:
    """Same answer through the other door — that is the point of the exercise."""
    _seed(db_factory, status=status)
    _agent_session(monkeypatch, run_kind="lead", task_id="t1", project_id="w1")
    res = asyncio.run(
        plan_commands.plan_task(
            plan_commands.AgentCaller("lead-sess", OWNER), task_id="t1", subtasks=SUBTASKS
        )
    )
    assert "read-only" in res["error"]


# ---------------------------------------------------------------------------
# Who may write — the agent gate still applies to agents
# ---------------------------------------------------------------------------


def test_active_task_plan_is_lead_only_for_agents(db_factory, monkeypatch) -> None:
    """A chat session may not edit a running task's plan; it must inject and
    let the lead decide (VALUZ-CHATPLAN D6 strict)."""
    _seed(db_factory, status="active")
    _agent_session(monkeypatch, project_id="w1")  # a chat session: no run_kind
    res = asyncio.run(
        plan_commands.modify_plan(
            plan_commands.AgentCaller("chat-1", OWNER), task_id="t1", add=SUBTASKS
        )
    )
    assert "lead-owned" in res["error"]


def test_draft_task_plan_is_writable_by_a_project_mate(db_factory, monkeypatch) -> None:
    _seed(db_factory, status="draft")
    _agent_session(monkeypatch, project_id="w1")
    res = asyncio.run(
        plan_commands.plan_task(
            plan_commands.AgentCaller("chat-1", OWNER), task_id="t1", subtasks=SUBTASKS
        )
    )
    assert "error" not in res
    assert res["current_version"] == 1


def test_owner_may_edit_a_running_plan_but_must_pass_the_version(db_factory) -> None:
    """A human is not a lead session, so the role half of the agent gate cannot
    apply to them — the CAS token is what protects a running task from a
    mid-air human edit, so it is required rather than optional here."""
    _seed(db_factory, status="active", plan={"subtasks": SUBTASKS})

    blind = asyncio.run(
        plan_commands.modify_plan(
            plan_commands.OwnerCaller(OWNER), task_id="t1", update=[{"key": "a", "title": "A2"}]
        )
    )
    assert "expected_version is required" in blind["error"]
    assert blind["current_version"] == 1

    ok = asyncio.run(
        plan_commands.modify_plan(
            plan_commands.OwnerCaller(OWNER),
            task_id="t1",
            update=[{"key": "a", "title": "A2"}],
            expected_version=1,
        )
    )
    assert "error" not in ok
    assert ok["current_version"] == 2


def test_stale_version_is_a_conflict_not_a_silent_overwrite(db_factory) -> None:
    _seed(db_factory, status="active", plan={"subtasks": SUBTASKS})
    res = asyncio.run(
        plan_commands.modify_plan(
            plan_commands.OwnerCaller(OWNER),
            task_id="t1",
            update=[{"key": "a", "title": "A2"}],
            expected_version=0,  # what a stale reader would have seen
        )
    )
    assert res["error"] == "PLAN_VERSION_CONFLICT"
    assert res["current_version"] == 1


# ---------------------------------------------------------------------------
# Ownership + the committed-plan rule
# ---------------------------------------------------------------------------


def test_another_owners_task_is_indistinguishable_from_a_missing_one(db_factory) -> None:
    _seed(db_factory, status="draft")
    res = asyncio.run(
        plan_commands.plan_task(
            plan_commands.OwnerCaller(OTHER), task_id="t1", subtasks=SUBTASKS
        )
    )
    assert "not found" in res["error"]


def test_a_committed_plan_cannot_be_re_planned_from_either_door(db_factory) -> None:
    """This guard used to live in the MCP handler alone, so REST could wipe a
    running task's plan and lay down a fresh one."""
    _seed(db_factory, status="active", plan={"subtasks": SUBTASKS})
    res = asyncio.run(
        plan_commands.plan_task(
            plan_commands.OwnerCaller(OWNER), task_id="t1", subtasks=SUBTASKS
        )
    )
    assert "already has a committed plan" in res["error"]


# ---------------------------------------------------------------------------
# Reads stay looser than writes
# ---------------------------------------------------------------------------


def test_owner_can_read_the_plan_of_a_settled_task(db_factory) -> None:
    """Read-only is read-ONLY, not invisible — the detail page still renders a
    finished task's plan."""
    _seed(db_factory, status="completed", plan={"subtasks": SUBTASKS})
    res = asyncio.run(plan_commands.get_plan(plan_commands.OwnerCaller(OWNER), task_id="t1"))
    assert "error" not in res
    assert [n["key"] for n in res["subtasks"]] == ["a"]


def test_agent_outside_the_project_cannot_read(db_factory, monkeypatch) -> None:
    _seed(db_factory, status="active", plan={"subtasks": SUBTASKS})
    _agent_session(monkeypatch, project_id="other-ws")

    class _Reader:
        async def get_session(self, _uid: str, sid: str):
            return SimpleNamespace(id=sid, project_id="other-ws", metadata={"valuz": {}})

    monkeypatch.setattr(plan_commands, "data_reader", lambda: _Reader())
    res = asyncio.run(
        plan_commands.get_plan(plan_commands.AgentCaller("chat-x", OWNER), task_id="t1")
    )
    assert "does not match" in res["error"]
