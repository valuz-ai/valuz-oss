"""The OAuth callback installs the whole credential group, not just the row it authorized.

The unit tests around ``oauth_sharing`` prove the propagation itself. These drive
the real ``oauth_callback`` route function — the path a user actually walks —
because that wiring is what decides whether anything reaches the screen: the
token exchange and tool probe are mocked, the datastore is real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from mcp.shared.auth import OAuthToken
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes import connectors as routes
from valuz_agent.infra.database import Base
from valuz_agent.integrations.connector_oauth import OauthMetadata
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)

USER = "local-test-owner"
_STATE = "state-xyz"
_META = OauthMetadata(
    authorization_endpoint="https://reportify.cn/oauth/authorize",
    token_endpoint="https://api.reportify.cn/v2/oauth/token",
    resource="https://mcp.reportify.cn",
).model_dump_json()


@pytest.fixture
async def sessionmaker_(tmp_path) -> AsyncIterator:
    db_file = tmp_path / "oauth_cb.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                ConnectorRow.__table__,
                ConnectorAttrRow.__table__,
                ConnectorOAuthRow.__table__,
            ],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@asynccontextmanager
async def _uow(factory):
    session = factory()
    try:
        yield session
    finally:
        await session.close()


async def _run_callback(factory, *, connector_id: str) -> None:
    """Drive the real callback with the network mocked out."""
    pkce = json.dumps(
        {
            "connector_id": connector_id,
            "user_id": USER,
            "code_verifier": "v",
            "client_id": "cid-1",
            "client_secret": None,
            "server_url": "https://data.valuz.cn/mcp/search",
            "redirect_uri": "http://127.0.0.1:8000/v1/connectors/oauth/callback",
        }
    )
    cache = AsyncMock()
    cache.get.return_value = pkce

    helper = AsyncMock()
    helper.get_oauth_token.return_value = OAuthToken(
        access_token="a1", refresh_token="r1", expires_in=3600
    )

    with (
        patch.object(routes, "async_unit_of_work", lambda: _uow(factory)),
        patch.object(routes.ext, "cache", cache),
        # Imported inside the route body → patch it at the source module.
        patch(
            "valuz_agent.integrations.connector_oauth.McpOauthHelper",
            return_value=helper,
        ),
        patch.object(routes, "_probe_oauth_tool_count", AsyncMock(return_value=11)),
    ):
        await routes.oauth_callback(code="code-1", state=_STATE)


def _pending(slug: str, url: str) -> ConnectorRow:
    return ConnectorRow(
        slug=slug,
        display_name=slug,
        connector_type="recommended",
        transport="http",
        url=url,
        auth_type="oauth",
        oauth_metadata=_META,
        oauth_client_info_json='{"client_id": "cid-1"}',
        enabled=False,
        status="pending_auth",
    )


async def test_callback_installs_the_unlisted_sibling(sessionmaker_) -> None:
    """Connect Valuz search from the catalog → quotes lands installed + connected.

    This is the regression: only ``valuz-search`` has a row, and the sibling has
    never been installed. It must not be left sitting in「可添加」.
    """
    async with sessionmaker_() as db:
        row = await ConnectorDatastore(db).create(
            USER, _pending("valuz-search", "https://data.valuz.cn/mcp/search")
        )
        search_id = row.id

    await _run_callback(sessionmaker_, connector_id=search_id)

    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        search = await ds.get_by_slug(USER, "valuz-search")
        assert search is not None
        assert (search.status, search.enabled, search.tool_count) == ("connected", True, 11)

        stock = await ds.get_by_slug(USER, "valuz-data")
        assert stock is not None, "sibling was never installed — it stays in「可添加」"
        assert (stock.status, stock.enabled) == ("connected", True)
        assert stock.tool_count == 11
        assert stock.url == "https://data.valuz.cn/mcp"
        # A refresh needs all three, not just the token.
        assert json.loads(stock.oauth_token_json or "{}")["access_token"] == "a1"
        assert stock.oauth_client_info_json is not None
        assert stock.oauth_metadata is not None


async def test_callback_authorizes_an_already_installed_sibling(sessionmaker_) -> None:
    """The other order: both installed and pending, authorizing one lights up both."""
    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        row = await ds.create(USER, _pending("valuz-search", "https://data.valuz.cn/mcp/search"))
        search_id = row.id
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create(
            USER, _pending("valuz-data", "https://data.valuz.cn/mcp")
        )

    await _run_callback(sessionmaker_, connector_id=search_id)

    async with sessionmaker_() as db:
        stock = await ConnectorDatastore(db).get_by_slug(USER, "valuz-data")
        assert stock is not None
        assert (stock.status, stock.enabled, stock.tool_count) == ("connected", True, 11)


async def test_callback_leaves_an_unrelated_connector_alone(sessionmaker_) -> None:
    """A token for the valuz group must never reach a connector outside it."""
    async with sessionmaker_() as db:
        ds = ConnectorDatastore(db)
        row = await ds.create(USER, _pending("valuz-search", "https://data.valuz.cn/mcp/search"))
        search_id = row.id
    async with sessionmaker_() as db:
        await ConnectorDatastore(db).create(
            USER, _pending("github", "https://api.githubcopilot.com/mcp/")
        )

    await _run_callback(sessionmaker_, connector_id=search_id)

    async with sessionmaker_() as db:
        github = await ConnectorDatastore(db).get_by_slug(USER, "github")
        assert github is not None
        assert github.oauth_token_json is None
        assert github.status == "pending_auth"
