"""Connector ORM model.

A connector represents an MCP server the user has wired into their project.
There are three flavours:

- ``builtin``: First-party data sources bundled with Valuz (e.g. the
  Reportify MCP). Seeded at boot; the user cannot delete them.
- ``directory``: Well-known third-party MCP servers surfaced in the
  Connector Directory (GitHub, Notion, Linear, …). Installed via the
  in-conversation ``connector_install`` flow with dynamic OAuth (RFC 7591).
- ``custom``: User-defined MCP servers. Two transports:
  ``http``  — any HTTP/SSE-based MCP server reachable over the network.
  ``stdio`` — local process-based MCP server (filesystem, git, browser, …)
              spawned by the Electron main process.

Credential references (``auth_secret_ref``) and OAuth state are stored in
``FileSecretStore`` (never in plain SQL):

  secret_store["connector/{id}/api_key"]       — raw API-key / bearer token
  secret_store["connector/{id}/oauth_token"]   — JSON-serialised OAuthToken
  secret_store["connector/oauth_state/{state}"] — transient PKCE state (TTL)
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin, PrimaryKeyMixin, TimestampMixin

# Canonical set of connector auth strategies. Single source of truth shared
# by the API schemas, the service layer and the catalog so callers don't
# have to guess valid values. ``oauth`` is the self-contained PKCE flow
# (connectors.py); ``bearer`` / ``none`` are now purely informational —
# header/param injection is driven solely by the object-list + per-entry
# ``secret`` (see service.build_overrides), not by auth_type. There is
# deliberately no ``oauth_account`` and no ``api_key``.
AuthType = Literal["none", "bearer", "oauth"]

# Canonical set of connector transports. ``http``/``sse`` are network MCP
# servers; ``stdio`` is a local process spawned by the desktop shell.
TransportType = Literal["http", "sse", "stdio"]


class ConnectorRow(Base, PrimaryKeyMixin, TimestampMixin, OwnedMixin):
    """One MCP connector installed (or built-in) for the local user."""

    __tablename__ = "valuz_connector"
    # DB-level enforcement of the canonical AuthType / TransportType sets —
    # the column stays a plain String (SQLite has no native enum) but a
    # CHECK constraint rejects out-of-set values at write time.
    __table_args__ = (
        CheckConstraint(
            "auth_type IN ('none', 'bearer', 'oauth')",
            name="ck_valuz_connector_auth_type",
        ),
        CheckConstraint(
            "transport IN ('http', 'sse', 'stdio')",
            name="ck_valuz_connector_transport",
        ),
    )

    slug: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)

    connector_type: Mapped[str] = mapped_column(String(32))
    transport: Mapped[str] = mapped_column(String(16), default="http")

    url: Mapped[str | None] = mapped_column(Text)

    auth_type: Mapped[str] = mapped_column(String(32), default="none")

    oauth_metadata_json: Mapped[str | None] = mapped_column(Text)
    oauth_client_info_json: Mapped[str | None] = mapped_column(Text)

    command: Mapped[str | None] = mapped_column(Text)
    args_json: Mapped[str | None] = mapped_column(Text)
    working_dir: Mapped[str | None] = mapped_column(Text)
    env_json: Mapped[str | None] = mapped_column(Text)
    # Non-secret plaintext only (GET echoes these). Secret entries live in
    # the secret store, indexed by cred_manifest_json. See connector
    # credential-schema exec-plan (three-way storage split).
    headers_json: Mapped[str | None] = mapped_column(Text)
    params_json: Mapped[str | None] = mapped_column(Text)
    # [{key, target, name, secret_ref}] — no values; the value of each
    # secret entry lives at secret_store["connector/{id}/cred/{key}"].
    cred_manifest_json: Mapped[str | None] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(32), default="unknown")
    tool_count: Mapped[int | None] = mapped_column(Integer)
    last_tested_at: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)
