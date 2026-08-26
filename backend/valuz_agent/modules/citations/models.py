"""Host-owned persistence for document research artifacts."""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class DocumentSummaryArtifactRow(
    Base,
    PrimaryKeyMixin,
    TimestampMixin,
    UserMixin,
):
    """One immutable-version summary cache entry.

    The unique cache identity includes both prompt and policy revisions so
    changing generation or citation rules never silently reuses an older
    artifact.
    """

    __tablename__ = "valuz_document_summary_artifact"

    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_version: Mapped[str] = mapped_column(String(128), nullable=False)
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citation_bundle_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    research_session_id: Mapped[str | None] = mapped_column(String(36), default=None)
    message_id: Mapped[str | None] = mapped_column(String(36), default=None)
    model_id: Mapped[str | None] = mapped_column(String(256), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    generated_at: Mapped[int | None] = mapped_column(BigInteger, default=None)

    __table_args__ = (
        Index(
            "ux_doc_summary_cache_key",
            "user_id",
            "document_id",
            "document_version",
            "profile",
            "prompt_revision",
            "policy_revision",
            unique=True,
        ),
        Index(
            "ix_doc_summary_latest",
            "user_id",
            "document_id",
            "updated_at",
        ),
    )


__all__ = ["DocumentSummaryArtifactRow"]
