"""Optional owner-explicit admission gates; an empty registry preserves OSS behavior."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from valuz_agent.ports.automation_runtime import AutomationRunCommand


@dataclass(frozen=True, slots=True)
class AutomationRunAdmission:
    allowed: bool = True
    reason: str | None = None
    status: Literal["skipped", "failed"] = "skipped"


@dataclass(frozen=True, slots=True)
class BackgroundInputCommand:
    """A persisted background input at the actual turn boundary, with its stored owner."""

    user_id: str
    input_id: str
    session_id: str
    project_id: str


class AutomationRunGuardPort(Protocol):
    async def check(
        self, command: AutomationRunCommand, *, project_id: str, target_session_id: str | None
    ) -> AutomationRunAdmission: ...


async def evaluate_automation_run_guards(
    command: AutomationRunCommand, *, project_id: str, target_session_id: str | None
) -> AutomationRunAdmission:
    from valuz_agent.ports.extensions import ext

    for guard in tuple(ext.automation_run_guards):
        try:
            result = await guard.check(
                command, project_id=project_id, target_session_id=target_session_id
            )
        except Exception as exc:
            return AutomationRunAdmission(False, f"AUTOMATION_GUARD_UNAVAILABLE: {exc}", "failed")
        if not result.allowed:
            return result
    return AutomationRunAdmission()


async def evaluate_background_input_guards(
    command: BackgroundInputCommand,
) -> AutomationRunAdmission:
    """Reuse registered admission policies without pretending an input is an Automation row.

    ``check_input`` is additive/optional so existing automation-only policies
    and an empty OSS registry preserve their original behavior.
    """
    from valuz_agent.ports.extensions import ext

    for guard in tuple(ext.automation_run_guards):
        check_input: (
            Callable[[BackgroundInputCommand], Awaitable[AutomationRunAdmission]] | None
        ) = getattr(guard, "check_input", None)
        if check_input is None:
            continue
        try:
            result = await check_input(command)
        except Exception as exc:
            return AutomationRunAdmission(False, f"BACKGROUND_GUARD_UNAVAILABLE: {exc}", "failed")
        if not result.allowed:
            return result
    return AutomationRunAdmission()
