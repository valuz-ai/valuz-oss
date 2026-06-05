"""In-process MCP server exposing the ``create_mcp`` tool.

Lets the agent create a connector on behalf of the user during a session.
After creation the user must go to the Connectors page to authorize/connect.

Wire shape::

    POST /internal/mcp/connectors/mcp
      headers:
        X-Valuz-Internal:   <per-process token>
        X-Valuz-Session-Id: <kernel session id>  (informational only)
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP

from valuz_agent.modules.connectors.models import AuthType, TransportType

logger = logging.getLogger(__name__)

_session_var: ContextVar[str | None] = ContextVar("valuz_connectors_mcp_session_id", default=None)

_mcp = FastMCP("valuz-connectors")


def _make_connector_service(db: Any) -> Any:
    """Build a ``ConnectorService`` bound to an async session.

    The route-layer ``_get_service`` is a FastAPI dependency (``async def``
    with ``Depends()`` defaults) — it can't be called directly outside the
    DI machinery. MCP tools instead open their own ``async_unit_of_work``
    and construct the service here, then ``await`` its async methods.
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.secret_store import FileSecretStore
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService

    return ConnectorService(
        datastore=ConnectorDatastore(db),
        secrets=FileSecretStore(settings.secrets_dir),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

_DESCRIPTION = """Create a new MCP connector so the agent can use external tools.

IMPORTANT: Whenever the user asks to install, connect, or add an MCP service,
always use this tool. Do not attempt any other approach.

## Deciding which mode to use

- User names a service ("install GitHub MCP") → Mode A (recommended, by slug).
- User gives a URL or local command → Mode B (custom).

## Mode A: install a recommended connector (by slug)

1. Call list_recommended_mcp. Each entry has a `requires` field that tells
   you exactly what to collect — you do NOT need to read any schema:
   - `{}` → no credentials. Call create_mcp with just `slug`.
   - `{"bearer_token": true}` → ask the user for the API key / token, then
     call with `slug` + `bearer_token=<value>`.
   - `{"oauth": ["client_id","client_secret"]}` → ask the user for those
     (a registered OAuth app), then call with `slug` + `client_id` +
     `client_secret`.
2. Pass ONLY `slug` (+ the credentials above). url / display_name /
   transport / auth_type are filled from the recommended entry
   automatically — do not pass them.

## Mode B: install a custom connector

- display_name: REQUIRED. Ask the user if not given.
- url: required for http/sse. command: required for stdio.
- transport: "http" | "sse" | "stdio" — inferred if omitted.

## Credential parameters (both modes)

- bearer_token: the API key / bearer token. THE common case — use this
  whenever the user gives a single token/key. For a custom connector it
  becomes a secret `Authorization: Bearer <token>` header; for a
  recommended connector it is placed on that connector's declared secret
  field.
- client_id / client_secret: a manually-registered OAuth app's
  credentials (recommended OAuth connectors, or a custom OAuth server).
- oauth_authorization_endpoint / oauth_token_endpoint: ONLY for a custom
  OAuth server with no discovery / no dynamic client registration
  (GitHub-style). Usually omit — endpoints are auto-discovered.
- headers / params: ONLY for extra/advanced headers or query params the
  cases above don't cover. JSON, either a flat object {"Name":"value"}
  (all plaintext) OR a list [{"key":"...","secret":true|false,
  "value":"..."}] to mark an entry secret. `params` go in the URL query.
- auth_type: "none" (default) | "bearer" | "oauth". Mostly informational;
  header/param injection is driven by the params above, not by auth_type.
  Set "oauth" (or just omit) for servers that use OAuth — the backend
  auto-probes and starts the OAuth flow if required.

Do NOT pass connector_type or a raw credentials object — they are not
parameters; the tool derives connector_type and routes OAuth client
credentials via client_id/client_secret.

## After calling this tool

- If the response has authorization_url, IMMEDIATELY paste the full URL to
  the user and ask them to open it in a browser to finish OAuth. Do not
  summarize or shorten it.
- If ok=false with credentials_required, you missed required credentials —
  collect them from the user and retry.

Returns JSON with ok, connector_id, status, and next_step."""


@_mcp.tool(description="List all MCP connectors the user has connected (status=connected).")
async def list_connected_mcp() -> str:
    """Return a JSON list of connected connectors."""
    try:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work(commit=False) as db:
            svc = _make_connector_service(db)
            connectors = [
                {"id": v.id, "slug": v.slug, "display_name": v.display_name, "status": v.status}
                for v in await svc.list_connectors()
                if v.status == "connected"
            ]
        return json.dumps({"ok": True, "connectors": connectors}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("list_connected_mcp failed")
        return json.dumps({"ok": False, "error": str(exc)})


def _recommended_requires(entry: dict) -> dict:
    """Normalize a recommended-connector entry's credential declaration
    into one hint the agent can act on without parsing three schemas:

      {}                         → no credentials; install directly
      {"bearer_token": True}     → collect one secret; pass as bearer_token
      {"oauth": ["client_id",…]} → collect these; pass as oauth_* args
    """
    oauth_req = [
        f["key"] for f in entry.get("oauth_credentials_schema", []) if f.get("required", True)
    ]
    if oauth_req:
        return {"oauth": oauth_req}
    hp = (entry.get("header_schema") or []) + (entry.get("param_schema") or [])
    if any(f.get("required", True) for f in hp):
        return {"bearer_token": True}
    return {}


@_mcp.tool(
    description=(
        "List the recommended MCP connectors, each with an 'installed' flag"
        " indicating whether the user has already connected it."
    )
)
async def list_recommended_mcp() -> str:
    """Return a JSON list of recommended connectors with installed status."""
    try:
        from valuz_agent.api.routes.connectors import (
            CONNECTOR_DIRECTORY,
            _localize,
        )
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work(commit=False) as db:
            svc = _make_connector_service(db)
            installed_slugs = {v.slug for v in await svc.list_connectors()}

        items = [
            {
                "slug": e["slug"],
                "display_name": _localize(e.get("display_name"), "zh-CN") or e["slug"],
                "description": _localize(e.get("description"), "zh-CN"),
                "auth_type": e.get("auth_type", "oauth"),
                "transport": e.get("transport", "http"),
                "installed": e["slug"] in installed_slugs,
                "requires": _recommended_requires(e),
            }
            for e in CONNECTOR_DIRECTORY
        ]
        return json.dumps({"ok": True, "items": items}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("list_recommended_mcp failed")
        return json.dumps({"ok": False, "error": str(exc)})


@_mcp.tool(description=_DESCRIPTION)
async def create_mcp(
    display_name: str | None = None,
    slug: str | None = None,
    url: str | None = None,
    command: str | None = None,
    transport: TransportType | None = None,
    description: str | None = None,
    auth_type: AuthType = "none",
    client_id: str | None = None,
    client_secret: str | None = None,
    bearer_token: str | None = None,
    headers: str | None = None,  # JSON: {name:value} OR [{key,secret,value}]
    params: str | None = None,  # JSON: {name:value} OR [{key,secret,value}]
    oauth_authorization_endpoint: str | None = None,
    oauth_token_endpoint: str | None = None,
    args: str | None = None,  # JSON string → list[str]
    working_dir: str | None = None,
    env: str | None = None,  # JSON string → dict
) -> str:
    """Create a connector and return a JSON result."""
    try:
        return await _invoke(
            display_name=display_name,
            transport=transport,
            slug=slug,
            description=description,
            url=url,
            auth_type=auth_type,
            client_id=client_id,
            client_secret=client_secret,
            bearer_token=bearer_token,
            headers=headers,
            params=params,
            oauth_authorization_endpoint=oauth_authorization_endpoint,
            oauth_token_endpoint=oauth_token_endpoint,
            command=command,
            args=args,
            working_dir=working_dir,
            env=env,
        )
    except Exception as exc:
        logger.exception("create_mcp failed")
        return json.dumps({"ok": False, "error": str(exc)})


async def _invoke(
    *,
    display_name: str | None,
    transport: TransportType | None,
    slug: str | None,
    description: str | None,
    url: str | None,
    auth_type: AuthType,
    client_id: str | None,
    client_secret: str | None,
    bearer_token: str | None,
    headers: str | None,
    params: str | None,
    oauth_authorization_endpoint: str | None,
    oauth_token_endpoint: str | None,
    command: str | None,
    args: str | None,
    working_dir: str | None,
    env: str | None,
) -> str:
    from valuz_agent.api.routes.connectors import (
        CONNECTOR_DIRECTORY,
        CreateConnectorRequest,
        HeaderParam,
        _localize,
        create_connector,
    )
    from valuz_agent.infra.db import async_unit_of_work

    def _cred_list(raw: str | None) -> list[HeaderParam]:
        """Accept either a flat ``{name: value}`` object (all plaintext —
        agent-ergonomic) or a ``[{key, secret?, value}]`` list (per-entry
        secret). Translated here to the connector object-list contract."""
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            return [HeaderParam(key=str(k), secret=False, value=str(v)) for k, v in data.items()]
        out: list[HeaderParam] = []
        for it in data or []:
            if isinstance(it, dict) and it.get("key"):
                out.append(
                    HeaderParam(
                        key=str(it["key"]),
                        secret=bool(it.get("secret", False)),
                        value=it.get("value"),
                    )
                )
        return out

    # ``bearer_token`` is translated below into an explicit secret entry —
    # an Authorization header for custom connectors, or the recommended
    # entry's declared secret field for a recommended connector.
    parsed_headers: list[HeaderParam] = _cred_list(headers)
    parsed_params: list[HeaderParam] = _cred_list(params)
    parsed_args: list[str] | None = json.loads(args) if args else None
    parsed_env: dict[str, str] | None = json.loads(env) if env else None

    # Normalize transport aliases and infer from context
    if transport in (None, "streamable-http"):
        transport = "stdio" if command and not url else "http"

    async with async_unit_of_work() as db:
        svc = _make_connector_service(db)
        entry = next((e for e in CONNECTOR_DIRECTORY if e["slug"] == slug), None) if slug else None

        if entry:
            # ── Recommended connector ────────────────────────────────────────
            # Catalog display_name/description may be an i18n dict
            # ({"zh-CN": ..., "en-US": ...}); resolve to a plain string
            # before it reaches CreateConnectorRequest. No Accept-Language
            # at the MCP tool boundary, so default to zh-CN (matches the
            # route layer's _parse_accept_language default).
            entry_display = _localize(entry.get("display_name"), "zh-CN") or slug
            entry_desc = _localize(entry.get("description"), "zh-CN")
            existing = await svc._ds.get_by_slug(slug)
            if existing and existing.status == "connected":
                return json.dumps(
                    {
                        "ok": True,
                        "connector_id": existing.id,
                        "slug": slug,
                        "status": "connected",
                        "next_step": "该连接器已连接，可直接在项目中启用使用。",
                    },
                    ensure_ascii=False,
                )

            recommended_auth_type = entry.get("auth_type", "oauth")
            # Non-OAuth recommended connectors declare their credential
            # via header_schema/param_schema. The tool's
            # `bearer_token` arg fills the declared secret field — gate on
            # it so the agent collects the credential before installing.
            fields_decl: list[dict] = (entry.get("header_schema") or []) + (
                entry.get("param_schema") or []
            )
            if (
                fields_decl
                and recommended_auth_type != "oauth"
                and any(f.get("required", True) for f in fields_decl)
                and not bearer_token
            ):
                desc = "; ".join(
                    str(f.get("name"))
                    + (f"（示例：{f['placeholder']}）" if f.get("placeholder") else "")
                    for f in fields_decl
                    if f.get("required", True)
                )
                return json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"安装 {entry_display} 需要提供凭证，"
                            f"请先向用户索取后作 bearer_token 传入再重新调用 "
                            f"create_mcp：{desc}"
                        ),
                        "credentials_required": [
                            f.get("key") for f in fields_decl if f.get("required", True)
                        ],
                    },
                    ensure_ascii=False,
                )
            # OAuth recommended connectors (e.g. GitHub) still declare
            # client_id/client_secret via oauth_credentials_schema.
            schema: list[dict] = entry.get("oauth_credentials_schema", [])
            if schema:
                # Map tool params to oauth_credentials_schema keys for
                # validation. client_id→client_id; client_secret→
                # client_secret (legacy schemas keyed "api_key" fall back
                # to bearer_token).
                provided: dict[str, str | None] = {
                    "api_key": bearer_token,
                    "client_id": client_id,
                    "client_secret": client_secret or bearer_token,
                }
                missing = [
                    f["key"] for f in schema if f.get("required") and not provided.get(f["key"])
                ]
                if missing:
                    fields_desc = "; ".join(
                        f"{f['key']}（{f['label']}）"
                        + (f"，示例：{f['placeholder']}" if f.get("placeholder") else "")
                        for f in schema
                        if f["key"] in missing
                    )
                    return json.dumps(
                        {
                            "ok": False,
                            "error": (
                                f"安装 {entry_display} 需要提供以下凭证，"
                                f"请先向用户索取再重新调用 create_mcp：{fields_desc}"
                            ),
                            "credentials_required": missing,
                        },
                        ensure_ascii=False,
                    )

            credentials: dict[str, str] = {}
            if client_id:
                credentials["client_id"] = client_id
            if client_secret:
                credentials["client_secret"] = client_secret

            # Non-OAuth recommended connectors carry their credential in
            # the object-list. Map the tool's bearer_token onto the
            # field that declares it — key=field.name, prefix applied so
            # the stored value is final (backend never re-prefixes).
            recommended_params: list[HeaderParam] = []
            if recommended_auth_type != "oauth" and bearer_token:
                # First secret field wins; target derived from its schema
                # (header_schema preferred). Single credential per connector.
                _mapped = False
                for _tgt, _sk in (("header", "header_schema"), ("param", "param_schema")):
                    for f in entry.get(_sk, []):
                        if not f.get("secret"):
                            continue
                        prefix = f.get("prefix") or ""
                        value = (
                            bearer_token
                            if bearer_token.startswith(prefix)
                            else f"{prefix}{bearer_token}"
                        )
                        field_item = HeaderParam(
                            key=f["name"], secret=bool(f.get("secret")), value=value
                        )
                        if _tgt == "param":
                            recommended_params.append(field_item)
                        else:
                            parsed_headers.append(field_item)
                        _mapped = True
                        break
                    if _mapped:
                        break

            body = CreateConnectorRequest(
                slug=slug,
                display_name=entry_display or display_name,
                transport=entry.get("transport", "http"),
                description=entry_desc,
                connector_type="recommended",
                url=entry.get("url"),
                auth_type=recommended_auth_type,
                credentials=credentials,
                headers=parsed_headers or None,
                params=(parsed_params + recommended_params) or None,
                oauth_authorization_endpoint=oauth_authorization_endpoint or None,
                oauth_token_endpoint=oauth_token_endpoint or None,
            )
        else:
            # ── Custom connector (slug ignored if not a recommended one) ──────
            if not display_name:
                return json.dumps(
                    {"ok": False, "error": "display_name is required for custom connectors"}
                )
            if transport in ("http", "sse") and not url:
                return json.dumps({"ok": False, "error": "url is required for http/sse transport"})
            if transport == "stdio" and not command:
                return json.dumps({"ok": False, "error": "command is required for stdio transport"})

            # bearer_token becomes an explicit secret Authorization entry
            # (final value, Bearer-prefixed).
            if auth_type == "bearer" and bearer_token:
                parsed_headers.append(
                    HeaderParam(
                        key="Authorization",
                        secret=True,
                        value=(
                            bearer_token
                            if bearer_token.lower().startswith("bearer ")
                            else f"Bearer {bearer_token}"
                        ),
                    )
                )

            # Custom OAuth: a manually-registered app's client_id/secret
            # (servers without discovery / DCR, e.g. GitHub-style).
            custom_credentials: dict[str, str] = {}
            if client_id:
                custom_credentials["client_id"] = client_id
            if client_secret:
                custom_credentials["client_secret"] = client_secret

            body = CreateConnectorRequest(
                display_name=display_name,
                transport=transport,
                description=description,
                url=url,
                auth_type=auth_type,
                headers=parsed_headers or None,
                params=parsed_params or None,
                credentials=custom_credentials,
                oauth_authorization_endpoint=oauth_authorization_endpoint or None,
                oauth_token_endpoint=oauth_token_endpoint or None,
                command=command,
                args=parsed_args or [],
                working_dir=working_dir,
                env=parsed_env,
            )

        result = await create_connector(body=body, svc=svc)
        if result.authorization_url:
            return json.dumps(
                {
                    "ok": True,
                    "connector_id": result.id,
                    "slug": result.slug,
                    "status": "pending_auth",
                    "authorization_url": result.authorization_url,
                    "next_step": (
                        "请前往「连接器」页面，找到该连接器并点击「授权连接」完成 OAuth 授权。"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "ok": True,
                "connector_id": result.id,
                "slug": result.slug,
                "status": "connecting",
                "next_step": "连接器已创建，可前往「连接器」页面点击「测试」验证连接状态。",
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# ASGI wrapper
# ---------------------------------------------------------------------------


def connectors_mcp_session_manager_run() -> Any:
    _mcp.streamable_http_app()
    return _mcp.session_manager.run()


def build_connectors_mcp_asgi() -> Any:
    from starlette.responses import PlainTextResponse

    inner = _mcp.streamable_http_app()

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        from valuz_agent.infra.config import settings as _settings

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        if headers.get("x-valuz-internal") != _settings.internal_mcp_token:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        ctx_token = _session_var.set(session_id)
        try:
            await inner(scope, receive, send)
        finally:
            _session_var.reset(ctx_token)

    return _app


def connectors_mcp_url(*, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/internal/mcp/connectors/mcp"


__all__ = [
    "build_connectors_mcp_asgi",
    "connectors_mcp_session_manager_run",
    "connectors_mcp_url",
    "create_mcp",
    "_invoke",
]
