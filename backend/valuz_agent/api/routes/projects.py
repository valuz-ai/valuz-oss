from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.api.deps import get_session_service, get_workspace_service
from valuz_agent.modules.projects.models import ProjectCreateRequest
from valuz_agent.modules.projects.service import (
    WorkspaceDeletePreview,
    WorkspaceDetail,
    WorkspaceListItem,
    WorkspaceService,
)
from valuz_agent.modules.sessions.service import SessionService

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


class LastSessionPickResponse(BaseModel):
    """Per-workspace memory of the last (runtime, provider, model) picked.

    Returned by ``GET /v1/workspaces/{id}/last-session-pick``. All three
    fields can be ``None`` when the workspace has no usable session
    history (fresh project, or only OAuth-stub sessions). Frontend
    falls back to global Settings → Default in that case.
    """

    runtime_provider: str | None
    provider_id: str | None
    model_id: str | None


@router.get("")
async def list_workspaces(
    svc: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, list[WorkspaceListItem]]:
    return {"workspaces": await svc.list_workspaces()}


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDetail:
    try:
        return await svc.get_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown workspace: {workspace_id}") from exc


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreateRequest,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDetail:
    try:
        return await svc.create_project(payload.name, payload.root_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{workspace_id}")
async def rename_workspace(
    workspace_id: str,
    name: str,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDetail:
    try:
        return await svc.rename_workspace(workspace_id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{workspace_id}/instructions")
async def update_instructions(
    workspace_id: str,
    instructions_md: str,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, bool]:
    try:
        await svc.update_instructions(workspace_id, instructions_md)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/{workspace_id}/files")
async def list_files(
    workspace_id: str,
    depth: int = 2,
    include_hidden: bool = False,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, list[dict[str, object]]]:
    try:
        return {
            "files": await svc.list_files(workspace_id, depth=depth, include_hidden=include_hidden)
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workspace_id}/delete-preview")
async def delete_preview(
    workspace_id: str,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDeletePreview:
    try:
        return await svc.preview_delete(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: str,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> None:
    try:
        await svc.delete_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class McpServersPayload(BaseModel):
    slugs: list[str]


@router.get("/{workspace_id}/last-session-pick")
def get_last_session_pick(
    workspace_id: str,
    svc: SessionService = Depends(get_session_service),
) -> LastSessionPickResponse:
    """Return the (runtime, provider, model) from this workspace's most
    recent session, or empty fields if it has none.

    Powers per-project picker memory in the project composer: a new
    session in this workspace pre-fills the picker with whatever the
    user last picked here, rather than the global Settings default.
    """
    pick = svc.get_workspace_last_pick(workspace_id)
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


@router.get("/{workspace_id}/connectors")
async def get_connectors(
    workspace_id: str,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, list[str]]:
    try:
        return {"slugs": await svc.get_connectors(workspace_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{workspace_id}/connectors")
async def set_connectors(
    workspace_id: str,
    payload: McpServersPayload,
    svc: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, bool]:
    try:
        await svc.set_connectors(workspace_id, payload.slugs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
