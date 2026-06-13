"""HTTP routes for Agents and Project Members.

Endpoints:
  GET  /v1/agents                        — list all official agents
  GET  /v1/agents/{slug}                 — get single agent
  GET  /v1/projects/{id}/agents                  — list project members
  POST /v1/projects/{id}/agents                  — create blank agent
  POST /v1/projects/{id}/agents:deploy            — 派驻 (live-reference) a library agent
  PATCH /v1/projects/{id}/agents/{slug}          — update member agent
  DELETE /v1/projects/{id}/agents/{slug}         — delete member + kernel agent
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import require_current_user_id
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.agents.service import (
    AgentNotDeletableError,
    AgentNotFoundError,
    AgentService,
    AgentStillDeployedError,
    MemberAlreadyExistsError,
    MemberNotFoundError,
)

router = APIRouter(tags=["agents"])

# Cross-runtime reasoning-effort budget (mirrors kernel ``EffortLevel`` /
# ``ModelSettings.effort``). ``None`` = no agent-level override (SDK default).
# Defined locally so the API layer doesn't import kernel internals directly.
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


async def _get_agent_service(
    db: AsyncSession = Depends(get_async_session),
) -> AgentService:
    """Per-request AgentService bound to the request's async DB session.

    The ConnectorService is injected so AgentService delegates connector→MCP
    resolution instead of touching the secret store directly.
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.secret_store import FileSecretStore
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService

    connector_svc = ConnectorService(ConnectorDatastore(db), FileSecretStore(settings.secrets_dir))
    return AgentService(db, connector_service=connector_svc)


# ---------------------------------------------------------------------------
# Response / Request schemas
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    instructions: str
    runtime: str
    model: str
    skills: list[str]
    connector_types: list[str]
    provider_id: str | None = None
    effort: EffortLevel | None = None
    source: str
    readonly: bool = False
    deletable: bool = True
    # Preset icon key or uploaded asset URL (08-agents-module v2); null = unset.
    avatar: str | None = None
    # Shared kernel AgentConfig id (v2 live-reference). null until first deploy
    # (built lazily). Surfaced so the frontend can map a project member back to

    model_config = {"from_attributes": True}


class ConnectorBindingInput(BaseModel):
    type: str
    account_id: str | None = None


class CreateBlankAgentRequest(BaseModel):
    # Optional: backend derives a CJK-preserving slug from ``name`` when
    # omitted (VALUZ-AGENT-SLUG). UI no longer computes slugs client-side.
    agent_slug: str | None = None
    name: str
    instructions: str = ""
    runtime: str = "claude_agent"
    model: str = "claude-sonnet-4-6"
    provider_id: str | None = None
    effort: EffortLevel | None = None
    skills: list[str] | None = None
    connector_bindings: list[ConnectorBindingInput] | None = None


class DeployAgentRequest(BaseModel):
    """v2派驻: reference a library agent into a project. Config lives on the
    agent (live reference), so there's no per-deploy provider/model/connector
    override anymore — just the source agent + an optional project-local handle."""

    source_agent_slug: str
    # Optional: backend derives from the source agent's name when omitted,
    # unique within the target project (VALUZ-AGENT-SLUG).
    agent_slug: str | None = None


class ProjectMemberResponse(BaseModel):
    id: str
    project_id: str
    agent_slug: str
    source_agent_slug: str | None

    model_config = {"from_attributes": True}


class AgentSummary(BaseModel):
    """Kernel agent summary returned alongside membership rows."""

    id: str
    name: str
    model: str
    runtime_provider: str
    instructions: str
    skills: list[str]
    # Connector slugs currently bound to this agent (from metadata).
    connectors: list[str]
    # Pinned model provider id (from metadata); null = env/default fallback.
    provider_id: str | None = None
    # Reasoning-effort budget; null = no override (runtime SDK default).
    effort: EffortLevel | None = None


class MemberWithAgentResponse(BaseModel):
    member: ProjectMemberResponse
    agent: AgentSummary | None


def _agent_to_summary(agent: Any) -> AgentSummary:
    meta = agent.metadata or {}
    bindings = meta.get("connector_bindings") or []
    connectors = [b["type"] for b in bindings if isinstance(b, dict) and b.get("type")]
    return AgentSummary(
        id=agent.id,
        name=agent.name,
        model=agent.model,
        runtime_provider=str(agent.runtime_provider),
        instructions=agent.instructions,
        skills=list(agent.skills),
        connectors=connectors,
        provider_id=meta.get("provider_id"),
        effort=getattr(agent, "effort", None),
    )


def _member_with_agent(row: dict[str, Any]) -> MemberWithAgentResponse:
    return MemberWithAgentResponse(
        member=ProjectMemberResponse.model_validate(row["member"]),
        agent=_agent_to_summary(row["agent"]) if row["agent"] is not None else None,
    )


# ---------------------------------------------------------------------------
# Agent routes
# ---------------------------------------------------------------------------


@router.get("/v1/agents")
async def list_agents(
    source: str | None = None,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> dict:
    """List agents, optionally filtered by source (official|custom)."""
    from valuz_agent.ports.extensions import ext

    rows = await svc.list_agents(user_id, source=source)
    items = [AgentResponse.model_validate(r).model_dump() for r in rows]
    items = await ext.resource_enhancer.enhance("agent", items)
    return {"agents": items}


@router.get("/v1/agents/{slug}", response_model=AgentResponse)
async def get_agent(
    slug: str,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> AgentResponse:
    """Get a single agent by slug."""
    try:
        row = await svc.get_agent(user_id, slug)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    return AgentResponse.model_validate(row)


@router.get("/v1/agents/{slug}/deployments")
async def list_agent_deployments(
    slug: str,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> dict:
    """List the projects (projects) this agent is派驻'd into (live-reference).

    Powers the agent detail「派驻于 N 个项目」panel + delete-guard UX.
    """
    try:
        deployments = await svc.list_deployments(user_id, slug)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    return {"deployments": deployments, "count": len(deployments)}


class CreateAgentRequest(BaseModel):
    # Optional: backend derives a CJK-preserving, globally-unique slug from
    # ``name`` when omitted (VALUZ-AGENT-SLUG). UI sends name only.
    slug: str | None = None
    name: str
    description: str = ""
    instructions: str = ""
    runtime: str = "claude_agent"
    model: str = "claude-sonnet-4-6"
    skills: list[str] = []
    connector_types: list[str] = []
    provider_id: str | None = None
    effort: EffortLevel | None = None
    avatar: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    runtime: str | None = None
    model: str | None = None
    skills: list[str] | None = None
    connector_types: list[str] | None = None
    provider_id: str | None = None
    effort: EffortLevel | None = None
    avatar: str | None = None


@router.post("/v1/agents", status_code=201, response_model=AgentResponse)
async def create_agent(
    payload: CreateAgentRequest,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> AgentResponse:
    """Create a user-defined agent."""
    try:
        row = await svc.create_agent(user_id, payload.model_dump())
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentResponse.model_validate(row)


@router.patch("/v1/agents/{slug}", response_model=AgentResponse)
async def update_agent(
    slug: str,
    payload: UpdateAgentRequest,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> AgentResponse:
    """Patch an agent (official or custom)."""
    try:
        row = await svc.update_agent(user_id, slug, payload.model_dump(exclude_none=True))
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    return AgentResponse.model_validate(row)


@router.delete("/v1/agents/{slug}", status_code=204)
async def delete_agent(
    slug: str,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> None:
    """Delete an agent."""
    try:
        await svc.delete_agent(user_id, slug)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    except (AgentStillDeployedError, AgentNotDeletableError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Project member routes
# ---------------------------------------------------------------------------


@router.get(
    "/v1/projects/{project_id}/agents",
    response_model=dict[str, list[MemberWithAgentResponse]],
)
async def list_members(
    project_id: str,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> dict[str, list[MemberWithAgentResponse]]:
    """List all agent members in a project."""
    rows = await svc.list_members(user_id, project_id)
    return {"agents": [_member_with_agent(r) for r in rows]}


@router.post(
    "/v1/projects/{project_id}/agents",
    status_code=201,
    response_model=MemberWithAgentResponse,
)
async def create_blank_agent(
    project_id: str,
    payload: CreateBlankAgentRequest,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> MemberWithAgentResponse:
    """Create a blank (source-agent-free) agent in a project."""
    bindings = (
        [b.model_dump() for b in payload.connector_bindings] if payload.connector_bindings else None
    )
    try:
        result = await svc.create_blank_agent(
            user_id,
            project_id=project_id,
            agent_slug=payload.agent_slug,
            name=payload.name,
            instructions=payload.instructions,
            runtime=payload.runtime,
            model=payload.model,
            connector_bindings=bindings,
            skills=payload.skills,
            provider_id=payload.provider_id,
            effort=payload.effort,
        )
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _member_with_agent(result)


@router.post(
    "/v1/projects/{project_id}/agents:deploy",
    status_code=201,
    response_model=MemberWithAgentResponse,
)
async def deploy_agent(
    project_id: str,
    payload: DeployAgentRequest,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> MemberWithAgentResponse:
    """派驻: deploy (live-reference) a library agent into a project."""
    try:
        result = await svc.deploy_agent(
            user_id,
            project_id=project_id,
            source_agent_slug=payload.source_agent_slug,
            agent_slug=payload.agent_slug,
        )
    except AgentNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Source agent not found: {payload.source_agent_slug}"
        ) from exc
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _member_with_agent(result)


@router.delete(
    "/v1/projects/{project_id}/agents/{agent_slug}",
    status_code=204,
)
async def delete_member(
    project_id: str,
    agent_slug: str,
    user_id: str = Depends(require_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> None:
    """Delete a project agent and its kernel AgentConfig."""
    try:
        await svc.delete_member(user_id, project_id, agent_slug)
    except MemberNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_slug}") from exc
