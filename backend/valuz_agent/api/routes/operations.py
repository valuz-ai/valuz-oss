"""Durable confirmation/status API for generic product operations."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.facade.projects import ProjectLibrary, get_project_library
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.operations.models import OperationRecordRow
from valuz_agent.modules.operations.schemas import (
    OperationDecisionRequest,
    OperationStatusRequest,
    OperationStatusResponse,
    OperationView,
)
from valuz_agent.modules.operations.service import OperationService
from valuz_agent.modules.playbooks import operations as _playbook_operations  # noqa: F401
from valuz_agent.modules.skills import operations as _skill_operations  # noqa: F401

router = APIRouter(prefix="/v1/operations", tags=["operations"])


async def get_operation_service(
    projects: ProjectLibrary = Depends(get_project_library),
) -> AsyncGenerator[OperationService, None]:
    async with async_unit_of_work() as db:
        yield OperationService(db, projects)


def operation_view(row: OperationRecordRow) -> OperationView:
    return OperationView(
        id=row.id,
        project_id=row.project_id,
        operation_type=row.operation_type,
        operation_version=row.operation_version,
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
        origin_session_id=row.origin_session_id,
        origin_tool_call_id=row.origin_tool_call_id,
        origin_playbook_run_id=row.origin_playbook_run_id,
        origin_automation_run_id=row.origin_automation_run_id,
        target_refs=row.target_refs,
        input_payload=row.input_payload,
        preview=row.preview,
        expected_revisions=row.expected_revisions,
        risk_level=row.risk_level,
        confirmation_policy=row.confirmation_policy,
        state=row.state,
        proposal_hash=row.proposal_hash,
        canonical_result_refs=row.canonical_result_refs,
        result_payload=row.result_payload,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{operation_id}")
async def get_operation(
    operation_id: str,
    service: OperationService = Depends(get_operation_service),
    user_id: str = Depends(get_current_user_id),
) -> OperationView:
    try:
        return operation_view(await service.get(user_id, operation_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{operation_id}/confirm")
async def confirm_operation(
    operation_id: str,
    body: OperationDecisionRequest,
    service: OperationService = Depends(get_operation_service),
    user_id: str = Depends(get_current_user_id),
) -> OperationView:
    try:
        row = await service.confirm(
            user_id,
            operation_id,
            expected_proposal_hash=body.proposal_hash,
            comment=body.comment,
            decision=body.decision,
        )
        return operation_view(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{operation_id}/cancel")
async def cancel_operation(
    operation_id: str,
    body: OperationDecisionRequest,
    service: OperationService = Depends(get_operation_service),
    user_id: str = Depends(get_current_user_id),
) -> OperationView:
    try:
        row = await service.cancel(
            user_id,
            operation_id,
            expected_proposal_hash=body.proposal_hash,
            comment=body.comment,
        )
        return operation_view(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/status/batch")
async def operation_status(
    body: OperationStatusRequest,
    service: OperationService = Depends(get_operation_service),
    user_id: str = Depends(get_current_user_id),
) -> OperationStatusResponse:
    rows = await service.status(user_id, body.operation_ids)
    return OperationStatusResponse(operations={row.id: operation_view(row) for row in rows})


__all__ = ["get_operation_service", "operation_view", "router"]
