"""Project CRUD routes — /api/v1/projects.

Projects are pure host-workspace records (cwd + name + metadata). Agents
live globally and are referenced by sessions; a project no longer carries
its own agent.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_store
from app.schemas import (
    CreateProjectRequest,
    DataResponse,
    ProjectData,
    ProjectListResponse,
    ProjectResponse,
    SessionData,
    SessionListResponse,
    StopReasonSchema,
    UpdateProjectRequest,
    ValidateCwdData,
    ValidateCwdRequest,
    ValidateCwdResponse,
)
from src.core import (
    Project,
    Session,
    StorePort,
)
from src.core.workspace import bootstrap_project_workspace

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

StoreDep = Annotated[StorePort, Depends(get_store)]


def _project_to_data(project: Project) -> ProjectData:
    return ProjectData(
        id=project.id,
        name=project.name,
        cwd=project.cwd,
        status=project.status,
        created_at=project.created_at,
        metadata=project.metadata,
    )


def _session_to_data(session: Session) -> SessionData:
    stop_reason = None
    if session.stop_reason is not None:
        stop_reason = StopReasonSchema(**dataclasses.asdict(session.stop_reason))
    return SessionData(
        id=session.id,
        project_id=session.project_id,
        agent_id=session.agent_id,
        runtime_provider=session.runtime_provider,
        model=session.model,
        status=session.status,
        stop_reason=stop_reason,
        created_at=session.created_at,
        metadata=session.metadata,
    )


def _validate_cwd(raw: str) -> ValidateCwdData:
    if not raw:
        return ValidateCwdData(
            exists=False, is_dir=False, writable=False, has_dot_claude=False, error="empty path"
        )
    abs_path = os.path.abspath(os.path.expanduser(raw))
    exists = os.path.exists(abs_path)
    is_dir = os.path.isdir(abs_path) if exists else False
    writable = os.access(abs_path, os.W_OK) if exists else False
    has_dot_claude = os.path.isdir(os.path.join(abs_path, ".claude")) if is_dir else False
    return ValidateCwdData(
        exists=exists,
        is_dir=is_dir,
        writable=writable,
        has_dot_claude=has_dot_claude,
        absolute_path=abs_path,
    )


@router.post("", status_code=201, response_model=ProjectResponse)
async def create_project(
    body: CreateProjectRequest,
    store: StoreDep,
) -> dict[str, Any]:
    cwd_check = _validate_cwd(body.cwd)
    if not (cwd_check.exists and cwd_check.is_dir and cwd_check.writable):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid cwd: exists={cwd_check.exists}, is_dir={cwd_check.is_dir}, "
                f"writable={cwd_check.writable}"
            ),
        )

    project = Project(
        id=str(uuid.uuid4()),
        name=body.name,
        cwd=cwd_check.absolute_path or body.cwd,
        metadata=body.metadata,
    )
    await store.save_project(project)
    try:
        bootstrap_project_workspace(project)
    except OSError:
        # Filesystem hiccups should not fail project creation —
        # the project record is the source of truth; CLAUDE.md is best-effort seed.
        pass
    return {"data": _project_to_data(project)}


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    store: StoreDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    projects = await store.list_projects(limit=limit, offset=offset)
    return {"data": [_project_to_data(p) for p in projects]}


@router.post("/validate-cwd", response_model=ValidateCwdResponse)
async def validate_cwd(body: ValidateCwdRequest) -> dict[str, Any]:
    return {"data": _validate_cwd(body.cwd)}


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    store: StoreDep,
) -> dict[str, Any]:
    project = await store.load_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status == "deleted":
        raise HTTPException(status_code=410, detail="Project deleted")
    return {"data": _project_to_data(project)}


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    store: StoreDep,
) -> dict[str, Any]:
    project = await store.load_project(project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    new_name = body.name if body.name is not None else project.name
    new_metadata = body.metadata if body.metadata is not None else project.metadata
    updated = dataclasses.replace(project, name=new_name, metadata=new_metadata)
    await store.save_project(updated)
    return {"data": _project_to_data(updated)}


@router.delete("/{project_id}", response_model=DataResponse)
async def delete_project(
    project_id: str,
    store: StoreDep,
) -> dict[str, Any]:
    deleted = await store.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"data": None}


@router.get("/{project_id}/sessions", response_model=SessionListResponse)
async def list_project_sessions(
    project_id: str,
    store: StoreDep,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    project = await store.load_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    sessions = await store.list_sessions(
        project_id=project_id, status=status, limit=limit, offset=offset
    )
    return {"data": [_session_to_data(s) for s in sessions]}
