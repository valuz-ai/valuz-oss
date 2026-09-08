"""``valuz_feedback`` — one row per (user, message, action, block_ref)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class FeedbackRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """A user outcome signal on a kernel Message.

    ``created_at`` is the first occurrence, ``updated_at`` the latest;
    ``occurrences`` counts repeats. ``block_ref`` is ``NOT NULL DEFAULT ''``
    on purpose: NULLs are distinct under a unique index, which would let the
    whole-turn row duplicate.
    """

    __tablename__ = "valuz_feedback"
    __table_args__ = (
        CheckConstraint(
            "action IN ('rating', 'copy', 'regenerate', 'fork', 'share', 'research_share')",
            name="ck_valuz_feedback_action",
        ),
        CheckConstraint(
            "value IS NULL OR value IN ('up', 'down')",
            name="ck_valuz_feedback_value",
        ),
        CheckConstraint(
            "source IN ('ui', 'api', 'server')",
            name="ck_valuz_feedback_source",
        ),
        UniqueConstraint(
            "user_id", "message_id", "action", "block_ref", name="uq_valuz_feedback_subject"
        ),
        Index("ix_valuz_feedback_session", "user_id", "session_id"),
        Index("ix_valuz_feedback_updated_at", "updated_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: kernel ``messages.id`` — weak reference (no FK: kernel tables may live elsewhere).
    message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    block_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    value: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    surface: Mapped[str | None] = mapped_column(String(16), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
