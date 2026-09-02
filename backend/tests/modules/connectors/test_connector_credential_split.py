"""Slice 3+4 — per-entry secret split, desired-state update, unified
injection. Covers the exec-plan Acceptance points that are unit-testable
at the service / injection layer (1, 3, 4, 5, 6, 7, 8, 9 + delete).

Secret values now live in the connector row's ``cred_secrets_json`` column, so
``build_overrides`` is a pure/sync function on the row and the service no longer
takes a secret store.
"""

from __future__ import annotations

import json

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401  (surfaces src.core)
from valuz_agent.adapters.mcp_resolver import _build_http_config
from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import (
    CatalogFieldSpec,
    ConnectorService,
    CredEntry,
    build_overrides,
    build_request_overrides,
    merge_params_into_url,
)


@pytest_asyncio.fixture
async def svc():
    # The host is fully async now (aiosqlite); back the datastore with a
    # shared in-memory async engine so every session sees the same DB.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    yield ConnectorService(datastore=ConnectorDatastore(session))
    await session.close()
    await engine.dispose()


async def _row(svc, cid):
    return await ConnectorDatastore(svc._ds._db).get_by_id("local-test-owner", cid)


def _stored(row) -> dict[str, dict]:
    return json.loads(row.headers_json) if row.headers_json else {}


# ── Acceptance #1 — catalog field forces secret; value withheld by GET ──
async def test_should_mark_catalog_secret_header_and_withhold_value(svc):
    fields = [CatalogFieldSpec(key="api_key", name="X-API-Key", target="header", secret=True)]
    v = await svc.create_connector(
        "local-test-owner",
        slug="acme",
        display_name="Acme",
        transport="http",
        url="https://mcp.acme.test/mcp",
        auth_type="none",
        headers=[CredEntry(key="X-API-Key", secret=False, value="k")],
        catalog_fields=fields,
    )
    row = await svc.get_connector("local-test-owner", v.id)
    # GET hides the secret value; entry present, no value.
    assert row is not None
    h = next(e for e in row.headers if e.key == "X-API-Key")
    assert h.secret is True and h.value is None
    # Stored as a secret entry (value present in DB, flagged → withheld by GET).
    assert _stored(await _row(svc, v.id))["X-API-Key"] == {"value": "k", "secret": True}
    # Injection materialises it.
    headers, _ = build_overrides(await _row(svc, v.id))
    assert headers == {"X-API-Key": "k"}


# ── Acceptance #3 — custom split: secret→cred_secrets, plaintext→json ──────────
async def test_should_split_custom_entries_by_client_secret_flag(svc):
    v = await svc.create_connector(
        "local-test-owner",
        slug="cust",
        display_name="Cust",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[
            CredEntry(key="Authorization", secret=True, value="Bearer k"),
            CredEntry(key="X-Trace", secret=False, value="t"),
        ],
    )
    row = await svc.get_connector("local-test-owner", v.id)
    assert row is not None
    auth = next(e for e in row.headers if e.key == "Authorization")
    trace = next(e for e in row.headers if e.key == "X-Trace")
    assert auth.secret is True and auth.value is None  # hidden
    assert trace.secret is False and trace.value == "t"  # echoed
    headers, _ = build_overrides(await _row(svc, v.id))
    assert headers == {"X-Trace": "t", "Authorization": "Bearer k"}


# ── Acceptance #4 — catalog `fields` authoritative over client secret ──
async def test_should_let_catalog_fields_override_client_secret_flag(svc):
    # Catalog says secret=True; malicious client claims secret=False.
    fields = [CatalogFieldSpec(key="api_key", name="Authorization", target="header", secret=True)]
    v = await svc.create_connector(
        "local-test-owner",
        slug="acme",
        display_name="Acme",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[CredEntry(key="Authorization", secret=False, value="Bearer k")],
        catalog_fields=fields,
    )
    row = await svc.get_connector("local-test-owner", v.id)
    assert row is not None
    auth = next(e for e in row.headers if e.key == "Authorization")
    assert auth.secret is True and auth.value is None  # treated as secret


# ── Acceptance #5 — params secret → cred_secrets + manifest(target=param) ──────
async def test_should_route_secret_param_to_store_and_inject_into_query(svc):
    fields = [CatalogFieldSpec(key="token", name="token", target="param", secret=True)]
    v = await svc.create_connector(
        "local-test-owner",
        slug="acme",
        display_name="Acme",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        params=[CredEntry(key="token", secret=False, value="p")],
        catalog_fields=fields,
    )
    row = await svc.get_connector("local-test-owner", v.id)
    assert row is not None
    tok = next(e for e in row.params if e.key == "token")
    assert tok.secret is True and tok.value is None
    headers, params = build_overrides(await _row(svc, v.id))
    assert params == {"token": "p"}
    assert merge_params_into_url("https://x.test/mcp", params) == ("https://x.test/mcp?token=p")


# ── Acceptance #6 — plaintext param merge into URL query ──────────────
def test_should_merge_params_overriding_same_key_and_preserving_others():
    out = merge_params_into_url("https://x.test/mcp?region=eu&keep=1", {"region": "us"})
    assert out == "https://x.test/mcp?keep=1&region=us"


# ── Acceptance #7 — desired-state: rotate / preserve(blank) / delete ───
async def test_should_apply_desired_state_semantics_on_update(svc):
    v = await svc.create_connector(
        "local-test-owner",
        slug="d",
        display_name="D",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[
            CredEntry(key="Authorization", secret=True, value="Bearer one"),
            CredEntry(key="X-Trace", secret=False, value="t1"),
        ],
    )
    cid = v.id

    # Blank value on the secret + resend plaintext → preserved unchanged.
    await svc.update_connector(
        "local-test-owner",
        cid,
        headers=[
            CredEntry(key="Authorization", secret=True, value=None),
            CredEntry(key="X-Trace", secret=False, value="t1"),
        ],
    )
    headers, _ = build_overrides(await _row(svc, cid))
    assert headers == {"X-Trace": "t1", "Authorization": "Bearer one"}

    # Rotate the secret, drop X-Trace entirely (absent → deleted).
    await svc.update_connector(
        "local-test-owner",
        cid,
        headers=[CredEntry(key="Authorization", secret=True, value="Bearer two")],
    )
    headers, _ = build_overrides(await _row(svc, cid))
    assert headers == {"Authorization": "Bearer two"}


# Phase B retired the legacy api_key+bearer desugar entirely — the
# object-list is the only credential path. A bearer Authorization is now
# just a normal explicit secret entry (covered by the custom-split test
# above); migrated legacy connectors keep working via the manifest
# backfill (test_acceptance::...migrated_bearer...).


# ── Acceptance #8 — probe and resolver share build_overrides ──────────
async def test_should_inject_identically_via_resolver_and_build_overrides(svc):
    v = await svc.create_connector(
        "local-test-owner",
        slug="parity",
        display_name="Parity",
        transport="http",
        url="https://x.test/mcp?keep=1",
        auth_type="none",
        headers=[CredEntry(key="X-H", secret=True, value="hv")],
        params=[CredEntry(key="region", secret=False, value="us")],
    )
    row = await _row(svc, v.id)
    exp_headers, exp_params = build_request_overrides(row)
    exp_url = merge_params_into_url(row.url, exp_params)

    # ``none`` auth → the resolver never touches OAuth, so ``connectors`` is unused.
    cfgs = await _build_http_config(row, svc._ds)
    assert cfgs is not None and len(cfgs) == 1
    assert dict(cfgs[0].headers) == exp_headers
    assert cfgs[0].url == exp_url


# ── delete clears the row (and its secret values) ─────────────────────
async def test_should_delete_connector_and_its_secrets(svc):
    v = await svc.create_connector(
        "local-test-owner",
        slug="del",
        display_name="Del",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[CredEntry(key="X-Secret", secret=True, value="zzz")],
    )
    assert _stored(await _row(svc, v.id))["X-Secret"] == {"value": "zzz", "secret": True}
    assert await svc.delete_connector("local-test-owner", v.id) is True
    assert await svc.get_connector("local-test-owner", v.id) is None
