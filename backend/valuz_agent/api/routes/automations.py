"""HTTP routes for automations.

Replaces ``/v1/schedules/*`` per ADR-021. Same overall shape (list / detail /
create / update / pause / resume / run-now / runs + workspace-targets and
trigger validation), with two visible differences:

- Resource path is ``/v1/automations`` and the response field is
  ``automations`` not ``tasks``.
- ``validate-cron`` is joined by ``validate-interval`` for the new
  Interval trigger form.

Body shapes live in :mod:`valuz_agent.modules.automations.schemas` —
the ``Trigger`` discriminated union does most of the heavy lifting so this
file stays a thin pass-through.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from valuz_agent.api.deps import get_automation_service
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload,
    AutomationDetailResponse,
    AutomationGroupResponse,
    AutomationRunAcceptedResponse,
    AutomationRunItemResponse,
    AutomationUpdatePayload,
    AutomationWorkspaceTargetsResponse,
    CronValidateRequest,
    CronValidationResultResponse,
    IntervalValidateRequest,
    IntervalValidationResultResponse,
)
from valuz_agent.modules.automations.service import AutomationService

router = APIRouter(prefix="/v1/automations", tags=["automations"])


@router.get("")
async def list_automations(
    workspace_id: str | None = None,
    svc: AutomationService = Depends(get_automation_service),
) -> dict[str, list[AutomationGroupResponse]]:
    """List automations grouped by workspace.

    Unfiltered global view collapses chat-kind automations into one virtual
    "Chat" group (each chat automation still owns a distinct workspace_id;
    the consolidation is purely a display rule). Filtered view (per
    project) keeps one group per workspace.
    """
    return {"groups": await svc.list_automation_groups(workspace_id)}


@router.post("", status_code=201)
async def create_automation(
    payload: AutomationCreatePayload,
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Create an automation.

    ``workspace_kind`` and ``agent_kind`` drive the four routing paths from
    ADR-021 §4. HTTP callers always pass ``calling_session_workspace_id``
    as ``None`` here — that field is reserved for the ``automation`` MCP
    tool, which knows the caller's chat workspace.
    """
    return await svc.create(payload)


@router.post("/validate-cron")
async def validate_cron(
    payload: CronValidateRequest,
    svc: AutomationService = Depends(get_automation_service),
) -> CronValidationResultResponse:
    """Validate a cron expression + render the human-readable description.

    Frontend uses this for the form-side "preview next runs" affordance —
    the result mirrors what the runner would compute at fire time.
    """
    return svc.validate_cron(payload.expr, payload.timezone)


@router.post("/validate-interval")
async def validate_interval(
    payload: IntervalValidateRequest,
    svc: AutomationService = Depends(get_automation_service),
) -> IntervalValidationResultResponse:
    """Validate an interval (seconds) against the 30s floor.

    Floor matches ``TriggerEvaluator.MIN_INTERVAL_SECONDS`` — the tick is
    30s and anything below it would race the tick.
    """
    return svc.validate_interval(payload.seconds)


@router.get("/workspace-targets")
async def list_workspace_targets(
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationWorkspaceTargetsResponse:
    """List workspaces eligible as the target of a new automation.

    Owned by the automations module so the rule "Chat sentinel + project
    workspaces, no ephemeral chat rows" stays adjacent to the create logic
    that consumes it. The frontend renders the response verbatim.
    """
    return AutomationWorkspaceTargetsResponse(targets=await svc.list_workspace_targets())


@router.get("/{automation_id}")
async def get_automation(
    automation_id: str,
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Fetch a single automation's detail."""
    return await svc.get_automation_detail(automation_id)


@router.patch("/{automation_id}")
async def update_automation(
    automation_id: str,
    payload: AutomationUpdatePayload,
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Patch fields on an automation.

    ``trigger`` is all-or-nothing; ``agent_slug`` swap is intra-workspace
    only (cross-workspace / cross-kind changes require delete + recreate
    — see ADR-021 §6).
    """
    return await svc.update(automation_id, payload)


@router.delete("/{automation_id}", status_code=204)
async def delete_automation(
    automation_id: str,
    svc: AutomationService = Depends(get_automation_service),
) -> None:
    """Delete an automation and cascade its run history."""
    await svc.delete(automation_id)


@router.post("/{automation_id}/pause")
async def pause_automation(
    automation_id: str,
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Pause an automation. Clears ``next_run_at`` so the tick skips it
    until resumed."""
    return await svc.pause(automation_id)


@router.post("/{automation_id}/resume")
async def resume_automation(
    automation_id: str,
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Resume a paused automation. Recomputes ``next_run_at`` from the
    current time so cron rows align to the next scheduled tick and
    interval rows wait one full cadence before the next fire."""
    return await svc.resume(automation_id)


@router.post("/{automation_id}/run-now", status_code=202)
async def run_automation_now(
    automation_id: str,
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationRunAcceptedResponse:
    """Enqueue an immediate run.

    Single-flight: returns 409 if the latest run is still ``queued`` or
    ``running`` so two rapid clicks don't burn double tokens.
    """
    return await svc.run_now(automation_id)


@router.get("/{automation_id}/runs")
async def list_automation_runs(
    automation_id: str,
    limit: int = 20,
    cursor: str | None = None,
    svc: AutomationService = Depends(get_automation_service),
) -> dict[str, list[AutomationRunItemResponse]]:
    """List execution history for an automation."""
    return {"runs": await svc.list_runs(automation_id, limit=limit, cursor=cursor)}
