"""Generic host→revision binding.

Which revision a host surface (a research desk, a company page, a dashboard
slot) is currently showing. Kept in the kernel rather than in an edition
because the semantics are not industry-specific: ``host_type`` is an open
string the product surface owns, and this layer only stores, scopes and
concurrency-controls the pointer.

Revision-exact by design — never "the latest". A generation creates a version;
adopting it is a separate, user-confirmed write, so a regeneration cannot
silently replace what the user is looking at.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_artifact_binding",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("host_type", sa.String(length=64), nullable=False),
        sa.Column("host_id", sa.String(length=128), nullable=False),
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("artifact_id", sa.String(length=16), nullable=False),
        sa.Column("artifact_revision_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # One binding per slot per owner — this is what makes the
        # compare-and-set adoption meaningful: two tabs adopting different
        # versions cannot both win.
        sa.UniqueConstraint(
            "user_id",
            "host_type",
            "host_id",
            "slot",
            name="uq_artifact_binding_host_slot",
        ),
    )
    op.create_index(
        "ix_valuz_artifact_binding_artifact_id",
        "valuz_artifact_binding",
        ["artifact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_valuz_artifact_binding_artifact_id", table_name="valuz_artifact_binding"
    )
    op.drop_table("valuz_artifact_binding")
