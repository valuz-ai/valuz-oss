"""SQLAlchemy ORM models — dialect-agnostic (SQLite, PostgreSQL, MySQL).

All instant columns (``created_at`` / ``started_at`` / ``ended_at`` /
``timestamp``) are Unix epoch milliseconds (UTC) stored as plain ``BIGINT``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON
from src.core.owner_context import get_owner_id
from src.core.time_utils import now_ms


class Base(DeclarativeBase):
    pass


def _owner_column() -> Mapped[str]:
    """Owner id column shared by every kernel table.

    Required (``NOT NULL``) and stamped from ``owner_context`` (host-seeded at
    boot). Indexed because the commercial overlay filters by owner.
    """
    return mapped_column(String(64), nullable=False, index=True, default=get_owner_id)


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _owner_column()
    name: Mapped[str] = mapped_column(String(255), default="")
    cwd: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deleted')",
            name="ck_projects_status",
        ),
        Index("ix_projects_created_at", "created_at"),
        Index("ix_projects_status", "status"),
    )


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _owner_column()
    name: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(100), default="claude-sonnet-4-6")
    runtime_provider: Mapped[str] = mapped_column(String(20), default="claude_agent")
    instructions: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    callable_agents: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    mcp_servers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    permission_mode: Mapped[str] = mapped_column(String(20), default="default")
    max_turns: Mapped[int] = mapped_column(Integer, default=50)
    max_cost_usd: Mapped[float] = mapped_column(Float, default=10.0)
    effort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    thinking: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    __table_args__ = (
        CheckConstraint(
            "permission_mode IN ('default', 'auto_review', 'full_access')",
            name="ck_agents_permission_mode",
        ),
        CheckConstraint(
            "status IN ('active', 'deleted')",
            name="ck_agents_status",
        ),
        Index("ix_agents_created_at", "created_at"),
        Index("ix_agents_status", "status"),
    )


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _owner_column()
    project_id: Mapped[str] = mapped_column(String(36))
    agent_id: Mapped[str] = mapped_column(String(36))
    # Per-session cwd override; "" = fall back to project.cwd.
    cwd: Mapped[str] = mapped_column(Text, default="")
    runtime_provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    mcp_servers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model_provider: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model_settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    permission_mode: Mapped[str] = mapped_column(String(20), default="full_access")
    mode: Mapped[str] = mapped_column(String(20), default="default")
    status: Mapped[str] = mapped_column(String(20), default="created")
    stop_reason: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    runtime_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    todos: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "permission_mode IN ('default', 'auto_review', 'full_access')",
            name="ck_sessions_permission_mode",
        ),
        CheckConstraint(
            "mode IN ('default', 'plan', 'goal')",
            name="ck_sessions_mode",
        ),
        CheckConstraint(
            "status IN ('created', 'idle', 'running', 'terminated')",
            name="ck_sessions_status",
        ),
        CheckConstraint(
            "runtime_provider IN ('claude_agent', 'codex', 'deepagents')",
            name="ck_sessions_runtime_provider",
        ),
        Index("ix_sessions_status", "status"),
        Index("ix_sessions_created_at", "created_at"),
        Index("ix_sessions_project_id", "project_id"),
        Index("ix_sessions_agent_id", "agent_id"),
    )


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _owner_column()
    session_id: Mapped[str] = mapped_column(String(36))
    user_message: Mapped[dict[str, Any]] = mapped_column(JSON)
    assistant_message: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    stop_reason: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    total_turns: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[int] = mapped_column(BigInteger)
    ended_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    todos: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'errored', 'cancelled')",
            name="ck_messages_status",
        ),
        Index("ix_messages_session_started", "session_id", "started_at"),
    )


class EventModel(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = _owner_column()
    session_id: Mapped[str] = mapped_column(String(36))
    message_id: Mapped[str] = mapped_column(String(36))
    type: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_events_session_timestamp", "session_id", "timestamp"),
        Index("ix_events_session_type", "session_id", "type"),
        Index("ix_events_message_id", "message_id"),
    )
