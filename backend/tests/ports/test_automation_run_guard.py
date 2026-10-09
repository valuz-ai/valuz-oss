from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.ports.automation_run_guard import (
    AutomationRunAdmission,
    evaluate_automation_run_guards,
)
from valuz_agent.ports.automation_runtime import AutomationRunCommand, NoopAutomationExecutionLease
from valuz_agent.ports.extensions import ext


async def test_empty_registry_preserves_oss_admission(monkeypatch):
    monkeypatch.setattr(ext, "automation_run_guards", [])
    assert (
        await evaluate_automation_run_guards(
            AutomationRunCommand("owner", "a", "r"), project_id="p", target_session_id="main"
        )
    ).allowed


@pytest.mark.parametrize("unavailable,status", [(False, "skipped"), (True, "failed")])
async def test_guard_denies_before_executor_with_owner_explicit_command(
    tmp_path, monkeypatch, unavailable, status
):
    import valuz_agent.infra.db as db_module

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'guard.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[AutomationRow.__table__, AutomationRunRow.__table__]
            )
        )
    monkeypatch.setattr(
        db_module, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False)
    )
    seen = []

    class Guard:
        async def check(self, command, *, project_id, target_session_id):
            seen.append(
                (
                    command.user_id,
                    command.automation_id,
                    command.run_id,
                    project_id,
                    target_session_id,
                )
            )
            if unavailable:
                raise ConnectionError("Policy unavailable")
            return AutomationRunAdmission(False, "Paused")

    monkeypatch.setattr(ext, "automation_run_guards", [Guard()])
    async with async_unit_of_work() as db:
        db.add(
            AutomationRow(
                id="a",
                user_id="owner",
                name="follow",
                project_id="p",
                agent_kind="library_agent",
                agent_slug="valurion",
                trigger_kind="manual",
                prompt_template="check",
                target_session_id="main",
            )
        )
        db.add(
            AutomationRunRow(
                id="r",
                user_id="owner",
                automation_id="a",
                project_id="p",
                triggered_at=1,
                trigger_type="manual",
                status="queued",
            )
        )
    runner = InProcessAutomationRunner()
    runner._triggers = SimpleNamespace(next_fire_at=Mock(return_value=None))
    assert not await runner._admit_run("other", "a", "r", NoopAutomationExecutionLease())
    assert not seen
    assert not await runner._admit_run("owner", "a", "r", NoopAutomationExecutionLease())
    assert seen == [("owner", "a", "r", "p", "main")]
    async with async_unit_of_work(commit=False) as db:
        run = await db.get(AutomationRunRow, "r")
        assert run.status == status and run.completed_at is not None
        assert run.session_id is None
    await engine.dispose()
