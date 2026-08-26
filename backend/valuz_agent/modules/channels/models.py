"""ORM models for external agent channels."""

from __future__ import annotations

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class AgentChannelBindingRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Bind one Valuz agent to one external channel identity."""

    __tablename__ = "valuz_agent_channel_binding"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "platform",
            "agent_slug",
            name="uq_agent_channel_binding_agent",
        ),
    )

    platform: Mapped[str] = mapped_column(String(32), index=True)
    channel_instance_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_slug: Mapped[str] = mapped_column(String(128), index=True)
    bot_id: Mapped[str] = mapped_column(String(256), index=True)
    secret_ref: Mapped[str | None] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean(), default=True)
    bot_name: Mapped[str | None] = mapped_column(String(128))
    ws_url: Mapped[str | None] = mapped_column(String(512))


class ChannelChatBindingRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Bind one external chat (a group) to one Valuz project.

    "A group is a project": one chat binds to exactly one project, while a
    project may be bound from several chats (an internal group and a client
    group, say) whose sessions stay independent. See
    docs/design/channel-project-binding-and-default-lead.md §3.2.
    """

    __tablename__ = "valuz_channel_chat_binding"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "channel_instance_id",
            "external_chat_id",
            name="uq_channel_chat_binding_chat",
        ),
    )

    channel_instance_id: Mapped[str] = mapped_column(String(128), index=True)
    external_chat_id: Mapped[str] = mapped_column(String(256), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    # Who answers in this chat by default; NULL = the app binding's agent.
    default_agent_slug: Mapped[str | None] = mapped_column(String(128))
    # Cached for display on the Valuz side — the IM group name is not otherwise
    # available without another API round trip.
    external_chat_name: Mapped[str | None] = mapped_column(String(256))
    # Audit: which IM account established the binding (see §7 — adding a bot to
    # a group opens those projects to everyone in it).
    bound_by_external_user: Mapped[str | None] = mapped_column(String(256))
    # Valuz created this group (so the bot owns it and may delete it). A group
    # the user made is never deleted from here — unbinding is the most this
    # side may do to something it does not own.
    created_by_valuz: Mapped[bool] = mapped_column(Boolean(), default=False)


class ChannelThreadBindingRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Bind one external channel thread to one Valuz session."""

    __tablename__ = "valuz_channel_thread_binding"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "channel_instance_id",
            "external_chat_id",
            "external_thread_id",
            "agent_slug",
            "project_id",
            name="uq_channel_thread_binding_route",
        ),
    )

    channel_instance_id: Mapped[str] = mapped_column(String(128), index=True)
    external_chat_id: Mapped[str] = mapped_column(String(256), index=True)
    external_thread_id: Mapped[str] = mapped_column(String(256), index=True)
    agent_slug: Mapped[str] = mapped_column(String(128), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
