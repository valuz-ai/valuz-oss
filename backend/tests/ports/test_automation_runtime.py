from __future__ import annotations

import pytest

from valuz_agent.ports.automation_runtime import (
    AutomationRunCommand,
    InProcessAutomationRuntime,
    NoopAutomationExecutionLease,
)


@pytest.mark.asyncio
async def test_default_runtime_preserves_runner_and_failure_monitor_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def record(name: str) -> None:
        calls.append(name)

    monkeypatch.setattr(
        "valuz_agent.modules.automations.in_process_runner.automation_runner.startup",
        lambda: record("runner-start"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.automations.failure_monitor.automation_failure_monitor.startup",
        lambda: record("monitor-start"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.automations.failure_monitor.automation_failure_monitor.shutdown",
        lambda: record("monitor-stop"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.automations.in_process_runner.automation_runner.shutdown",
        lambda: record("runner-stop"),
    )

    runtime = InProcessAutomationRuntime()
    await runtime.startup()
    await runtime.shutdown()

    assert calls == ["runner-start", "monitor-start", "monitor-stop", "runner-stop"]


@pytest.mark.asyncio
async def test_default_runtime_enqueues_same_command_on_existing_fifo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    async def enqueue(automation_id: str, run_id: str, user_id: str) -> None:
        calls.append((automation_id, run_id, user_id))

    monkeypatch.setattr(
        "valuz_agent.modules.automations.in_process_runner.automation_runner.enqueue",
        enqueue,
    )
    command = AutomationRunCommand(
        user_id="owner-1",
        automation_id="automation-1",
        run_id="run-1",
    )

    await InProcessAutomationRuntime().enqueue(command)

    assert calls == [("automation-1", "run-1", "owner-1")]


@pytest.mark.asyncio
async def test_default_execution_lease_is_always_current() -> None:
    lease = NoopAutomationExecutionLease()

    await lease.heartbeat()

    assert await lease.is_current() is True
    assert lease.token == "local-in-process"
    assert lease.attempt == 1
