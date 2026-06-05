"""Agent routes — /api/v1/agents.

Agents are global capability presets. Sessions reference an agent at
creation time and copy ``instructions`` / ``skills`` / ``mcp_servers`` /
``model`` from it as defaults; once persisted on the session those
values are the runtime's source of truth.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app._validators import validate_mcp_servers, validate_skills
from app.dependencies import get_store
from app.schemas import (
    AgentData,
    AgentListResponse,
    AgentResponse,
    CreateAgentRequest,
    DataResponse,
    McpHttpServerConfigSchema,
    McpStdioServerConfigSchema,
    SubAgentDefSchema,
    ToolDefSchema,
    UpdateAgentRequest,
)
from src.core import (
    AgentConfig,
    McpServerConfig,
    McpStdioServerConfig,
    StorePort,
    unresolved_tool_names,
)
from src.core.agent_config import SubAgentDef
from src.core.tools import ToolDef

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

StoreDep = Annotated[StorePort, Depends(get_store)]


def _tools_from_schema(schemas: list[ToolDefSchema]) -> tuple[ToolDef, ...]:
    return tuple(
        ToolDef(
            name=t.name,
            description=t.description,
            parameters=t.parameters,
            read_only=t.read_only,
            permission=t.permission,
        )
        for t in schemas
    )


def _callable_agents_from_schema(schemas: list[SubAgentDefSchema]) -> tuple[SubAgentDef, ...]:
    return tuple(
        SubAgentDef(
            name=a.name,
            description=a.description,
            prompt=a.prompt,
            tools=tuple(a.tools),
            model=a.model,
            skills=tuple(a.skills) if a.skills is not None else None,
            metadata=a.metadata,
        )
        for a in schemas
    )


def _mcp_to_schema(
    cfg: McpServerConfig,
) -> McpHttpServerConfigSchema | McpStdioServerConfigSchema:
    if isinstance(cfg, McpStdioServerConfig):
        return McpStdioServerConfigSchema(
            name=cfg.name,
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env),
            env_vars=list(cfg.env_vars),
        )
    return McpHttpServerConfigSchema(
        name=cfg.name,
        url=cfg.url,
        transport=cfg.transport,
        headers=dict(cfg.headers),
    )


def _agent_to_data(agent: AgentConfig) -> AgentData:
    return AgentData(
        id=agent.id,
        name=agent.name,
        model=agent.model,
        runtime_provider=agent.runtime_provider,
        instructions=agent.instructions,
        permission_mode=agent.permission_mode,
        max_turns=agent.max_turns,
        max_cost_usd=agent.max_cost_usd,
        tools=[
            ToolDefSchema(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
                read_only=t.read_only,
                permission=t.permission,
            )
            for t in agent.tools
        ],
        callable_agents=[
            SubAgentDefSchema(
                name=a.name,
                description=a.description,
                prompt=a.prompt,
                tools=list(a.tools),
                model=a.model,
                skills=list(a.skills) if a.skills is not None else None,
                metadata=a.metadata,
            )
            for a in agent.callable_agents
        ],
        skills=list(agent.skills),
        mcp_servers=[_mcp_to_schema(cfg) for cfg in agent.mcp_servers],
        effort=agent.effort,
        thinking=agent.thinking,
        status=agent.status,
        created_at=agent.created_at,
        metadata=agent.metadata,
    )


def _validate_registered_tools(tools: tuple[ToolDef, ...]) -> None:
    unresolved = unresolved_tool_names(tools)
    if unresolved:
        names = ", ".join(sorted(unresolved))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or unregistered tools: {names}",
        )


@router.post("", status_code=201, response_model=AgentResponse)
async def create_agent(
    body: CreateAgentRequest,
    store: StoreDep,
) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name must not be empty.")

    tools = _tools_from_schema(body.tools)
    _validate_registered_tools(tools)
    validate_skills(body.skills)
    mcp_configs = validate_mcp_servers(body.mcp_servers)

    agent = AgentConfig(
        id=str(uuid.uuid4()),
        name=body.name,
        model=body.model,
        runtime_provider=body.runtime_provider,
        instructions=body.instructions,
        permission_mode=body.permission_mode,
        max_turns=body.max_turns,
        max_cost_usd=body.max_cost_usd,
        tools=tools,
        callable_agents=_callable_agents_from_schema(body.callable_agents),
        skills=tuple(body.skills),
        mcp_servers=tuple(mcp_configs),
        effort=body.effort,
        thinking=body.thinking,
        metadata=body.metadata,
    )
    await store.save_agent(agent)
    return {"data": _agent_to_data(agent)}


@router.get("", response_model=AgentListResponse)
async def list_agents(
    store: StoreDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    agents = await store.list_agents(limit=limit, offset=offset)
    return {"data": [_agent_to_data(a) for a in agents]}


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    store: StoreDep,
) -> dict[str, Any]:
    agent = await store.load_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"data": _agent_to_data(agent)}


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    store: StoreDep,
) -> dict[str, Any]:
    agent = await store.load_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    updates = body.model_dump(exclude_unset=True)
    if "tools" in updates:
        updates["tools"] = _tools_from_schema(body.tools)  # type: ignore[arg-type]
        _validate_registered_tools(updates["tools"])
    if "callable_agents" in updates:
        updates["callable_agents"] = _callable_agents_from_schema(
            body.callable_agents  # type: ignore[arg-type]
        )
    if "skills" in updates:
        validate_skills(updates["skills"])
        updates["skills"] = tuple(updates["skills"])
    if "mcp_servers" in updates:
        updates["mcp_servers"] = tuple(
            validate_mcp_servers(body.mcp_servers)  # type: ignore[arg-type]
        )
    updated = dataclasses.replace(agent, **updates)
    await store.save_agent(updated)
    return {"data": _agent_to_data(updated)}


@router.delete("/{agent_id}", response_model=DataResponse)
async def delete_agent(
    agent_id: str,
    store: StoreDep,
) -> dict[str, Any]:
    deleted = await store.delete_agent(agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"data": None}
