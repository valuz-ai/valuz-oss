"""HTTP routes for automations.

Replaces ``/v1/schedules/*`` per ADR-021. Same overall shape (list / detail /
create / update / pause / resume / run-now / runs + project-targets and
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

from valuz_agent.api.deps import get_automation_service, get_current_user_id
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload,
    AutomationDetailResponse,
    AutomationGroupResponse,
    AutomationItemResponse,
    AutomationProjectTargetsResponse,
    AutomationProposalConfirmRequest,
    AutomationProposalStatusEntry,
    AutomationProposalStatusRequest,
    AutomationProposalStatusResponse,
    AutomationRunAcceptedResponse,
    AutomationRunItemResponse,
    AutomationUpdatePayload,
    CronValidateRequest,
    CronValidationResultResponse,
    IntervalValidateRequest,
    IntervalValidationResultResponse,
)
from valuz_agent.modules.automations.service import AutomationService

router = APIRouter(prefix="/v1/automations", tags=["automations"])


@router.get("")
async def list_automations(
    project_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> dict[str, list[AutomationGroupResponse]]:
    """List automations grouped by project.

    Unfiltered global view collapses chat-kind automations into one virtual
    "Chat" group (each chat automation still owns a distinct project_id;
    the consolidation is purely a display rule). Filtered view (per
    project) keeps one group per project.
    """
    return {"groups": await svc.list_automation_groups(project_id, user_id=user_id)}


@router.post("", status_code=201)
async def create_automation(
    payload: AutomationCreatePayload,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Create an automation.

    ``project_kind`` and ``agent_kind`` drive the four routing paths from
    ADR-021 §4. HTTP callers always pass ``calling_session_project_id``
    as ``None`` here — that field is reserved for the ``automation`` MCP
    tool, which knows the caller's chat project.
    """
    return await svc.create(payload, user_id=user_id)


@router.post("/proposals/{session_id}/confirm", status_code=201)
async def confirm_automation_proposal(
    session_id: str,
    payload: AutomationProposalConfirmRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationItemResponse:
    """Create the automation a ``create`` tool call proposed.

    The user confirms the proposal card; we re-resolve the session's project /
    bound-agent context (so a chat defaults the agent and a project session
    binds to its project + member Lead — exactly as the proposing tool did),
    then persist. ``tool_call_id`` is stamped on the row so a later session
    reload can detect the automation already exists. Typed module errors
    (invalid cron, agent-not-in-project, task-only-on-project, …) propagate to
    the global handler.
    """
    from valuz_agent.integrations.automations_mcp_server import _resolve_session_context

    project_id, project_kind, bound_agent_slug = await _resolve_session_context(session_id, user_id)
    create_payload = AutomationService.build_create_payload(
        name=payload.name,
        prompt_template=payload.prompt_template,
        trigger=payload.trigger,
        agent_slug=payload.agent_slug,
        action_kind=payload.action_kind,
        project_kind=project_kind,
        project_id=project_id,
        session_agent_slug=bound_agent_slug,
        worktree=payload.worktree,
        playbook_definition_id=payload.playbook_definition_id,
        playbook_version=payload.playbook_version,
    )
    # MCP-from-chat: forward the calling session's project so library agents land
    # in the user's current chat project rather than a freshly created one.
    calling_ws = project_id if project_kind == "chat" else None
    return await svc.create(
        create_payload,
        calling_session_project_id=calling_ws,
        origin_tool_call_id=payload.tool_call_id,
        user_id=user_id,
    )


@router.post("/proposals/{session_id}/status")
async def automation_proposal_status(
    session_id: str,
    payload: AutomationProposalStatusRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationProposalStatusResponse:
    """Map proposing ``tool_call_id``s → already-created automations.

    The frontend calls this on session re-entry (in-memory card state is lost on
    reload) to seed confirmed cards instead of showing a fresh Confirm button.
    ``session_id`` scopes the request semantically; the lookup is owner-scoped by
    the persisted ``origin_tool_call_id`` (globally unique per user).
    """
    _ = session_id
    mapping = await svc.confirmed_origin_map(payload.tool_call_ids, user_id=user_id)
    return AutomationProposalStatusResponse(
        confirmed={
            tid: AutomationProposalStatusEntry(automation_id=aid) for tid, aid in mapping.items()
        }
    )


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


@router.get("/project-targets")
async def list_project_targets(
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationProjectTargetsResponse:
    """List projects eligible as the target of a new automation.

    Owned by the automations module so the rule "Chat sentinel + project
    projects, no ephemeral chat rows" stays adjacent to the create logic
    that consumes it. The frontend renders the response verbatim.
    """
    return AutomationProjectTargetsResponse(targets=await svc.list_project_targets(user_id=user_id))


@router.get("/{automation_id}")
async def get_automation(
    automation_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Fetch a single automation's detail."""
    return await svc.get_automation_detail(automation_id, user_id=user_id)


@router.patch("/{automation_id}")
async def update_automation(
    automation_id: str,
    payload: AutomationUpdatePayload,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Patch fields on an automation.

    ``trigger`` is all-or-nothing; ``agent_slug`` swap is intra-project
    only (cross-project / cross-kind changes require delete + recreate
    — see ADR-021 §6).
    """
    return await svc.update(automation_id, payload, user_id=user_id)


@router.delete("/{automation_id}", status_code=204)
async def delete_automation(
    automation_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> None:
    """Delete an automation and cascade its run history."""
    await svc.delete(automation_id, user_id=user_id)


@router.post("/{automation_id}/pause")
async def pause_automation(
    automation_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Pause an automation. Clears ``next_run_at`` so the tick skips it
    until resumed."""
    return await svc.pause(automation_id, user_id=user_id)


@router.post("/{automation_id}/resume")
async def resume_automation(
    automation_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationDetailResponse:
    """Resume a paused automation. Recomputes ``next_run_at`` from the
    current time so cron rows align to the next scheduled tick and
    interval rows wait one full cadence before the next fire."""
    return await svc.resume(automation_id, user_id=user_id)


@router.post("/{automation_id}/run-now", status_code=202)
async def run_automation_now(
    automation_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> AutomationRunAcceptedResponse:
    """Enqueue an immediate run.

    Single-flight: returns 409 if the latest run is still ``queued`` or
    ``running`` so two rapid clicks don't burn double tokens.
    """
    return await svc.run_now(automation_id, user_id=user_id)


@router.get("/{automation_id}/runs")
async def list_automation_runs(
    automation_id: str,
    limit: int = 20,
    cursor: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: AutomationService = Depends(get_automation_service),
) -> dict[str, list[AutomationRunItemResponse]]:
    """List execution history for an automation."""
    return {"runs": await svc.list_runs(automation_id, limit=limit, cursor=cursor, user_id=user_id)}
