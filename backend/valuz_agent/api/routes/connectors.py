"""Connector management routes."""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import async_unit_of_work, get_async_session
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.connectors.catalog import load_catalog
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import AuthType, ConnectorRow, TransportType
from valuz_agent.modules.connectors.service import (
    CatalogFieldSpec,
    ConnectorService,
    ConnectorView,
    CredEntry,
    CredView,
    build_overrides,
    merge_params_into_url,
)
from valuz_agent.ports.extensions import ext

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])

logger = logging.getLogger(__name__)


def _pkce_cache_key(state: str) -> str:
    """Namespaced ``ext.cache`` key for a connector OAuth PKCE handoff."""
    return f"connector:oauth_pkce:{state}"


# RFC 7230 token charset — also a safe (conservative) query-param name set.
_HEADER_PARAM_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


class HeaderParam(BaseModel):
    """One header or query-param entry.

    ``secret`` is the per-entry "is this a credential" marker (the
    server-side secret/plaintext split + manifest land in Slice 3; until
    then the route bridges this list to the legacy plaintext dict). ``value``
    is the *final complete value* — the client sends any scheme prefix
    (``Bearer ``…) itself; the backend never synthesises one. A secret entry
    is returned without ``value`` once the split exists.
    """

    key: str
    secret: bool = False
    value: str | None = None


def _validate_unique_keys(
    items: list["HeaderParam"] | None,
) -> list["HeaderParam"] | None:
    """Reject duplicate ``key`` within a headers/params list (→ 422)."""
    if items is None:
        return items
    seen: set[str] = set()
    for it in items:
        if it.key in seen:
            raise ValueError(f"duplicate key {it.key!r}")
        seen.add(it.key)
    return items


class OauthCredentialField(BaseModel):
    key: str
    label: str
    placeholder: str | None = None
    required: bool = True
    secret: bool = False


class CatalogField(BaseModel):
    """Declared credential/config unit for a catalog connector.

    Whether it targets a header or a query-param is determined by which
    schema it is declared in (``header_schema`` / ``param_schema``) — not
    by a per-entry ``target``, so an invalid/mismatched target is
    impossible by construction. The server is authoritative over
    ``secret`` (anti-tamper — the client's per-entry ``secret`` is ignored
    for catalog connectors). ``name`` is the real header / query-param name
    the backend matches request entries against; ``prefix`` is a UI-prefill
    hint only (the backend never applies it — the client sends the final
    complete value).
    """

    key: str
    label: str | dict[str, str] | None = None
    placeholder: str | None = None
    required: bool = True
    name: str
    secret: bool = False
    prefix: str | None = None

    @field_validator("name")
    @classmethod
    def _name_is_token(cls, v: str) -> str:
        if not v or not _HEADER_PARAM_NAME_RE.match(v):
            raise ValueError(f"invalid header/param name {v!r}")
        return v


class CatalogConnector(BaseModel):
    """A connector nested within a group."""

    slug: str
    display_name: str
    description: str | None = None
    url: str = ""
    auth_type: AuthType
    transport: TransportType
    installed: bool = False
    oauth_credentials_schema: list[OauthCredentialField] = []
    header_schema: list[CatalogField] = []
    param_schema: list[CatalogField] = []
    credentials_help_url: str | None = None
    # stdio
    command: str | None = None
    args: list[str] = []
    working_dir: str | None = None
    env: dict[str, str] | None = None


class CatalogGroup(BaseModel):
    """A named group containing multiple connectors."""

    kind: str = "group"
    slug: str
    display_name: str
    description: str | None = None
    icon_url: str | None = None
    categories: list[str] = []
    connectors: list[CatalogConnector]


class CatalogItem(BaseModel):
    """A standalone connector — not part of any group."""

    kind: str = "connector"
    slug: str
    display_name: str
    description: str | None = None
    icon_url: str | None = None
    categories: list[str] = []
    url: str = ""
    auth_type: AuthType
    transport: TransportType
    installed: bool = False
    oauth_credentials_schema: list[OauthCredentialField] = []
    header_schema: list[CatalogField] = []
    param_schema: list[CatalogField] = []
    credentials_help_url: str | None = None
    # stdio
    command: str | None = None
    args: list[str] = []
    working_dir: str | None = None
    env: dict[str, str] | None = None


class CatalogListResponse(BaseModel):
    items: list[CatalogGroup | CatalogItem]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ConnectorItem(BaseModel):
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
    headers: list[HeaderParam]
    params: list[HeaderParam]
    enabled: bool
    status: str
    tool_count: int | None
    last_tested_at: int | None
    error_message: str | None
    created_at: int
    updated_at: int


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorItem]


class CreateConnectorRequest(BaseModel):
    slug: str | None = None
    display_name: str
    transport: TransportType
    description: str | None = None
    connector_type: str = "custom"
    # HTTP / SSE
    url: str | None = None
    auth_type: AuthType = "none"
    headers: list[HeaderParam] | None = None
    params: list[HeaderParam] | None = None
    # OAuth credentials (client_id / client_secret for connectors that require manual registration)
    credentials: dict[str, str] = {}
    # Pre-populated OAuth endpoints (set by auto-discovery to avoid a second probe)
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None
    oauth_registration_endpoint: str | None = None
    # Stdio
    command: str | None = None
    args: list[str] = []
    working_dir: str | None = None
    env: dict[str, str] | None = None

    @field_validator("headers", "params")
    @classmethod
    def _unique_keys(cls, v: list[HeaderParam] | None) -> list[HeaderParam] | None:
        return _validate_unique_keys(v)


class UpdateConnectorRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    url: str | None = None
    auth_type: AuthType | None = None
    headers: list[HeaderParam] | None = None
    params: list[HeaderParam] | None = None
    command: str | None = None
    args: list[str] | None = None
    working_dir: str | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None

    @field_validator("headers", "params")
    @classmethod
    def _unique_keys(cls, v: list[HeaderParam] | None) -> list[HeaderParam] | None:
        return _validate_unique_keys(v)


class ToolInfo(BaseModel):
    """One MCP tool exposed by a connector: its name + human description.

    ``description`` comes straight from the MCP server's ``list_tools()``
    (``Tool.description``) and may be absent for tools that declare none.
    """

    name: str
    description: str | None = None


class TestConnectorResponse(BaseModel):
    ok: bool
    tool_count: int | None = None
    # Tool names only — kept for backward compatibility with existing
    # callers (e.g. the Settings "Test" toast). ``tool_details`` is the
    # richer name+description list the Connectors detail panel renders.
    tools: list[str] = []
    tool_details: list[ToolInfo] = []
    error: str | None = None


class CreateConnectorResponse(BaseModel):
    id: str
    slug: str
    needs_auth: bool = False
    authorization_url: str | None = None


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


async def _get_service(
    db: AsyncSession = Depends(get_async_session),
) -> ConnectorService:
    return ConnectorService(datastore=ConnectorDatastore(db))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_connectors(
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    """List all connectors (builtin, recommended, custom).

    For recommended connectors (those backed by a catalog entry, matched
    by slug) the catalog's i18n display_name / description override the
    values frozen into the DB at install time — otherwise switching the
    UI locale wouldn't re-localize already-installed connectors. Custom
    connectors keep the user-supplied display_name/description.
    """
    from valuz_agent.ports.extensions import ext

    locale = _parse_accept_language(accept_language)
    items = [_view_to_item(v, locale).model_dump() for v in await svc.list_connectors(user_id)]
    items = await ext.resource_list_hook.apply("connector", items, user_id=user_id)
    return {"connectors": items}


async def _probe_oauth_tool_count(row: ConnectorRow) -> int | None:
    """Count an OAuth connector's tools using the token on ``row``; None if unreachable.

    Tries the row's declared transport first, then the other one — a catalog
    entry's ``transport`` is a hint, and a server speaking the other dialect
    should still land ``connected`` with a real count. Never raises: a failed
    probe costs only the count, and must not fail an authorization that already
    succeeded.
    """
    url = row.url or ""
    if not url:
        return None
    try:
        access_token = json.loads(row.oauth_token_json or "{}").get("access_token", "")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not access_token:
        return None

    from mcp.client.session import ClientSession

    headers = {"Authorization": f"Bearer {access_token}"}

    async def _try(transport: str) -> int:
        if transport == "sse":
            from mcp.client.sse import sse_client

            async with sse_client(url, headers=headers, timeout=15, sse_read_timeout=15) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return len((await s.list_tools()).tools)
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(headers=headers, timeout=15.0) as hc:
            async with streamable_http_client(url, http_client=hc) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return len((await s.list_tools()).tools)

    primary = row.transport if row.transport in ("http", "sse") else "http"
    fallback = "sse" if primary == "http" else "http"
    try:
        return await _try(primary)
    except BaseException:
        try:
            return await _try(fallback)
        except BaseException as exc:
            logger.warning("Tool probe failed for connector %s: %s", row.slug, exc)
            return None


async def _install_from_authorized_sibling(
    svc: ConnectorService,
    user_id: str,
    slug: str,
    body: CreateConnectorRequest,
    entry: dict[str, Any] | None,
) -> CreateConnectorResponse | None:
    """Install an OAuth connector by adopting an authorized sibling's credentials.

    Members of a catalog credential group front one upstream service behind one
    authorization server (see ``modules.connectors.oauth_sharing``), so a token
    already held by one member is valid here. Returns the finished response when
    the install completed without consent, or None to fall through to the normal
    OAuth flow — when this connector shares credentials with nobody, when no
    sibling is authorized yet, or when it is already connected (an explicit
    re-authorization must reach the real consent screen).
    """
    from valuz_agent.modules.connectors.oauth_sharing import (
        inherit_oauth_credentials,
        sibling_slugs,
    )

    if not sibling_slugs(slug):
        return None

    existing = await svc._ds.get_by_slug(user_id, slug)
    if existing is not None and existing.status == "connected":
        return None

    target = existing or ConnectorRow(
        slug=slug,
        display_name=body.display_name,
        description=body.description,
        connector_type=body.connector_type,
        transport=body.transport if body.transport in ("http", "sse") else "http",
        url=(entry.get("url") if entry else None) or body.url or "",
        auth_type="oauth",
        enabled=False,
        status="pending_auth",
    )

    source_slug = await inherit_oauth_credentials(
        user_id, target, svc._ds, probe=_probe_oauth_tool_count
    )
    if source_slug is None:
        return None

    target.updated_at = now_ms()
    saved = (
        await svc._ds.update(target)
        if existing is not None
        else await svc._ds.create(user_id, target)
    )
    logger.info(
        "Connector %s installed from %s's credentials — no consent round-trip", slug, source_slug
    )
    return CreateConnectorResponse(id=saved.id, slug=saved.slug, needs_auth=False)


@router.post("")
async def create_connector(
    body: CreateConnectorRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
) -> CreateConnectorResponse:
    """Add a custom or recommended MCP connector.

    - If auth_type == "oauth": creates the row, starts the OAuth flow, and
      returns needs_auth=True + authorization_url for the frontend to open.
    - Otherwise: creates the row with status="connecting", fires a background
      probe, and returns immediately. The frontend should poll GET /{id} until
      status is "connected" or "error" (max 30 s).
    """
    if body.transport == "stdio" and not body.command:
        raise HTTPException(status_code=422, detail="command is required for stdio connectors")
    if body.transport != "stdio" and not body.url:
        raise HTTPException(status_code=422, detail="url is required for HTTP/SSE connectors")

    # ── Auto-discover OAuth for HTTP connectors with no explicit auth ─────────
    # Universal create-time backstop: whenever the auth method is unknown
    # (auth_type="none"), proactively probe — regardless of caller. The
    # agent-facing ``create_mcp`` tool always lands here (it has no two-step
    # UI). The desktop/web UI normally pre-discovers via
    # ``POST /v1/connectors/discover`` and submits an explicit auth_type +
    # pre-filled endpoints, but this block still guarantees "unknown auth ⇒
    # discover" if a create races/precedes that proactive probe.
    if body.auth_type == "none" and body.transport in ("http", "sse") and body.url:
        from valuz_agent.integrations.connector_oauth import OAuthDiscoverHelper

        _discover = OAuthDiscoverHelper(body.url)
        try:
            _discovered = await _discover.get_oauth_metadata()
            # Discoverable OAuth metadata is NOT proof that OAuth is mandatory:
            # freemium servers (e.g. Firecrawl) advertise
            # ``/.well-known/oauth-protected-resource`` so signed-in users get
            # per-account attribution, yet still serve fully anonymous calls.
            # Only force the OAuth flow when the server actually rejects an
            # unauthenticated ``initialize`` — otherwise keep ``auth_type=none``
            # so the connector connects with no login the user never asked for.
            if _discovered is not None and await _discover.server_allows_anonymous():
                logger.info(
                    "connector create: %s advertises OAuth but serves anonymous; "
                    "keeping auth_type=none",
                    body.url,
                )
                _discovered = None
        except Exception:
            _discovered = None
        finally:
            await _discover.close()
        if _discovered is not None:
            # Flip to OAuth whenever OAuth metadata is discovered — NOT only
            # when DCR (registration_endpoint) is present. A non-DCR OAuth
            # server submitted with auth_type="none" (e.g. the agent
            # ``create_mcp`` tool against a GitHub-style server) must still
            # become a proper ``pending_auth`` OAuth connector; the OAuth
            # branch below then falls back to a public-PKCE client or to
            # supplied / previously-saved client credentials. The old
            # ``registration_endpoint is not None`` guard silently fell
            # through and created a dead no-auth connector instead
            # (needs_auth=False, status=error, zero signal to the caller).
            body = body.model_copy(
                update={
                    "auth_type": "oauth",
                    "oauth_authorization_endpoint": _discovered.authorization_endpoint,
                    "oauth_token_endpoint": _discovered.token_endpoint,
                    "oauth_registration_endpoint": _discovered.registration_endpoint,
                }
            )

    # ── OAuth path ────────────────────────────────────────────────────────────
    if body.auth_type == "oauth":
        slug = body.slug or body.display_name
        entry = next((e for e in CONNECTOR_DIRECTORY if e["slug"] == slug), None)

        # A member of an already-authorized credential group needs no consent of
        # its own. Checked before discovery/registration, which would otherwise
        # be wasted round-trips to a server we already hold a valid token for.
        inherited = await _install_from_authorized_sibling(svc, user_id, slug, body, entry)
        if inherited is not None:
            return inherited

        from valuz_agent.infra.config import settings as _settings
        from valuz_agent.integrations.connector_oauth import McpOauthHelper, OAuthDiscoverHelper

        server_url: str = (entry.get("url") if entry else None) or body.url or ""
        static_auth = (
            entry.get("oauth_authorization_endpoint") if entry else None
        ) or body.oauth_authorization_endpoint
        static_token = (
            entry.get("oauth_token_endpoint") if entry else None
        ) or body.oauth_token_endpoint
        static_reg = (
            entry.get("oauth_registration_endpoint") if entry else None
        ) or body.oauth_registration_endpoint

        if static_auth and static_token:
            from valuz_agent.integrations.connector_oauth import OauthMetadata

            oauth_meta = OauthMetadata(
                authorization_endpoint=static_auth,
                token_endpoint=static_token,
                registration_endpoint=static_reg,
            )
        else:
            discover = OAuthDiscoverHelper(server_url)
            try:
                oauth_meta = await discover.get_oauth_metadata()
            finally:
                await discover.close()
            if oauth_meta is None:
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not discover OAuth metadata for {server_url!r}",
                )

        from mcp.shared.auth import OAuthClientMetadata

        redirect_uri = f"{_settings.backend_base_url}/v1/connectors/oauth/callback"
        client_meta = OAuthClientMetadata(
            client_name="Valuz",
            redirect_uris=[redirect_uri],  # type: ignore[arg-type]
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        helper = McpOauthHelper(
            server_url=server_url,
            client_metadata=client_meta,
            token_endpoint=oauth_meta.token_endpoint,
            authorization_endpoint=oauth_meta.authorization_endpoint,
            resource=oauth_meta.resource,
            registration_endpoint=oauth_meta.registration_endpoint,
        )

        existing = await svc._ds.get_by_slug(user_id, slug)
        saved_client_id: str | None = None
        saved_client_secret: str | None = None
        if existing and existing.oauth_client_info_json:
            try:
                _saved = json.loads(existing.oauth_client_info_json)
                saved_client_id = _saved.get("client_id")
                saved_client_secret = _saved.get("client_secret")
            except (json.JSONDecodeError, AttributeError):
                pass

        client_id: str | None = None
        client_secret: str | None = None
        client_info_json: str | None = None

        # Resolve a client_id by sequential fallback: DCR → supplied → saved.
        # If all three miss, a Client ID is REQUIRED — refuse the create
        # loudly with 422 instead of silently proceeding as a public PKCE
        # client (which fails at most servers and used to mask the gap).
        if oauth_meta.registration_endpoint:
            try:
                client_info = await helper.register_client([redirect_uri])
                client_id = client_info.client_id
                client_secret = client_info.client_secret
                client_info_json = client_info.model_dump_json()
                helper.client_id = client_id
                helper.client_secret = client_secret
            except Exception as exc:
                logger.warning("Dynamic client registration failed for %s: %s", slug, exc)

        if client_id is None and body.credentials.get("client_id"):
            client_id = body.credentials["client_id"]
            client_secret = body.credentials.get("client_secret") or None
            helper.client_id = client_id
            helper.client_secret = client_secret
            client_info_json = json.dumps({"client_id": client_id, "client_secret": client_secret})

        if client_id is None and saved_client_id:
            client_id = saved_client_id
            client_secret = saved_client_secret
            helper.client_id = client_id
            helper.client_secret = client_secret

        if client_id is None:
            await helper.close()
            raise HTTPException(
                status_code=422,
                detail=(
                    "OAuth client_id is required for this server: no dynamic "
                    "client registration endpoint was discovered and no "
                    "client_id was supplied or previously saved. Provide "
                    "credentials.client_id (and credentials.client_secret if "
                    "applicable)."
                ),
            )

        auth_url, state, code_verifier = await helper.get_authorization_url()
        await helper.close()

        if existing is None:
            row = ConnectorRow(
                slug=slug,
                display_name=body.display_name,
                description=body.description,
                connector_type=body.connector_type,
                transport=body.transport if body.transport in ("http", "sse") else "http",
                url=server_url,
                auth_type="oauth",
                oauth_metadata=oauth_meta.model_dump_json(),
                oauth_client_info_json=client_info_json,
                enabled=False,
                status="pending_auth",
            )
            saved_row = await svc._ds.create(user_id, row)
        else:
            existing.status = "pending_auth"
            existing.oauth_metadata = oauth_meta.model_dump_json()
            if client_info_json is not None:
                existing.oauth_client_info_json = client_info_json
            existing.updated_at = now_ms()
            saved_row = await svc._ds.update(existing)

        # Stash the transient PKCE handoff in the ephemeral state store (a file
        # store locally, Redis on the shared backend), keyed by the unguessable
        # ``state`` and expiring after a few minutes.
        await ext.cache.set(
            _pkce_cache_key(state),
            json.dumps(
                {
                    "connector_id": saved_row.id,
                    "user_id": user_id,
                    "code_verifier": code_verifier,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "server_url": server_url,
                    "redirect_uri": redirect_uri,
                }
            ),
            ttl_seconds=600,
        )

        return CreateConnectorResponse(
            id=saved_row.id,
            slug=saved_row.slug,
            needs_auth=True,
            authorization_url=auth_url,
        )

    # ── Non-OAuth path: create + async probe ──────────────────────────────────
    import asyncio

    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.infra.db import async_unit_of_work as _async_unit_of_work

    _catalog_fields = _catalog_field_specs(body.slug)
    _missing = _catalog_missing_required(body.slug, body.headers, body.params)
    if _missing:
        raise HTTPException(
            status_code=422,
            detail=f"missing required credential field(s): {', '.join(_missing)}",
        )

    view = await svc.create_connector(
        user_id,
        slug=body.slug,
        display_name=body.display_name,
        transport=body.transport,
        description=body.description,
        connector_type=body.connector_type,
        url=body.url,
        auth_type=body.auth_type,
        headers=_to_cred_entries(body.headers),
        params=_to_cred_entries(body.params),
        catalog_fields=_catalog_fields,
        command=body.command,
        args=body.args,
        working_dir=body.working_dir,
        env=body.env,
    )

    connector_id_nonauth = view.id

    async def _background_probe() -> None:
        try:
            async with _async_unit_of_work() as db:
                bg_svc = ConnectorService(datastore=ConnectorDatastore(db))
                await _probe_connector(connector_id_nonauth, bg_svc, user_id)
        except Exception as exc:
            logger.warning("Background probe failed for %s: %s", connector_id_nonauth, exc)

    asyncio.create_task(_background_probe())

    # Return immediately with status="connecting" so the frontend knows to poll
    return CreateConnectorResponse(
        id=view.id,
        slug=view.slug,
        needs_auth=False,
    )


# ---------------------------------------------------------------------------
# OAuth metadata discovery (explicit, frontend-driven pre-fill)
# ---------------------------------------------------------------------------


class DiscoverConnectorRequest(BaseModel):
    url: str
    transport: TransportType = "http"


class DiscoverConnectorResponse(BaseModel):
    # "oauth" when RFC 8414 / OIDC metadata was found, else "none".
    auth_type: AuthType
    discovered: bool
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None
    # None => server has no dynamic client registration; the user must supply
    # a pre-registered client_id / client_secret.
    oauth_registration_endpoint: str | None = None


@router.post("/discover")
async def discover_connector(body: DiscoverConnectorRequest) -> DiscoverConnectorResponse:
    """Probe an MCP server's OAuth metadata so the UI can pre-fill auth_type +
    endpoints before the user confirms creation.

    This is the explicit, frontend-driven counterpart to the in-create
    auto-discovery (which is now retained only for the agent-facing
    ``create_mcp`` tool — see ``create_connector``). stdio / missing-url
    requests resolve to ``auth_type="none"`` without a network probe.
    """
    if body.transport not in ("http", "sse") or not body.url:
        return DiscoverConnectorResponse(auth_type="none", discovered=False)

    from valuz_agent.integrations.connector_oauth import OAuthDiscoverHelper

    discover = OAuthDiscoverHelper(body.url)
    try:
        meta = await discover.get_oauth_metadata()
        # Discoverable OAuth ≠ mandatory OAuth: a server that also serves
        # anonymous calls (e.g. Firecrawl) should report auth_type="none" so the
        # UI never forces a login the user does not need. See
        # ``OAuthDiscoverHelper.server_allows_anonymous``.
        if meta is not None and await discover.server_allows_anonymous():
            meta = None
    except Exception:
        meta = None
    finally:
        await discover.close()

    if meta is None:
        return DiscoverConnectorResponse(auth_type="none", discovered=False)

    return DiscoverConnectorResponse(
        auth_type="oauth",
        discovered=True,
        oauth_authorization_endpoint=meta.authorization_endpoint,
        oauth_token_endpoint=meta.token_endpoint,
        oauth_registration_endpoint=meta.registration_endpoint,
    )


# ---------------------------------------------------------------------------
# Directory catalog + OAuth schemas
# ---------------------------------------------------------------------------


class OAuthCallbackResult(BaseModel):
    connector_id: str
    ok: bool
    error: str | None = None


_SUPPORTED_LOCALES = ("zh-CN", "en-US")


def _parse_accept_language(header: str | None) -> str:
    """Pick the best supported locale from an ``Accept-Language`` header.

    Returns one of ``_SUPPORTED_LOCALES``; defaults to ``zh-CN``. Ignores
    q-values — the desktop client sends a single token, so first-match wins.
    """
    if not header:
        return "zh-CN"
    for raw in header.split(","):
        tag = raw.split(";")[0].strip()
        if tag in _SUPPORTED_LOCALES:
            return tag
        # Match language part only ("en" → "en-US")
        prefix = tag.split("-")[0].lower()
        for supported in _SUPPORTED_LOCALES:
            if supported.split("-")[0].lower() == prefix:
                return supported
    return "zh-CN"


def _localize(value: object, locale: str) -> str | None:
    """Resolve a catalog field that may be a plain string (legacy) or an
    i18n dict ``{"zh-CN": ..., "en-US": ...}``. Falls back to en-US, then
    to any available value. Returns ``None`` if the input is missing."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for candidate in (locale, "en-US", "zh-CN"):
            v = value.get(candidate)
            if isinstance(v, str) and v:
                return v
        # Fall back to any non-empty string value
        for v in value.values():
            if isinstance(v, str) and v:
                return v
        return None
    return str(value)


@router.get("/recommended")
async def list_recommended(
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> CatalogListResponse:
    """List recommended connectors.

    ``display_name`` / ``description`` in the catalog JSON may be either a
    plain string (legacy, single-language) or an i18n object
    (``{"zh-CN": ..., "en-US": ...}``). The active locale is taken from
    the ``Accept-Language`` header set by the desktop client.
    """
    locale = _parse_accept_language(accept_language)
    installed_slugs = {v.slug for v in await svc.list_connectors(user_id)}
    items: list[CatalogGroup | CatalogItem] = []
    for entry in CATALOG_ITEMS:
        if entry["_kind"] == "group":
            connectors = [
                CatalogConnector(
                    slug=c["slug"],
                    display_name=_localize(
                        c.get("display_name") or entry.get("display_name", c["slug"]),
                        locale,
                    )
                    or c["slug"],
                    description=_localize(c.get("description") or entry.get("description"), locale),
                    url=c.get("url", ""),
                    auth_type=c.get("auth_type", "oauth"),
                    transport=c.get("transport", "http"),
                    installed=c["slug"] in installed_slugs,
                    oauth_credentials_schema=[
                        OauthCredentialField(**f) for f in c.get("oauth_credentials_schema", [])
                    ],
                    header_schema=[CatalogField(**f) for f in c.get("header_schema", [])],
                    param_schema=[CatalogField(**f) for f in c.get("param_schema", [])],
                    credentials_help_url=c.get("credentials_help_url"),
                    command=c.get("command"),
                    args=c.get("args", []),
                    working_dir=c.get("working_dir"),
                    env=c.get("env"),
                )
                for c in entry.get("connectors", [])
            ]
            items.append(
                CatalogGroup(
                    slug=entry["slug"],
                    display_name=_localize(entry.get("display_name", entry["slug"]), locale)
                    or entry["slug"],
                    description=_localize(entry.get("description"), locale),
                    icon_url=entry.get("icon_url"),
                    categories=entry.get("categories", []),
                    connectors=connectors,
                )
            )
        else:
            items.append(
                CatalogItem(
                    slug=entry["slug"],
                    display_name=_localize(entry.get("display_name", entry["slug"]), locale)
                    or entry["slug"],
                    description=_localize(entry.get("description"), locale),
                    icon_url=entry.get("icon_url"),
                    categories=entry.get("categories", []),
                    url=entry.get("url", ""),
                    auth_type=entry.get("auth_type", "oauth"),
                    transport=entry.get("transport", "http"),
                    installed=entry["slug"] in installed_slugs,
                    oauth_credentials_schema=[
                        OauthCredentialField(**f) for f in entry.get("oauth_credentials_schema", [])
                    ],
                    header_schema=[CatalogField(**f) for f in entry.get("header_schema", [])],
                    param_schema=[CatalogField(**f) for f in entry.get("param_schema", [])],
                    credentials_help_url=entry.get("credentials_help_url"),
                    command=entry.get("command"),
                    args=entry.get("args", []),
                    working_dir=entry.get("working_dir"),
                    env=entry.get("env"),
                )
            )
    return CatalogListResponse(items=items)


# ---------------------------------------------------------------------------
# OAuth install flow
# ---------------------------------------------------------------------------


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> HTMLResponse:
    """OAuth authorization-code callback — exchanges ``code`` for tokens.

    The browser is redirected here by the authorization server after the
    user grants access. This endpoint:

    1. Looks up the PKCE handoff in the ephemeral state store, keyed by ``state``.
    2. Exchanges the code for an ``OAuthToken``.
    3. Stores the token JSON at ``connector/{id}/oauth_token``.
    4. Sets the connector status to ``connected`` + ``enabled=True``.
    5. Deletes the transient PKCE state from the store.
    6. Returns a tiny HTML page that signals success to the Electron shell
       (or shows an error).
    """
    from valuz_agent.integrations.connector_oauth import McpOauthHelper, persist_oauth_token

    pkce_json = await ext.cache.get(_pkce_cache_key(state))
    if not pkce_json:
        return _oauth_html_result(ok=False, error="OAuth state not found or expired")
    try:
        pkce = json.loads(pkce_json)
    except json.JSONDecodeError:
        return _oauth_html_result(ok=False, error="Malformed OAuth state")

    connector_id: str = pkce["connector_id"]
    user_id: str = pkce.get("user_id", "")
    code_verifier: str = pkce["code_verifier"]
    server_url: str = pkce["server_url"]
    redirect_uri: str = pkce["redirect_uri"]
    client_id: str | None = pkce.get("client_id")
    client_secret: str | None = pkce.get("client_secret")

    async with async_unit_of_work() as db:
        ds = ConnectorDatastore(db)
        row = await ds.get_by_id(user_id, connector_id)
        if row is None:
            return _oauth_html_result(ok=False, error="Connector not found")

        oauth_meta_json = row.oauth_metadata
        if not oauth_meta_json:
            return _oauth_html_result(ok=False, error="OAuth metadata missing on connector row")

        from valuz_agent.integrations.connector_oauth import OauthMetadata

        oauth_meta = OauthMetadata.model_validate_json(oauth_meta_json)

        from mcp.shared.auth import OAuthClientMetadata

        client_meta = OAuthClientMetadata(
            client_name="Valuz",
            redirect_uris=[redirect_uri],  # type: ignore[arg-type]
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        helper = McpOauthHelper(
            server_url=server_url,
            client_metadata=client_meta,
            token_endpoint=oauth_meta.token_endpoint,
            authorization_endpoint=oauth_meta.authorization_endpoint,
            resource=oauth_meta.resource,
            client_id=client_id,
            client_secret=client_secret,
        )
        try:
            token = await helper.get_oauth_token(code=code, code_verifier=code_verifier)
        except Exception as exc:
            logger.warning("Token exchange failed for connector %s: %s", connector_id, exc)
            row.status = "error"
            row.error_message = str(exc)
            row.updated_at = now_ms()
            await ds.update(row)
            await ext.cache.delete(_pkce_cache_key(state))
            return _oauth_html_result(ok=False, error=str(exc))
        finally:
            await helper.close()

        # Persist the token onto the row (with its absolute expiry); the final
        # ds.update below commits it with the connected status.
        persist_oauth_token(row, token, now_ms())
        await ext.cache.delete(_pkce_cache_key(state))

        # Probe tools BEFORE writing status=connected so that tool_count and
        # status land in the DB together — no race with the frontend poller.
        tool_count = await _probe_oauth_tool_count(row)

        row.status = "connected"
        row.enabled = True
        row.error_message = None
        row.tool_count = tool_count
        row.last_tested_at = now_ms()
        row.updated_at = now_ms()
        updated = await ds.update(row)
        from valuz_agent.modules.connectors.oauth_sharing import propagate_oauth_credentials
        from valuz_agent.modules.connectors.service import after_connector_oauth_authorized_hook

        # One consent authorizes the whole credential group — hand the token to
        # every sibling fronting the same upstream service, installing the ones
        # with no row yet, and probe each so it lands live (connected + counted)
        # exactly like the row we just authorized, with no Test click needed.
        await propagate_oauth_credentials(
            user_id, updated, ds, probe=_probe_oauth_tool_count, install_missing=True
        )
        await after_connector_oauth_authorized_hook(db, user_id, updated)

        logger.info(
            "Connector %s (%s) OAuth connected, tool_count=%s",
            connector_id,
            row.slug,
            tool_count,
        )
        return _oauth_html_result(ok=True, connector_id=connector_id, slug=row.slug)


def _oauth_html_result(
    ok: bool,
    *,
    connector_id: str | None = None,
    slug: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Return a tiny HTML page that hands the OAuth result back to the desktop app.

    The authorize URL is opened in the user's **system browser** (the Electron
    shell denies in-app ``window.open`` and shells out — see
    ``windows.ts`` ``setWindowOpenHandler``), so there is NO ``window.opener``
    to ``postMessage`` back to. We therefore redirect to a
    ``valuz-oss://connector-oauth`` deep link, which Launch Services routes to
    the running app (``app.on("open-url")`` → ``deep-link-received``). The
    legacy ``postMessage`` + auto-close is kept as a best-effort fallback for
    the popup/embedded-window case.
    """
    from valuz_agent.infra.config import settings

    params: dict[str, str] = {"ok": "1" if ok else "0"}
    if connector_id:
        params["connector_id"] = connector_id
    if slug:
        params["slug"] = slug
    if error:
        params["error"] = error
    deep_link = f"{settings.deep_link_protocol}://connector-oauth?{urlencode(params)}"

    title = "✓ Connected successfully" if ok else "✗ Authorization failed"
    sub = "Returning to Valuz…" if ok else (error or "Unknown error")
    legacy_msg = (
        f"""window.opener.postMessage({{ type: "connector_oauth_success",
                connector_id: {json.dumps(connector_id)}, slug: {json.dumps(slug)} }}, "*");"""
        if ok
        else f"""window.opener.postMessage({{ type: "connector_oauth_error",
                error: {json.dumps(error)} }}, "*");"""
    )
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Valuz – Connector OAuth</title>
<style>body{{font-family:system-ui,sans-serif;text-align:center;padding:3rem;}}
h2{{color:{"#22c55e" if ok else "#ef4444"};}}
a{{color:#725cf9;}}</style>
</head>
<body>
  <h2>{title}</h2>
  <p>{sub}</p>
  <p style="color:#888;font-size:.85rem">If Valuz didn't come to the front,
     <a href="{deep_link}">click here</a> — you can then close this tab.</p>
  <script>
    // Primary: deep-link back into the desktop app (system browser has no opener).
    try {{ window.location.href = {json.dumps(deep_link)}; }} catch (e) {{}}
    // Fallback: popup/embedded-window flow still gets the postMessage + close.
    try {{ if (window.opener) {{ {legacy_msg} }} }} catch (e) {{}}
    setTimeout(() => {{ try {{ window.close(); }} catch (e) {{}} }}, {"1500" if ok else "3000"});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_cred_entries(items: list[HeaderParam] | None) -> list[CredEntry] | None:
    """API ``HeaderParam`` list → service ``CredEntry`` list (``None``
    passes through, meaning "not provided" — that target is untouched)."""
    if items is None:
        return None
    return [CredEntry(key=it.key, secret=it.secret, value=it.value) for it in items]


def _catalog_field_specs(slug: str | None) -> list[CatalogFieldSpec] | None:
    """Resolve the catalog ``fields`` for a slug into server-authoritative
    specs. ``None`` when the slug is not a catalog connector or declares no
    fields → the service treats every entry as custom (client decides
    ``secret``)."""
    if not slug:
        return None
    entry = next((e for e in CONNECTOR_DIRECTORY if e["slug"] == slug), None)
    if entry is None:
        return None
    # `target` is derived from which schema the field is declared in —
    # never authored per-entry. The service-layer CatalogFieldSpec still
    # carries it (manifest / build_overrides are keyed on target).
    specs = [
        CatalogFieldSpec(
            key=f["key"],
            name=f["name"],
            target=target,
            secret=bool(f.get("secret", False)),
        )
        for target, schema_key in (("header", "header_schema"), ("param", "param_schema"))
        for f in (entry.get(schema_key) or [])
    ]
    return specs or None


def _catalog_missing_required(
    slug: str | None,
    headers: list[HeaderParam] | None,
    params: list[HeaderParam] | None,
) -> list[str]:
    """Catalog ``required`` fields with no non-empty value in the request.

    Matching is by ``entry.key == field.name`` within the field's target.
    Used for the create-time 422 guard (the desired-state preserve
    semantics make a required check meaningless on edit)."""
    if not slug:
        return []
    entry = next((e for e in CONNECTOR_DIRECTORY if e["slug"] == slug), None)
    if entry is None:
        return []
    have_h = {h.key for h in (headers or []) if h.value}
    have_p = {p.key for p in (params or []) if p.value}
    missing: list[str] = []
    for schema_key, pool in (("header_schema", have_h), ("param_schema", have_p)):
        for f in entry.get(schema_key) or []:
            if not f.get("required", True):
                continue
            if f["name"] not in pool:
                missing.append(f["name"])
    return missing


def _creds_to_params(items: list[CredView]) -> list[HeaderParam]:
    """Service ``CredView`` list → API ``HeaderParam`` list (secret entries
    carry no value)."""
    return [HeaderParam(key=c.key, secret=c.secret, value=c.value) for c in items]


def _view_to_item(view: ConnectorView, locale: str = "zh-CN") -> ConnectorItem:
    # If this connector matches a catalog entry by slug, prefer the catalog's
    # i18n display_name/description so switching the UI locale updates the
    # already-installed row without a DB migration. Custom connectors miss
    # the lookup and keep the user-supplied values.
    catalog_entry = next((e for e in CONNECTOR_DIRECTORY if e["slug"] == view.slug), None)
    display_name = view.display_name
    description = view.description
    if catalog_entry is not None:
        catalog_name = _localize(catalog_entry.get("display_name"), locale)
        catalog_desc = _localize(catalog_entry.get("description"), locale)
        if catalog_name:
            display_name = catalog_name
        if catalog_desc is not None:
            description = catalog_desc
    return ConnectorItem(
        id=view.id,
        slug=view.slug,
        display_name=display_name,
        description=description,
        connector_type=view.connector_type,
        transport=view.transport,
        url=view.url,
        auth_type=view.auth_type,
        has_api_key=view.has_api_key,
        command=view.command,
        args=view.args,
        working_dir=view.working_dir,
        headers=_creds_to_params(view.headers),
        params=_creds_to_params(view.params),
        enabled=view.enabled,
        status=view.status,
        tool_count=view.tool_count,
        last_tested_at=view.last_tested_at,
        error_message=view.error_message,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get("/{connector_id}")
async def get_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> ConnectorItem:
    """Get a single connector by ID."""
    view = await svc.get_connector(user_id, connector_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _view_to_item(view, _parse_accept_language(accept_language))


@router.patch("/{connector_id}")
async def update_connector(
    connector_id: str,
    body: UpdateConnectorRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> ConnectorItem:
    """Update a connector's configuration."""
    _existing = await svc.get_connector(user_id, connector_id)
    if _existing is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    # Recommended stdio connectors are catalog-owned: command/args/working_dir
    # and identity fields (display_name/description/url/auth_type/headers/params)
    # are managed by the catalog entry. Only ``env`` (user runtime parameters)
    # and ``enabled`` are mutable.
    if _existing.connector_type == "recommended" and _existing.transport == "stdio":
        _locked_present = any(
            v is not None
            for v in (
                body.display_name,
                body.description,
                body.url,
                body.auth_type,
                body.headers,
                body.params,
                body.command,
                body.args,
                body.working_dir,
            )
        )
        if _locked_present:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Recommended stdio connector: only ``env`` (and "
                    "``enabled``) can be edited; command/args/identity "
                    "fields are managed by the catalog entry."
                ),
            )
    view = await svc.update_connector(
        user_id,
        connector_id,
        display_name=body.display_name,
        description=body.description,
        url=body.url,
        auth_type=body.auth_type,
        headers=_to_cred_entries(body.headers),
        params=_to_cred_entries(body.params),
        catalog_fields=_catalog_field_specs(_existing.slug),
        command=body.command,
        args=body.args,
        working_dir=body.working_dir,
        env=body.env,
        enabled=body.enabled,
    )
    if view is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    # Re-probe if connection params changed (status was reset to "connecting")
    if view.status == "connecting":
        _cid = view.id

        async def _background_probe() -> None:
            try:
                async with async_unit_of_work() as db:
                    bg_svc = ConnectorService(datastore=ConnectorDatastore(db))
                    await _probe_connector(_cid, bg_svc, user_id)
            except Exception as exc:
                logger.warning("Background probe failed for %s: %s", _cid, exc)

        asyncio.create_task(_background_probe())

    return _view_to_item(view, _parse_accept_language(accept_language))


@router.delete("/{connector_id}")
async def delete_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
) -> dict[str, bool]:
    """Delete a custom or directory connector."""
    ok = await svc.delete_connector(user_id, connector_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Connector not found or cannot be deleted")
    return {"ok": True}


@router.post("/{connector_id}/enable")
async def enable_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> ConnectorItem:
    """Enable a connector."""
    view = await svc.set_enabled(user_id, connector_id, enabled=True)
    if view is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _view_to_item(view, _parse_accept_language(accept_language))


@router.post("/{connector_id}/disable")
async def disable_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> ConnectorItem:
    """Disable a connector."""
    view = await svc.set_enabled(user_id, connector_id, enabled=False)
    if view is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _view_to_item(view, _parse_accept_language(accept_language))


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: ConnectorService = Depends(_get_service),
) -> TestConnectorResponse:
    """Test an MCP connector (HTTP, SSE, or stdio) using the MCP client library."""
    view = await svc.get_connector(user_id, connector_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return await _probe_connector(connector_id, svc, user_id)


def _tools_to_info(mcp_tools: object) -> list[ToolInfo]:
    """Map MCP ``list_tools()`` results → ``ToolInfo`` (name + description).

    Pure + side-effect free so it can be unit-tested without a live MCP
    server. Accepts any iterable of objects exposing ``.name`` and an
    optional ``.description`` attribute.
    """
    result: list[ToolInfo] = []
    for t in mcp_tools:  # type: ignore[attr-defined]
        name = getattr(t, "name", None)
        if not name:
            continue
        result.append(ToolInfo(name=name, description=getattr(t, "description", None)))
    return result


async def _retry_async[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retry_if: Callable[[BaseException], bool],
    delays: tuple[float, ...],
) -> T:
    """Await ``fn``; if it raises an exception matching ``retry_if``, back off and
    retry — one extra attempt per entry in ``delays``. Re-raises the last error
    once the retries are exhausted or the error doesn't match.
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except BaseException as exc:
            if attempt >= len(delays) or not retry_if(exc):
                raise
            await asyncio.sleep(delays[attempt])
            attempt += 1


def _is_unauthorized(exc: BaseException) -> bool:
    """Detect a 401 from the MCP/httpx stack — i.e. an expired access token.

    The MCP client may wrap the underlying error in an ``ExceptionGroup``; unwrap
    to the leaf, prefer the typed ``HTTPStatusError`` status, and fall back to a
    string match for transports that surface the 401 only in the message.
    """
    import httpx

    inner: BaseException = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]
    if isinstance(inner, httpx.HTTPStatusError):
        return inner.response.status_code == 401
    return "401" in str(inner)


async def _probe_connector(
    connector_id: str, svc: ConnectorService, user_id: str
) -> TestConnectorResponse:
    """Run the MCP probe for a connector and persist the result. Never raises."""
    import os
    import shlex
    import shutil
    import subprocess

    import httpx
    from mcp.client.session import ClientSession

    view = await svc.get_connector(user_id, connector_id)
    if view is None:
        return TestConnectorResponse(ok=False, error="Connector not found")

    def _unwrap(exc: BaseException) -> BaseException:
        inner = exc
        while hasattr(inner, "exceptions") and getattr(inner, "exceptions", None):
            inner = inner.exceptions[0]  # type: ignore[attr-defined]
        return inner

    # ── Stdio probe ──────────────────────────────────────────────────────────
    if view.transport == "stdio":
        if not view.command:
            return TestConnectorResponse(
                ok=False, error="Stdio connector has no command configured"
            )

        row = await svc._ds.get_by_id(user_id, connector_id)
        env: dict[str, str] | None = None
        if row and row.env_json:
            try:
                parsed_env = json.loads(row.env_json)
                if isinstance(parsed_env, dict):
                    env = {str(k): str(v) for k, v in parsed_env.items()}
            except json.JSONDecodeError:
                pass

        try:
            from mcp.client.stdio import StdioServerParameters, stdio_client

            # Resolving the user's interactive PATH spawns a login shell per
            # candidate (``zsh -l`` / ``bash -l`` sources rc files), up to 5s
            # each — blocking. Run it off the event loop so a connector test
            # never freezes the server for other requests.
            def _detect_shell_path(default: str) -> str:
                for shell in ("zsh", "bash"):
                    try:
                        out = subprocess.check_output(
                            [shell, "-l", "-c", "echo $PATH"],
                            text=True,
                            timeout=5,
                            stderr=subprocess.DEVNULL,
                        ).strip()
                        lines = [line for line in out.splitlines() if line.strip()]
                        if lines:
                            return lines[-1]
                    except Exception:
                        continue
                return default

            shell_path_str: str = await asyncio.to_thread(
                _detect_shell_path, os.environ.get("PATH", "")
            )

            # Bundled stdio connectors reference their entry point with the
            # ``{mcp_dir}`` placeholder; expand it the same way the runtime
            # resolver does, or the probe spawns ``python {mcp_dir}/...`` as a
            # literal path and the server exits with "Connection closed".
            from valuz_agent.adapters.mcp_resolver import expand_mcp_dir

            raw_command = expand_mcp_dir(view.command)
            extra_args: list[str] = []
            if " " in raw_command:
                parts = shlex.split(raw_command)
                raw_command = parts[0]
                extra_args = parts[1:]

            resolved_command = shutil.which(raw_command, path=shell_path_str) or raw_command
            probe_args = extra_args + [expand_mcp_dir(str(a)) for a in (view.args or [])]
            probe_env: dict[str, str] = os.environ.copy()
            probe_env["PATH"] = shell_path_str
            if env:
                probe_env.update(env)

            params = StdioServerParameters(
                command=resolved_command, args=probe_args, env=probe_env, cwd=view.working_dir
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    tool_infos = _tools_to_info(result.tools)
            await svc.record_test_result(user_id, connector_id, ok=True, tool_count=len(tool_infos))
            return TestConnectorResponse(
                ok=True,
                tool_count=len(tool_infos),
                tools=[ti.name for ti in tool_infos],
                tool_details=tool_infos,
            )
        except BaseException as exc:
            error_msg = str(_unwrap(exc))
            logger.warning("Stdio connector test failed for %s: %s", connector_id, error_msg)
            await svc.record_test_result(user_id, connector_id, ok=False, error_message=error_msg)
            return TestConnectorResponse(ok=False, error=error_msg)

    # ── HTTP / SSE probe ─────────────────────────────────────────────────────
    if not view.url:
        return TestConnectorResponse(ok=False, error="Connector has no URL configured")

    # Same injection truth as the runtime resolver (Acceptance #8).
    row2 = await svc._ds.get_by_id(user_id, connector_id)
    if row2 is None:
        ov_headers: dict[str, str] = {}
        ov_params: dict[str, str] = {}
    else:
        ov_headers, ov_params = build_overrides(row2)

    if view.auth_type == "oauth":
        # OAuth layers on AFTER build_overrides — mirrors the resolver.
        token_json = row2.oauth_token_json if row2 is not None else None
        if token_json:
            try:
                from mcp.shared.auth import OAuthToken

                token = OAuthToken.model_validate_json(token_json)
                ov_headers["Authorization"] = f"Bearer {token.access_token}"
            except Exception:
                pass

    probe_url = merge_params_into_url(view.url, ov_params)

    async def _http_probe(transport: str) -> list[ToolInfo]:
        if transport == "sse":
            from mcp.client.sse import sse_client

            async with sse_client(
                probe_url, headers=ov_headers, timeout=15, sse_read_timeout=15
            ) as (
                r,
                w,
            ):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return _tools_to_info((await s.list_tools()).tools)
        else:
            from mcp.client.streamable_http import streamable_http_client

            async with httpx.AsyncClient(headers=ov_headers, timeout=15.0) as hc:
                async with streamable_http_client(probe_url, http_client=hc) as (r, w, _):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        return _tools_to_info((await s.list_tools()).tools)

    async def _attempt() -> list[ToolInfo]:
        primary = view.transport if view.transport in ("http", "sse") else "http"
        fallback = "sse" if primary == "http" else "http"
        try:
            return await _http_probe(primary)
        except BaseException as first_exc:
            try:
                return await _http_probe(fallback)
            except BaseException:
                raise _unwrap(first_exc) from None

    try:
        try:
            # A no-auth connector answering 401 is anomalous — almost always a
            # transient rate-limit on a free anonymous tier (e.g. Firecrawl
            # throttles bursts), which is why the auto-probe sometimes fails
            # while a manual reconnect a moment later succeeds. Retry with a
            # short backoff so the default probe self-heals. OAuth 401s are real
            # (token) and handled by the refresh path below — never retried here.
            tool_infos = await _retry_async(
                _attempt,
                retry_if=lambda e: view.auth_type != "oauth" and _is_unauthorized(e),
                delays=(1.5, 3.0),
            )
        except BaseException as exc:
            # An OAuth connector whose access token expired answers 401. Try a
            # silent refresh with the stored refresh_token, then retry once with
            # the fresh token before giving up (a hard failure leaves the caller
            # to re-authorize).
            if view.auth_type == "oauth" and row2 is not None and _is_unauthorized(exc):
                refreshed_json = await ext.connector_oauth_refresh.refresh_after_unauthorized(
                    row=row2,
                    connectors=svc._ds,
                    token_json=row2.oauth_token_json,
                )
                if not refreshed_json:
                    raise
                from mcp.shared.auth import OAuthToken

                token = OAuthToken.model_validate_json(refreshed_json)
                ov_headers["Authorization"] = f"Bearer {token.access_token}"
                tool_infos = await _attempt()
            else:
                raise

        await svc.record_test_result(user_id, connector_id, ok=True, tool_count=len(tool_infos))
        return TestConnectorResponse(
            ok=True,
            tool_count=len(tool_infos),
            tools=[ti.name for ti in tool_infos],
            tool_details=tool_infos,
        )
    except BaseException as exc:
        error_msg = str(_unwrap(exc))
        logger.warning("Connector test failed for %s: %s", connector_id, error_msg)
        await svc.record_test_result(user_id, connector_id, ok=False, error_message=error_msg)
        return TestConnectorResponse(ok=False, error=error_msg)


# ---------------------------------------------------------------------------
# Directory endpoints (catalog v2 — groups + standalone connectors)
# ---------------------------------------------------------------------------

_CATALOG: list[dict] = load_catalog()

# CONNECTOR_DIRECTORY: flat list of all connectors for OAuth slug lookup.
# CATALOG_ITEMS: raw catalog entries preserving order (groups + standalone connectors).
CONNECTOR_DIRECTORY: list[dict] = []
CATALOG_ITEMS: list[dict] = []

for _entry in _CATALOG:
    if "connectors" in _entry:
        # Group entry — multiple connectors under one display group.
        CATALOG_ITEMS.append({**_entry, "_kind": "group"})
        for _c in _entry["connectors"]:
            CONNECTOR_DIRECTORY.append(
                {
                    "slug": _c["slug"],
                    "display_name": _c.get("display_name")
                    or _entry.get("display_name", _c["slug"]),
                    "description": _c.get("description") or _entry.get("description"),
                    "icon_url": _entry.get("icon_url"),
                    "categories": _entry.get("categories", []),
                    "url": _c.get("url", ""),
                    "auth_type": _c.get("auth_type", "oauth"),
                    "transport": _c.get("transport", "http"),
                    "oauth_credentials_schema": _c.get("oauth_credentials_schema", []),
                    "header_schema": _c.get("header_schema", []),
                    "param_schema": _c.get("param_schema", []),
                    "credentials_help_url": _c.get("credentials_help_url"),
                    "oauth_authorization_endpoint": _c.get("oauth_authorization_endpoint")
                    or _entry.get("oauth_authorization_endpoint"),
                    "oauth_token_endpoint": _c.get("oauth_token_endpoint")
                    or _entry.get("oauth_token_endpoint"),
                    "command": _c.get("command"),
                    "args": _c.get("args", []),
                    "working_dir": _c.get("working_dir"),
                    "env": _c.get("env"),
                }
            )
    else:
        # Standalone connector entry — not part of any group.
        CATALOG_ITEMS.append({**_entry, "_kind": "connector"})
        CONNECTOR_DIRECTORY.append(
            {
                "slug": _entry["slug"],
                "display_name": _entry.get("display_name", _entry["slug"]),
                "description": _entry.get("description"),
                "icon_url": _entry.get("icon_url"),
                "categories": _entry.get("categories", []),
                "url": _entry.get("url", ""),
                "auth_type": _entry.get("auth_type", "oauth"),
                "transport": _entry.get("transport", "http"),
                "oauth_credentials_schema": _entry.get("oauth_credentials_schema", []),
                "header_schema": _entry.get("header_schema", []),
                "param_schema": _entry.get("param_schema", []),
                "credentials_help_url": _entry.get("credentials_help_url"),
                "oauth_authorization_endpoint": _entry.get("oauth_authorization_endpoint"),
                "oauth_token_endpoint": _entry.get("oauth_token_endpoint"),
                "command": _entry.get("command"),
                "args": _entry.get("args", []),
                "working_dir": _entry.get("working_dir"),
                "env": _entry.get("env"),
            }
        )
