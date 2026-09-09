"""A run must always reach a terminal state — never stay ``queued``.

``_execute_run`` prepares a run (project name → template variables → rendered
prompt → pinned Playbook) before it creates the session. That preparation used
to run with no exception handler: the ``try`` opened after the ``_active_ids``
single-flight claim had only a ``finally``, and the ``except`` that writes
``status="failed"`` sat in a nested block around ``create_session`` alone.

Anything raised in the preparation region therefore escaped ``_execute_run``,
the unit of work rolled back, and the row stayed ``queued`` forever — at which
point ``AutomationService.run_now``'s single-flight check refused every retry
with ``AutomationAlreadyQueued`` until a restart let
``_reconcile_stranded_runs`` clear it. The reported Windows ``tzdata`` crash
landed exactly here; these tests pin the class of failure, not that one cause.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner


@asynccontextmanager
async def _fake_uow(*args, **kwargs):
    yield Mock()


def _automation() -> SimpleNamespace:
    return SimpleNamespace(
        id="auto-1",
        user_id="u1",
        project_id="proj-1",
        agent_slug="researcher",
        name="Daily digest",
        prompt_template="Review {{project.name}}",
        action_kind="chat",
        worktree=False,
        trigger_kind="manual",
        timezone="Asia/Shanghai",
        last_run_at=None,
        status="enabled",
        next_run_at=None,
        updated_at=0,
        playbook_definition_id=None,
        playbook_version=None,
    )


def _automation_run() -> SimpleNamespace:
    return SimpleNamespace(
        id="run-1",
        user_id="u1",
        automation_id="auto-1",
        project_id="proj-1",
        status="queued",
        trigger_type="manual",
        triggered_at=1000,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        error_code=None,
        error_message=None,
        error_message_key=None,
        session_id=None,
        extra_input=None,
        playbook_run_id=None,
    )


async def _run_with_prepare_failure(runner, exc: BaseException, ds: Mock, run) -> None:
    """Drive ``_execute_run`` with the preparation step raising ``exc``."""
    row = _automation()
    ds.get_automation = AsyncMock(return_value=row)
    ds.last_run = AsyncMock(return_value=run)
    ds.replace_run = AsyncMock()

    with (
        patch(
            "valuz_agent.modules.automations.datastore.AutomationDatastore",
            return_value=ds,
        ),
        patch(
            "valuz_agent.modules.playbooks.datastore.PlaybookDatastore",
            return_value=Mock(),
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
        patch.object(runner, "_resolve_project_name", side_effect=exc),
        patch.object(runner, "_build_session_service", return_value=Mock()),
    ):
        await runner._execute_run("u1", "auto-1", "run-1", detach_chat=False)  # noqa: SLF001


def _runner() -> InProcessAutomationRunner:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()  # noqa: SLF001
    runner._triggers._default_tz = "UTC"  # noqa: SLF001
    return runner


@pytest.mark.asyncio
async def test_prepare_failure_marks_the_run_failed_instead_of_escaping() -> None:
    runner, ds, run = _runner(), Mock(), _automation_run()

    await _run_with_prepare_failure(runner, RuntimeError("project vanished"), ds, run)

    assert run.status == "failed"
    assert run.error_code == "RuntimeError"
    assert run.error_message == "project vanished"
    assert run.completed_at is not None
    ds.replace_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_failure_releases_the_single_flight_claim() -> None:
    """A stuck ``_active_ids`` entry would block the automation for the whole
    process lifetime even after the run row itself is finalized."""
    runner, ds, run = _runner(), Mock(), _automation_run()

    await _run_with_prepare_failure(runner, RuntimeError("boom"), ds, run)

    assert "auto-1" not in runner._active_ids  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_failed_failure_write_does_not_re_raise() -> None:
    """If the failure write itself dies (poisoned transaction), the worker loop
    must still keep its footing — and the ORIGINAL cause is what got logged."""
    runner, ds, run = _runner(), Mock(), _automation_run()
    ds.replace_run = AsyncMock(side_effect=RuntimeError("session is dead"))

    await _run_with_prepare_failure(runner, ValueError("the real cause"), ds, run)

    assert run.error_code == "ValueError"
    assert "auto-1" not in runner._active_ids  # noqa: SLF001


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    """``asyncio.CancelledError`` is a ``BaseException``; shutdown must still
    tear the worker down rather than be recorded as a run failure."""
    import asyncio

    runner, ds, run = _runner(), Mock(), _automation_run()

    with pytest.raises(asyncio.CancelledError):
        await _run_with_prepare_failure(runner, asyncio.CancelledError(), ds, run)

    assert run.status == "queued"
