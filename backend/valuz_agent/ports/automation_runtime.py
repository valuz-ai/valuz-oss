"""Automation scheduling/runtime transport extension point.

The host owns automation rows and execution semantics. Deployments may replace
only the lifecycle and enqueue transport; the OSS default keeps the existing
in-process tick loop, FIFO worker, and failure monitor unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AutomationRunCommand:
    """Immutable identity needed to execute one persisted automation run."""

    user_id: str
    automation_id: str
    run_id: str


@runtime_checkable
class AutomationRuntimePort(Protocol):
    """Deployment-owned automation lifecycle and enqueue transport."""

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def enqueue(self, command: AutomationRunCommand) -> None: ...


@runtime_checkable
class AutomationExecutionLease(Protocol):
    """Narrow fencing contract checked before durable execution writes."""

    token: str
    attempt: int

    async def heartbeat(self) -> None: ...

    async def is_current(self) -> bool: ...


@dataclass(slots=True)
class NoopAutomationExecutionLease:
    """Always-current lease used by the single-process OSS runtime."""

    token: str = "local-in-process"
    attempt: int = 1

    async def heartbeat(self) -> None:
        return None

    async def is_current(self) -> bool:
        return True


class InProcessAutomationRuntime:
    """OSS default preserving the desktop runner and monitor lifecycle."""

    async def startup(self) -> None:
        from valuz_agent.modules.automations.failure_monitor import (
            automation_failure_monitor,
        )
        from valuz_agent.modules.automations.in_process_runner import automation_runner

        await automation_runner.startup()
        await automation_failure_monitor.startup()

    async def shutdown(self) -> None:
        from valuz_agent.modules.automations.failure_monitor import (
            automation_failure_monitor,
        )
        from valuz_agent.modules.automations.in_process_runner import automation_runner

        await automation_failure_monitor.shutdown()
        await automation_runner.shutdown()

    async def enqueue(self, command: AutomationRunCommand) -> None:
        from valuz_agent.modules.automations.in_process_runner import automation_runner

        await automation_runner.enqueue(
            command.automation_id,
            command.run_id,
            command.user_id,
        )


__all__ = [
    "AutomationRunCommand",
    "AutomationExecutionLease",
    "AutomationRuntimePort",
    "InProcessAutomationRuntime",
    "NoopAutomationExecutionLease",
]
