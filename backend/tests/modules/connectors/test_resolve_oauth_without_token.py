"""Resolving an installed-but-unauthorised OAuth connector must skip, not crash.

Regression for the onboarding crash: deploying a team agent bound to an
installed OAuth connector (Valuz) with no stored token must resolve to nothing
and be skipped, never raise. Connector OAuth tokens now live on the row
(``oauth_token_json``, in the ``valuz_connector_attr`` side table), so a
token-less connector simply yields no MCP server — there is no separate secret
store to be ``None`` and crash the resolver.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Side-effect import — surfaces the kernel's ``app`` / ``src.core`` on sys.path
# before the mcp resolver (loaded lazily inside resolve_mcp_servers) imports
# ``app.schemas``.
import valuz_agent.boot.kernel  # noqa: F401,E402
from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.connectors.service import ConnectorService

USER = "local-test-owner"  # matches the autouse owner-context fixture


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resolve.db'}")
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
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _install_valuz(db) -> None:
    db.add(
        ConnectorRow(
            id="conn-valuz-search",
            user_id=USER,
            slug="valuz-search",
            display_name="Valuz · Search",
            connector_type="recommended",
            transport="http",
            auth_type="oauth",
            url="https://data.valuz.cn/mcp/search",
            enabled=True,
            status="pending_auth",
        )
    )
    await db.commit()


async def test_tokenless_oauth_connector_skips_without_crash(db) -> None:
    """A token-less OAuth connector resolves to nothing and is skipped — no crash."""
    await _install_valuz(db)
    svc = ConnectorService.with_defaults(db)

    out = await svc.resolve_mcp_servers(["valuz-search"], user_id=USER)

    assert out == []  # no stored OAuth token → skipped, and no crash
