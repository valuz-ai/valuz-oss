"""Chat-mode automation runs finalize OFF the serial worker.

A chat agent turn can run for minutes; running it inline on the single FIFO
worker would queue every other automation behind it — and deadlock a chat
automation that fires another via the ``run`` tool and waits for it. So the
worker hands the turn to ``_finish_chat_run`` (a background task) which owns the
turn, the run-row finalization, the reschedule, AND releasing the single-flight
guard. Guarded here.
"""

from __future__ import annotations

import itertools
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner


class _StaleLease:
    token = "stale"
    attempt = 1

    async def heartbeat(self) -> None:
        pass

    async def is_current(self) -> bool:
        return False


@asynccontextmanager
async def _fake_uow(*args, **kwargs):
    yield Mock()


def _row():
    return SimpleNamespace(
        project_id="proj-1",
        agent_slug="researcher",
        name="my-automation",
        user_id="u1",
        status="enabled",
        last_run_at=None,
        next_run_at=None,
        updated_at=0,
    )


def _run():
    return SimpleNamespace(
        id="run-1",
        status="running",
        started_at=1000,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        session_id="sess-1",
        error_code=None,
        error_message=None,
        error_message_key=None,
        triggered_at=500,
    )


def _session_service(events: list) -> Mock:
    result = SimpleNamespace(events=events)
    svc = Mock()
    svc.send_message_sync = AsyncMock(return_value=result)
    return svc


@pytest.mark.asyncio
async def test_finish_chat_run_succeeds_and_releases_single_flight() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)
    # The worker holds the single-flight across handoff; the background task
    # must release it so the same automation can run again.
    runner._active_ids["auto-1"] = "u1"

    row, run = _row(), _run()
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()

    svc = _session_service(
        [SimpleNamespace(event={"event_type": "message.assistant", "payload": {"text": "done"}})]
    )

    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(2000, 100),
        ),
        patch(
            "valuz_agent.modules.automations.datastore.AutomationDatastore",
            return_value=ds,
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
        patch.object(runner, "_build_session_service", return_value=svc),
    ):
        await runner._finish_chat_run(
            user_id="u1",
            automation_id="auto-1",
            run_id="run-1",
            session_id="sess-1",
            rendered_prompt="hello",
        )

    svc.send_message_sync.assert_awaited_once()
    assert run.status == "success"
    assert run.result_summary == "done"
    assert run.completed_at is not None
    assert run.duration_ms is not None  # backgrounded turn DOES record duration
    ds.update_automation.assert_awaited_once()  # automation rescheduled
    assert "auto-1" not in runner._active_ids  # single-flight released


@pytest.mark.asyncio
async def test_finish_chat_run_marks_session_error_failed_and_releases() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)
    runner._active_ids["auto-1"] = "u1"

    row, run = _row(), _run()
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()

    svc = _session_service(
        [
            SimpleNamespace(
                event={"event_type": "session_error", "payload": {"message": "Not logged in"}}
            )
        ]
    )

    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(2000, 100),
        ),
        patch(
            "valuz_agent.modules.automations.datastore.AutomationDatastore",
            return_value=ds,
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
        patch.object(runner, "_build_session_service", return_value=svc),
    ):
        await runner._finish_chat_run(
            user_id="u1",
            automation_id="auto-1",
            run_id="run-1",
            session_id="sess-1",
            rendered_prompt="hello",
        )

    assert run.status == "failed"
    assert run.error_code == "SessionError"
    assert "Not logged in" in (run.error_message or "")
    # Single-flight released even on failure so the automation isn't wedged.
    assert "auto-1" not in runner._active_ids


@pytest.mark.asyncio
async def test_stale_lease_cannot_persist_terminal_chat_state() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    row, run = _row(), _run()
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()
    svc = _session_service([])

    with (
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
        patch.object(runner, "_build_session_service", return_value=svc),
    ):
        await runner._finish_chat_run(
            user_id="u1",
            automation_id="auto-1",
            run_id="run-1",
            session_id="sess-1",
            rendered_prompt="hello",
            lease=_StaleLease(),
        )

    ds.replace_run.assert_not_awaited()
    ds.update_automation.assert_not_awaited()
