"""Versioned Playbook definitions and persisted execution records."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.facade.projects import ProjectLibrary, get_project_library
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)
from valuz_agent.modules.playbooks.schemas import (
    PlaybookCreateRequest,
    PlaybookDefinitionUpdateRequest,
    PlaybookDefinitionView,
    PlaybookRunCreateRequest,
    PlaybookRunUpdateRequest,
    PlaybookRunView,
    PlaybookVersionCreateRequest,
    PlaybookVersionView,
)
from valuz_agent.modules.playbooks.service import PlaybookService

router = APIRouter(prefix="/v1/playbooks", tags=["playbooks"])


async def get_playbook_service(
    projects: ProjectLibrary = Depends(get_project_library),
) -> AsyncGenerator[PlaybookService, None]:
    async with async_unit_of_work() as db:
        yield PlaybookService(db, projects)


def _definition(row: PlaybookDefinitionRow) -> PlaybookDefinitionView:
    return PlaybookDefinitionView(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        status=row.status,
        origin=row.origin,
        source_definition_id=row.source_definition_id,
        current_version=row.current_version,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version(row: PlaybookVersionRow) -> PlaybookVersionView:
    return PlaybookVersionView(
        id=row.id,
        definition_id=row.definition_id,
        version=row.version,
        content=row.content,
        reference_metadata=row.reference_metadata,
        default_executor=row.default_executor,
        created_by=row.created_by,
        produced_by_run=row.produced_by_run,
        base_version=row.base_version,
        created_at=row.created_at,
    )


def _run(row: PlaybookRunRow) -> PlaybookRunView:
    return PlaybookRunView(
        id=row.id,
        definition_id=row.definition_id,
        definition_version=row.definition_version,
        project_id=row.project_id,
        research_scope_id=row.research_scope_id,
        status=row.status,
        trigger_kind=row.trigger_kind,
        trigger_ref=row.trigger_ref,
        subject_refs=row.subject_refs,
        input_snapshot=row.input_snapshot,
        context_snapshot=row.context_snapshot,
        content_snapshot=row.content_snapshot,
        resolved_references=row.resolved_references,
        extra_instruction=row.extra_instruction,
        executor_snapshot=row.executor_snapshot,
        session_id=row.session_id,
        task_id=row.task_id,
        plan=row.plan,
        tasks=row.tasks,
        tool_calls=row.tool_calls,
        approvals=row.approvals,
        artifact_refs=row.artifact_refs,
        change_set_refs=row.change_set_refs,
        output_refs=row.output_refs,
        checkpoint=row.checkpoint,
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("")
async def list_playbooks(
    project_id: str | None = None,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> list[PlaybookDefinitionView]:
    try:
        return [_definition(item) for item in await service.list_definitions(user_id, project_id)]
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=201)
async def create_playbook(
    body: PlaybookCreateRequest,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, object]:
    try:
        definition, version = await service.create_definition(user_id, body)
        return {
            "definition": _definition(definition).model_dump(),
            "version": _version(version).model_dump(),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/list")
async def list_playbook_runs(
    project_id: str | None = None,
    definition_id: str | None = None,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> list[PlaybookRunView]:
    return [
        _run(item)
        for item in await service.list_runs(
            user_id, project_id=project_id, definition_id=definition_id
        )
    ]


@router.post("/runs", status_code=201)
async def create_playbook_run(
    body: PlaybookRunCreateRequest,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> PlaybookRunView:
    try:
        return _run(await service.create_run(user_id, body))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_playbook_run(
    run_id: str,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> PlaybookRunView:
    try:
        return _run(await service.get_run(user_id, run_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/runs/{run_id}")
async def update_playbook_run(
    run_id: str,
    body: PlaybookRunUpdateRequest,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> PlaybookRunView:
    try:
        return _run(await service.update_run(user_id, run_id, body))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# Dynamic Definition routes intentionally come after the static /runs routes;
# otherwise Starlette resolves `/runs` as `definition_id="runs"`.
@router.get("/{definition_id}")
async def get_playbook(
    definition_id: str,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, object]:
    try:
        definition = await service.get_definition(user_id, definition_id)
        versions = await service.list_versions(user_id, definition_id)
        return {
            "definition": _definition(definition).model_dump(),
            "current_version": _version(versions[0]).model_dump(),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{definition_id}")
async def update_playbook(
    definition_id: str,
    body: PlaybookDefinitionUpdateRequest,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> PlaybookDefinitionView:
    try:
        return _definition(await service.update_definition(user_id, definition_id, body))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{definition_id}/versions", status_code=201)
async def create_playbook_version(
    definition_id: str,
    body: PlaybookVersionCreateRequest,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, object]:
    try:
        definition, version = await service.create_version(user_id, definition_id, body)
        return {
            "definition": _definition(definition).model_dump(),
            "version": _version(version).model_dump(),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{definition_id}/versions")
async def list_playbook_versions(
    definition_id: str,
    service: PlaybookService = Depends(get_playbook_service),
    user_id: str = Depends(get_current_user_id),
) -> list[PlaybookVersionView]:
    try:
        return [_version(item) for item in await service.list_versions(user_id, definition_id)]
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["get_playbook_service", "router"]
