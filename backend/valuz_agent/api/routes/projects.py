from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from valuz_agent.api.deps import (
    get_current_user_id,
    get_project_pack_service,
    get_project_service,
    get_session_service,
)
from valuz_agent.api.routes.onboarding import _resolve_deploy_target
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.agent_packs.errors import PackImportFailed
from valuz_agent.modules.project_packs.errors import (
    ProjectNotExportable,
    ProjectPackImportFailed,
    ProjectPackNotFound,
)
from valuz_agent.modules.project_packs.service import ProjectPackService
from valuz_agent.modules.projects.models import ProjectCreateRequest
from valuz_agent.modules.projects.service import (
    ProjectDeletePreview,
    ProjectDetail,
    ProjectListItem,
    ProjectService,
)
from valuz_agent.modules.sessions.service import SessionService
from valuz_agent.modules.settings.preferences import get_default_effort

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
    # The last chat conversation's agent — seeds the composer's Chat mode.
    agent_slug: str | None = None
    # The Lead of the last task — seeds the composer's Task mode, separately
    # from the chat agent so each mode remembers its own role.
    task_agent_slug: str | None = None


@router.get("")
async def list_projects(
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, list[ProjectListItem]]:
    return {"projects": await svc.list_projects(user_id)}


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    try:
        return await svc.get_project(user_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}") from exc


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    try:
        return await svc.create_project(user_id, payload.name, payload.root_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{project_id}/files", status_code=201)
async def upload_project_files(
    project_id: str,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, object]:
    """Upload one or more files into the project's cwd (multipart form).

    Each upload's ``filename`` is the target relative path (must be
    relative; parent dirs are created). Used by cloud-managed projects
    whose cwd the client cannot reach directly; equally valid locally.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    written: list[str] = []
    try:
        for upload in files:
            data = await upload.read()
            rel = await svc.write_file(user_id, project_id, upload.filename or "", data)
            written.append(rel)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project_id": project_id, "written": written}


@router.patch("/{project_id}")
async def rename_project(
    project_id: str,
    name: str,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    try:
        return await svc.rename_project(user_id, project_id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{project_id}/default-lead")
async def set_default_lead(
    project_id: str,
    agent_slug: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> ProjectDetail:
    """Set (or clear, by omitting ``agent_slug``) the project's default task lead."""
    try:
        return await svc.set_default_lead(user_id, project_id, agent_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{project_id}/instructions")
async def update_instructions(
    project_id: str,
    instructions_md: str,
    user_id: str = Depends(get_current_user_id),
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
    worktree: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, list[dict[str, object]]]:
    try:
        return {
            "files": await svc.list_files(
                user_id,
                project_id,
                depth=depth,
                include_hidden=include_hidden,
                worktree=worktree,
            )
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# File CONTENT is no longer served by the API: the single-file read
# (``/files/{path}``) and raw download (``/raw-files/{path}``) endpoints were
# removed. A file's bytes are fetched by the client from an access address it
# gets via ``POST /v1/files/resolve`` (local path / presigned URL) — the backend
# never proxies file streams. The directory-tree ``GET /files`` above stays.
# See docs/design/file-address-resolution.md.


@router.get("/{project_id}/delete-preview")
async def delete_preview(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
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
    user_id: str = Depends(get_current_user_id),
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
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> LastSessionPickResponse:
    """Return the (runtime, provider, model) from this project's most
    recent session, or empty fields if it has none.

    Powers per-project picker memory in the project composer: a new
    session in this project pre-fills the picker with whatever the
    user last picked here, rather than the global Settings default.
    """
    pick = await svc.get_project_last_pick(project_id, user_id=user_id)
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
        agent_slug=pick.get("agent_slug"),
        task_agent_slug=pick.get("task_agent_slug"),
    )


@router.get("/{project_id}/connectors")
async def get_connectors(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
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
    user_id: str = Depends(get_current_user_id),
    svc: ProjectService = Depends(get_project_service),
) -> dict[str, bool]:
    try:
        await svc.set_connectors(user_id, project_id, payload.slugs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Export / Import (.valuzpack archives — unified pack format, project target)
# ---------------------------------------------------------------------------


class ImportProjectPreviewMember(BaseModel):
    agent_slug: str
    source_agent_slug: str
    name: str
    description: str
    in_library: bool


class ImportProjectPreviewAutomation(BaseModel):
    name: str
    agent_slug: str
    trigger_kind: str
    cron_expr: str | None
    interval_seconds: int | None
    status: str


class ImportProjectPreviewSkill(BaseModel):
    slug: str
    source: str


class ImportProjectPreviewConnector(BaseModel):
    slug: str
    display_name: str
    requires_credentials: bool
    requires_setup: bool
    already_present: bool


class ImportProjectPreviewResponse(BaseModel):
    preview_id: str
    project: dict[str, Any]
    name_conflict: bool
    members: list[ImportProjectPreviewMember]
    automations: list[ImportProjectPreviewAutomation]
    project_skills: list[str]
    project_connectors: list[str]
    skills: list[ImportProjectPreviewSkill]
    connectors: list[ImportProjectPreviewConnector]
    has_memory: bool


class ImportProjectConfirmRequest(BaseModel):
    preview_id: str
    # Optional user-picked project folder. When omitted, the service creates
    # the project under a managed cwd (``data_dir/projects/{id}/``).
    root_path: str | None = None
    # Optional rename on the way in — how the importer resolves a clash with
    # a project they already own. Omitted keeps the name from the pack.
    name: str | None = None


class ConnectorToConfigure(BaseModel):
    slug: str
    display_name: str
    requires_credentials: bool
    requires_setup: bool


class ImportProjectConfirmResponse(BaseModel):
    status: str
    project: dict[str, Any] | None = None
    project_id: str | None = None
    project_name: str | None = None
    members_created: int = 0
    members_reused: int = 0
    agents_created: int = 0
    agents_skipped: int = 0
    automations_created: int = 0
    automation_errors: list[dict[str, str]] = []
    members: list[dict[str, Any]] = []
    automations: list[dict[str, Any]] = []
    connectors_to_configure: list[ConnectorToConfigure] = []


def _safe_project_filename(name: str) -> str:
    import re

    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-")
    return (stem or "project")[:64]


@router.get("/{project_id}/export")
async def export_project(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectPackService = Depends(get_project_pack_service),
    project_svc: ProjectService = Depends(get_project_service),
) -> StreamingResponse:
    """Export the project (team + automations + project skills + project
    connectors + memory) as a downloadable ``.valuzpack`` archive (the unified
    pack format — a project pack carries a ``project`` target)."""
    try:
        data = await svc.export_project(user_id, project_id)
    except ProjectPackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectNotExportable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Filename derives from the project name (looked up best-effort).
    name_stem = "project"
    try:
        name_stem = (await project_svc.get_project(user_id, project_id)).name
    except Exception:  # noqa: BLE001 — filename best-effort
        pass
    filename = f"{_safe_project_filename(name_stem)}.valuzpack"
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/import-preview",
    response_model=ImportProjectPreviewResponse,
)
async def import_project_preview(
    file: Annotated[UploadFile, File(...)],
    user_id: str = Depends(get_current_user_id),
    svc: ProjectPackService = Depends(get_project_pack_service),
) -> ImportProjectPreviewResponse:
    """Stage an uploaded ``.valuzpack`` project archive and return what's
    inside (members, automations, skills, connectors) plus a ``preview_id``
    to confirm with. The legacy ``.valuz-project`` format is rejected."""
    data = await file.read()
    try:
        preview = await svc.preview_import(user_id, data)
    except ProjectPackImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportProjectPreviewResponse.model_validate(preview)


@router.post(
    "/import/confirm",
    response_model=ImportProjectConfirmResponse,
)
async def import_project_confirm(
    body: ImportProjectConfirmRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ProjectPackService = Depends(get_project_pack_service),
    db: Any = Depends(get_async_session),
) -> ImportProjectConfirmResponse:
    """Commit a staged import: install skills + connectors, recreate library
    agents de-duped by slug, create the project row, restore memory, recreate
    members + automations + project skill/connector configs. If a project
    with the same name exists for the caller, SKIP (return
    ``status="skipped_name_conflict"``)."""
    runtime, provider_id, model = await _resolve_deploy_target(db, user_id)
    effort = await get_default_effort(db, user_id=user_id)
    try:
        result = await svc.confirm_import(
            user_id,
            body.preview_id,
            runtime=runtime,
            provider_id=provider_id,
            model=model,
            effort=effort,
            root_path=body.root_path,
            name=body.name,
        )
    except (ProjectPackImportFailed, PackImportFailed) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # Directory the user picked is already bound to another project.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ImportProjectConfirmResponse(
        status=result.get("status", "created"),
        project=result.get("project"),
        project_id=result.get("project_id"),
        project_name=result.get("project_name"),
        members_created=result.get("members_created", 0),
        members_reused=result.get("members_reused", 0),
        agents_created=result.get("agents_created", 0),
        agents_skipped=result.get("agents_skipped", 0),
        automations_created=result.get("automations_created", 0),
        automation_errors=result.get("automation_errors", []),
        members=result.get("members", []),
        automations=result.get("automations", []),
        connectors_to_configure=[
            ConnectorToConfigure(**c) for c in result.get("connectors_to_configure", [])
        ],
    )
