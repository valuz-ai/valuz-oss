from __future__ import annotations

import pytest

from valuz_agent.facade.execution import execution_runtime


@pytest.mark.asyncio
async def test_headless_runtime_starts_only_execution_collaborators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def record(name: str) -> None:
        calls.append(name)

    monkeypatch.setattr(
        "valuz_agent.boot.steps.configure_structured_logging",
        lambda: calls.append("logging"),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.steps.init_kernel",
        lambda _host: record("kernel-start"),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.steps.bind_data_service",
        lambda _host: record("data-start"),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.steps.install_binding_change_listener",
        lambda: calls.append("bindings"),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.steps.dispose_data_service",
        lambda _host: record("data-stop"),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.steps.shutdown_kernel",
        lambda: record("kernel-stop"),
    )

    async with execution_runtime():
        calls.append("execute")

    assert calls == [
        "logging",
        "kernel-start",
        "data-start",
        "bindings",
        "execute",
        "data-stop",
        "kernel-stop",
    ]
