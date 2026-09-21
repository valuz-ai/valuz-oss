"""Service: run input validation, cancel semantics, artifact recording, contract rules."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.contracts import (
    AgentExecution,
    ArtifactResult,
    CodeExecution,
    ConversationResult,
    JsonInput,
)
from valuz_agent.modules.automations.errors import (
    AutomationArtifactInvalid,
    AutomationCancelUnsupported,
    AutomationInputInvalid,
    AutomationRunNotActive,
    AutomationTaskArtifactUnsupported,
)
from valuz_agent.modules.automations.schemas import ManualTrigger
from valuz_agent.modules.automations.service import AutomationService
from valuz_agent.ports.extensions import ext

SCHEMA = {
    "type": "object",
    "properties": {"symbol": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["symbol"],
}


def _row(**over: Any) -> Any:
    base = dict(
        id="auto-1",
        user_id="u1",
        project_id="proj-1",
        name="daily",
        status="enabled",
        execution_kind="code",
        input_kind="json",
        input_schema=SCHEMA,
        default_input={"limit": 5},
        result_kind="artifact",
        result_schema={"type": "object", "required": ["summary"]},
        action_kind="chat",
        agent_slug=None,
        agent_kind=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _run(**over: Any) -> Any:
    base = dict(
        id="run-1",
        user_id="u1",
        automation_id="auto-1",
        project_id="proj-1",
        status="running",
        trigger_type="manual",
        triggered_at=1,
        started_at=2,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        error_code=None,
        error_message=None,
        error_message_key=None,
        session_id=None,
        created_files=None,
        playbook_run_id=None,
        executor_ref=None,
        invoked_by_ref=None,
        artifact_json=None,
        input_json=None,
        extra_input=None,
        files_json=None,
        log_tail=None,
        cancel_requested_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> AutomationService:
    service = AutomationService(db=Mock(), event_bus=Mock())
    ds = Mock()
    ds.get_automation_for_update = AsyncMock()
    ds.get_automation = AsyncMock()
    ds.get_run = AsyncMock()
    ds.active_run = AsyncMock(return_value=None)
    ds.create_run = AsyncMock(side_effect=lambda user_id, run: run)
    ds.replace_run = AsyncMock()
    service._ds = ds  # noqa: SLF001
    monkeypatch.setattr(ext.automation_runtime, "enqueue", AsyncMock())
    return service


class TestRunNowInput:
    @pytest.mark.asyncio
    async def test_json_input_merged_with_default_and_stored(self, svc: AutomationService) -> None:
        svc._ds.get_automation_for_update.return_value = _row()  # noqa: SLF001
        accepted = await svc.run_now(
            "auto-1",
            run_input={"symbol": "SH:600519"},
            trigger_type="api",
            invoked_by_ref="site:abc",
            user_id="u1",
        )
        run = svc._ds.create_run.await_args.args[1]  # noqa: SLF001
        assert accepted.status == "queued"
        assert run.input_json == {"symbol": "SH:600519", "limit": 5}
        assert run.extra_input is None
        assert run.trigger_type == "api"
        assert run.invoked_by_ref == "site:abc"
        ext.automation_runtime.enqueue.assert_awaited_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_invalid_json_input_is_refused_before_any_run_exists(
        self, svc: AutomationService
    ) -> None:
        svc._ds.get_automation_for_update.return_value = _row()  # noqa: SLF001
        with pytest.raises(AutomationInputInvalid) as excinfo:
            await svc.run_now("auto-1", run_input={"symbol": 12}, user_id="u1")
        assert "input.symbol" in str(excinfo.value.message) or "input.symbol" in str(excinfo.value)
        svc._ds.create_run.assert_not_awaited()  # noqa: SLF001
        with pytest.raises(AutomationInputInvalid):
            await svc.run_now("auto-1", run_input={}, user_id="u1")  # symbol required

    @pytest.mark.asyncio
    async def test_text_input_lands_in_extra_input(self, svc: AutomationService) -> None:
        svc._ds.get_automation_for_update.return_value = _row(  # noqa: SLF001
            input_kind="text", input_schema=None, default_input=None
        )
        await svc.run_now("auto-1", extra_input="task_id=abc", trigger_type="agent", user_id="u1")
        run = svc._ds.create_run.await_args.args[1]  # noqa: SLF001
        assert run.extra_input == "task_id=abc" and run.input_json is None

    @pytest.mark.asyncio
    async def test_none_input_refuses_any_input(self, svc: AutomationService) -> None:
        svc._ds.get_automation_for_update.return_value = _row(  # noqa: SLF001
            input_kind="none", input_schema=None, default_input=None
        )
        with pytest.raises(AutomationInputInvalid):
            await svc.run_now("auto-1", run_input="x", user_id="u1")
        await svc.run_now("auto-1", user_id="u1")
        assert svc._ds.create_run.await_count == 1  # noqa: SLF001


class TestCancel:
    @pytest.mark.asyncio
    async def test_queued_run_is_terminalised(self, svc: AutomationService) -> None:
        svc._ds.get_automation.return_value = _row()  # noqa: SLF001
        run = _run(status="queued")
        svc._ds.get_run.return_value = run  # noqa: SLF001
        with patch.object(svc, "_resolve_task_links", AsyncMock(return_value={})):
            detail = await svc.cancel_run("auto-1", "run-1", user_id="u1")
        assert run.status == "cancelled"
        assert run.error_code == "AUTOMATION_CANCELLED"
        assert run.completed_at is not None
        assert detail.status == "cancelled"

    @pytest.mark.asyncio
    async def test_running_code_run_gets_a_cancel_request(self, svc: AutomationService) -> None:
        svc._ds.get_automation.return_value = _row()  # noqa: SLF001
        run = _run(status="running")
        svc._ds.get_run.return_value = run  # noqa: SLF001
        with patch.object(svc, "_resolve_task_links", AsyncMock(return_value={})):
            detail = await svc.cancel_run("auto-1", "run-1", user_id="u1")
        assert run.status == "running"
        assert run.cancel_requested_at is not None
        assert detail.cancel_requested_at == run.cancel_requested_at

    @pytest.mark.asyncio
    async def test_running_agent_run_is_unsupported_and_terminal_is_refused(
        self, svc: AutomationService
    ) -> None:
        svc._ds.get_automation.return_value = _row(execution_kind="agent")  # noqa: SLF001
        svc._ds.get_run.return_value = _run(status="running")  # noqa: SLF001
        with pytest.raises(AutomationCancelUnsupported):
            await svc.cancel_run("auto-1", "run-1", user_id="u1")
        svc._ds.get_run.return_value = _run(status="success")  # noqa: SLF001
        with pytest.raises(AutomationRunNotActive):
            await svc.cancel_run("auto-1", "run-1", user_id="u1")


class TestRecordArtifact:
    @pytest.mark.asyncio
    async def test_records_validated_artifact_and_fires_hooks(self, svc: AutomationService) -> None:
        svc._ds.get_automation.return_value = _row()  # noqa: SLF001
        run = _run()
        svc._ds.get_run.return_value = run  # noqa: SLF001
        hooks = AsyncMock()
        with (
            patch("valuz_agent.modules.automations.code_runner.fire_artifact_hooks", hooks),
            patch.object(svc, "_resolve_task_links", AsyncMock(return_value={})),
        ):
            detail = await svc.record_artifact(
                "auto-1",
                "run-1",
                artifact={"summary": "brief", "x": 1},
                producer="agent",
                user_id="u1",
            )
        assert run.artifact_json == {"summary": "brief", "x": 1}
        assert run.result_summary == "brief"
        assert run.files_json == []
        assert detail.artifact == {"summary": "brief", "x": 1}
        assert hooks.await_args.args[0].producer == "agent"

    @pytest.mark.asyncio
    async def test_a_conversation_automation_may_still_record_an_artifact(
        self, svc: AutomationService
    ) -> None:
        """Production, 2026-09-21: a run on a conversation automation reached for
        output, was refused, and its card stayed empty. 'conversation' means the
        artifact is optional — not forbidden."""
        svc._ds.get_automation.return_value = _row(result_kind="conversation")  # noqa: SLF001
        run = _run()
        svc._ds.get_run.return_value = run  # noqa: SLF001
        with (
            patch("valuz_agent.modules.automations.code_runner.fire_artifact_hooks", AsyncMock()),
            patch.object(svc, "_resolve_task_links", AsyncMock(return_value={})),
        ):
            detail = await svc.record_artifact(
                "auto-1", "run-1", artifact={"summary": "x", "asOf": "2026-09-21"}, user_id="u1"
            )
        assert run.artifact_json == {"summary": "x", "asOf": "2026-09-21"}
        assert detail.artifact == {"summary": "x", "asOf": "2026-09-21"}

    @pytest.mark.asyncio
    async def test_refuses_bad_artifacts_and_inactive_runs(self, svc: AutomationService) -> None:
        svc._ds.get_run.return_value = _run()  # noqa: SLF001
        svc._ds.get_automation.return_value = _row()  # noqa: SLF001
        with pytest.raises(AutomationArtifactInvalid):
            await svc.record_artifact("auto-1", "run-1", artifact={"no": "summary"}, user_id="u1")
        svc._ds.get_run.return_value = _run(status="failed")  # noqa: SLF001
        with pytest.raises(AutomationRunNotActive):
            await svc.record_artifact("auto-1", "run-1", artifact={"summary": "x"}, user_id="u1")


class TestContractRules:
    def test_task_cannot_declare_artifact(self) -> None:
        with pytest.raises(AutomationTaskArtifactUnsupported):
            AutomationService._check_contracts(AgentExecution(mode="task"), ArtifactResult())

    def test_code_defaults_to_artifact(self) -> None:
        out = AutomationService._check_contracts(CodeExecution(entry="a.py"), ConversationResult())
        assert isinstance(out, ArtifactResult)

    def test_build_create_payload_code_needs_no_agent_or_prompt(self) -> None:
        payload = AutomationService.build_create_payload(
            name="daily",
            prompt_template=None,
            trigger=ManualTrigger(),
            agent_slug=None,
            action_kind="chat",
            project_kind="project",
            project_id="p",
            session_agent_slug=None,
            execution=CodeExecution(entry="a.py"),
            input_contract=JsonInput(schema=SCHEMA),
        )
        assert payload.agent_slug is None and payload.agent_kind is None
        assert payload.execution is not None and payload.execution.kind == "code"
        assert payload.result.kind == "artifact"
        assert payload.input.kind == "json"
