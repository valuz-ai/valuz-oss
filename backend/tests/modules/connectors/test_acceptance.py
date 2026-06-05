"""Slice 6 — backend-resolvable Acceptance cases.

Covers the exec-plan Acceptance items that don't need a live MCP server
or the browser: #2 (catalog prefix / final-value), #9 (migrated bearer
keeps working via backfilled manifest), #10 (OAuth regression — layered
after build_overrides), #11 (catalog field structural validation), and
the backend half of #12 (Phase B: catalog non-Authorization secret header
+ custom secret header usable end-to-end through create → injection).

The live-probe halves of #8/#12 and the UI e2e are exercised by Slice 6's
e2e / human RELEASE, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.mcp_resolver import _build_http_config
from valuz_agent.api.routes.connectors import (
    CatalogField,
    CreateConnectorRequest,
    HeaderParam,
)
from valuz_agent.infra.database import Base
from valuz_agent.infra.secret_store import InMemorySecretStore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import (
    CatalogFieldSpec,
    ConnectorService,
    CredEntry,
    build_overrides,
)


@pytest_asyncio.fixture
async def svc_and_secrets():
    # Host is fully async now (aiosqlite); a shared in-memory async engine
    # backs the datastore so every session sees the same DB.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    secrets = InMemorySecretStore()
    svc = ConnectorService(datastore=ConnectorDatastore(session), secrets=secrets)
    yield svc, secrets, session
    await session.close()
    await engine.dispose()


@dataclass
class _FakeRow:
    id: str = "c1"
    slug: str = "acme"
    url: str = "https://mcp.acme.test/mcp"
    transport: str = "http"
    auth_type: str = "oauth"
    headers_json: str | None = None
    params_json: str | None = None
    cred_manifest_json: str | None = None
    args_json: str | None = None


# ── Acceptance #2 — catalog prefix is UI-only; client sends final value ─
async def test_should_store_and_inject_the_final_value_verbatim_with_prefix(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
    # Catalog field declares prefix "Bearer " (UI prefill only). The client
    # sends the WHOLE string; backend stores/injects it verbatim — no
    # double "Bearer Bearer".
    fields = [CatalogFieldSpec(key="api_key", name="Authorization", target="header", secret=True)]
    v = await svc.create_connector(
        slug="acme",
        display_name="Acme",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[CredEntry(key="Authorization", secret=False, value="Bearer tok123")],
        catalog_fields=fields,
    )
    row = await ConnectorDatastore(svc._ds._db).get_by_id(v.id)
    headers, _ = build_overrides(row, secrets)
    assert headers == {"Authorization": "Bearer tok123"}


# ── Acceptance #9 — Slice-2-migrated bearer connector still injects ────
def test_should_keep_migrated_bearer_working_via_backfilled_manifest():
    # Shape a row exactly as the Slice-2 backfill leaves it: manifest
    # points at the *legacy* secret path holding the RAW token.
    secrets = InMemorySecretStore()
    secrets.put("connector/c1/api_key", "raw-token")
    row = _FakeRow(
        auth_type="bearer",
        cred_manifest_json=json.dumps(
            [
                {
                    "key": "api_key",
                    "target": "header",
                    "name": "Authorization",
                    "secret_ref": "connector/c1/api_key",
                }
            ]
        ),
    )
    headers, _ = build_overrides(row, secrets)
    # Transitional Bearer compat normalises the raw legacy token.
    assert headers == {"Authorization": "Bearer raw-token"}


# ── Acceptance #10 — OAuth layered AFTER build_overrides, unchanged ────
async def test_should_layer_oauth_authorization_after_build_overrides():
    secrets = InMemorySecretStore()
    secrets.put("connector/c1/oauth_token", json.dumps({"access_token": "oauth-abc"}))
    row = _FakeRow(auth_type="oauth")

    # build_overrides itself never touches OAuth.
    base_headers, _ = build_overrides(row, secrets)
    assert "Authorization" not in base_headers

    # The resolver overlays it after.
    cfgs = await _build_http_config(row, secrets=secrets)
    assert cfgs is not None and len(cfgs) == 1
    assert cfgs[0].headers["Authorization"] == "Bearer oauth-abc"


# ── Acceptance #11 — catalog field structural validation ──────────────
def test_should_reject_illegal_catalog_field_name():
    with pytest.raises(ValidationError):
        CatalogField(key="k", name="bad name with spaces")


# (the old out-of-range-target test is gone: header vs param is now decided
# by which schema the field sits in, so an invalid target is impossible.)


def test_should_reject_duplicate_keys_within_a_header_list():
    with pytest.raises(ValidationError):
        CreateConnectorRequest(
            display_name="d",
            transport="http",
            headers=[
                HeaderParam(key="X-Dup", secret=False, value="a"),
                HeaderParam(key="X-Dup", secret=False, value="b"),
            ],
        )


# ── Acceptance #12 (backend half) — Phase B capability end-to-end ─────
async def test_should_support_catalog_nonauth_secret_header_plus_custom_secret(
    svc_and_secrets,
):
    svc, secrets, _ = svc_and_secrets
    # Catalog declares a NON-Authorization secret header; user also adds a
    # custom secret header. Both must round-trip create → injection.
    fields = [CatalogFieldSpec(key="api_key", name="X-API-Key", target="header", secret=True)]
    v = await svc.create_connector(
        slug="acme",
        display_name="Acme",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[
            CredEntry(key="X-API-Key", secret=False, value="catalog-secret"),
            CredEntry(key="X-Custom", secret=True, value="custom-secret"),
            CredEntry(key="X-Plain", secret=False, value="plain"),
        ],
        catalog_fields=fields,
    )
    view = await svc.get_connector(v.id)
    assert view is not None
    by_key = {e.key: e for e in view.headers}
    assert by_key["X-API-Key"].secret is True and by_key["X-API-Key"].value is None
    assert by_key["X-Custom"].secret is True and by_key["X-Custom"].value is None
    assert by_key["X-Plain"].secret is False and by_key["X-Plain"].value == "plain"

    row = await ConnectorDatastore(svc._ds._db).get_by_id(v.id)
    headers, _ = build_overrides(row, secrets)
    assert headers == {
        "X-Plain": "plain",
        "X-API-Key": "catalog-secret",
        "X-Custom": "custom-secret",
    }
    # Resolver parity (Acceptance #8) on the same row.
    cfgs = await _build_http_config(row, secrets=secrets)
    assert cfgs is not None and dict(cfgs[0].headers) == headers
