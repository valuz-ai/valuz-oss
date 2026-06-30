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

The connector's secret/optional blob attributes — header/param credentials and
stdio env — live OUT of this row in a sparse ``valuz_connector_attr`` key→value
table (one row per present attribute). The OAuth credentials (dynamic-client
info, token, and token expiry) live in their own 1:1 ``valuz_connector_oauth``
row, with the expiry pulled out as an indexed scalar for scheduled refresh.
These side tables are NOT ORM relationships: the datastore loads them on read
(``_hydrate``) into plain in-memory holders and persists them on write.
``ConnectorRow`` exposes both holders as transparent properties, so callers
still read/write ``row.headers_json``, ``row.oauth_token_json`` etc. unchanged.
``headers_json`` / ``params_json`` hold ``{name: {"value", "secret"}}`` —
``secret: true`` values are plaintext at rest but withheld from ``GET``.

The two NON-secret blobs — the discovered OAuth server metadata
(``oauth_metadata``) and the stdio launch args (``args``) — are plain columns ON
this row, not attrs: they belong to the connector definition, carry no
credentials, and are read on hot paths (resolve / token refresh). Both hold a
JSON string.

The transient PKCE handoff during the OAuth dance is NOT stored here — it is
ephemeral auth scratch kept in ``ext.cache`` (a file cache locally, Redis on the
shared backend), keyed by the ``state`` token.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin
from valuz_agent.infra.time_utils import now_ms


def _attr_prop(key: str) -> property:
    """A transparent ``str | None`` property backed by ``ConnectorRow._attrs``."""
    return property(
        lambda self: self._attr_get(key),
        lambda self, v: self._attr_set(key, v),
    )


def _oauth_prop(field: str) -> property:
    """A transparent property backed by the 1:1 ``ConnectorRow._oauth`` row."""
    return property(
        lambda self: self._oauth_get(field),
        lambda self, v: self._oauth_set(field, v),
    )


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


class ConnectorRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
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
        UniqueConstraint("user_id", "slug", name="uq_valuz_connector_user_slug"),
    )

    slug: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)

    connector_type: Mapped[str] = mapped_column(String(32))
    transport: Mapped[str] = mapped_column(String(16), default="http")

    url: Mapped[str | None] = mapped_column(Text)

    auth_type: Mapped[str] = mapped_column(String(32), default="none")
    # Discovered OAuth server metadata (token/authorization/registration
    # endpoints, scopes …) as a JSON string — non-secret, a property of the
    # server definition.
    oauth_metadata: Mapped[str | None] = mapped_column(Text)

    command: Mapped[str | None] = mapped_column(Text)
    working_dir: Mapped[str | None] = mapped_column(Text)
    # stdio launch args (JSON-string list) — non-secret, part of the launch spec.
    args: Mapped[str | None] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(32), default="unknown")
    tool_count: Mapped[int | None] = mapped_column(Integer)
    last_tested_at: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)

    # ── Side-table data — datastore-loaded plain holders, NOT relationships ──
    # ``valuz_connector_attr`` (key→value: headers/params/env) and the 1:1
    # ``valuz_connector_oauth`` row are deliberately NOT ORM relationships. The
    # datastore loads them on read (``_hydrate``) into the plain dict holders
    # below and persists them on write (desired-state replace). The transparent
    # ``*_json`` properties read/write those dicts, so callers are unchanged.
    # The owner ``user_id`` is stamped by the datastore when it materializes the
    # side rows — these holders carry values only.

    def _attr_store(self) -> dict[str, str]:
        store: dict[str, str] | None = getattr(self, "_attrs", None)
        if store is None:
            store = {}
            self._attrs = store
        return store

    def _attr_get(self, key: str) -> str | None:
        return self._attr_store().get(key)

    def _attr_set(self, key: str, value: object) -> None:
        store = self._attr_store()
        if value is None:
            store.pop(key, None)
        else:
            store[key] = str(value)

    def _oauth_store(self) -> dict[str, object]:
        store: dict[str, object] | None = getattr(self, "_oauth", None)
        if store is None:
            store = {}
            self._oauth = store
        return store

    def _oauth_get(self, field: str) -> object | None:
        return self._oauth_store().get(field)

    def _oauth_set(self, field: str, value: object) -> None:
        store = self._oauth_store()
        if value is None:
            store.pop(field, None)
        else:
            store[field] = value

    # The accessor names keep the ``_json`` suffix (callers read ``row.headers_json``
    # etc. unchanged), but the stored KEY/column drops it — the attr holder keys
    # ``headers`` / ``env``, and the oauth holder keys ``client_info`` / ``token``.
    oauth_client_info_json = _oauth_prop("client_info")
    oauth_token_json = _oauth_prop("token")
    env_json = _attr_prop("env")
    # Self-describing header/param entries ``{name: {"value", "secret"}}``.
    headers_json = _attr_prop("headers")
    params_json = _attr_prop("params")

    @property
    def oauth_token_expires_at(self) -> int | None:
        v = self._oauth_store().get("expires_at")
        return int(v) if isinstance(v, int) else None

    @oauth_token_expires_at.setter
    def oauth_token_expires_at(self, v: int | None) -> None:
        self._oauth_set("expires_at", None if v is None else int(v))


class ConnectorAttrRow(Base, UserMixin):
    """Sparse ``connector_id → key → value`` extension attributes.

    Holds a connector's remaining secret blob attributes (header/param creds and
    stdio env) out of the main row — one row per present attribute.
    ``ConnectorRow`` proxies them as properties. OAuth credentials moved to their
    own ``valuz_connector_oauth`` row; ``args`` / ``oauth_metadata`` are columns.

    Carries the owner's ``user_id`` (``UserMixin``) like every other business
    table, and deliberately has NO DB-level ForeignKey to ``valuz_connector``:
    referential cleanup is the datastore's explicit delete (these rows are not
    an ORM relationship), consistent with the other ``valuz_*`` tables.
    """

    __tablename__ = "valuz_connector_attr"

    connector_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class ConnectorOAuthRow(Base, UserMixin):
    """Per-connector OAuth credentials, split out of ``valuz_connector_attr``.

    One 1:1 row per OAuth connector: the dynamic-registration client info
    (``client_info``), the token (``token`` — access + refresh), and the token
    expiry (``expires_at``) pulled out as an INDEXED ``BigInteger`` scalar so a
    scheduled refresher can cheaply query tokens nearing expiry
    (``WHERE expires_at < :soon``). The server's non-secret OAuth endpoint
    metadata is NOT here — it lives on the ``valuz_connector`` row
    (``oauth_metadata``), since it is definitional.

    Carries the owner ``user_id`` (``UserMixin``) and has NO DB-level ForeignKey
    to ``valuz_connector`` (matches the other ``valuz_*`` tables); cleanup is the
    datastore's explicit delete (these rows are not an ORM relationship).
    """

    __tablename__ = "valuz_connector_oauth"

    connector_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_info: Mapped[str | None] = mapped_column(Text)
    token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, index=True)


class ProjectConnectorRow(Base, UserMixin):
    """Which connectors a project has enabled (per-owner, per-project).

    Replaces the legacy ``<project>/.claude/project-config.json`` ``connectors``
    list: that file-backed store assumed a per-user local filesystem, which a
    shared multi-client backend does not have. One row per (project, slug);
    mirrors the skills module's ``ProjectSkillConfigRow``.
    """

    __tablename__ = "valuz_project_connector"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    added_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
