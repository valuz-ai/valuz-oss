"""agents: add Valurion identity, resource, and prompt-inheritance contract

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-29

"""

import json
import time
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "valuz_agent"
_VALURION_SLUG = "valurion"
_VALURION_DEFAULT_EFFORT = "high"
_VALURION_DESCRIPTION = (
    "Your built-in assistant with access to all resources currently available to you."
)


def _install_valurion() -> None:
    """Pure-insert Valurion for existing owners without touching other Agents."""
    bind = op.get_bind()
    owners = {
        str(row[0]) for row in bind.execute(sa.text("SELECT DISTINCT user_id FROM valuz_agent"))
    }
    for user_id in owners:
        exists = bind.execute(
            sa.text("SELECT 1 FROM valuz_agent WHERE user_id = :user_id AND slug = :slug LIMIT 1"),
            {"user_id": user_id, "slug": _VALURION_SLUG},
        ).first()
        if exists is not None:
            continue

        # Reuse the owner's explicit default-assistant brain when present. Do
        # not inspect another Agent (especially valuz-helper) to infer Valurion
        # identity or behavior. No existing Agent is updated, renamed, deleted,
        # copied, or rebound.
        brain = (
            bind.execute(
                sa.text(
                    """
                    SELECT runtime, model, provider_id
                    FROM valuz_agent
                    WHERE user_id = :user_id AND slug = 'default-assistant'
                    LIMIT 1
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )
        now_ms = int(time.time() * 1000)
        insert_stmt = sa.text(
            """
                INSERT INTO valuz_agent (
                    slug, name, description, instructions, runtime, model, skills,
                    connector_types, knowledge_scope, provider_id, effort, kind,
                    resource_policy, inherit_global_instructions, permission_mode,
                    source, readonly, deletable, avatar, id, created_at, updated_at,
                    user_id
                ) VALUES (
                    :slug, 'Valurion', :description, '', :runtime, :model, :empty,
                    :empty, :empty, :provider_id, :effort, 'system',
                    'all_available', :inherit_global_instructions,
                    'full_access', 'builtin', :readonly, :deletable, 'bot',
                    :id, :created_at, :updated_at, :user_id
                )
                """
        ).bindparams(
            sa.bindparam("inherit_global_instructions", type_=sa.Boolean()),
            sa.bindparam("readonly", type_=sa.Boolean()),
            sa.bindparam("deletable", type_=sa.Boolean()),
        )
        bind.execute(
            insert_stmt,
            {
                "slug": _VALURION_SLUG,
                "description": _VALURION_DESCRIPTION,
                "runtime": (brain.get("runtime") if brain else None) or "claude_agent",
                "model": (brain.get("model") if brain else None) or "claude-sonnet-4-6",
                "empty": json.dumps([]),
                "provider_id": brain.get("provider_id") if brain else None,
                "effort": _VALURION_DEFAULT_EFFORT,
                "inherit_global_instructions": True,
                "readonly": True,
                "deletable": False,
                "id": str(uuid.uuid4()),
                "created_at": now_ms,
                "updated_at": now_ms,
                "user_id": user_id,
            },
        )


def _uninstall_valurion() -> None:
    """Remove only the system row introduced by this contract on downgrade."""
    bind = op.get_bind()
    delete_stmt = sa.text(
        """
        DELETE FROM valuz_agent
        WHERE slug = :slug
          AND kind = 'system'
          AND source = 'builtin'
          AND readonly = :readonly
          AND deletable = :deletable
        """
    ).bindparams(
        sa.bindparam("readonly", type_=sa.Boolean()),
        sa.bindparam("deletable", type_=sa.Boolean()),
    )
    bind.execute(
        delete_stmt,
        {"slug": _VALURION_SLUG, "readonly": True, "deletable": False},
    )


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(
            sa.Column(
                "knowledge_scope",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "kind",
                sa.String(length=16),
                nullable=False,
                server_default="standard",
            )
        )
        batch.add_column(
            sa.Column(
                "resource_policy",
                sa.String(length=24),
                nullable=False,
                server_default="explicit",
            )
        )
        batch.add_column(
            sa.Column(
                "inherit_global_instructions",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "permission_mode",
                sa.String(length=32),
                nullable=False,
                server_default="full_access",
            )
        )
        batch.create_check_constraint(
            "ck_valuz_agent_kind",
            "kind IN ('system', 'standard')",
        )
        batch.create_check_constraint(
            "ck_valuz_agent_resource_policy",
            "resource_policy IN ('explicit', 'all_available')",
        )
    _install_valurion()


def downgrade() -> None:
    _uninstall_valurion()
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(
            "ck_valuz_agent_resource_policy",
            type_="check",
        )
        batch.drop_constraint("ck_valuz_agent_kind", type_="check")
        batch.drop_column("permission_mode")
        batch.drop_column("inherit_global_instructions")
        batch.drop_column("resource_policy")
        batch.drop_column("kind")
        batch.drop_column("knowledge_scope")
