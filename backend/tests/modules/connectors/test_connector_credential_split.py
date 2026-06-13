"""Slice 3+4 — per-entry secret split, desired-state update, unified
injection. Covers the exec-plan Acceptance points that are unit-testable
at the service / injection layer (1, 3, 4, 5, 6, 7, 8, 9 + delete).
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401  (surfaces src.core)
from valuz_agent.adapters.mcp_resolver import _build_http_config
from valuz_agent.infra.database import Base
from valuz_agent.infra.secret_store import InMemorySecretStore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import (
    CatalogFieldSpec,
    ConnectorService,
    CredEntry,
    build_overrides,
    merge_params_into_url,
)


@pytest_asyncio.fixture
async def svc_and_secrets():
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
    secrets = InMemorySecretStore()
    svc = ConnectorService(datastore=ConnectorDatastore(session), secrets=secrets)
    yield svc, secrets, session
    await session.close()
    await engine.dispose()


async def _row(svc, session, cid):
    return await ConnectorDatastore(session).get_by_id("local-test-owner", cid)


# ── Acceptance #1 — catalog secret header → store + manifest, not json ──
async def test_should_route_catalog_secret_header_to_store_not_plaintext(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
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
    # Injection materialises it.
    headers, _ = build_overrides(await _row(svc, svc._ds._db, v.id), secrets)
    assert headers == {"X-API-Key": "k"}


# ── Acceptance #3 — custom split: secret→store, plaintext→json ─────────
async def test_should_split_custom_entries_by_client_secret_flag(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
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
    headers, _ = build_overrides(await _row(svc, svc._ds._db, v.id), secrets)
    assert headers == {"X-Trace": "t", "Authorization": "Bearer k"}


# ── Acceptance #4 — catalog `fields` authoritative over client secret ──
async def test_should_let_catalog_fields_override_client_secret_flag(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
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


# ── Acceptance #5 — params secret → store + manifest(target=param) ─────
async def test_should_route_secret_param_to_store_and_inject_into_query(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
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
    headers, params = build_overrides(await _row(svc, svc._ds._db, v.id), secrets)
    assert params == {"token": "p"}
    assert merge_params_into_url("https://x.test/mcp", params) == ("https://x.test/mcp?token=p")


# ── Acceptance #6 — plaintext param merge into URL query ──────────────
def test_should_merge_params_overriding_same_key_and_preserving_others():
    out = merge_params_into_url("https://x.test/mcp?region=eu&keep=1", {"region": "us"})
    assert out == "https://x.test/mcp?keep=1&region=us"


# ── Acceptance #7 — desired-state: rotate / preserve(blank) / delete ───
async def test_should_apply_desired_state_semantics_on_update(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
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
    headers, _ = build_overrides(await _row(svc, svc._ds._db, cid), secrets)
    assert headers == {"X-Trace": "t1", "Authorization": "Bearer one"}

    # Rotate the secret, drop X-Trace entirely (absent → deleted).
    await svc.update_connector(
        "local-test-owner",
        cid,
        headers=[CredEntry(key="Authorization", secret=True, value="Bearer two")],
    )
    headers, _ = build_overrides(await _row(svc, svc._ds._db, cid), secrets)
    assert headers == {"Authorization": "Bearer two"}


# Phase B retired the legacy api_key+bearer desugar entirely — the
# object-list is the only credential path. A bearer Authorization is now
# just a normal explicit secret entry (covered by the custom-split test
# above); migrated legacy connectors keep working via the Slice-2
# manifest backfill (test_acceptance::...migrated_bearer...).


# ── Acceptance #8 — probe and resolver share build_overrides ──────────
async def test_should_inject_identically_via_resolver_and_build_overrides(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
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
    row = await _row(svc, svc._ds._db, v.id)
    exp_headers, exp_params = build_overrides(row, secrets)
    exp_url = merge_params_into_url(row.url, exp_params)

    cfgs = await _build_http_config(row, secrets=secrets)
    assert cfgs is not None and len(cfgs) == 1
    assert dict(cfgs[0].headers) == exp_headers
    assert cfgs[0].url == exp_url


# ── delete clears manifest-referenced secrets ─────────────────────────
async def test_should_delete_manifest_secrets_on_connector_delete(svc_and_secrets):
    svc, secrets, _ = svc_and_secrets
    v = await svc.create_connector(
        "local-test-owner",
        slug="del",
        display_name="Del",
        transport="http",
        url="https://x.test/mcp",
        auth_type="none",
        headers=[CredEntry(key="X-Secret", secret=True, value="zzz")],
    )
    ref = f"connector/{v.id}/cred/header.X-Secret"
    assert secrets.get(ref) == "zzz"
    assert await svc.delete_connector("local-test-owner", v.id) is True
    assert secrets.get(ref) is None
