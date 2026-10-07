"""Merged catalogue gates respect durable queued inputs and already-executed receipts."""
# ruff: noqa: F811 — pytest imports reusable fixtures by parameter name

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.modules.sessions.test_input_queue import (  # noqa: F401
    OWNER,
    _FakeBus,
    _patch_drain,
    _queue_db,
)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.automations.app_plugin_guard import ManagedAutomationSourceGuard
from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import QueuedInputRow
from valuz_agent.modules.sessions.task_checks import CONFIG_KEY
from valuz_agent.ports.app_plugins import PolicyVerdict
from valuz_agent.ports.automation_runtime import NoopAutomationExecutionLease
from valuz_agent.ports.capability_policy import TaskCheckConfig
from valuz_agent.ports.extensions import ext


@pytest.fixture(autouse=True)
async def tables(_queue_db, monkeypatch):
    async with async_unit_of_work() as db:
        conn = await db.connection()
        await conn.run_sync(lambda c: AutomationRow.__table__.create(c))
        await conn.run_sync(lambda c: AutomationRunRow.__table__.create(c))
    monkeypatch.setattr(ext, "automation_run_guards", [ManagedAutomationSourceGuard()])


async def seed(*, managed=True, input_status="queued"):
    binding = {
        "app_plugin_id": "plugin",
        "scope": "org",
        "owner_key": "org",
        "version": "1",
        "sha256": "a" * 64,
        "origin_scope": "global",
        "origin_owner_key": "publisher",
    }
    async with async_unit_of_work() as db:
        db.add(
            AutomationRow(
                id="automation",
                user_id=OWNER,
                name="Managed",
                project_id="proj-1",
                agent_kind="library_agent",
                agent_slug="valurion",
                action_kind="chat",
                target_session_id="chat",
                prompt_template="prompt",
                trigger_kind="manual",
                status="enabled",
                result_kind="conversation",
                app_plugin_id="plugin" if managed else None,
                app_plugin_source_kind="catalog" if managed else None,
                app_plugin_catalog_binding=binding if managed else None,
            )
        )
        db.add(
            AutomationRunRow(
                id="run",
                user_id=OWNER,
                automation_id="automation",
                project_id="proj-1",
                status="running",
                trigger_type="manual",
                triggered_at=1,
                started_at=1,
                session_id="chat",
            )
        )
        db.add(
            QueuedInputRow(
                id="run",
                user_id=OWNER,
                session_id="chat",
                project_id="proj-1",
                status=input_status,
                input={
                    "text": "original prompt",
                    "attachments": [],
                    "source": "background",
                    CONFIG_KEY: TaskCheckConfig(
                        origin="automation",
                        automation_id="automation",
                        run_id="run",
                        configuration={"result_kind": "conversation"},
                    ).model_dump(mode="json"),
                },
                result_summary="real output" if input_status == "completed" else None,
                completed_at=2 if input_status == "completed" else None,
            )
        )


async def test_busy_input_rechecks_revocation_at_actual_dispatch(monkeypatch):
    from valuz_agent.modules.sessions import run_orchestrator

    await seed()
    policy = SimpleNamespace(
        authorize_catalog_automation=AsyncMock(
            return_value=PolicyVerdict(allowed=False, reason="revoked while busy")
        )
    )
    monkeypatch.setattr(ext, "app_plugin_policy", policy)
    calls = _patch_drain(monkeypatch)
    # The live session is busy on the first poll; authority changes before idle.
    busy = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(run_orchestrator, "_session_busy", busy)
    monkeypatch.setattr(run_orchestrator, "_BUSY_POLL_SECONDS", 0)
    await run_orchestrator._drain_queue_after_turn("chat", _FakeBus(), user_id=OWNER)
    assert calls == []
    async with async_unit_of_work(commit=False) as db:
        receipt = await SessionDatastore(db).get_queued(OWNER, "chat", "run")
        assert receipt.status == "failed" and "revoked while busy" in receipt.error_message
    policy.authorize_catalog_automation.assert_awaited_once()


async def test_nonmanaged_queued_chat_keeps_original_behavior(monkeypatch):
    from valuz_agent.modules.sessions import run_orchestrator

    await seed(managed=False)
    authorize = AsyncMock(side_effect=AssertionError("not a catalogue job"))
    monkeypatch.setattr(
        ext, "app_plugin_policy", SimpleNamespace(authorize_catalog_automation=authorize)
    )
    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("chat", _FakeBus(), user_id=OWNER)
    assert calls == ["original prompt"]
    authorize.assert_not_awaited()


@pytest.mark.parametrize("initial", ["completed", "dispatched"])
async def test_recovery_uses_receipt_despite_revoked_catalogue(monkeypatch, initial):
    await seed(input_status=initial)
    authorize = AsyncMock(
        return_value=PolicyVerdict(allowed=False, reason="revoked after execution")
    )
    monkeypatch.setattr(
        ext, "app_plugin_policy", SimpleNamespace(authorize_catalog_automation=authorize)
    )
    if initial == "dispatched":

        async def actual_completion(*args):
            async with async_unit_of_work() as db:
                receipt = await db.get(QueuedInputRow, "run")
                receipt.status, receipt.result_summary, receipt.completed_at = (
                    "completed",
                    "real output",
                    2,
                )

        monkeypatch.setattr(
            "valuz_agent.modules.automations.in_process_runner.asyncio.sleep", actual_completion
        )
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    await runner._execute_run(
        OWNER, "automation", "run", lease=NoopAutomationExecutionLease(), detach_chat=False
    )
    async with async_unit_of_work(commit=False) as db:
        run = await db.get(AutomationRunRow, "run")
        assert (
            run.status == "success"
            and run.result_summary == "real output"
            and run.session_id == "chat"
        )
    authorize.assert_not_awaited()


def test_guard_registered_through_feature_lifecycle_and_disposed(monkeypatch):
    from valuz_agent.features.automations import AutomationsPlugin
    from valuz_agent.plugin_host import PluginContext
    from valuz_agent.plugin_host.registry import HostRegistry

    monkeypatch.setattr(ext, "automation_run_guards", [])
    with ExitStack() as stack:
        ctx = PluginContext(
            plugin_id="oss-automations",
            stack=stack,
            role="worker",
            facets=frozenset({"ports"}),
            settings=SimpleNamespace(),
            extensions=ext,
            module_registry_getter=lambda: None,
            middleware_registry_getter=lambda: None,
            registry=HostRegistry(),
        )
        AutomationsPlugin().register(ctx)
        assert ext.automation_run_guards == [ManagedAutomationSourceGuard()]
    assert ext.automation_run_guards == []


async def test_completed_receipt_recovers_when_manifest_changed_back_to_code(monkeypatch):
    await seed(input_status="completed")
    async with async_unit_of_work() as db:
        row = await db.get(AutomationRow, "automation")
        row.execution_kind, row.code_entry, row.code_runtime, row.target_session_id = (
            "code",
            "job.py",
            "python",
            None,
        )
    authorize = AsyncMock(return_value=PolicyVerdict(allowed=False, reason="old source revoked"))
    monkeypatch.setattr(
        ext, "app_plugin_policy", SimpleNamespace(authorize_catalog_automation=authorize)
    )
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    code = AsyncMock(side_effect=AssertionError("must not re-execute a completed input as code"))
    monkeypatch.setattr(runner, "_start_code_run", code)
    await runner._execute_run(
        OWNER, "automation", "run", lease=NoopAutomationExecutionLease(), detach_chat=False
    )
    async with async_unit_of_work(commit=False) as db:
        assert (await db.get(AutomationRunRow, "run")).status == "success"
    authorize.assert_not_awaited()
    code.assert_not_awaited()


async def change_manifest(*, original_kind="conversation", legacy=False):
    from valuz_agent.modules.automations.in_process_runner import (
        _automation_check_config,
        _frame_agent_prompt,
    )

    async with async_unit_of_work() as db:
        row, run = await db.get(AutomationRow, "automation"), await db.get(AutomationRunRow, "run")
        receipt = await db.get(QueuedInputRow, "run")
        row.result_kind = original_kind
        config = _automation_check_config(row, run).model_dump(mode="json")
        if legacy:
            config["configuration"].pop("result_kind")
        receipt.input = {
            **receipt.input,
            "text": _frame_agent_prompt(row, run, "original work", None),
            CONFIG_KEY: config,
        }
        row.execution_kind, row.code_entry, row.code_runtime, row.target_session_id = (
            "code",
            "job.py",
            "python",
            None,
        )
        row.result_kind = "artifact" if original_kind == "conversation" else "conversation"


@pytest.mark.parametrize("entry", ["startup", "shutdown", "execute"])
async def test_receipt_survives_startup_shutdown_manifest_artifact_rewrite(monkeypatch, entry):
    await seed(input_status="completed")
    await change_manifest(legacy=entry == "startup")
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    enqueue = AsyncMock()
    monkeypatch.setattr(runner, "enqueue", enqueue)
    code = AsyncMock(side_effect=AssertionError("must never rerun original completed work"))
    monkeypatch.setattr(runner, "_start_code_run", code)
    if entry == "startup":
        await runner._reconcile_stranded_runs()
        enqueue.assert_awaited_once_with("automation", "run", OWNER)
    elif entry == "shutdown":
        runner._active_ids["automation"] = OWNER
        await runner._mark_active_runs_interrupted()
    # All three entry points keep the durable receipt, then settle it accurately.
    await runner._execute_run(
        OWNER, "automation", "run", lease=NoopAutomationExecutionLease(), detach_chat=False
    )
    async with async_unit_of_work(commit=False) as db:
        run = await db.get(AutomationRunRow, "run")
        assert run.status == "success" and run.result_summary == "real output"
    code.assert_not_awaited()


@pytest.mark.parametrize("legacy", [False, True])
async def test_original_artifact_contract_is_not_relaxed_after_manifest_change(monkeypatch, legacy):
    await seed(input_status="completed")
    await change_manifest(original_kind="artifact", legacy=legacy)
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    code = AsyncMock(side_effect=AssertionError("must never rerun original work"))
    monkeypatch.setattr(runner, "_start_code_run", code)
    await runner._execute_run(
        OWNER, "automation", "run", lease=NoopAutomationExecutionLease(), detach_chat=False
    )
    async with async_unit_of_work(commit=False) as db:
        run = await db.get(AutomationRunRow, "run")
        assert run.status == "failed" and "declared artifact" in run.error_message
    code.assert_not_awaited()


@pytest.mark.parametrize("original_kind", ["conversation", "artifact"])
async def test_ambiguous_newline_legacy_never_uses_rewritten_manifest(monkeypatch, original_kind):
    from valuz_agent.modules.automations.in_process_runner import (
        _automation_check_config,
        _frame_agent_prompt,
    )

    await seed(input_status="completed")
    async with async_unit_of_work() as db:
        row, run = await db.get(AutomationRow, "automation"), await db.get(AutomationRunRow, "run")
        row.name, row.result_kind = "Legal\nname", original_kind
        receipt = await db.get(QueuedInputRow, "run")
        config = _automation_check_config(row, run).model_dump(mode="json")
        config["configuration"].pop("result_kind")
        receipt.input = {
            **receipt.input,
            "text": _frame_agent_prompt(row, run, "work", None),
            CONFIG_KEY: config,
        }
        row.execution_kind, row.code_entry, row.code_runtime, row.target_session_id = (
            "code",
            "job.py",
            "python",
            None,
        )
        row.result_kind = "artifact" if original_kind == "conversation" else "conversation"
    runner = InProcessAutomationRunner()
    runner._triggers = Mock(next_fire_at=Mock(return_value=None))
    code = AsyncMock(side_effect=AssertionError("completed work must not rerun"))
    monkeypatch.setattr(runner, "_start_code_run", code)
    await runner._execute_run(
        OWNER, "automation", "run", lease=NoopAutomationExecutionLease(), detach_chat=False
    )
    async with async_unit_of_work(commit=False) as db:
        run, receipt = await db.get(AutomationRunRow, "run"), await db.get(QueuedInputRow, "run")
        assert run.status == "failed" and run.error_code == "original_result_contract_unavailable"
        assert "inspect" in run.error_message and "declared artifact" not in run.error_message
        assert receipt.status == "completed" and receipt.result_summary == "real output"
    code.assert_not_awaited()
