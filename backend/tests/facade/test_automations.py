from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.facade.automations import claim_due_runs
from valuz_agent.ports.automation_runtime import AutomationRunCommand


@asynccontextmanager
async def _fake_uow(*args, **kwargs):
    yield _fake_uow.db


_fake_uow.db = Mock()


@pytest.mark.asyncio
async def test_claim_due_runs_returns_explicit_owner_commands() -> None:
    due = SimpleNamespace(
        id="auto-1",
        user_id="owner-1",
        project_id="project-1",
        status="enabled",
        trigger_kind="interval",
        next_run_at=1_000,
        created_at=500,
    )
    blocked = SimpleNamespace(
        id="auto-2",
        user_id="owner-2",
        project_id="project-2",
        status="enabled",
        trigger_kind="cron",
        next_run_at=1_000,
        created_at=600,
    )
    ds = Mock()
    ds.find_due_automations_for_update = AsyncMock(return_value=[due, blocked])
    ds.active_run = AsyncMock(side_effect=[None, SimpleNamespace(id="active")])
    _fake_uow.db = Mock()

    with (
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
        patch(
            "valuz_agent.modules.automations.datastore.AutomationDatastore",
            return_value=ds,
        ),
    ):
        commands = await claim_due_runs(now=2_000, lateness_grace_ms=60_000)

    assert commands == [
        AutomationRunCommand(
            user_id="owner-1",
            automation_id="auto-1",
            run_id=commands[0].run_id,
        )
    ]
    assert len(_fake_uow.db.add.call_args_list) == 1
