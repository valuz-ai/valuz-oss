"""Credential sharing against a real ``ConnectorDatastore``.

The unit tests in ``test_oauth_sharing`` use a plain fake row. A real
``ConnectorRow`` keeps its OAuth fields in a side table (``valuz_connector_oauth``)
behind properties that proxy an in-memory holder, which the datastore replaces
wholesale on write — so "the copy happened" in memory does not prove "the sibling
can authenticate after a reload". These tests take the round trip.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.connectors.oauth_sharing import (
    inherit_oauth_credentials,
    propagate_oauth_credentials,
)

_USER = "u1"
_META = '{"token_endpoint": "https://api.reportify.cn/v2/oauth/token"}'
_CLIENT = '{"client_id": "cid-1"}'
_TOKEN = '{"access_token": "a1", "refresh_token": "r1"}'
_EXPIRES = 1_700_000_000_000


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "conn.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            ConnectorRow.__table__,
            ConnectorAttrRow.__table__,
            ConnectorOAuthRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _row(slug: str, *, authorized: bool) -> ConnectorRow:
    row = ConnectorRow(
        slug=slug,
        display_name=slug,
        connector_type="recommended",
        transport="http",
        url=f"https://mcp.reportify.cn/{slug.split('-')[-1]}/mcp",
        auth_type="oauth",
        enabled=authorized,
        status="connected" if authorized else "pending_auth",
    )
    if authorized:
        row.oauth_metadata = _META
        row.oauth_client_info_json = _CLIENT
        row.oauth_token_json = _TOKEN
        row.oauth_token_expires_at = _EXPIRES
    return row


async def test_propagated_credentials_survive_a_reload(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create(_USER, _row("valuz-search", authorized=True))
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create(_USER, _row("valuz-data", authorized=False))

    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        source = await ds.get_by_slug(_USER, "valuz-search")
        assert source is not None
        assert await propagate_oauth_credentials(_USER, source, ds, install_missing=True) == [
            "valuz-data"
        ]

    # Fresh session → the holder is re-hydrated from the side table, proving the
    # write landed rather than only mutating the in-memory proxy.
    async with sessionmaker_() as db:
        target = await ConnectorDatastore(db).get_by_slug(_USER, "valuz-data")
        assert target is not None
        assert target.oauth_token_json == _TOKEN
        assert target.oauth_client_info_json == _CLIENT  # refresh needs the client
        assert target.oauth_metadata == _META  # …and the endpoints
        assert target.oauth_token_expires_at == _EXPIRES
        assert (target.status, target.enabled) == ("connected", True)


async def test_authorizing_one_member_installs_the_other(sessionmaker_) -> None:
    """The headline path: connect Valuz search, full data shows up installed + connected.

    Only the authorized member exists to begin with — the sibling has no row at
    all, which is exactly the state a user is in when they connect one from the
    catalog for the first time.
    """
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create(_USER, _row("valuz-search", authorized=True))

    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        source = await ds.get_by_slug(_USER, "valuz-search")
        assert source is not None
        assert await propagate_oauth_credentials(_USER, source, ds, install_missing=True) == [
            "valuz-data"
        ]

    async with sessionmaker_() as db:
        installed = await ConnectorDatastore(db).get_by_slug(_USER, "valuz-data")
        assert installed is not None
        assert (installed.status, installed.enabled) == ("connected", True)
        assert installed.oauth_token_json == _TOKEN
        assert installed.oauth_client_info_json == _CLIENT
        # Definition comes from the catalog — the user never filled in a form.
        assert installed.url == "https://data.valuz.cn/mcp"
        assert installed.auth_type == "oauth"
        assert installed.connector_type == "builtin"


async def test_inherited_credentials_survive_a_reload(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create(_USER, _row("valuz-search", authorized=True))

    # Install the second member after the group was already authorized.
    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        fresh = _row("valuz-data", authorized=False)
        assert await inherit_oauth_credentials(_USER, fresh, ds) == "valuz-search"
        await ds.create(_USER, fresh)

    async with sessionmaker_() as db:
        target = await ConnectorDatastore(db).get_by_slug(_USER, "valuz-data")
        assert target is not None
        assert target.oauth_token_json == _TOKEN
        assert target.oauth_client_info_json == _CLIENT
        assert (target.status, target.enabled) == ("connected", True)


async def test_sharing_never_crosses_owners(sessionmaker_) -> None:
    """owner-a authorizing must install owner-a's own sibling, never touch owner-b's."""
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create("owner-a", _row("valuz-search", authorized=True))
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create("owner-b", _row("valuz-data", authorized=False))

    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        source = await ds.get_by_slug("owner-a", "valuz-search")
        assert source is not None
        assert await propagate_oauth_credentials("owner-a", source, ds, install_missing=True) == [
            "valuz-data"
        ]

    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        # owner-b's row is untouched — a different row entirely, still unauthorized.
        theirs = await ds.get_by_slug("owner-b", "valuz-data")
        assert theirs is not None
        assert theirs.oauth_token_json is None
        assert theirs.status == "pending_auth"

        # owner-a got their own, authorized row under the same slug.
        mine = await ds.get_by_slug("owner-a", "valuz-data")
        assert mine is not None
        assert mine.id != theirs.id
        assert mine.oauth_token_json == _TOKEN
        assert mine.status == "connected"
