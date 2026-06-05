"""Bidirectional converters between core dataclasses and ORM models."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from src.adapters.sqlalchemy_store.models import (
    AgentModel,
    EventModel,
    MessageModel,
    ProjectModel,
    SessionModel,
)
from src.core.agent_config import AgentConfig, SubAgentDef
from src.core.events import Event
from src.core.project import Project, ProjectStatus
from src.core.tools import ToolDef
from src.core.types import (
    Attachment,
    BudgetExhausted,
    EndTurn,
    Error,
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
    Message,
    MessageStatus,
    ModelProvider,
    ModelSettings,
    Session,
    StopReason,
    UserInterrupt,
    UserMessage,
)

_STOP_REASON_CONSTRUCTORS: dict[str, type[StopReason]] = {
    "end_turn": EndTurn,
    "budget_exhausted": BudgetExhausted,
    "error": Error,
    "user_interrupt": UserInterrupt,
}

_VALID_PERMISSION_MODES = {"default", "auto_review", "full_access"}
_VALID_SESSION_MODES = {"default", "plan", "goal"}
_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_VALID_SESSION_STATUSES = {"created", "idle", "running", "terminated"}
_VALID_RUNTIME_PROVIDERS = {"claude_agent", "codex", "deepagents"}
_VALID_ENTITY_STATUSES = {"active", "deleted"}
_VALID_MESSAGE_STATUSES = {"running", "completed", "errored", "cancelled"}


def _validate_permission_mode(
    value: str,
) -> Literal["default", "auto_review", "full_access"]:
    if value not in _VALID_PERMISSION_MODES:
        return "full_access"
    return value  # type: ignore[return-value]


def _validate_session_mode(
    value: str,
) -> Literal["default", "plan", "goal"]:
    if value not in _VALID_SESSION_MODES:
        return "default"
    return value  # type: ignore[return-value]


def _validate_effort(
    value: str | None,
) -> Literal["low", "medium", "high", "xhigh", "max"] | None:
    if value is None or value not in _VALID_EFFORTS:
        return None
    return value  # type: ignore[return-value]


def _validate_session_status(
    value: str,
) -> Literal["created", "idle", "running", "terminated"]:
    if value not in _VALID_SESSION_STATUSES:
        return "created"
    return value  # type: ignore[return-value]


def _validate_runtime_provider(
    value: str,
) -> Literal["claude_agent", "codex", "deepagents"]:
    """Coerce stored runtime_provider; defensive default for legacy rows.

    The DB CHECK constraint guards new writes, so this fallback only
    matters for read paths over hand-edited / legacy data.
    """
    if value not in _VALID_RUNTIME_PROVIDERS:
        return "deepagents"
    return value  # type: ignore[return-value]


def _validate_entity_status(
    value: str,
) -> Literal["active", "deleted"]:
    if value not in _VALID_ENTITY_STATUSES:
        return "active"
    return value  # type: ignore[return-value]


def _validate_message_status(value: str) -> MessageStatus:
    if value not in _VALID_MESSAGE_STATUSES:
        return "running"
    return value  # type: ignore[return-value]


# -- Project --


def project_to_model(project: Project) -> ProjectModel:
    return ProjectModel(
        id=project.id,
        name=project.name,
        cwd=project.cwd,
        status=project.status,
        metadata_=project.metadata,
    )


def model_to_project(model: ProjectModel) -> Project:
    status: ProjectStatus = _validate_entity_status(model.status)
    return Project(
        id=model.id,
        name=model.name,
        cwd=model.cwd,
        status=status,
        created_at=model.created_at,
        metadata=model.metadata_,
    )


# -- StopReason --


def stop_reason_to_dict(reason: StopReason | None) -> dict[str, Any] | None:
    if reason is None:
        return None
    return asdict(reason)


def dict_to_stop_reason(data: dict[str, Any] | None) -> StopReason | None:
    if data is None:
        return None

    type_key = data.get("type", "")
    cls = _STOP_REASON_CONSTRUCTORS.get(type_key)
    if cls is None:
        return None

    fields = {k: v for k, v in data.items() if k != "type"}
    return cls(**fields)


# -- Agent --


def agent_to_model(agent: AgentConfig) -> AgentModel:
    tools_data = [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "read_only": t.read_only,
            "permission": t.permission,
        }
        for t in agent.tools
    ]
    callable_agents_data = [asdict(a) for a in agent.callable_agents]

    return AgentModel(
        id=agent.id,
        name=agent.name,
        model=agent.model,
        runtime_provider=agent.runtime_provider,
        instructions=agent.instructions,
        tools=tools_data,
        callable_agents=callable_agents_data,
        skills=list(agent.skills),
        mcp_servers=[mcp_to_dict(c) for c in agent.mcp_servers],
        permission_mode=agent.permission_mode,
        max_turns=agent.max_turns,
        max_cost_usd=agent.max_cost_usd,
        effort=agent.effort,
        thinking=agent.thinking,
        status=agent.status,
        metadata_=agent.metadata,
    )


def model_to_agent(model: AgentModel) -> AgentConfig:
    tools = tuple(
        ToolDef(
            name=t["name"],
            description=t.get("description", ""),
            parameters=t.get("parameters", {}),
            read_only=t.get("read_only", False),
            permission=t.get("permission", "auto"),
        )
        for t in (model.tools or [])
    )
    callable_agents = tuple(
        SubAgentDef(
            name=a["name"],
            description=a.get("description", ""),
            prompt=a.get("prompt", ""),
            tools=tuple(a.get("tools", ())),
            model=a.get("model"),
            skills=tuple(a["skills"]) if a.get("skills") is not None else None,
            metadata=a.get("metadata", {}),
        )
        for a in (model.callable_agents or [])
    )

    return AgentConfig(
        id=model.id,
        name=model.name,
        model=model.model,
        runtime_provider=_validate_runtime_provider(model.runtime_provider),
        instructions=model.instructions,
        tools=tools,
        callable_agents=callable_agents,
        skills=tuple(model.skills or []),
        mcp_servers=tuple(dict_to_mcp(d) for d in (model.mcp_servers or [])),
        permission_mode=_validate_permission_mode(model.permission_mode),
        max_turns=model.max_turns,
        max_cost_usd=model.max_cost_usd,
        effort=_validate_effort(model.effort),
        thinking=model.thinking,
        status=_validate_entity_status(model.status),
        created_at=model.created_at,
        metadata=model.metadata_,
    )


# -- Session --


def session_to_model(session: Session) -> SessionModel:
    return SessionModel(
        id=session.id,
        project_id=session.project_id,
        agent_id=session.agent_id,
        cwd=session.cwd,
        runtime_provider=session.runtime_provider,
        model=session.model,
        instructions=session.instructions,
        skills=list(session.skills),
        mcp_servers=[mcp_to_dict(c) for c in session.mcp_servers],
        model_provider=model_provider_to_dict(session.model_provider),
        model_settings=model_settings_to_dict(session.model_settings),
        permission_mode=session.permission_mode,
        mode=session.mode,
        status=session.status,
        stop_reason=stop_reason_to_dict(session.stop_reason),
        created_at=session.created_at,
        metadata_=session.metadata,
        runtime_session_id=session.runtime_session_id,
        todos=list(session.todos) if session.todos is not None else None,
    )


def model_to_session(model: SessionModel) -> Session:
    return Session(
        id=model.id,
        project_id=model.project_id,
        agent_id=model.agent_id,
        cwd=model.cwd or "",
        runtime_provider=_validate_runtime_provider(model.runtime_provider),
        model=model.model,
        instructions=model.instructions,
        skills=tuple(model.skills or []),
        mcp_servers=tuple(dict_to_mcp(d) for d in (model.mcp_servers or [])),
        model_provider=dict_to_model_provider(model.model_provider),
        model_settings=dict_to_model_settings(model.model_settings),
        permission_mode=_validate_permission_mode(model.permission_mode),
        mode=_validate_session_mode(model.mode),
        status=_validate_session_status(model.status),
        stop_reason=dict_to_stop_reason(model.stop_reason),
        created_at=model.created_at,
        metadata=model.metadata_,
        runtime_session_id=model.runtime_session_id,
        todos=list(model.todos) if model.todos is not None else None,
    )


def model_provider_to_dict(p: ModelProvider | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"base_url": p.base_url, "api_key": p.api_key, "api_protocol": p.api_protocol}


_VALID_API_PROTOCOLS = frozenset({"anthropic", "openai_completion", "openai_response", "gemini"})


def dict_to_model_provider(data: dict[str, Any] | None) -> ModelProvider | None:
    if not data:
        return None
    protocol = data.get("api_protocol", "anthropic")
    # Defensive fallback for rows that escaped the alembic migration —
    # the old ``openai`` literal can't be disambiguated at the converter
    # layer (no runtime_provider in scope), so we default to
    # ``anthropic`` and let the factory's runtime-vs-protocol validator
    # surface the mismatch with a clear error message. Any value outside
    # the new enum is treated the same way.
    if protocol not in _VALID_API_PROTOCOLS:
        protocol = "anthropic"
    # ``base_url`` is the only optional field — preserve None so the
    # "first-party fallback" branch in each runtime fires. Legacy rows
    # that stored an empty string for the gateway-less case also
    # collapse to None here for the same reason (no UI flow should
    # actually have produced "" + a working session, but stay defensive).
    raw_base_url = data.get("base_url")
    base_url: str | None
    if raw_base_url is None or (isinstance(raw_base_url, str) and not raw_base_url.strip()):
        base_url = None
    else:
        base_url = str(raw_base_url)
    return ModelProvider(
        base_url=base_url,
        api_key=str(data.get("api_key", "")),
        api_protocol=protocol,
    )


_VALID_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


def model_settings_to_dict(s: ModelSettings | None) -> dict[str, Any] | None:
    if s is None:
        return None
    out: dict[str, Any] = {"temperature": s.temperature, "max_tokens": s.max_tokens}
    if s.effort is not None:
        out["effort"] = s.effort
    return out


def dict_to_model_settings(data: dict[str, Any] | None) -> ModelSettings | None:
    if not data:
        return None
    effort = data.get("effort")
    # Ignore unknown effort levels rather than raising — DB rows from
    # older schemas might predate a level; the runtime will fall back
    # to its SDK default when ``effort`` is None.
    if effort is not None and effort not in _VALID_EFFORT_LEVELS:
        effort = None
    return ModelSettings(
        temperature=data.get("temperature"),
        max_tokens=data.get("max_tokens"),
        effort=effort,
    )


# -- McpServerConfig --


def mcp_to_dict(cfg: McpServerConfig) -> dict[str, Any]:
    if isinstance(cfg, McpStdioServerConfig):
        return {
            "name": cfg.name,
            "transport": "stdio",
            "command": cfg.command,
            "args": list(cfg.args),
            "env": dict(cfg.env),
            "env_vars": list(cfg.env_vars),
        }
    return {
        "name": cfg.name,
        "url": cfg.url,
        "transport": cfg.transport,
        "headers": dict(cfg.headers),
    }


def dict_to_mcp(data: dict[str, Any]) -> McpServerConfig:
    transport = data.get("transport", "http")
    if transport == "stdio":
        raw_env = data.get("env") or {}
        env = {str(k): str(v) for k, v in raw_env.items()} if isinstance(raw_env, dict) else {}
        raw_args = data.get("args") or []
        args = tuple(str(a) for a in raw_args) if isinstance(raw_args, list) else ()
        raw_env_vars = data.get("env_vars") or []
        env_vars = tuple(str(v) for v in raw_env_vars) if isinstance(raw_env_vars, list) else ()
        return McpStdioServerConfig(
            name=str(data.get("name", "")),
            command=str(data.get("command", "")),
            args=args,
            env=env,
            env_vars=env_vars,
        )
    if transport not in ("http", "sse"):
        transport = "http"
    raw_headers = data.get("headers") or {}
    headers = (
        {str(k): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
    )
    return McpHttpServerConfig(
        name=str(data.get("name", "")),
        url=str(data.get("url", "")),
        transport=transport,
        headers=headers,
    )


# -- UserMessage --


def user_message_to_dict(msg: UserMessage) -> dict[str, Any]:
    return {
        "text": msg.text,
        "attachments": [{"filepath": a.filepath} for a in msg.attachments],
    }


def dict_to_user_message(data: dict[str, Any]) -> UserMessage:
    raw_attachments = data.get("attachments") or []
    attachments = tuple(Attachment(filepath=str(a.get("filepath", ""))) for a in raw_attachments)
    return UserMessage(text=str(data.get("text", "")), attachments=attachments)


# -- Message --


def message_to_model(message: Message) -> MessageModel:
    return MessageModel(
        id=message.id,
        session_id=message.session_id,
        user_message=user_message_to_dict(message.user_message),
        assistant_message=(
            {"text": message.assistant_message} if message.assistant_message is not None else None
        ),
        error_message=message.error_message,
        status=message.status,
        stop_reason=stop_reason_to_dict(message.stop_reason),
        total_turns=message.total_turns,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        cache_read_tokens=message.cache_read_tokens,
        cache_write_tokens=message.cache_write_tokens,
        model_usage=message.model_usage,
        started_at=message.started_at,
        ended_at=message.ended_at,
        metadata_=message.metadata,
        todos=list(message.todos) if message.todos is not None else None,
    )


def model_to_message(model: MessageModel) -> Message:
    assistant_text: str | None = None
    if model.assistant_message is not None:
        raw = (
            model.assistant_message.get("text")
            if isinstance(model.assistant_message, dict)
            else None
        )
        assistant_text = str(raw) if raw is not None else None
    return Message(
        id=model.id,
        session_id=model.session_id,
        user_message=dict_to_user_message(model.user_message or {}),
        started_at=model.started_at,
        status=_validate_message_status(model.status),
        assistant_message=assistant_text,
        error_message=model.error_message,
        stop_reason=dict_to_stop_reason(model.stop_reason),
        total_turns=model.total_turns,
        input_tokens=model.input_tokens,
        output_tokens=model.output_tokens,
        cache_read_tokens=model.cache_read_tokens,
        cache_write_tokens=model.cache_write_tokens,
        model_usage=model.model_usage,
        ended_at=model.ended_at,
        metadata=model.metadata_,
        todos=list(model.todos) if model.todos is not None else None,
    )


# -- Event --


def event_to_model(session_id: str, message_id: str, event: Event) -> EventModel:
    return EventModel(
        session_id=session_id,
        message_id=message_id,
        type=event.type,
        data=event.data,
        timestamp=event.timestamp,
    )


def model_to_event(model: EventModel) -> Event:
    return Event(
        type=model.type,  # type: ignore[arg-type]
        data=model.data,
        timestamp=model.timestamp,
    )
