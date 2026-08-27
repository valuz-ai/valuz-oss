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

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.i18n import t
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.agents.builtin import (
    VALURION_DESCRIPTION,
    VALURION_NAME,
    VALURION_SLUG,
)
from valuz_agent.modules.agents.service import (
    AgentManagedFieldError,
    AgentNotDeletableError,
    AgentNotFoundError,
    AgentService,
    AgentStillDeployedError,
    InvalidAgentSlugError,
    MemberAlreadyExistsError,
    MemberNotFoundError,
)

router = APIRouter(tags=["agents"])

# Cross-runtime reasoning-effort budget (mirrors kernel ``EffortLevel`` /
# ``ModelSettings.effort``). ``None`` = no agent-level override (SDK default).
# Defined locally so the API layer doesn't import kernel internals directly.
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

# Mirrors kernel ``RuntimeProvider`` (``kernel.src.core.types``). Validated at
# the API boundary so a bad value is rejected at write time, not at dispatch.
RuntimeProvider = Literal["claude_agent", "codex", "deepagents", "deepseek_harness"]


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
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService

    connector_svc = ConnectorService(ConnectorDatastore(db))
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
    knowledge_scope: list[str] = []
    provider_id: str | None = None
    effort: EffortLevel | None = None
    kind: Literal["system", "standard"] = "standard"
    resource_policy: Literal["explicit", "all_available"] = "explicit"
    inherit_global_instructions: bool = True
    permission_mode: str = "full_access"
    source: str
    readonly: bool = False
    deletable: bool = True
    # Preset icon key or uploaded asset URL (08-agents-module v2); null = unset.
    avatar: str | None = None
    # Shared kernel AgentConfig id (v2 live-reference). null until first deploy
    # (built lazily). Surfaced so the frontend can map a project member back to

    model_config = {"from_attributes": True}


def _agent_locale(accept_language: str | None) -> str:
    """Resolve the two UI locales from the request's language preference."""
    if isinstance(accept_language, str):
        for raw in accept_language.split(","):
            prefix = raw.split(";")[0].strip().split("-")[0].lower()
            if prefix == "zh":
                return "zh-CN"
            if prefix == "en":
                return "en-US"
    # API clients that do not send a preference retain the historical
    # English canonical display instead of receiving a process-global locale.
    return "en-US"


def _localize_agent_mapping(
    item: dict[str, Any],
    accept_language: str | None,
) -> dict[str, Any]:
    """Localize Valurion's presentation without mutating its stored identity."""
    if str(item.get("slug") or "") != VALURION_SLUG:
        return item
    locale = _agent_locale(accept_language)
    return {
        **item,
        "name": t(
            "agent.valurionName",
            fallback=VALURION_NAME,
            locale=locale,
        ),
        "description": t(
            "agent.valurionDescription",
            fallback=VALURION_DESCRIPTION,
            locale=locale,
        ),
    }


def _localized_agent_response(
    row: Any,
    accept_language: str | None,
) -> AgentResponse:
    data = AgentResponse.model_validate(row).model_dump()
    return AgentResponse.model_validate(
        _localize_agent_mapping(data, accept_language),
    )


class ConnectorBindingInput(BaseModel):
    type: str
    account_id: str | None = None


class CreateBlankAgentRequest(BaseModel):
    # Optional: backend derives a CJK-preserving slug from ``name`` when
    # omitted (VALUZ-AGENT-SLUG). UI no longer computes slugs client-side.
    agent_slug: str | None = None
    name: str
    instructions: str = ""
    runtime: RuntimeProvider | None = None  # None → factory default (ext.model_defaults)
    model: str | None = None  # None → factory default (ext.model_defaults)
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
    # How this member resolves resources. ``all_available`` members (Valurion)
    # carry an EMPTY ``skills`` on purpose — the real set is resolved from the
    # owner's library at session-creation time. Without this field a client
    # reading ``skills`` alone cannot tell "bound to nothing" from "bound to
    # everything", and renders an empty skill picker for an agent that in fact
    # holds the whole library.
    resource_policy: Literal["explicit", "all_available"] = "explicit"


class MemberWithAgentResponse(BaseModel):
    member: ProjectMemberResponse
    agent: AgentSummary | None


def _agent_to_summary(
    agent: Any,
    accept_language: str | None = None,
) -> AgentSummary:
    meta = agent.metadata or {}
    bindings = meta.get("connector_bindings") or []
    connectors = [b["type"] for b in bindings if isinstance(b, dict) and b.get("type")]
    name = agent.name
    if meta.get("agent_slug") == VALURION_SLUG:
        name = t(
            "agent.valurionName",
            fallback=VALURION_NAME,
            locale=_agent_locale(accept_language),
        )
    return AgentSummary(
        id=agent.id,
        name=name,
        model=agent.model,
        runtime_provider=str(agent.runtime_provider),
        instructions=agent.instructions,
        skills=list(agent.skills),
        connectors=connectors,
        provider_id=meta.get("provider_id"),
        effort=getattr(agent, "effort", None),
        # ``build_agent_config`` always stamps this into metadata; the default
        # only covers a config built before that (or by a test fixture).
        resource_policy=(
            "all_available" if meta.get("resource_policy") == "all_available" else "explicit"
        ),
    )


def _member_with_agent(
    row: dict[str, Any],
    accept_language: str | None = None,
) -> MemberWithAgentResponse:
    return MemberWithAgentResponse(
        member=ProjectMemberResponse.model_validate(row["member"]),
        agent=(
            _agent_to_summary(row["agent"], accept_language) if row["agent"] is not None else None
        ),
    )


# ---------------------------------------------------------------------------
# Agent routes
# ---------------------------------------------------------------------------


@router.get("/v1/agents")
async def list_agents(
    source: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    """List agents, optionally filtered by source (official|custom)."""
    from valuz_agent.ports.extensions import ext

    rows = await svc.list_agents(user_id, source=source)
    items = [AgentResponse.model_validate(r).model_dump() for r in rows]
    items = await ext.resource_list_hook.apply("agent", items, user_id=user_id)
    return {"agents": [_localize_agent_mapping(item, accept_language) for item in items]}


@router.get("/v1/agents/{slug}", response_model=AgentResponse)
async def get_agent(
    slug: str,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> AgentResponse:
    """Get a single agent by slug."""
    try:
        row = await svc.get_agent(user_id, slug)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    return _localized_agent_response(row, accept_language)


@router.get("/v1/agents/{slug}/deployments")
async def list_agent_deployments(
    slug: str,
    user_id: str = Depends(get_current_user_id),
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


@router.get("/v1/agents/{slug}/effective-resources")
async def get_agent_effective_resources(
    slug: str,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> dict[str, Any]:
    """Read-only, secret-free view of what a session for this Agent will carry.

    Answers for any Agent. The 409 that used to reject an explicit-binding
    Agent is gone: refusing them is what pushed clients into re-deriving
    session composition from the ``skills`` array, which does not include the
    always-on baseline every session gets.
    """
    try:
        manifest = await svc.resolve_effective_resources(user_id, slug)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    return manifest.to_api()


class CreateAgentRequest(BaseModel):
    # Optional: backend derives a CJK-preserving, globally-unique slug from
    # ``name`` when omitted (VALUZ-AGENT-SLUG). UI sends name only.
    slug: str | None = None
    name: str
    description: str = ""
    instructions: str = ""
    runtime: RuntimeProvider | None = None  # None → factory default (ext.model_defaults)
    model: str | None = None  # None → factory default (ext.model_defaults)
    skills: list[str] = []
    connector_types: list[str] = []
    knowledge_scope: list[str] = []
    inherit_global_instructions: bool = True
    permission_mode: str = "full_access"
    provider_id: str | None = None
    effort: EffortLevel | None = None
    avatar: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    runtime: RuntimeProvider | None = None
    model: str | None = None
    skills: list[str] | None = None
    connector_types: list[str] | None = None
    knowledge_scope: list[str] | None = None
    inherit_global_instructions: bool | None = None
    permission_mode: str | None = None
    provider_id: str | None = None
    effort: EffortLevel | None = None
    avatar: str | None = None


@router.post("/v1/agents", status_code=201, response_model=AgentResponse)
async def create_agent(
    payload: CreateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> AgentResponse:
    """Create a user-defined agent."""
    try:
        row = await svc.create_agent(user_id, payload.model_dump())
    except InvalidAgentSlugError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _localized_agent_response(row, accept_language)


@router.patch("/v1/agents/{slug}", response_model=AgentResponse)
async def update_agent(
    slug: str,
    payload: UpdateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> AgentResponse:
    """Patch an agent (official or custom)."""
    try:
        row = await svc.update_agent(user_id, slug, payload.model_dump(exclude_unset=True))
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    except AgentManagedFieldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _localized_agent_response(row, accept_language)


class CopyAgentRequest(BaseModel):
    name: str | None = None
    # Optional, same rules as CreateAgentRequest.slug: a copy derives one from
    # the new name when omitted.
    slug: str | None = None


@router.post("/v1/agents/{slug}/copy", status_code=201, response_model=AgentResponse)
async def copy_agent(
    slug: str,
    payload: CopyAgentRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> AgentResponse:
    """Copy portable Agent configuration using the Valurion-specific rules."""
    try:
        row = await svc.copy_agent(
            user_id,
            slug,
            name=payload.name if payload is not None else None,
            new_slug=payload.slug if payload is not None else None,
        )
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {slug}") from exc
    return _localized_agent_response(row, accept_language)


@router.delete("/v1/agents/{slug}", status_code=204)
async def delete_agent(
    slug: str,
    cascade: bool = False,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> None:
    """Delete an agent.

    ``cascade=true`` first 解除 every 派驻 (project membership) the agent has,
    then deletes it — the confirmed-delete path. Without it, an agent still
    deployed to a project returns 409 (the caller must 解除派驻 first).
    """
    try:
        await svc.delete_agent(user_id, slug, cascade=cascade)
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
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict[str, list[MemberWithAgentResponse]]:
    """List all agent members in a project."""
    rows = await svc.list_members(user_id, project_id)
    return {"agents": [_member_with_agent(r, accept_language) for r in rows]}


@router.post(
    "/v1/projects/{project_id}/agents",
    status_code=201,
    response_model=MemberWithAgentResponse,
)
async def create_blank_agent(
    project_id: str,
    payload: CreateBlankAgentRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
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
    except InvalidAgentSlugError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _member_with_agent(result, accept_language)


@router.post(
    "/v1/projects/{project_id}/agents:deploy",
    status_code=201,
    response_model=MemberWithAgentResponse,
)
async def deploy_agent(
    project_id: str,
    payload: DeployAgentRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
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
    except InvalidAgentSlugError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _member_with_agent(result, accept_language)


# ---------------------------------------------------------------------------
# Natural-language agent proposal (confirm)
# ---------------------------------------------------------------------------


class ProposeAgentConfirmRequest(BaseModel):
    """Spec of an agent the user is confirming after the assistant proposed it
    via the ``propose_agent`` tool. Backend derives a unique slug from ``name``."""

    name: str
    instructions: str
    description: str = ""
    runtime: RuntimeProvider | None = None  # None → factory default (ext.model_defaults)
    model: str | None = None  # None → factory default (ext.model_defaults)
    effort: EffortLevel | None = None
    skills: list[str] = []
    connectors: list[str] = []
    avatar: str | None = None


class ProposeAgentConfirmResponse(BaseModel):
    agent: AgentSummary
    member: ProjectMemberResponse | None = None
    deployed: bool
    project_id: str | None = None


async def _resolve_session_project_id(
    user_id: str, session_id: str, db: AsyncSession
) -> str | None:
    """REAL project id for the calling session, or None.

    A session always carries ``metadata.valuz.project_id`` — but a quick
    chat / 新对话 binds to an *ephemeral* ``ProjectRow(kind="chat")``, which
    must NOT be treated as a deployable project. So we resolve the id from
    metadata and then confirm the project is ``kind == "project"``; a chat /
    temp project (or a missing one) resolves to None, so confirm creates the
    library agent only (no派驻)."""
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.sessions import project_index

    # session→project is a host fact (``valuz_project_session``) — no kernel
    # round-trip (DataService design §5). Then confirm ``kind == "project"``.
    project_id = await project_index.project_of(session_id)
    if not project_id:
        return None
    row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    return project_id if (row is not None and row.kind == "project") else None


@router.post(
    "/v1/agents/proposals/{session_id}/confirm",
    status_code=201,
    response_model=ProposeAgentConfirmResponse,
)
async def confirm_agent_proposal(
    session_id: str,
    payload: ProposeAgentConfirmRequest,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
    db: AsyncSession = Depends(get_async_session),
) -> ProposeAgentConfirmResponse:
    """Create the proposed agent in the library and, when the session is bound
    to a project, deploy it into that project as a member.

    Skill slugs that aren't indexed are dropped defensively (they'd bind to
    nothing at session build anyway); connector slugs map to ``connector_types``.
    """
    from valuz_agent.modules.skills.datastore import SkillDatastore

    # Bind only skill slugs that actually resolve in the index — an unindexed
    # slug would be silently dropped at session build, so storing it is a lie.
    indexed = {r.slug for r in await SkillDatastore(db).list_skills(user_id)}
    skills = [s for s in payload.skills if s in indexed]
    dropped = [s for s in payload.skills if s not in indexed]
    if dropped:
        import logging

        logging.getLogger(__name__).warning(
            "confirm_agent_proposal: dropping unindexed skill slugs %s", dropped
        )

    project_id = await _resolve_session_project_id(user_id, session_id, db)
    bindings = [{"type": s} for s in payload.connectors]

    try:
        if project_id:
            result = await svc.create_blank_agent(
                user_id,
                project_id=project_id,
                agent_slug=None,
                name=payload.name,
                instructions=payload.instructions,
                description=payload.description,
                runtime=payload.runtime,
                model=payload.model,
                connector_bindings=bindings or None,
                skills=skills,
                effort=payload.effort,
            )
            return ProposeAgentConfirmResponse(
                agent=_agent_to_summary(result["agent"]),
                member=ProjectMemberResponse.model_validate(result["member"]),
                deployed=True,
                project_id=project_id,
            )
        # No project on the session → create the library agent only.
        row = await svc.create_agent(
            user_id,
            {
                "name": payload.name,
                "description": payload.description,
                "instructions": payload.instructions,
                "runtime": payload.runtime,
                "model": payload.model,
                "skills": skills,
                "connector_types": payload.connectors,
                "effort": payload.effort,
                "avatar": payload.avatar,
            },
        )
        agent = await svc.build_agent_config(row)
        return ProposeAgentConfirmResponse(
            agent=_agent_to_summary(agent),
            member=None,
            deployed=False,
            project_id=None,
        )
    except InvalidAgentSlugError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MemberAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/v1/projects/{project_id}/agents/{agent_slug}",
    status_code=204,
)
async def delete_member(
    project_id: str,
    agent_slug: str,
    user_id: str = Depends(get_current_user_id),
    svc: AgentService = Depends(_get_agent_service),
) -> None:
    """Delete a project agent and its kernel AgentConfig."""
    try:
        await svc.delete_member(user_id, project_id, agent_slug)
    except MemberNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_slug}") from exc
