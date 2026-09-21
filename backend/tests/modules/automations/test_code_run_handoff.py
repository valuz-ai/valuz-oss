"""Runner: code rows hand off to ``_finish_code_run``; artifact results are enforced."""

from __future__ import annotations

import itertools
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.code_runner import CodeRunResult
from valuz_agent.modules.automations.in_process_runner import (
    InProcessAutomationRunner,
    _frame_agent_prompt,
)
from valuz_agent.ports.automation_result import AutomationArtifactFile
from valuz_agent.ports.extensions import ext


def _row(**over: Any) -> Any:
    base = dict(
        id="auto-1",
        name="daily",
        project_id="proj-1",
        user_id="u1",
        status="enabled",
        execution_kind="code",
        code_runtime="python",
        code_entry="a.py",
        code_timeout_s=10,
        input_kind="json",
        result_kind="artifact",
        action_kind="chat",
        agent_slug=None,
        playbook_definition_id=None,
        last_run_at=None,
        next_run_at=None,
        updated_at=0,
        trigger_kind="manual",
        timezone=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _run(**over: Any) -> Any:
    base = dict(
        id="run-1",
        user_id="u1",
        status="queued",
        trigger_type="manual",
        triggered_at=500,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        error_code=None,
        error_message=None,
        error_message_key=None,
        session_id=None,
        input_json={"symbol": "A"},
        extra_input=None,
        invoked_by_ref=None,
        invoked_by_session_id=None,
        artifact_json=None,
        files_json=None,
        executor_ref=None,
        log_tail=None,
        playbook_run_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _uow_with(db: Any):
    @asynccontextmanager
    async def _uow(*args: Any, **kwargs: Any):
        yield db

    return _uow


@pytest.mark.asyncio
async def test_execute_run_marks_running_and_hands_off_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    row, run = _row(), _run()
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    finish = AsyncMock()
    with (
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.modules.playbooks.datastore.PlaybookDatastore", return_value=Mock()),
        patch("valuz_agent.infra.db.async_unit_of_work", _uow_with(Mock())),
        patch.object(runner, "_finish_code_run", finish),
    ):
        await runner._execute_run("u1", "auto-1", "run-1", detach_chat=False)
    assert run.status == "running"
    assert run.started_at is not None
    ds.replace_run.assert_awaited_once()
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["run_id"] == "run-1"
    # The background task owns the single-flight release.
    assert runner._active_ids.get("auto-1") == "u1"


@pytest.mark.asyncio
async def test_execute_run_skips_a_cancelled_run() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    row, run = _row(), _run(status="cancelled")
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    with (
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.modules.playbooks.datastore.PlaybookDatastore", return_value=Mock()),
        patch("valuz_agent.infra.db.async_unit_of_work", _uow_with(Mock())),
    ):
        await runner._execute_run("u1", "auto-1", "run-1", detach_chat=False)
    ds.replace_run.assert_not_awaited()
    assert run.status == "cancelled"
    assert "auto-1" not in runner._active_ids


@pytest.mark.asyncio
async def test_finish_code_run_writes_terminal_state_and_fires_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)
    runner._triggers._default_tz = "UTC"
    runner._active_ids["auto-1"] = "u1"
    row, run = _row(), _run(status="running", started_at=1000)
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.get_run = AsyncMock(return_value=run)
    ds.last_artifact_run = AsyncMock(return_value=None)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()
    db = Mock()
    db.expunge_all = Mock()
    seen: list[Any] = []

    class _Hook:
        async def on_artifact(self, event: Any) -> None:
            seen.append(event)

    monkeypatch.setattr(ext, "automation_result_hooks", [_Hook()])
    result = CodeRunResult(
        status="success",
        artifact={"summary": "done"},
        executor_ref="local:7",
        log_tail="log",
        files=[
            AutomationArtifactFile(
                artifact_id="art-1", name="r.csv", mime_type="text/csv", size_bytes=3
            )
        ],
        exit_code=0,
    )
    run_code = AsyncMock(return_value=result)
    with (
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.infra.db.async_unit_of_work", _uow_with(db)),
        patch("valuz_agent.modules.automations.code_runner.run_code_automation", run_code),
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(2000, 100),
        ),
        patch.object(runner, "_user_locale", AsyncMock(return_value="en-US")),
    ):
        await runner._finish_code_run(user_id="u1", automation_id="auto-1", run_id="run-1")

    assert run_code.await_args.kwargs["effective_input"] == {"symbol": "A"}
    assert run.status == "success"
    assert run.artifact_json == {"summary": "done"}
    assert run.files_json == [
        {
            "artifact_id": "art-1",
            "name": "r.csv",
            "mime_type": "text/csv",
            "size_bytes": 3,
            "error": None,
        }
    ]
    assert run.executor_ref == "local:7"
    assert run.log_tail == "log"
    assert run.result_summary == "done"
    assert run.duration_ms is not None
    ds.update_automation.assert_awaited_once()
    assert row.next_run_at == 9_999
    ds.trim_runs.assert_awaited_once()
    assert [e.run_id for e in seen] == ["run-1"]
    assert seen[0].producer == "code"
    assert "auto-1" not in runner._active_ids


@pytest.mark.asyncio
async def test_finish_code_run_does_not_overwrite_a_cancelled_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers._default_tz = "UTC"
    runner._active_ids["auto-1"] = "u1"
    row = _row()
    run = _run(status="cancelled", started_at=1000)
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.get_run = AsyncMock(return_value=run)
    ds.last_artifact_run = AsyncMock(return_value=None)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    db = Mock()
    db.expunge_all = Mock()
    run_code = AsyncMock(
        return_value=CodeRunResult(status="cancelled", error_code="AUTOMATION_CANCELLED")
    )
    with (
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.infra.db.async_unit_of_work", _uow_with(db)),
        patch("valuz_agent.modules.automations.code_runner.run_code_automation", run_code),
        patch.object(runner, "_user_locale", AsyncMock(return_value="en-US")),
    ):
        await runner._finish_code_run(user_id="u1", automation_id="auto-1", run_id="run-1")
    ds.replace_run.assert_not_awaited()
    assert "auto-1" not in runner._active_ids


def _session_service(events: list[Any]) -> Mock:
    svc = Mock()
    svc.send_message_sync = AsyncMock(return_value=SimpleNamespace(events=events))
    return svc


@pytest.mark.asyncio
async def test_finish_chat_run_fails_when_artifact_result_never_recorded() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)
    runner._active_ids["auto-1"] = "u1"
    row = _row(execution_kind="agent", agent_slug="r", result_kind="artifact")
    run = _run(status="running", started_at=1000, session_id="sess-1")
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()
    db = Mock()
    db.refresh = AsyncMock()
    playbooks = Mock()
    playbooks.get_run = AsyncMock(return_value=None)
    svc = _session_service(
        [
            SimpleNamespace(
                event={"event_type": "message.assistant", "payload": {"text": "Let me publish"}}
            )
        ]
    )
    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(2000, 100),
        ),
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.modules.playbooks.datastore.PlaybookDatastore", return_value=playbooks),
        patch("valuz_agent.infra.db.async_unit_of_work", _uow_with(db)),
        patch.object(runner, "_build_session_service", return_value=svc),
    ):
        await runner._finish_chat_run(
            user_id="u1",
            automation_id="auto-1",
            run_id="run-1",
            session_id="sess-1",
            rendered_prompt="hi",
        )
    db.refresh.assert_awaited_once()
    assert run.status == "failed"
    assert run.error_code == "AUTOMATION_NO_ARTIFACT"


@pytest.mark.asyncio
async def test_finish_chat_run_succeeds_with_recorded_artifact() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)
    runner._active_ids["auto-1"] = "u1"
    row = _row(execution_kind="agent", agent_slug="r", result_kind="artifact")
    run = _run(
        status="running",
        started_at=1000,
        session_id="sess-1",
        artifact_json={"summary": "brief ready"},
    )
    ds = Mock()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()
    db = Mock()
    db.refresh = AsyncMock()
    playbooks = Mock()
    playbooks.get_run = AsyncMock(return_value=None)
    svc = _session_service(
        [SimpleNamespace(event={"event_type": "message.assistant", "payload": {"text": "done"}})]
    )
    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(2000, 100),
        ),
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.modules.playbooks.datastore.PlaybookDatastore", return_value=playbooks),
        patch("valuz_agent.infra.db.async_unit_of_work", _uow_with(db)),
        patch.object(runner, "_build_session_service", return_value=svc),
    ):
        await runner._finish_chat_run(
            user_id="u1",
            automation_id="auto-1",
            run_id="run-1",
            session_id="sess-1",
            rendered_prompt="hi",
        )
    assert run.status == "success"
    assert run.result_summary == "brief ready"


def test_frame_agent_prompt_names_the_run_and_carries_input() -> None:
    row = _row(execution_kind="agent", result_kind="artifact")
    run = _run(trigger_type="cron")
    framed = _frame_agent_prompt(row, run, "Write the brief", {"symbol": "A", "n": 1})
    assert framed.startswith('<automation-run automation_id="auto-1" run_id="run-1" trigger="cron"')
    assert "</automation-run>" in framed
    assert "<automation-input>" in framed and '"symbol": "A"' in framed
    assert framed.rstrip().endswith("Write the brief")
    # No input block for a text / none automation.
    assert "<automation-input>" not in _frame_agent_prompt(row, run, "x", "plain text")
