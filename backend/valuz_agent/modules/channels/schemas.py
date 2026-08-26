"""Shared channel routing schemas.

These types describe the normalized event after a platform adapter has parsed
Feishu / WeCom / DingTalk callbacks. They intentionally do not model provider
LLM channels; those live under ``modules.providers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChannelPlatform(StrEnum):
    FEISHU = "feishu"
    WECOM = "wecom"
    DINGTALK = "dingtalk"


class ChannelRouteDecisionKind(StrEnum):
    REUSE_SESSION = "reuse_session"
    QUEUE_SESSION = "queue_session"
    NEW_SESSION = "new_session"
    ASK_PROJECT = "ask_project"
    NOT_DEPLOYED = "not_deployed"


@dataclass(frozen=True, slots=True)
class AgentPlacement:
    """A project-local deployment of a library agent."""

    project_id: str
    project_name: str
    agent_slug: str
    source_agent_slug: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelChatBinding:
    """An external chat pinned to one project — "this group is that project"."""

    channel_instance_id: str
    external_chat_id: str
    project_id: str
    default_agent_slug: str | None = None
    external_chat_name: str | None = None
    bound_by_external_user: str | None = None
    created_by_valuz: bool = False


@dataclass(frozen=True, slots=True)
class ChannelMentionContext:
    """Normalized group/direct chat mention routed to one Valuz agent."""

    user_id: str
    channel_instance_id: str
    external_chat_id: str
    external_thread_id: str | None
    mentioned_agent_slug: str
    explicit_project_id: str | None = None
    explicit_project_name: str | None = None
    is_top_level_mention: bool = True
    # 1:1 with the bot. A direct chat is a personal quick chat and must not be
    # pulled into a project by placement heuristics — only an explicit project
    # hint or a binding the user made on purpose can do that.
    is_direct_chat: bool = False
    continuation_hint: bool = False
    explicit_continue_hint: bool = False
    explicit_new_hint: bool = False
    request_id: str | None = None
    external_message_id: str | None = None
    external_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelRouteKey:
    """Stable key for mapping an external thread to a Valuz session."""

    channel_instance_id: str
    external_chat_id: str
    external_thread_id: str
    agent_slug: str
    project_id: str


@dataclass(frozen=True, slots=True)
class ChannelThreadBinding:
    """Host-side memory that links an external chat thread to a Valuz session."""

    channel_instance_id: str
    external_chat_id: str
    external_thread_id: str
    agent_slug: str
    project_id: str | None
    session_id: str | None
    session_accepts_turn: bool = True
    session_status: str | None = None


@dataclass(frozen=True, slots=True)
class AgentChannelBinding:
    """One agent's binding to one external channel identity."""

    id: str
    owner_user_id: str
    platform: str
    channel_instance_id: str
    agent_slug: str
    bot_id: str
    secret_ref: str | None
    enabled: bool
    bot_name: str | None = None
    ws_url: str | None = None


@dataclass(frozen=True, slots=True)
class AgentChannelRouteDecision:
    kind: ChannelRouteDecisionKind
    agent_slug: str
    project_id: str | None
    session_id: str | None
    reason: str
    candidates: tuple[AgentPlacement, ...] = ()


__all__ = [
    "AgentChannelBinding",
    "AgentChannelRouteDecision",
    "AgentPlacement",
    "ChannelChatBinding",
    "ChannelMentionContext",
    "ChannelPlatform",
    "ChannelRouteKey",
    "ChannelRouteDecisionKind",
    "ChannelThreadBinding",
]
