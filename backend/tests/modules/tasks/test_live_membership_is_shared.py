"""Live membership is a query, not a memory — the last process-local state to go.

"Does this task still have members running?" gated three decisions. It used to
be answered from a dict on the orchestrator, filled in by whichever process
served the member's ``dispatch`` HTTP call. Every OTHER process saw a task with
no members at all, and ``dispatch`` goes through the load balancer — so on a
four-process deployment the wrong answer was the common one.

Two of the three readers had the plan as a second opinion and degraded safely.
The third, ``finish_task(stopped)``'s live-member guard, had none: it simply did
not fire, and a lead could kill a task out from under members it had dispatched
— from any process but theirs. Its error branch then queried the database for
the very list it had just failed to consult.

The answer now comes from the run rows, which ``dispatch`` writes before it
returns. These tests seed those rows WITHOUT going through dispatch, which is
exactly what "another process dispatched this member" looks like from here.

This file replaces ``test_spawn_atomicity.py``, whose subject — a spawn/halt
race over the shared set — cannot occur once there is no shared set. Its
structural guards (both halves must stay plain ``def``) protected an invariant
that no longer exists.
"""

# ruff: noqa: I001
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.datastore import TaskSessionDatastore
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

OWNER = "local-test-owner"
TASK = "t-shared"
LEAD = "lead-shared"


async def _seed(*, member_status: str | None, plan: dict | None = None) -> None:
    """A task with a lead, and optionally one member run in *member_status*.

    Nothing here goes through ``dispatch``: these rows stand for work a
    DIFFERENT process started. That is the whole point — the old registry could
    only ever see members born in its own process.
    """
    async with async_unit_of_work() as db:
        db.add(
            TaskRow(
                id=TASK,
                user_id=OWNER,
                project_id="w1",
                file_path="/tmp/t.md",
                title="t",
                goal="g",
                status="active",
                created_by="user",
                lead_agent_slug="lead",
                current_holder=LEAD,
                plan=plan if plan is not None else {},
            )
        )
        db.add(
            TaskSessionRow(
                user_id=OWNER,
                project_id="w1",
                task_id=TASK,
                session_id=LEAD,
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        if member_status is not None:
            db.add(
                TaskSessionRow(
                    user_id=OWNER,
                    project_id="w1",
                    task_id=TASK,
                    session_id="mem-elsewhere",
                    agent_slug="worker",
                    sequence=1,
                    kind="subtask",
                    subtask_key="s1",
                    status=member_status,
                )
            )


@pytest.mark.asyncio
async def test_a_member_started_elsewhere_reads_as_live(db_factory) -> None:
    """The base fact every other test here rests on."""
    await _seed(member_status="active")
    async with async_unit_of_work(commit=False) as db:
        ds = TaskSessionDatastore(db)
        assert await ds.has_active_members(TASK)
        assert await ds.active_member_sessions(TASK) == ["mem-elsewhere"]


@pytest.mark.asyncio
async def test_a_settled_member_stops_being_live(db_factory) -> None:
    """Finishing a member needs no separate de-registration.

    The terminal write to its run row IS the de-registration, which is why
    ``finalize_actor`` no longer touches any membership bookkeeping.
    """
    await _seed(member_status="completed")
    async with async_unit_of_work(commit=False) as db:
        assert not await TaskSessionDatastore(db).has_active_members(TASK)


@pytest.mark.asyncio
async def test_finish_task_stopped_refuses_over_a_member_it_never_dispatched(
    db_factory,
) -> None:
    """The guard that had no fallback, exercised from the wrong process.

    A lead deep in a long member run cannot tell "still building" from "hung",
    tries a few times, and stops the whole task — which is exactly what this
    guard exists to refuse. Read from process memory, it saw no members and let
    the stop through whenever the member had been dispatched anywhere else.
    """
    await _seed(member_status="active")
    orch = TaskOrchestrator()

    result = await orch.finalization.finish_task(
        task_id=TASK,
        project_id="w1",
        lead_session_id=LEAD,
        summary="calling it done",
        status="stopped",
        user_id=OWNER,
    )

    assert result["ok"] is False, (
        "a live member must block finish_task(stopped) no matter which process "
        "dispatched it"
    )
    assert "s1" in result["error"], "and the live subtask must be named"

    async with async_unit_of_work(commit=False) as db:
        task = await db.get(TaskRow, TASK)
        assert task is not None and task.status == "active", "the task must survive"


@pytest.mark.asyncio
async def test_force_still_overrides_the_guard(db_factory) -> None:
    """``force=True`` is the deliberate override and must keep working."""
    await _seed(member_status="active")
    orch = TaskOrchestrator()

    result = await orch.finalization.finish_task(
        task_id=TASK,
        project_id="w1",
        lead_session_id=LEAD,
        summary="I mean it",
        status="stopped",
        force=True,
        user_id=OWNER,
    )
    assert result.get("ok") is not False, "force must not be blocked by the guard"


@pytest.mark.asyncio
async def test_lead_does_not_finalize_while_a_member_runs_elsewhere(db_factory) -> None:
    """The idle-exit check, from a process that knows nothing about the member.

    The plan covered this before — a member's node sits ``in_progress`` — so
    this one degraded safely. It is pinned anyway: the plan is a SECOND
    opinion, and a member dispatched before a plan write lands would have had
    neither.
    """
    await _seed(member_status="active", plan={})
    coordination = CoordinationService()

    assert not await coordination.lead_idle_with_no_pending(
        TASK, "w1", OWNER, lead_session_id=""
    ), "an active member run must keep the lead waiting"


@pytest.mark.asyncio
async def test_lead_finalizes_once_the_member_settles(db_factory) -> None:
    """The other half: with nothing live and nothing unresolved, it may exit."""
    await _seed(member_status="completed", plan={})
    coordination = CoordinationService()

    assert await coordination.lead_idle_with_no_pending(
        TASK, "w1", OWNER, lead_session_id=""
    )
