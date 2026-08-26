"""document research: versioned citation-bearing summary artifacts

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_document_summary_artifact",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("document_version", sa.String(length=128), nullable=False),
        sa.Column("profile", sa.String(length=32), nullable=False),
        sa.Column("prompt_revision", sa.String(length=64), nullable=False),
        sa.Column("policy_revision", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citation_bundle_json", sa.Text(), nullable=False),
        sa.Column("research_session_id", sa.String(length=36), nullable=True),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("model_id", sa.String(length=256), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_valuz_document_summary_artifact_user_id",
        "valuz_document_summary_artifact",
        ["user_id"],
    )
    op.create_index(
        "ux_doc_summary_cache_key",
        "valuz_document_summary_artifact",
        [
            "user_id",
            "document_id",
            "document_version",
            "profile",
            "prompt_revision",
            "policy_revision",
        ],
        unique=True,
    )
    op.create_index(
        "ix_doc_summary_latest",
        "valuz_document_summary_artifact",
        ["user_id", "document_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_doc_summary_latest", table_name="valuz_document_summary_artifact")
    op.drop_index("ux_doc_summary_cache_key", table_name="valuz_document_summary_artifact")
    op.drop_index(
        "ix_valuz_document_summary_artifact_user_id",
        table_name="valuz_document_summary_artifact",
    )
    op.drop_table("valuz_document_summary_artifact")
