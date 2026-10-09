"""Optional preparation/receipt observation at the existing-chat queue boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from valuz_agent.ports.automation_runtime import AutomationRunCommand


@dataclass(frozen=True, slots=True)
class AutomationInputPreparation:
    allowed: bool = True
    reason: str | None = None
    status: Literal["skipped", "failed"] = "skipped"
    additional_context: str = ""


@dataclass(frozen=True, slots=True)
class AutomationInputOutcome:
    command: AutomationRunCommand
    project_id: str
    session_id: str
    input_id: str
    status: str
    completed_at: int | None


class AutomationInputPreparationPort(Protocol):
    async def prepare(
        self, command: AutomationRunCommand, *, project_id: str, session_id: str
    ) -> AutomationInputPreparation: ...

    async def on_receipt(self, outcome: AutomationInputOutcome) -> None: ...


async def prepare_automation_input(
    command: AutomationRunCommand, *, project_id: str, session_id: str
) -> AutomationInputPreparation:
    from valuz_agent.ports.extensions import ext

    context: list[str] = []
    for provider in tuple(ext.automation_input_preparers):
        try:
            result = await provider.prepare(command, project_id=project_id, session_id=session_id)
        except Exception:
            return AutomationInputPreparation(
                False, "AUTOMATION_INPUT_PREPARATION_UNAVAILABLE", "failed"
            )
        if not result.allowed:
            return result
        if result.additional_context:
            context.append(result.additional_context)
    return AutomationInputPreparation(additional_context="\n\n".join(context))


async def observe_automation_input(outcome: AutomationInputOutcome) -> None:
    """Observers re-read their authorities; observation failure is not acknowledgement."""
    import logging

    from valuz_agent.ports.extensions import ext

    for provider in tuple(ext.automation_input_preparers):
        try:
            await provider.on_receipt(outcome)
        except Exception:
            logging.getLogger(__name__).exception("Automation input receipt observer failed")
