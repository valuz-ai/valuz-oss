"""agent plugins — the ``plugin`` install unit and its member links

Two tables (design: agent-plugins-support §4.1):

* ``valuz_plugin`` — one installed plugin per owner + name: the normalized
  Agent Plugins ``plugin.json`` (JSON text), the normalized ``mcp.json``
  (portable form), where it came from (``source`` / ``source_ref``), the layout
  it was read from (``format``), its on-disk ``PLUGIN_ROOT`` / ``PLUGIN_DATA``
  and the plugin-level enable switch.
* ``valuz_plugin_component`` — the many-to-many membership between a plugin and
  the user's library resources (skills by slug in the user skill root,
  connectors by slug). Carries the member's declared content hash, whether the
  plugin brought the resource in (``origin=installed``) or merely linked an
  existing one (``linked``) — the input to reference-counted uninstall — plus
  the ``content_differs`` conflict flag and the ``disabled_by_plugin`` marker
  the plugin enable/disable toggle uses to leave user-disabled members alone.

The ``valuz_skill_index`` / ``valuz_connector`` tables gain NO plugin column:
membership is always read from here.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_plugin",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("mcp_json", sa.Text(), nullable=True),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("data_path", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("user_id", "name", name="uq_valuz_plugin_user_name"),
    )
    op.create_index("ix_valuz_plugin_user_id", "valuz_plugin", ["user_id"])

    op.create_table(
        "valuz_plugin_component",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("plugin_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("slug", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("meta_version", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("content_differs", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disabled_by_plugin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("plugin_id", "kind", "slug", name="uq_valuz_plugin_component_member"),
    )
    op.create_index("ix_valuz_plugin_component_user_id", "valuz_plugin_component", ["user_id"])
    op.create_index("ix_valuz_plugin_component_plugin_id", "valuz_plugin_component", ["plugin_id"])


def downgrade() -> None:
    op.drop_index("ix_valuz_plugin_component_plugin_id", table_name="valuz_plugin_component")
    op.drop_index("ix_valuz_plugin_component_user_id", table_name="valuz_plugin_component")
    op.drop_table("valuz_plugin_component")
    op.drop_index("ix_valuz_plugin_user_id", table_name="valuz_plugin")
    op.drop_table("valuz_plugin")
