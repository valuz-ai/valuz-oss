"""Persistent chat automations finish from their own input receipt, never the latest session."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.contracts import AgentExecution
from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner
from valuz_agent.modules.sessions.input_receipts import SessionInputReceipt
from valuz_agent.modules.sessions.task_checks import CONFIG_KEY
from valuz_agent.ports.automation_runtime import NoopAutomationExecutionLease


def _receipt(*args, **kwargs):
    kwargs.setdefault("input", {CONFIG_KEY: {"configuration": {"result_kind": "conversation"}}})
    return SessionInputReceipt(*args, **kwargs)


@asynccontextmanager
async def uow(*args, **kwargs):
    yield Mock()


@pytest.mark.parametrize(
    "outcome,expected", [("completed", "success"), ("failed", "failed"), ("cancelled", "cancelled")]
)
async def test_run_waits_for_its_receipt_and_preserves_same_session(outcome, expected):
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    row = SimpleNamespace(
        target_session_id="main", project_id="project", result_kind="conversation", status="enabled"
    )
    run = SimpleNamespace(
        id="run-1",
        status="running",
        cancel_requested_at=None,
        started_at=1,
        triggered_at=1,
        playbook_run_id=None,
        artifact_json=None,
    )
    ds = Mock(
        get_run=AsyncMock(return_value=run),
        get_automation=AsyncMock(return_value=row),
        replace_run=AsyncMock(),
        update_automation=AsyncMock(),
    )
    receipt = _receipt("run-1", "main", "queued")
    library = Mock(
        get_input=AsyncMock(
            side_effect=[
                None,
                _receipt(
                    "run-1",
                    "main",
                    outcome,
                    result_summary="own output",
                    error_message="own error" if expected == "failed" else None,
                ),
            ]
        ),
        enqueue_background=AsyncMock(return_value=receipt),
    )

    async def tick(*args):
        assert run.status == "running" and not ds.replace_run.called

    with (
        patch("valuz_agent.facade.sessions.SessionLibrary", return_value=library),
        patch("valuz_agent.infra.db.async_unit_of_work", uow),
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
        patch("valuz_agent.modules.automations.in_process_runner.asyncio.sleep", tick),
    ):
        await runner._finish_existing_chat_run(
            user_id="owner",
            automation_id="a",
            run_id="run-1",
            session_id="main",
            rendered_prompt="check",
            lease=NoopAutomationExecutionLease(),
        )
    assert run.status == expected and run.session_id == "main"
    assert run.result_summary == "own output"
    assert library.enqueue_background.call_args.kwargs["input_id"] == "run-1"


async def test_replayed_run_reuses_completed_receipt_without_new_input():
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    row = SimpleNamespace(
        target_session_id="main", project_id="project", result_kind="conversation", status="enabled"
    )
    run = SimpleNamespace(
        id="run-2", status="running", started_at=1, triggered_at=1, playbook_run_id=None
    )
    ds = Mock(
        get_run=AsyncMock(return_value=run),
        get_automation=AsyncMock(return_value=row),
        replace_run=AsyncMock(),
        update_automation=AsyncMock(),
    )
    library = Mock(
        get_input=AsyncMock(
            return_value=_receipt("run-2", "main", "completed", result_summary="second output")
        ),
        enqueue_background=AsyncMock(),
    )
    with (
        patch("valuz_agent.facade.sessions.SessionLibrary", return_value=library),
        patch("valuz_agent.infra.db.async_unit_of_work", uow),
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
    ):
        await runner._finish_existing_chat_run(
            user_id="owner",
            automation_id="a2",
            run_id="run-2",
            session_id="main",
            rendered_prompt="newly rendered text differs",
            lease=NoopAutomationExecutionLease(),
        )
    library.enqueue_background.assert_not_called()
    assert run.status == "success" and run.result_summary == "second output"


def test_target_session_cannot_use_task_mode():
    with pytest.raises(ValueError, match="chat"):
        AgentExecution(mode="task", target_session_id="main")


@pytest.mark.parametrize(
    "meta,owner,project,agent",
    [
        ({"project_id": "p", "agent_slug": "a", "task_id": "t"}, "u", "p", "a"),
        ({"project_id": "p", "agent_slug": "a", "worktree": {"path": "x"}}, "u", "p", "a"),
        ({"project_id": "p", "agent_slug": "a"}, "wrong", "p", "a"),
        ({"project_id": "p", "agent_slug": "a"}, "u", "other", "a"),
        ({"project_id": "p", "agent_slug": "a"}, "u", "p", "other"),
    ],
)
async def test_target_validates_owner_project_agent_and_chat(meta, owner, project, agent):
    from valuz_agent.modules.sessions.background_targets import validate_chat_target
    from valuz_agent.modules.sessions.errors import SessionNotFound, SessionNotRunnable

    async def read(uid, sid):
        return SimpleNamespace(status="idle", metadata={"valuz": meta}) if uid == "u" else None

    with patch(
        "valuz_agent.modules.sessions.background_targets.data_reader",
        return_value=SimpleNamespace(get_session=read),
    ):
        with pytest.raises((SessionNotFound, SessionNotRunnable)):
            await validate_chat_target(owner, "main", project_id=project, agent_slug=agent)


def test_code_target_is_rejected_instead_of_silently_discarded():
    from valuz_agent.modules.automations.contracts import CodeExecution

    with pytest.raises(ValueError):
        CodeExecution.model_validate(
            {"kind": "code", "runtime": "python", "entry": "job.py", "target_session_id": "main"}
        )


async def test_output_lookup_uses_dispatched_input_not_latest_run(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from valuz_agent.infra.database import Base
    from valuz_agent.modules.automations.datastore import AutomationDatastore
    from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
    from valuz_agent.modules.sessions.models import QueuedInputRow

    path = tmp_path / "runs.db"
    sync_engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(
        sync_engine,
        tables=[AutomationRow.__table__, AutomationRunRow.__table__, QueuedInputRow.__table__],
    )
    sync_engine.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for key, triggered, state in (("older", 1, "dispatched"), ("newer", 2, "queued")):
            db.add(
                AutomationRow(
                    id=key,
                    user_id="u",
                    name=key,
                    project_id="p",
                    agent_kind="library_agent",
                    agent_slug="valurion",
                    prompt_template="check",
                    trigger_kind="manual",
                    target_session_id="main",
                )
            )
            db.add(
                AutomationRunRow(
                    id=key,
                    user_id="u",
                    automation_id=key,
                    project_id="p",
                    session_id="main",
                    triggered_at=triggered,
                    trigger_type="manual",
                    status="running",
                )
            )
            db.add(
                QueuedInputRow(
                    id=key, user_id="u", session_id="main", input={"text": key}, status=state
                )
            )
        await db.commit()
        assert (await AutomationDatastore(db).get_run_by_session("u", "main")).id == "older"
        assert await AutomationDatastore(db).get_run_by_session("wrong", "main") is None
        pending = await db.get(QueuedInputRow, "older")
        pending.status = "completed"
        await db.commit()
        # A human turn on this reusable session is not an automation output.
        assert await AutomationDatastore(db).get_run_by_session("u", "main") is None
    await engine.dispose()


async def test_creation_reuses_library_agent_binding_of_target_without_deploying():
    from valuz_agent.modules.automations.schemas import AutomationCreatePayload, ManualTrigger
    from valuz_agent.modules.automations.service import AutomationService

    service = AutomationService.__new__(AutomationService)
    service._get_project_info = AsyncMock(return_value=("Chat", "chat"))
    service._members = Mock(get=AsyncMock(side_effect=AssertionError("No member deployment")))
    target = SimpleNamespace(metadata={"valuz": {"project_id": "p", "agent_slug": "valurion"}})
    payload = AutomationCreatePayload(
        name="follow",
        project_kind="chat",
        project_id="p",
        agent_kind="library_agent",
        agent_slug="valurion",
        trigger=ManualTrigger(),
        execution=AgentExecution(target_session_id="main"),
    )
    with patch(
        "valuz_agent.modules.sessions.background_targets.validate_chat_target",
        AsyncMock(return_value=target),
    ):
        assert await service._resolve_project_and_agent(
            payload, calling_session_project_id=None, user_id="u"
        ) == ("p", "valurion")
    service._members.get.assert_not_called()


async def test_legacy_task_update_cannot_keep_existing_chat_target():
    from valuz_agent.modules.automations.errors import AutomationContractInvalid
    from valuz_agent.modules.automations.schemas import AutomationUpdatePayload
    from valuz_agent.modules.automations.service import AutomationService

    service = AutomationService.__new__(AutomationService)
    row = SimpleNamespace(
        id="a", project_id="p", execution_kind="agent", target_session_id="main", action_kind="chat"
    )
    service._ds = Mock(get_automation=AsyncMock(return_value=row))
    with pytest.raises(AutomationContractInvalid):
        await service.update("a", AutomationUpdatePayload(action_kind="task"), user_id="u")
    assert row.action_kind == "chat"


@pytest.mark.parametrize("mode", ["deny", "facts", "replay", "observer_failure"])
async def test_optional_preparation_port_skips_without_input_or_reuses_original_receipt(
    mode, monkeypatch
):
    from valuz_agent.ports.automation_input import AutomationInputPreparation
    from valuz_agent.ports.extensions import ext

    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    run = SimpleNamespace(
        id="port-run", status="running", started_at=1, triggered_at=1, playbook_run_id=None
    )
    row = SimpleNamespace(
        target_session_id="main", project_id="project", result_kind="conversation", status="enabled"
    )
    ds = Mock(
        get_run=AsyncMock(return_value=run),
        get_automation=AsyncMock(return_value=row),
        replace_run=AsyncMock(),
        update_automation=AsyncMock(),
    )
    terminal = _receipt("port-run", "main", "completed", result_summary="Real completed summary")
    library = Mock(
        get_input=AsyncMock(
            return_value=terminal if mode in {"replay", "observer_failure"} else None
        ),
        enqueue_background=AsyncMock(return_value=terminal),
    )
    observer = AsyncMock(
        side_effect=RuntimeError("observer failed") if mode == "observer_failure" else None
    )
    preparation = AsyncMock(
        return_value=AutomationInputPreparation(False, "NO_CHANGE")
        if mode == "deny"
        else AutomationInputPreparation(additional_context="Verified fact context")
    )
    monkeypatch.setattr(
        ext,
        "automation_input_preparers",
        [SimpleNamespace(prepare=preparation, on_receipt=observer)],
    )
    with (
        patch("valuz_agent.facade.sessions.SessionLibrary", return_value=library),
        patch("valuz_agent.infra.db.async_unit_of_work", uow),
        patch("valuz_agent.modules.automations.datastore.AutomationDatastore", return_value=ds),
    ):
        await runner._finish_existing_chat_run(
            user_id="owner",
            automation_id="automation",
            run_id="port-run",
            session_id="main",
            rendered_prompt="Original fixed preamble and prompt",
            lease=NoopAutomationExecutionLease(),
        )
    if mode == "deny":
        library.enqueue_background.assert_not_called()
        observer.assert_not_called()
        assert run.status == "skipped" and run.result_summary == "NO_CHANGE"
    elif mode == "facts":
        args = library.enqueue_background.call_args
        assert args.args == ("main", "Original fixed preamble and prompt\n\nVerified fact context")
        assert args.kwargs["input_id"] == "port-run"
        assert run.status == "success"
        assert observer.call_args.args[0].input_id == "port-run"
    else:
        preparation.assert_not_called()
        library.enqueue_background.assert_not_called()
        observer.assert_awaited_once()
        assert run.status == "success"  # Observation is not invented delivery acknowledgement.


async def test_preparation_failure_fails_closed_without_main_input(monkeypatch):
    from valuz_agent.ports.automation_input import prepare_automation_input
    from valuz_agent.ports.automation_runtime import AutomationRunCommand
    from valuz_agent.ports.extensions import ext

    monkeypatch.setattr(
        ext,
        "automation_input_preparers",
        [SimpleNamespace(prepare=AsyncMock(side_effect=ConnectionError("source unavailable")))],
    )
    result = await prepare_automation_input(
        AutomationRunCommand("owner", "a", "run"), project_id="p", session_id="main"
    )
    assert not result.allowed and result.status == "failed"
    assert result.reason == "AUTOMATION_INPUT_PREPARATION_UNAVAILABLE"
