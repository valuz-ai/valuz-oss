"""artifacts: versioned deliverables (identity / key / head / revision / content)

Creates the five tables only. Nothing reads or writes them yet, and the existing
``valuz_session_artifact`` is left completely untouched — no new column, no drop
— so this revision is safe to ship ahead of both the backfill and the cutover.

Deliberately NOT doing the data move here: the backfill has to read every
delivered file end to end to hash it, which on the cloud deployment is a
bucket-wide read. Alembic runs at process start, on every replica; one large
project would hold up the release. That work belongs to a separate, resumable
job (``scripts/migrate_artifacts.py``).

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_artifact",
        # Short base32 handle rather than the usual 32-hex id: it appears in the
        # on-disk layout (``.artifact/<id>/v3/report.pdf``), which humans read.
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("worktree", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        # Directory the deliverable lives in, so a rename can register the new
        # name in it — name keys are directory-qualified.
        sa.Column("rel_dir", sa.String(length=1024), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_valuz_artifact_user_id", "valuz_artifact", ["user_id"])
    op.create_index("ix_valuz_artifact_project_id", "valuz_artifact", ["project_id"])
    op.create_index(
        "ix_artifact_scope_recent",
        "valuz_artifact",
        ["user_id", "project_id", "worktree", "updated_at"],
    )

    op.create_table(
        "valuz_artifact_key",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("worktree", sa.String(length=128), nullable=False),
        sa.Column("key_kind", sa.String(length=8), nullable=False),
        sa.Column("key_value", sa.String(length=1024), nullable=False),
        sa.Column("artifact_id", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # One artifact per (scope, kind, value) — this is what makes the lookup
        # a lookup rather than a guess.
        sa.UniqueConstraint(
            "user_id", "project_id", "worktree", "key_kind", "key_value", name="ux_artifact_key"
        ),
    )
    op.create_index("ix_valuz_artifact_key_user_id", "valuz_artifact_key", ["user_id"])
    op.create_index("ix_valuz_artifact_key_artifact_id", "valuz_artifact_key", ["artifact_id"])

    op.create_table(
        "valuz_artifact_head",
        # Keyed by artifact so the delivery compare-and-set updates exactly one
        # narrow row instead of contending on the artifact itself.
        sa.Column("artifact_id", sa.String(length=16), nullable=False),
        sa.Column("revision_id", sa.String(length=16), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_valuz_artifact_head_user_id", "valuz_artifact_head", ["user_id"])

    op.create_table(
        "valuz_artifact_revision",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("artifact_id", sa.String(length=16), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=16), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_session_id", sa.String(length=36), nullable=True),
        # Audit only, never a constraint: the MCP layer drops the runtime's
        # tool_use id before a handler sees it.
        sa.Column("source_tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_format", sa.String(length=32), nullable=True),
        sa.Column("schema_version", sa.String(length=32), nullable=True),
        sa.Column("renderer_version", sa.String(length=32), nullable=True),
        sa.Column("content_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=80), nullable=False),
        sa.Column("abs_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("legacy_row_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # Idempotency: same bytes on the same artifact is the same generation,
        # however many times the tool call is replayed or retried.
        sa.UniqueConstraint(
            "user_id", "artifact_id", "content_hash", name="ux_artifact_revision_content"
        ),
    )
    op.create_index("ix_valuz_artifact_revision_user_id", "valuz_artifact_revision", ["user_id"])
    op.create_index(
        "ix_valuz_artifact_revision_artifact_id", "valuz_artifact_revision", ["artifact_id"]
    )
    op.create_index(
        "ix_valuz_artifact_revision_source_session_id",
        "valuz_artifact_revision",
        ["source_session_id"],
    )
    op.create_index(
        "ix_valuz_artifact_revision_content_id", "valuz_artifact_revision", ["content_id"]
    )
    op.create_index(
        "ix_valuz_artifact_revision_content_hash", "valuz_artifact_revision", ["content_hash"]
    )
    # Lets the backfill skip rows it already moved, so the job is re-runnable.
    op.create_index(
        "ix_valuz_artifact_revision_legacy_row_id", "valuz_artifact_revision", ["legacy_row_id"]
    )
    op.create_index(
        "ix_artifact_revision_chain", "valuz_artifact_revision", ["artifact_id", "version_no"]
    )

    op.create_table(
        "valuz_artifact_content",
        sa.Column("storage_kind", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("content_inline", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=80), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_valuz_artifact_content_user_id", "valuz_artifact_content", ["user_id"])
    # Not unique: identical bytes are still stored once per revision (the
    # readable on-disk layout rules out physical dedup), so this is a lookup for
    # "have I seen these bytes", not a uniqueness guarantee.
    op.create_index(
        "ix_artifact_content_hash", "valuz_artifact_content", ["user_id", "content_hash"]
    )


def downgrade() -> None:
    op.drop_table("valuz_artifact_content")
    op.drop_table("valuz_artifact_revision")
    op.drop_table("valuz_artifact_head")
    op.drop_table("valuz_artifact_key")
    op.drop_table("valuz_artifact")
