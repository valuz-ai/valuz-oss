"""HTTP routes for the agent-team template library (built-in Agent Packs).

  GET  /v1/agent-templates              — list the recommended team packs
  POST /v1/agent-templates/{id}:add     — import a pack's agents into the library

A built-in pack is a ready-made set of agents (the recommended teams: 内容 /
投研 / 产研). `:add` imports the missing ones (idempotent by fixed slug),
materializing the pack's bundled skills on the way. Deploying the resulting
agents into a project — where they regroup into a team — is the existing,
separate flow (onboarding layers it on top of this same import). The pack format
is defined in ``docs/agent-pack/design.md``; user import/export builds on it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import require_current_user_id
from valuz_agent.api.routes.agents import AgentResponse
from valuz_agent.api.routes.onboarding import _resolve_deploy_target
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.agent_packs.errors import PackImportFailed, PackNotFound
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.service import AgentNotFoundError, AgentService
from valuz_agent.modules.settings.preferences import get_default_effort

router = APIRouter(tags=["agent-templates"])

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


async def _get_pack_service(
    db: AsyncSession = Depends(get_async_session),
) -> AgentPackService:
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.ports.extensions import ext

    connector_svc = ConnectorService(ConnectorDatastore(db), ext.secret_store)
    agent_svc = AgentService(db, connector_service=connector_svc)  # type: ignore[arg-type]
    return AgentPackService(agent_svc)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AgentTemplateRoleResponse(BaseModel):
    slug: str
    name: str
    description: str
    instructions: str
    avatar: str
    skills: list[str]
    connector_types: list[str]
    runtime: str
    effort: EffortLevel | None = None
    in_library: bool


class AgentTemplateResponse(BaseModel):
    id: str
    scenario: str
    name: str
    description: str
    icon: str
    added: bool
    roles: list[AgentTemplateRoleResponse]


class AgentTemplateListResponse(BaseModel):
    templates: list[AgentTemplateResponse]


class AddAgentTemplateResponse(BaseModel):
    template_id: str
    created: int
    skipped: int
    roles: list[AgentResponse]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/v1/agent-templates", response_model=AgentTemplateListResponse)
async def list_agent_templates(
    user_id: str = Depends(require_current_user_id),
    svc: AgentPackService = Depends(_get_pack_service),
) -> AgentTemplateListResponse:
    """List the built-in team packs, annotated with per-agent/per-pack library
    state so the panel can mark a fully-present pack as added."""
    packs = await svc.list_packs(user_id)
    return AgentTemplateListResponse.model_validate({"templates": packs})


@router.post("/v1/agent-templates/{template_id}:add", response_model=AddAgentTemplateResponse)
async def add_agent_template(
    template_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: AgentPackService = Depends(_get_pack_service),
    db: AsyncSession = Depends(get_async_session),
) -> AddAgentTemplateResponse:
    """Import the pack's missing agents into the caller's library.

    Runtime / model / provider / effort follow the user's global defaults — the
    same resolver onboarding uses (raises 422 when no model channel is wired).
    """
    runtime, provider_id, model = await _resolve_deploy_target(db)
    effort = await get_default_effort(db)
    try:
        result = await svc.import_pack(
            user_id,
            template_id,
            runtime=runtime,
            provider_id=provider_id,
            model=model,
            effort=effort,
        )
    except PackNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Agent pack not found: {template_id}") from exc

    return AddAgentTemplateResponse(
        template_id=result["template_id"],
        created=result["created"],
        skipped=result["skipped"],
        roles=[AgentResponse.model_validate(r) for r in result["roles"]],
    )


# ---------------------------------------------------------------------------
# Import / export (user-supplied .valuzpack archives)
# ---------------------------------------------------------------------------


class ExportCollection(BaseModel):
    name: str | None = None
    description: str | None = None
    scenario: str | None = None
    icon: str | None = None


class ExportPackRequest(BaseModel):
    agent_slugs: list[str]
    collection: ExportCollection | None = None


class ImportPreviewAgent(BaseModel):
    slug: str
    name: str
    description: str
    in_library: bool


class ImportPreviewSkill(BaseModel):
    slug: str
    source: str


class ImportPreviewConnector(BaseModel):
    slug: str
    display_name: str
    requires_credentials: bool
    requires_setup: bool
    already_present: bool


class ImportPreviewCollection(BaseModel):
    id: str
    name: str
    description: str
    scenario: str
    icon: str


class ImportPackPreviewResponse(BaseModel):
    preview_id: str
    collection: ImportPreviewCollection | None = None
    agents: list[ImportPreviewAgent]
    skills: list[ImportPreviewSkill]
    connectors: list[ImportPreviewConnector]


class ImportPackConfirmRequest(BaseModel):
    preview_id: str


class ConnectorToConfigure(BaseModel):
    slug: str
    display_name: str
    requires_credentials: bool
    requires_setup: bool


class ImportPackConfirmResponse(BaseModel):
    created: int
    skipped: int
    roles: list[AgentResponse]
    connectors_to_configure: list[ConnectorToConfigure]


def _safe_filename(name: str) -> str:
    """Conservative filename stem for the Content-Disposition header."""
    import re

    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
    return (stem or "agents")[:64]


@router.post("/v1/agent-packs/export")
async def export_agent_pack(
    body: ExportPackRequest,
    user_id: str = Depends(require_current_user_id),
    svc: AgentPackService = Depends(_get_pack_service),
) -> StreamingResponse:
    """Export the chosen library agents (with their skills + connector
    definitions, secrets stripped) as a downloadable ``.valuzpack`` archive."""
    if not body.agent_slugs:
        raise HTTPException(status_code=422, detail="agent_slugs must not be empty")
    collection: dict[str, Any] | None = body.collection.model_dump() if body.collection else None
    try:
        data = await svc.export_agents(user_id, body.agent_slugs, collection=collection)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if body.collection and body.collection.name:
        stem = body.collection.name
    elif len(body.agent_slugs) == 1:
        stem = body.agent_slugs[0]
    else:
        stem = "agents"
    filename = f"{_safe_filename(stem)}.valuzpack"
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/v1/agent-packs/import", response_model=ImportPackPreviewResponse)
async def import_agent_pack(
    file: Annotated[UploadFile, File(...)],
    user_id: str = Depends(require_current_user_id),
    svc: AgentPackService = Depends(_get_pack_service),
) -> ImportPackPreviewResponse:
    """Upload a ``.valuzpack`` and stage it — returns a preview (what's inside,
    which agents already exist, which connectors need setup) plus a
    ``preview_id`` to confirm with."""
    data = await file.read()
    try:
        preview = await svc.preview_import(user_id, data)
    except PackImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportPackPreviewResponse.model_validate(preview)


@router.post("/v1/agent-packs/import/confirm", response_model=ImportPackConfirmResponse)
async def confirm_agent_pack_import(
    body: ImportPackConfirmRequest,
    user_id: str = Depends(require_current_user_id),
    svc: AgentPackService = Depends(_get_pack_service),
    db: AsyncSession = Depends(get_async_session),
) -> ImportPackConfirmResponse:
    """Commit a staged import: install skills, register connectors, create the
    agents (runtime/model/provider follow the user's defaults, like ``:add``)."""
    runtime, provider_id, model = await _resolve_deploy_target(db)
    effort = await get_default_effort(db)
    try:
        result = await svc.confirm_import(
            user_id,
            body.preview_id,
            runtime=runtime,
            provider_id=provider_id,
            model=model,
            effort=effort,
        )
    except PackImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ImportPackConfirmResponse(
        created=result["created"],
        skipped=result["skipped"],
        roles=[AgentResponse.model_validate(r) for r in result["roles"]],
        connectors_to_configure=[
            ConnectorToConfigure(**c) for c in result["connectors_to_configure"]
        ],
    )
