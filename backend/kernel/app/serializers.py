"""Schema ↔ domain serializers — shared by the HTTP routes and the host's
in-process kernel client. Pure conversions only (no HTTP concerns)."""

from __future__ import annotations

import dataclasses
from typing import Any

from app.schemas import (
    AgentConfigSchema,
    EventData,
    McpHttpServerConfigSchema,
    McpStdioServerConfigSchema,
    ModelProviderResponseSchema,
    ModelSettingsSchema,
    SessionData,
    StopReasonSchema,
    SubAgentDefSchema,
    TodoItem,
    ToolDefSchema,
)
from src.core import (
    AgentConfig,
    Event,
    McpServerConfig,
    McpStdioServerConfig,
    Session,
)


def mcp_to_schema(
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


def agent_config_to_schema(cfg: Any) -> AgentConfigSchema:
    return AgentConfigSchema(
        id=cfg.id,
        name=cfg.name,
        model=cfg.model,
        runtime_provider=cfg.runtime_provider,
        instructions=cfg.instructions,
        permission_mode=cfg.permission_mode,
        max_turns=cfg.max_turns,
        max_cost_usd=cfg.max_cost_usd,
        tools=[
            ToolDefSchema(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
                read_only=t.read_only,
                permission=t.permission,
            )
            for t in cfg.tools
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
            for a in cfg.callable_agents
        ],
        skills=list(cfg.skills),
        mcp_servers=[mcp_to_schema(c) for c in cfg.mcp_servers],
        effort=cfg.effort,
        thinking=cfg.thinking,
        metadata=cfg.metadata,
    )


def agent_config_from_schema(schema: AgentConfigSchema) -> AgentConfig:
    from src.adapters.sqlalchemy_store.converters import dict_to_agent_config

    cfg = dict_to_agent_config(schema.model_dump())
    assert cfg is not None  # name is required on the schema
    return cfg


def session_to_data(session: Session) -> SessionData:
    stop_reason = None
    if session.stop_reason is not None:
        sr_dict = dataclasses.asdict(session.stop_reason)
        stop_reason = StopReasonSchema(**sr_dict)
    return SessionData(
        id=session.id,
        agent_config=agent_config_to_schema(session.agent_config),
        runtime_provider=session.runtime_provider,
        cwd=session.cwd,
        model=session.model,
        model_provider=(
            ModelProviderResponseSchema(
                base_url=session.model_provider.base_url,
                api_protocol=session.model_provider.api_protocol,
            )
            if session.model_provider is not None
            else None
        ),
        model_settings=(
            ModelSettingsSchema(
                temperature=session.model_settings.temperature,
                max_tokens=session.model_settings.max_tokens,
                effort=session.model_settings.effort,
            )
            if session.model_settings is not None
            else None
        ),
        instructions=session.instructions,
        skills=list(session.skills),
        mcp_servers=[mcp_to_schema(cfg) for cfg in session.mcp_servers],
        permission_mode=session.permission_mode,
        mode=session.mode,
        status=session.status,
        stop_reason=stop_reason,
        created_at=session.created_at,
        metadata=session.metadata,
        runtime_session_id=session.runtime_session_id,
        todos=[TodoItem(**t) for t in session.todos] if session.todos is not None else None,
    )


def event_to_data(event: Event) -> EventData:
    return EventData(type=event.type, data=event.data, timestamp=event.timestamp)
