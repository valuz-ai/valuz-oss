"""Automation-to-Playbook runtime contract tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner


@asynccontextmanager
async def _fake_uow(*args, **kwargs):
    yield Mock()


class _Playbooks:
    def __init__(self) -> None:
        self.definition = SimpleNamespace(
            id="pb-1",
            name="Quarterly thesis review",
            project_id="definition-project",
            current_version=2,
        )
        self.version = SimpleNamespace(
            definition_id="pb-1",
            version=1,
            goal="Re-test the pinned v1 thesis",
            applicability={"asset_types": ["equity"]},
            inputs=[{"name": "ticker", "required": True}],
            context_reads=["thesis", "signals"],
            stages=[{"id": "review", "instruction": "Use v1 review logic"}],
            required_skills=["earnings-analysis"],
            allowed_skills=["stock-analysis"],
            conditions=[{"if": "material_change", "then": "revalue"}],
            approvals=[{"before": "strategy_write"}],
            outputs=["thesis_revision"],
            context_writes=[{"target": "thesis"}],
            failure_policy="stop",
        )
        self.runs: dict[str, object] = {}
        self.pending: object | None = None

    async def get_definition(self, user_id: str, definition_id: str):
        return self.definition if definition_id == self.definition.id else None

    async def get_version(self, user_id: str, definition_id: str, version: int):
        if definition_id == self.definition.id and version == self.version.version:
            return self.version
        return None

    async def get_run(self, user_id: str, run_id: str):
        return self.runs.get(run_id)

    def add(self, row: object) -> None:
        self.pending = row

    async def flush(self) -> None:
        assert self.pending is not None
        self.pending.id = "playbook-run-1"  # type: ignore[attr-defined]
        self.runs["playbook-run-1"] = self.pending


def _automation() -> SimpleNamespace:
    return SimpleNamespace(
        id="auto-1",
        user_id="u1",
        project_id="execution-project",
        agent_slug="researcher",
        name="Review NVDA",
        prompt_template="Review {{project.name}} for NVDA",
        action_kind="chat",
        worktree=False,
        trigger_kind="manual",
        timezone=None,
        last_run_at=None,
        status="enabled",
        next_run_at=None,
        updated_at=0,
        playbook_definition_id="pb-1",
        # Definition.current_version is already 2, but this Automation must
        # continue executing the immutable version it pinned at create time.
        playbook_version=1,
    )


def _automation_run() -> SimpleNamespace:
    return SimpleNamespace(
        id="run-1",
        user_id="u1",
        automation_id="auto-1",
        project_id="execution-project",
        status="queued",
        triggered_at=1000,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        error_code=None,
        error_message=None,
        error_message_key=None,
        session_id=None,
        extra_input="Use the latest filed 10-Q as evidence.",
        playbook_run_id=None,
    )


@pytest.mark.asyncio
async def test_pinned_playbook_creates_and_completes_linked_run() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers._default_tz = "UTC"
    runner._triggers.next_fire_at = Mock(return_value=9999)

    row = _automation()
    run = _automation_run()
    automation_ds = Mock()
    automation_ds.get_automation = AsyncMock(return_value=row)
    automation_ds.last_run = AsyncMock(return_value=run)
    automation_ds.replace_run = AsyncMock()
    automation_ds.update_automation = AsyncMock()
    automation_ds.trim_runs = AsyncMock()

    playbooks = _Playbooks()
    session = SimpleNamespace(id="session-1")
    result = SimpleNamespace(
        events=[
            SimpleNamespace(
                event={
                    "event_type": "message.assistant",
                    "payload": {"text": "Pinned review completed"},
                }
            )
        ]
    )
    session_svc = Mock()
    session_svc.create_session = AsyncMock(return_value=session)
    session_svc.send_message_sync = AsyncMock(return_value=result)

    with (
        patch(
            "valuz_agent.modules.automations.datastore.AutomationDatastore",
            return_value=automation_ds,
        ),
        patch(
            "valuz_agent.modules.playbooks.datastore.PlaybookDatastore",
            return_value=playbooks,
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
        patch.object(runner, "_resolve_project_name", return_value="Semis"),
        patch.object(runner, "_build_session_service", return_value=session_svc),
    ):
        await runner._execute_run(
            "u1",
            "auto-1",
            "run-1",
            detach_chat=False,
        )

    assert run.playbook_run_id == "playbook-run-1"
    playbook_run = playbooks.runs["playbook-run-1"]
    assert playbook_run.definition_version == 1  # type: ignore[attr-defined]
    assert playbook_run.plan == playbooks.version.stages  # type: ignore[attr-defined]
    assert playbook_run.status == "completed"  # type: ignore[attr-defined]
    assert playbook_run.trigger_kind == "automation"  # type: ignore[attr-defined]
    assert playbook_run.trigger_ref == "run-1"  # type: ignore[attr-defined]
    assert playbook_run.output_refs[-1] == {  # type: ignore[attr-defined]
        "type": "session",
        "id": "session-1",
        "status": "success",
    }

    sent_prompt = session_svc.send_message_sync.await_args.args[1]
    assert "Pinned version: 1" in sent_prompt
    assert "Re-test the pinned v1 thesis" in sent_prompt
    assert "Use v1 review logic" in sent_prompt
    assert "Use the latest filed 10-Q as evidence." in sent_prompt
    assert "Pinned version: 2" not in sent_prompt


@pytest.mark.asyncio
async def test_shutdown_stops_linked_playbook_run() -> None:
    playbook_run = SimpleNamespace(
        status="running",
        error_code=None,
        error_message=None,
        completed_at=None,
        checkpoint={},
    )
    playbooks = Mock()
    playbooks.get_run = AsyncMock(return_value=playbook_run)
    automation_run = SimpleNamespace(
        user_id="u1",
        playbook_run_id="playbook-run-1",
    )

    await InProcessAutomationRunner._stop_linked_playbook_run(
        playbooks,
        automation_run,
        completed_at=42,
    )

    assert playbook_run.status == "stopped"
    assert playbook_run.error_code == "AUTOMATION_INTERRUPTED_BY_SHUTDOWN"
    assert playbook_run.completed_at == 42
