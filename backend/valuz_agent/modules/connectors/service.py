"""Connector service — CRUD, credential storage, desired-state updates.

Credential model:

- The client sends ``headers`` / ``params`` as object-lists
  ``[{key, secret, value}]``. ``key`` is the *actual* header / query-param
  name; ``value`` is the final complete value (no prefix synthesis).
- Both plaintext and secret entries live together in ``headers_json`` /
  ``params_json`` as ``{name: {"value": v, "secret": bool}}`` — each entry
  self-describes. ``secret`` decides only whether the value is withheld from
  ``GET`` (it is stored plaintext either way). A catalog connector's declared
  ``fields`` can FORCE ``secret`` true (anti-tamper); otherwise the client's
  per-entry flag wins.
- Update is *desired-state*: a provided list is the full set. Non-empty
  value = set/rotate; blank value = preserve original; an existing entry
  absent from the list = delete. A ``None`` list = not provided (that target
  untouched).

The object-list is the only credential path: ``api_key`` / ``auth_header_name``
were retired in Phase B. ``build_overrides`` injects every stored value (never
branches on ``auth_type`` / ``auth_header_name``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import AuthType, ConnectorRow, TransportType

logger = logging.getLogger(__name__)


@dataclass
class CredEntry:
    """One header/param spec from the client (API ``HeaderParam`` → this)."""

    key: str
    secret: bool = False
    value: str | None = None


@dataclass
class CatalogFieldSpec:
    """Server-authoritative declared field for a catalog connector."""

    key: str  # logical id — manifest identity for catalog entries
    name: str  # actual header/param name, matched against CredEntry.key
    target: str  # "header" | "param"
    secret: bool


@dataclass
class CredView:
    """One header/param as returned to the client. Secret → no value."""

    key: str
    secret: bool
    value: str | None


@dataclass
class ConnectorView:
    id: str
    slug: str
    display_name: str
    description: str | None
    connector_type: str
    transport: TransportType
    url: str | None
    auth_type: AuthType
    has_api_key: bool
    command: str | None
    args: list[str]
    working_dir: str | None
    headers: list[CredView]
    params: list[CredView]
    enabled: bool
    status: str
    tool_count: int | None
    last_tested_at: int | None
    error_message: str | None
    created_at: int
    updated_at: int


def _parse_cred_entries(raw: str | None) -> dict[str, dict[str, Any]]:
    """Parse ``headers_json`` / ``params_json`` → ``{name: {"value", "secret"}}``.

    Tolerant of a legacy ``{name: value}`` (plain string) entry — it is read as a
    non-secret entry so a row written by the pre-unified model still injects.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in parsed.items():
        if isinstance(v, dict) and isinstance(v.get("value"), str):
            out[str(k)] = {"value": v["value"], "secret": bool(v.get("secret", False))}
        elif isinstance(v, str):  # legacy plaintext shape
            out[str(k)] = {"value": v, "secret": False}
    return out


def _is_secret_entry(
    e: CredEntry, target: str, catalog_fields: list[CatalogFieldSpec] | None
) -> bool:
    """Whether a client entry is a secret.

    A catalog field can FORCE secret (anti-tamper — a client can't downgrade a
    known-secret field); otherwise the client's per-entry flag decides.
    """
    if catalog_fields:
        for f in catalog_fields:
            if f.target == target and f.name == e.key and f.secret:
                return True
    return e.secret


@dataclass
class _Storage:
    headers_json: str | None
    params_json: str | None


def _compute_storage(
    *,
    headers: list[CredEntry] | None,
    params: list[CredEntry] | None,
    catalog_fields: list[CatalogFieldSpec] | None,
    existing_headers_json: str | None,
    existing_params_json: str | None,
) -> _Storage:
    """Compute ``headers_json`` / ``params_json`` from desired-state inputs.

    Each entry is stored self-describing: ``{name: {"value", "secret"}}``. A
    ``None`` list means "not provided" → that target is carried verbatim. For a
    provided list (the full desired set): a non-empty value sets/rotates; a
    blank value preserves the existing entry; an existing entry absent from the
    list is dropped (its value — secret or not — disappears with it).
    """

    def _build(
        entries: list[CredEntry] | None, existing_json: str | None, target: str
    ) -> dict[str, dict[str, Any]]:
        existing = _parse_cred_entries(existing_json)
        if entries is None:
            return existing  # not provided → carry verbatim
        new: dict[str, dict[str, Any]] = {}
        for e in entries:
            if e.value:
                new[e.key] = {
                    "value": e.value,
                    "secret": _is_secret_entry(e, target, catalog_fields),
                }
            elif e.key in existing:  # blank value → preserve original
                new[e.key] = existing[e.key]
        return new

    h = _build(headers, existing_headers_json, "header")
    p = _build(params, existing_params_json, "param")
    return _Storage(
        headers_json=json.dumps(h) if h else None,
        params_json=json.dumps(p) if p else None,
    )


class ConnectorService:
    def __init__(
        self,
        datastore: ConnectorDatastore,
        remote_catalog: object | None = None,
    ) -> None:
        self._ds = datastore
        self._remote_catalog = remote_catalog

    @classmethod
    def with_defaults(cls, db: Any) -> ConnectorService:
        """Build a ConnectorService over an existing DB session.

        Cohesion seam for callers outside this module: they may not import
        ``ConnectorDatastore`` directly (module-boundary rule), so this is the
        sanctioned way to get a working service from just a session — e.g.
        ``AgentService`` resolving an agent's ``connector_types`` into MCP
        servers on session-creation paths that have no DI container.
        """
        return cls(datastore=ConnectorDatastore(db))

    async def list_connectors(
        self, user_id: str, *, org_id: str | None = None
    ) -> list[ConnectorView]:
        local = [_row_to_view(r) for r in await self._ds.list_all(user_id)]
        if self._remote_catalog is None:
            return local
        try:
            remote = self._remote_catalog.list_remote_connectors(org_id=org_id)
            return local + remote
        except Exception:
            return local

    async def resolve_mcp_servers(self, slugs: list[str], user_id: str | None = None) -> list[Any]:
        """Materialise enabled connector slugs into kernel ``McpServerConfig``.

        Cohesion seam: the connector module owns credential/header injection
        (``build_overrides``) and connector rows, so it also owns translating a
        chosen set of connectors into runnable MCP server configs. Callers
        (e.g. ``AgentService``) depend on this instead of reaching into the
        secret store directly. Delegates the kernel-shaping to the
        ``mcp_resolver`` adapter, passing this service's own datastore +
        secret store.
        """
        if not slugs:
            return []
        from valuz_agent.adapters.mcp_resolver import resolve_mcp_servers

        return await resolve_mcp_servers(
            enabled_slugs=slugs,
            connectors=self._ds,
            user_id=user_id,
        )

    async def get_connector(self, user_id: str, connector_id: str) -> ConnectorView | None:
        row = await self._ds.get_by_id(user_id, connector_id)
        return _row_to_view(row) if row else None

    async def create_connector(
        self,
        user_id: str,
        *,
        slug: str | None = None,
        display_name: str,
        transport: TransportType,
        description: str | None = None,
        connector_type: str = "custom",
        # http / sse
        url: str | None = None,
        auth_type: AuthType = "none",
        headers: list[CredEntry] | None = None,
        params: list[CredEntry] | None = None,
        catalog_fields: list[CatalogFieldSpec] | None = None,
        # stdio
        command: str | None = None,
        args: list[str] | None = None,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ConnectorView:
        import re

        explicit_slug = slug is not None
        _slug = slug or display_name
        _slug = re.sub(r"[^a-z0-9_-]", "-", _slug.lower().strip())[:64]
        existing = await self._ds.get_by_slug(user_id, _slug)
        if existing is not None and explicit_slug:
            return _row_to_view(existing)
        if existing is not None:
            from uuid import uuid4

            _slug = f"{_slug}-{uuid4().hex[:6]}"

        if transport == "stdio":
            row = ConnectorRow(
                slug=_slug,
                display_name=display_name,
                description=description,
                connector_type=connector_type,
                transport="stdio",
                auth_type="none",
                command=command,
                args=json.dumps(args or []),
                working_dir=working_dir,
                env_json=json.dumps(env) if env else None,
                enabled=True,
                status="connecting",
            )
            return _row_to_view(await self._ds.create(user_id, row))

        row = ConnectorRow(
            slug=_slug,
            display_name=display_name,
            description=description,
            connector_type=connector_type,
            transport=transport if transport in ("http", "sse") else "http",
            url=url,
            auth_type=auth_type,
            enabled=True,
            status="connecting",
        )
        saved = await self._ds.create(user_id, row)

        storage = _compute_storage(
            headers=headers,
            params=params,
            catalog_fields=catalog_fields,
            existing_headers_json=None,
            existing_params_json=None,
        )
        saved.headers_json = storage.headers_json
        saved.params_json = storage.params_json
        return _row_to_view(await self._ds.update(saved))

    async def update_connector(
        self,
        user_id: str,
        connector_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        url: str | None = None,
        auth_type: AuthType | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        headers: list[CredEntry] | None = None,
        params: list[CredEntry] | None = None,
        catalog_fields: list[CatalogFieldSpec] | None = None,
        enabled: bool | None = None,
    ) -> ConnectorView | None:
        row = await self._ds.get_by_id(user_id, connector_id)
        if row is None:
            return None
        if display_name is not None:
            row.display_name = display_name
        if description is not None:
            row.description = description
        if url is not None:
            row.url = url
        if auth_type is not None:
            row.auth_type = auth_type
        if command is not None:
            row.command = command
        if args is not None:
            row.args = json.dumps(args)
        if working_dir is not None:
            row.working_dir = working_dir
        if env is not None:
            row.env_json = json.dumps(env)

        creds_touched = headers is not None or params is not None
        if creds_touched:
            storage = _compute_storage(
                headers=headers,
                params=params,
                catalog_fields=catalog_fields,
                existing_headers_json=row.headers_json,
                existing_params_json=row.params_json,
            )
            row.headers_json = storage.headers_json
            row.params_json = storage.params_json

        if enabled is not None:
            row.enabled = enabled
            if enabled and row.status == "disabled":
                row.status = "connecting"
            elif not enabled:
                row.status = "disabled"
        # Reset status when MCP connection params change.
        if any(v is not None for v in (url, auth_type, command, args)) or creds_touched:
            if row.status not in ("disabled", "pending_auth"):
                row.status = "connecting"
        row.updated_at = now_ms()
        return _row_to_view(await self._ds.update(row))

    async def delete_connector(self, user_id: str, connector_id: str) -> bool:
        row = await self._ds.get_by_id(user_id, connector_id)
        if row is None:
            return False
        if row.connector_type == "builtin":
            return False
        # Secret material (creds + OAuth token) lives in this connector's own
        # columns, so deleting the row drops every credential with it.
        return await self._ds.delete(user_id, connector_id)

    async def set_enabled(
        self, user_id: str, connector_id: str, *, enabled: bool
    ) -> ConnectorView | None:
        row = await self._ds.get_by_id(user_id, connector_id)
        if row is None:
            return None
        row.enabled = enabled
        row.status = "unknown" if enabled else "disabled"
        row.updated_at = now_ms()
        return _row_to_view(await self._ds.update(row))

    async def record_test_result(
        self,
        user_id: str,
        connector_id: str,
        *,
        ok: bool,
        tool_count: int | None = None,
        error_message: str | None = None,
    ) -> ConnectorView | None:
        row = await self._ds.get_by_id(user_id, connector_id)
        if row is None:
            return None
        row.status = "connected" if ok else "error"
        row.tool_count = tool_count
        row.last_tested_at = now_ms()
        row.error_message = None if ok else error_message
        row.updated_at = now_ms()
        return _row_to_view(await self._ds.update(row))


def _effective_status(row: ConnectorRow) -> str:
    """Display status for the API, normalising one misleading case.

    An OAuth connector connects through the login flow (``pending_auth`` →
    ``connected``), never through a background "connecting" probe. A row left at
    ``connecting`` — created before authorization and never logged in — is
    really "not connected, needs login", so report it as ``pending_auth``.
    Otherwise the UI shows a perpetual 连接中 (and the nav attention dot, which
    ignores ``connecting``, never fires). Non-OAuth connectors are untouched:
    their ``connecting`` is a real in-flight probe.
    """
    if row.auth_type == "oauth" and row.status == "connecting":
        return "pending_auth"
    return row.status


def _row_to_view(row: ConnectorRow) -> ConnectorView:
    args: list[str] = []
    if row.args:
        try:
            parsed = json.loads(row.args)
            if isinstance(parsed, list):
                args = [str(a) for a in parsed]
        except json.JSONDecodeError:
            pass

    h_entries = _parse_cred_entries(row.headers_json)
    p_entries = _parse_cred_entries(row.params_json)
    # Secret entries are withheld (value=None); plaintext entries are echoed.
    headers: list[CredView] = [
        CredView(key=k, secret=e["secret"], value=None if e["secret"] else e["value"])
        for k, e in h_entries.items()
    ]
    params: list[CredView] = [
        CredView(key=k, secret=e["secret"], value=None if e["secret"] else e["value"])
        for k, e in p_entries.items()
    ]

    return ConnectorView(
        id=row.id,
        slug=row.slug,
        display_name=row.display_name,
        description=row.description,
        connector_type=row.connector_type,
        # DB→domain trust boundary: every write path constrains transport /
        # auth_type to their canonical Literal sets, so the stored strings are
        # always valid members.
        transport=cast(TransportType, row.transport),
        url=row.url,
        auth_type=cast(AuthType, row.auth_type),
        has_api_key=any(e["secret"] for e in h_entries.values())
        or any(e["secret"] for e in p_entries.values()),
        command=row.command,
        args=args,
        working_dir=row.working_dir,
        headers=headers,
        params=params,
        enabled=row.enabled,
        status=_effective_status(row),
        tool_count=row.tool_count,
        last_tested_at=row.last_tested_at,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def build_overrides(
    row: ConnectorRow,
) -> tuple[dict[str, str], dict[str, str]]:
    """Single source of truth for connector header/param injection.

    Returns ``(headers, params)`` — every stored value from ``headers_json`` /
    ``params_json``, secret or not. **Does not** branch on ``auth_type``. OAuth
    is layered on by the caller *after* this (it needs a live token fetch).

    Transitional compat: a *secret* ``Authorization`` whose stored value does
    not already start with ``Bearer `` gets the ``Bearer `` prefix — legacy /
    migrated tokens are stored raw and rely on this to inject byte-identically.
    """
    headers: dict[str, str] = {}
    for name, e in _parse_cred_entries(row.headers_json).items():
        val = e["value"]
        is_bare_auth = name.lower() == "authorization" and not val.lower().startswith("bearer ")
        if e["secret"] and is_bare_auth:
            val = f"Bearer {val}"
        headers[name] = val
    params: dict[str, str] = {
        name: e["value"] for name, e in _parse_cred_entries(row.params_json).items()
    }
    return headers, params


def merge_params_into_url(url: str, params: dict[str, str]) -> str:
    """Merge ``params`` into ``url``'s query string.

    Same-name keys are overridden by ``params``; non-conflicting existing
    query pairs are preserved; values are urlencoded. Shared by the runtime
    resolver and the probe so both hit byte-identical URLs.
    """
    if not params:
        return url
    parts = urlsplit(url)
    existing = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in params
    ]
    merged = existing + list(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


__all__ = [
    "CatalogFieldSpec",
    "CredEntry",
    "CredView",
    "ConnectorService",
    "ConnectorView",
    "build_overrides",
    "merge_params_into_url",
]
