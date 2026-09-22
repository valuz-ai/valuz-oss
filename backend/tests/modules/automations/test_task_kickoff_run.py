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

import asyncio
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
        id="auto-1",
        action_kind="task",
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
        id="run-1",
        trigger_type="cron",
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

    kickoff = AsyncMock(return_value=fake_task)
    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(1000, 100),
        ),
        patch(
            "valuz_agent.modules.tasks.orchestrator.task_orchestrator.lifecycle.kickoff",
            new=kickoff,
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
    # The task row remembers the RUN, so the lead can be traced back to it
    # even before (or without) the session link above.
    assert kickoff.await_args.kwargs["trigger_automation_run_id"] == "run-1"
    assert kickoff.await_args.kwargs["trigger_automation_id"] == "auto-1"


@pytest.mark.asyncio
async def test_lead_session_is_linked_when_it_appears_after_kickoff(monkeypatch) -> None:
    """``kickoff`` returns before the lead exists (cold sandbox, ~20 s), so the
    immediate lookup is empty on every real kickoff. The run used to stay
    unlinked forever (valuz/valuz#26); now a bounded linker writes the session
    id once the lead run row shows up."""
    from valuz_agent.modules.automations import in_process_runner as mod

    monkeypatch.setattr(mod, "_LEAD_LINK_POLL_S", 0.0)
    monkeypatch.setattr(mod, "_LEAD_LINK_BUDGET_S", 5.0)

    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)
    ds = Mock()
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()
    row = SimpleNamespace(
        id="auto-1",
        action_kind="task",
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
        id="run-1",
        trigger_type="cron",
        status="queued",
        started_at=None,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        session_id=None,
        invoked_by_session_id=None,
        triggered_at=500,
    )
    lead_run = SimpleNamespace(kind="lead", session_id="lead-sess-2", status="active")
    ts_ds = Mock()
    # Empty at kickoff, present on the linker's second look.
    ts_ds.list_runs = AsyncMock(side_effect=[[], [], [lead_run]])
    link_ds = Mock()
    link_ds.get_run = AsyncMock(return_value=run)
    link_ds.replace_run = AsyncMock()

    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(1000, 100),
        ),
        patch(
            "valuz_agent.modules.tasks.orchestrator.task_orchestrator.lifecycle.kickoff",
            new=AsyncMock(return_value=SimpleNamespace(id="task-2")),
        ),
        patch("valuz_agent.modules.tasks.datastore.TaskSessionDatastore", return_value=ts_ds),
        patch(
            "valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=link_ds
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
    ):
        await runner._execute_task_kickoff(
            ds=ds,
            row=row,
            run=run,
            run_id="run-1",
            automation_id="auto-1",
            rendered_prompt="p",
            user_id="u1",
        )
        # Kickoff itself did not wait for the lead.
        assert run.status == "success" and run.session_id is None
        assert mod._detached, "a linker should be running behind the kickoff"
        await asyncio.gather(*list(mod._detached))

    assert run.session_id == "lead-sess-2"
    link_ds.replace_run.assert_awaited_once_with(run)
    assert ts_ds.list_runs.await_count == 3
