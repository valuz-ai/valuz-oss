from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.api.deps import (
    get_project_service,
    get_session_service,
    require_current_user_id,
)
from valuz_agent.modules.projects.models import ProjectCreateRequest
from valuz_agent.modules.projects.service import (
    ProjectDeletePreview,
    ProjectDetail,
    ProjectListItem,
    ProjectService,
)
from valuz_agent.modules.sessions.service import SessionService

router = APIRouter(prefix="/v1/projects", tags=["projects"])


class LastSessionPickResponse(BaseModel):
    """Per-project memory of the last (runtime, provider, model) picked.

    Returned by ``GET /v1/projects/{id}/last-session-pick``. All three
    fields can be ``None`` when the project has no usable session
    history (fresh project, or only OAuth-stub sessions). Frontend
    falls back to global Settings → Default in that case.
    """

    runtime_provider: str | None
    provider_id: str | None
    model_id: str | None


@router.get("")
async def list_projects(
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, list[ProjectListItem]]:
    return {"projects": await svc.list_projects(user_id)}


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    try:
        return await svc.get_project(user_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}") from exc


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreateRequest,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    try:
        return await svc.create_project(user_id, payload.name, payload.root_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{project_id}")
async def rename_project(
    project_id: str,
    name: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    try:
        return await svc.rename_project(user_id, project_id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{project_id}/instructions")
async def update_instructions(
    project_id: str,
    instructions_md: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, bool]:
    try:
        await svc.update_instructions(user_id, project_id, instructions_md)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/{project_id}/files")
async def list_files(
    project_id: str,
    depth: int = 2,
    include_hidden: bool = False,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, list[dict[str, object]]]:
    try:
        return {
            "files": await svc.list_files(
                user_id, project_id, depth=depth, include_hidden=include_hidden
            )
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/delete-preview")
async def delete_preview(
    project_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDeletePreview:
    try:
        return await svc.preview_delete(user_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> None:
    try:
        await svc.delete_project(user_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class McpServersPayload(BaseModel):
    slugs: list[str]


@router.get("/{project_id}/last-session-pick")
async def get_last_session_pick(
    project_id: str,
    svc: SessionService = Depends(get_session_service),
) -> LastSessionPickResponse:
    """Return the (runtime, provider, model) from this project's most
    recent session, or empty fields if it has none.

    Powers per-project picker memory in the project composer: a new
    session in this project pre-fills the picker with whatever the
    user last picked here, rather than the global Settings default.
    """
    pick = await svc.get_project_last_pick(project_id)
    if pick is None:
        return LastSessionPickResponse(
            runtime_provider=None,
            provider_id=None,
            model_id=None,
        )
    return LastSessionPickResponse(
        runtime_provider=pick.get("runtime_provider"),
        provider_id=pick.get("provider_id"),
        model_id=pick.get("model_id"),
    )


@router.get("/{project_id}/connectors")
async def get_connectors(
    project_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, list[str]]:
    try:
        return {"slugs": await svc.get_connectors(user_id, project_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{project_id}/connectors")
async def set_connectors(
    project_id: str,
    payload: McpServersPayload,
    user_id: str = Depends(require_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, bool]:
    try:
        await svc.set_connectors(user_id, project_id, payload.slugs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
