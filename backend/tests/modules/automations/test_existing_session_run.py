"""Persistent chat automations finish from their own input receipt, never the latest session."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.contracts import AgentExecution
from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner
from valuz_agent.modules.sessions.input_receipts import SessionInputReceipt
from valuz_agent.ports.automation_runtime import NoopAutomationExecutionLease


@asynccontextmanager
async def uow(*args, **kwargs):
    yield Mock()


@pytest.mark.parametrize(
    "outcome,expected", [("completed", "success"), ("failed", "failed"), ("cancelled", "cancelled")]
)
async def test_run_waits_for_its_receipt_and_preserves_same_session(outcome, expected):
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    row = SimpleNamespace(target_session_id="main", result_kind="conversation", status="enabled")
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
    receipt = SessionInputReceipt("run-1", "main", "queued")
    library = Mock(
        get_input=AsyncMock(
            side_effect=[
                None,
                SessionInputReceipt(
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
    row = SimpleNamespace(target_session_id="main", result_kind="conversation", status="enabled")
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
            return_value=SessionInputReceipt(
                "run-2", "main", "completed", result_summary="second output"
            )
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
