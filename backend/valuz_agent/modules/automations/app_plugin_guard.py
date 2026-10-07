"""Live catalogue admission at the existing session's actual dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.automations.app_plugin_authorization import authorize_managed_automation
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.ports.automation_run_guard import AutomationRunAdmission, BackgroundInputCommand
from valuz_agent.ports.automation_runtime import AutomationRunCommand


@dataclass(frozen=True)
class ManagedAutomationSourceGuard:
    async def check(
        self, command: AutomationRunCommand, *, project_id: str, target_session_id: str | None
    ) -> AutomationRunAdmission:
        async with async_unit_of_work(commit=False) as db:
            store = AutomationDatastore(db)
            row = await store.get_automation(command.user_id, command.automation_id)
            if row is None:
                return AutomationRunAdmission(False, "Automation source no longer exists", "failed")
            if not row.app_plugin_id:
                return AutomationRunAdmission()
            run = await store.get_run(command.user_id, command.automation_id, command.run_id)
            if run is None or run.status not in {"queued", "running"}:
                return AutomationRunAdmission(
                    False, "Managed automation run is no longer pending", "failed"
                )
            if row.project_id != project_id or row.target_session_id != target_session_id:
                return AutomationRunAdmission(False, "Managed automation target changed", "failed")
            try:
                await authorize_managed_automation(row, user_id=command.user_id)
            except Exception as exc:
                return AutomationRunAdmission(
                    False, f"app_plugin_source_unavailable: {exc}", "failed"
                )
        return AutomationRunAdmission()

    async def check_input(self, command: BackgroundInputCommand) -> AutomationRunAdmission:
        async with async_unit_of_work(commit=False) as db:
            run = await AutomationDatastore(db).get_run_by_input_id(
                command.user_id, command.input_id
            )
            # Plain chats and personal-result messages are not plugin jobs.
            if run is None:
                return AutomationRunAdmission()
            if run.session_id != command.session_id:
                return AutomationRunAdmission(False, "Automation input session mismatch", "failed")
            automation_id = run.automation_id
        return await self.check(
            AutomationRunCommand(command.user_id, automation_id, command.input_id),
            project_id=command.project_id,
            target_session_id=command.session_id,
        )
