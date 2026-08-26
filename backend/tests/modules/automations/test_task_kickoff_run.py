"""`_execute_task_kickoff` run-row bookkeeping.

Guarded here:

  * **duration_ms** — left UNSET for a task kickoff. The kickoff is
    fire-and-forget (the lead session runs in the background), so the few-ms
    kickoff cost never matches the task's real running time; the activity log
    shows "—" instead of a misleading duration.
  * **result_summary** — the bare task title, with no "kicked off" prefix and
    NOT the raw ``f"Task kicked off: {task.id}"`` (an opaque id the user can't
    act on; deep-linking already rides ``session_id``).
"""

from __future__ import annotations

import itertools
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner


@asynccontextmanager
async def _fake_uow(*args, **kwargs):
    yield Mock()


@pytest.mark.asyncio
async def test_task_kickoff_leaves_duration_unset_and_titles_summary() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)

    ds = Mock()
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()

    row = SimpleNamespace(
        project_id="proj-1",
        agent_slug="researcher",
        name="my-automation",
        user_id="u1",
        status="enabled",
        last_run_at=None,
        next_run_at=None,
        updated_at=0,
    )
    run = SimpleNamespace(
        status="queued",
        started_at=None,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        session_id=None,
        invoked_by_session_id=None,
        triggered_at=500,
    )

    fake_task = SimpleNamespace(id="task-abc-123")
    lead_run = SimpleNamespace(kind="lead", session_id="lead-sess-1", status="active")
    ts_ds = Mock()
    ts_ds.list_runs = AsyncMock(return_value=[lead_run])

    prompt = "研究今日A股热门板块与资金情况"

    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(1000, 100),
        ),
        patch(
            "valuz_agent.modules.tasks.orchestrator.task_orchestrator.lifecycle.kickoff",
            new=AsyncMock(return_value=fake_task),
        ),
        patch(
            "valuz_agent.modules.tasks.datastore.TaskSessionDatastore",
            return_value=ts_ds,
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
    ):
        await runner._execute_task_kickoff(
            ds=ds,
            row=row,
            run=run,
            run_id="run-1",
            automation_id="auto-1",
            rendered_prompt=prompt,
            user_id="u1",
        )

    # Fire-and-forget kickoff: duration is left unset (UI shows "—").
    assert run.status == "success"
    assert run.duration_ms is None

    # Summary is the bare task title — no prefix, not the raw task id.
    assert run.result_summary == prompt  # title == rendered prompt (< 60 chars)
    assert "task-abc-123" not in run.result_summary
    assert not run.result_summary.startswith("Task kicked off:")
    assert not run.result_summary.startswith("Started task:")

    # Lead session deep-link still wired.
    assert run.session_id == "lead-sess-1"
