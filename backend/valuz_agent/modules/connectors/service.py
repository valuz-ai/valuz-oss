"""Connector service — CRUD, credential split, desired-state updates.

Credential model (see docs/exec-plans/active/connector-credential-schema.md):

- The client sends ``headers`` / ``params`` as object-lists
  ``[{key, secret, value}]``. ``key`` is the *actual* header / query-param
  name; ``value`` is the final complete value (no prefix synthesis).
- Per-entry ``secret`` decides the storage lane:
    * custom connector → client ``secret`` is authoritative.
    * catalog connector → the catalog ``fields`` are authoritative
      (client ``secret`` is ignored — anti-tamper). Matching is by
      ``entry.key == field.name`` within the same ``target``.
  Secret → ``secret_store["connector/{id}/cred/{target}.{key}"]`` + a
  ``cred_manifest_json`` entry ``{key,target,name,secret_ref}``.
  Plaintext → ``headers_json`` / ``params_json``.
- Update is *desired-state*: a provided list is the full set. Non-empty
  value = set/rotate; empty/missing value = preserve original; an
  existing item absent from the list = delete (plaintext removed; secret
  ref deleted + manifest entry dropped). A ``None`` list = not provided
  (that target untouched). No deletion guard — the client must resend the
  full list, including blank-value secret entries.

The object-list is the only credential path: ``api_key`` /
``auth_header_name`` were retired in Phase B. Connectors migrated from the
legacy bearer model keep working because the migration backfilled an
equivalent ``cred_manifest_json`` entry; ``build_overrides`` reads only the
manifest + plaintext json (never ``auth_type`` / ``auth_header_name``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from valuz_agent.infra.secret_store import FileSecretStore
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


def _resolve_field(
    entry_key: str,
    target: str,
    catalog_fields: list[CatalogFieldSpec] | None,
) -> tuple[bool, str, str]:
    """Return ``(effective_secret, identity_key, name)`` for one entry.

    Catalog connectors: a field matching ``name == entry_key`` and the
    same ``target`` is authoritative over ``secret`` and contributes the
    stable manifest identity (its logical ``key``). Anything else (custom
    connector, or an extra entry not declared by the catalog) falls back
    to the client flag, with identity == name == the actual header name.
    """
    if catalog_fields:
        for f in catalog_fields:
            if f.target == target and f.name == entry_key:
                return f.secret, f.key, f.name
    return False, entry_key, entry_key


def _parse_json_dict(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _parse_manifest(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for m in parsed:
        if (
            isinstance(m, dict)
            and {"key", "target", "name", "secret_ref"} <= set(m)
            and m["target"] in ("header", "param")
        ):
            out.append({k: str(m[k]) for k in ("key", "target", "name", "secret_ref")})
    return out


@dataclass
class _Storage:
    headers_json: str | None
    params_json: str | None
    manifest_json: str | None


def _compute_storage(
    *,
    connector_id: str,
    secrets: FileSecretStore,
    headers: list[CredEntry] | None,
    params: list[CredEntry] | None,
    catalog_fields: list[CatalogFieldSpec] | None,
    existing_headers_json: str | None,
    existing_params_json: str | None,
    existing_manifest_json: str | None,
) -> _Storage:
    """Compute the three storage columns from desired-state inputs.

    A ``None`` ``headers`` / ``params`` list means "not provided" — that
    target's existing plaintext + manifest entries are carried verbatim
    (no desired-state diff, no orphan deletion) so a PATCH that touches
    only one of them leaves the other intact.
    """
    ex_plain_h = _parse_json_dict(existing_headers_json)
    ex_plain_p = _parse_json_dict(existing_params_json)
    ex_manifest = _parse_manifest(existing_manifest_json)
    ex_manifest_by_id: dict[tuple[str, str], dict[str, str]] = {
        (m["target"], m["key"]): m for m in ex_manifest
    }

    new_plain: dict[tuple[str, str], str] = {}
    new_manifest_by_id: dict[tuple[str, str], dict[str, str]] = {}

    def _carry_existing(target: str, plain: dict[str, str]) -> None:
        for name, val in plain.items():
            new_plain[(target, name)] = val
        for (mt, mk), m in ex_manifest_by_id.items():
            if mt == target:
                new_manifest_by_id[(mt, mk)] = m

    def _apply(target: str, entries: list[CredEntry], plain: dict[str, str]) -> None:
        for e in entries:
            eff_secret, idk, name = _resolve_field(e.key, target, catalog_fields)
            if not (catalog_fields and eff_secret):
                # Custom connector / non-declared extra entry: client decides.
                eff_secret = e.secret
                idk = name = e.key
            v = e.value
            if v:
                if eff_secret:
                    ref = f"connector/{connector_id}/cred/{target}.{idk}"
                    secrets.put(ref, v)
                    new_manifest_by_id[(target, idk)] = {
                        "key": idk,
                        "target": target,
                        "name": name,
                        "secret_ref": ref,
                    }
                else:
                    new_plain[(target, name)] = v
            else:  # empty/missing value → preserve original
                if (target, idk) in ex_manifest_by_id:
                    new_manifest_by_id[(target, idk)] = ex_manifest_by_id[(target, idk)]
                elif name in plain:
                    new_plain[(target, name)] = plain[name]

    if headers is None:
        _carry_existing("header", ex_plain_h)
    else:
        _apply("header", headers, ex_plain_h)
    if params is None:
        _carry_existing("param", ex_plain_p)
    else:
        _apply("param", params, ex_plain_p)

    # ── Orphan secret cleanup: existing manifest entry not carried over ──
    for (mt, mk), m in ex_manifest_by_id.items():
        if (mt, mk) not in new_manifest_by_id:
            try:
                secrets.delete(m["secret_ref"])
            except Exception:
                pass

    h = {name: v for (t, name), v in new_plain.items() if t == "header"}
    p = {name: v for (t, name), v in new_plain.items() if t == "param"}
    manifest = list(new_manifest_by_id.values())
    return _Storage(
        headers_json=json.dumps(h) if h else None,
        params_json=json.dumps(p) if p else None,
        manifest_json=json.dumps(manifest) if manifest else None,
    )


class ConnectorService:
    def __init__(
        self,
        datastore: ConnectorDatastore,
        secrets: FileSecretStore,
        remote_catalog: object | None = None,
    ) -> None:
        self._ds = datastore
        self._secrets = secrets
        self._remote_catalog = remote_catalog

    async def list_connectors(self, *, org_id: str | None = None) -> list[ConnectorView]:
        local = [_row_to_view(r) for r in await self._ds.list_all()]
        if self._remote_catalog is None:
            return local
        try:
            remote = self._remote_catalog.list_remote_connectors(org_id=org_id)
            return local + remote
        except Exception:
            return local

    async def resolve_mcp_servers(self, slugs: list[str]) -> list[Any]:
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
            secrets=self._secrets,
            enabled_slugs=slugs,
            connectors=self._ds,
        )

    async def get_connector(self, connector_id: str) -> ConnectorView | None:
        row = await self._ds.get_by_id(connector_id)
        return _row_to_view(row) if row else None

    async def create_connector(
        self,
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

        _slug = slug or display_name
        _slug = re.sub(r"[^a-z0-9_-]", "-", _slug.lower().strip())[:64]
        if await self._ds.get_by_slug(_slug):
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
                args_json=json.dumps(args or []),
                working_dir=working_dir,
                env_json=json.dumps(env) if env else None,
                enabled=True,
                status="connecting",
            )
            return _row_to_view(await self._ds.create(row))

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
        saved = await self._ds.create(row)

        storage = _compute_storage(
            connector_id=saved.id,
            secrets=self._secrets,
            headers=headers,
            params=params,
            catalog_fields=catalog_fields,
            existing_headers_json=None,
            existing_params_json=None,
            existing_manifest_json=None,
        )
        saved.headers_json = storage.headers_json
        saved.params_json = storage.params_json
        saved.cred_manifest_json = storage.manifest_json
        return _row_to_view(await self._ds.update(saved))

    async def update_connector(
        self,
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
        row = await self._ds.get_by_id(connector_id)
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
            row.args_json = json.dumps(args)
        if working_dir is not None:
            row.working_dir = working_dir
        if env is not None:
            row.env_json = json.dumps(env)

        creds_touched = headers is not None or params is not None
        if creds_touched:
            storage = _compute_storage(
                connector_id=connector_id,
                secrets=self._secrets,
                headers=headers,
                params=params,
                catalog_fields=catalog_fields,
                existing_headers_json=row.headers_json,
                existing_params_json=row.params_json,
                existing_manifest_json=row.cred_manifest_json,
            )
            row.headers_json = storage.headers_json
            row.params_json = storage.params_json
            row.cred_manifest_json = storage.manifest_json

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

    async def delete_connector(self, connector_id: str) -> bool:
        row = await self._ds.get_by_id(connector_id)
        if row is None:
            return False
        if row.connector_type == "builtin":
            return False
        # Drop every credential this connector owns: manifest-referenced
        # secrets (the Slice-2 backfill already points migrated bearer
        # connectors here too) and the OAuth token.
        for m in _parse_manifest(row.cred_manifest_json):
            try:
                self._secrets.delete(m["secret_ref"])
            except Exception:
                pass
        try:
            self._secrets.delete(f"connector/{connector_id}/oauth_token")
        except Exception:
            pass
        return await self._ds.delete(connector_id)

    async def set_enabled(self, connector_id: str, *, enabled: bool) -> ConnectorView | None:
        row = await self._ds.get_by_id(connector_id)
        if row is None:
            return None
        row.enabled = enabled
        row.status = "unknown" if enabled else "disabled"
        row.updated_at = now_ms()
        return _row_to_view(await self._ds.update(row))

    async def record_test_result(
        self,
        connector_id: str,
        *,
        ok: bool,
        tool_count: int | None = None,
        error_message: str | None = None,
    ) -> ConnectorView | None:
        row = await self._ds.get_by_id(connector_id)
        if row is None:
            return None
        row.status = "connected" if ok else "error"
        row.tool_count = tool_count
        row.last_tested_at = now_ms()
        row.error_message = None if ok else error_message
        row.updated_at = now_ms()
        return _row_to_view(await self._ds.update(row))


def _row_to_view(row: ConnectorRow) -> ConnectorView:
    args: list[str] = []
    if row.args_json:
        try:
            parsed = json.loads(row.args_json)
            if isinstance(parsed, list):
                args = [str(a) for a in parsed]
        except json.JSONDecodeError:
            pass

    plain_h = _parse_json_dict(row.headers_json)
    plain_p = _parse_json_dict(row.params_json)
    headers: list[CredView] = [CredView(key=k, secret=False, value=v) for k, v in plain_h.items()]
    params: list[CredView] = [CredView(key=k, secret=False, value=v) for k, v in plain_p.items()]
    for m in _parse_manifest(row.cred_manifest_json):
        view_entry = CredView(key=m["name"], secret=True, value=None)
        if m["target"] == "param":
            params.append(view_entry)
        else:
            headers.append(view_entry)

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
        has_api_key=bool(_parse_manifest(row.cred_manifest_json)),
        command=row.command,
        args=args,
        working_dir=row.working_dir,
        headers=headers,
        params=params,
        enabled=row.enabled,
        status=row.status,
        tool_count=row.tool_count,
        last_tested_at=row.last_tested_at,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def build_overrides(
    row: ConnectorRow, secrets: FileSecretStore
) -> tuple[dict[str, str], dict[str, str]]:
    """Single source of truth for connector header/param injection.

    Returns ``(headers, params)``: plaintext ``headers_json`` /
    ``params_json`` as the base, then every ``cred_manifest_json`` entry's
    secret value placed by ``target``. **Does not** branch on ``auth_type``
    and **does not** read ``auth_header_name`` — the manifest is
    authoritative (Slice 2 backfilled legacy bearer connectors into it).
    OAuth is layered on by the caller *after* this (it needs a live token
    fetch), per the exec-plan.

    Transitional compat (removed in Slice 7): an ``Authorization`` header
    whose resolved secret value does not already start with ``Bearer ``
    gets the ``Bearer `` prefix. New-model values are sent final by the
    client (catalog ``prefix`` prefills ``Bearer `` in the form), so they
    pass through untouched; legacy / Slice-2-migrated secrets store the raw
    token and rely on this to stay byte-identical to the old injection.
    """
    headers: dict[str, str] = dict(_parse_json_dict(row.headers_json))
    params: dict[str, str] = dict(_parse_json_dict(row.params_json))
    for m in _parse_manifest(row.cred_manifest_json):
        val = secrets.get(m["secret_ref"])
        if val is None:
            continue
        if m["target"] == "param":
            params[m["name"]] = val
        else:
            name = m["name"]
            if name.lower() == "authorization" and not val.lower().startswith("bearer "):
                val = f"Bearer {val}"
            headers[name] = val
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
