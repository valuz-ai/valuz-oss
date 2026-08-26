from __future__ import annotations

import json
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import (
    ConnectorService,
    CredEntry,
    after_connector_oauth_authorized_hook,
)
from valuz_agent.ports.connector_lifecycle import (
    ConnectorLifecycleHook,
    ConnectorOAuthSnapshot,
    ConnectorSecretSnapshot,
    NoopConnectorLifecycleHook,
)
from valuz_agent.ports.extensions import ext


class RecordingConnectorHook(ConnectorLifecycleHook):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sessions: list[Any] = []

    async def after_connector_saved(
        self,
        *,
        db,
        user_id: str,
        connector,
        secret_snapshot: ConnectorSecretSnapshot,
        origin: str,
    ) -> None:
        self.sessions.append(db)
        self.calls.append(
            (
                "saved",
                {
                    "user_id": user_id,
                    "slug": connector.slug,
                    "origin": origin,
                    "headers_json": secret_snapshot.headers_json,
                    "params_json": secret_snapshot.params_json,
                    "env_json": secret_snapshot.env_json,
                },
            )
        )

    async def after_connector_oauth_authorized(
        self,
        *,
        db,
        user_id: str,
        connector,
        oauth_snapshot: ConnectorOAuthSnapshot,
    ) -> None:
        self.sessions.append(db)
        self.calls.append(
            (
                "oauth",
                {
                    "user_id": user_id,
                    "slug": connector.slug,
                    "client_info_json": oauth_snapshot.client_info_json,
                    "token_json": oauth_snapshot.token_json,
                    "token_expires_at": oauth_snapshot.token_expires_at,
                },
            )
        )

    async def before_connector_delete(self, *, db, user_id: str, connector) -> None:
        self.sessions.append(db)
        self.calls.append(("delete", {"user_id": user_id, "slug": connector.slug}))


@pytest_asyncio.fixture
async def svc_and_hook():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    hook = RecordingConnectorHook()
    ext.connector_lifecycle = hook
    try:
        yield ConnectorService(datastore=ConnectorDatastore(session)), hook
    finally:
        ext.connector_lifecycle = NoopConnectorLifecycleHook()
        await session.close()
        await engine.dispose()


async def test_connector_lifecycle_hook_runs_on_create_update_delete(svc_and_hook):
    svc, hook = svc_and_hook
    created = await svc.create_connector(
        "owner-1",
        slug="github",
        display_name="GitHub",
        transport="http",
        url="https://mcp.github.test",
        auth_type="none",
        headers=[CredEntry(key="Authorization", secret=True, value="Bearer token")],
    )

    await svc.update_connector("owner-1", created.id, display_name="GitHub Updated")
    assert await svc.delete_connector("owner-1", created.id) is True

    assert [name for name, _ in hook.calls] == ["saved", "saved", "delete"]
    assert hook.sessions == [svc._ds.session, svc._ds.session, svc._ds.session]
    first = hook.calls[0][1]
    assert first["user_id"] == "owner-1"
    assert first["slug"] == "github"
    assert first["origin"] == "created"
    assert json.loads(first["headers_json"]) == {
        "Authorization": {"value": "Bearer token", "secret": True}
    }
    second = hook.calls[1][1]
    assert second["origin"] == "updated"
    assert hook.calls[2][1] == {"user_id": "owner-1", "slug": "github"}


async def test_connector_lifecycle_hook_runs_after_probe_result(svc_and_hook):
    svc, hook = svc_and_hook
    created = await svc.create_connector(
        "owner-1",
        slug="firecrawl",
        display_name="Firecrawl",
        transport="http",
        url="https://mcp.firecrawl.test",
        auth_type="none",
    )

    updated = await svc.record_test_result(
        "owner-1",
        created.id,
        ok=True,
        tool_count=3,
    )

    assert updated is not None
    assert updated.status == "connected"
    assert [name for name, _ in hook.calls] == ["saved", "saved"]
    assert hook.calls[-1][1]["slug"] == "firecrawl"
    assert hook.calls[-1][1]["origin"] == "updated"


async def test_connector_lifecycle_hook_runs_after_oauth_authorized(svc_and_hook):
    svc, hook = svc_and_hook
    created = await svc.create_connector(
        "owner-1",
        slug="oauthy",
        display_name="OAuthy",
        transport="http",
        url="https://mcp.oauthy.test",
        auth_type="oauth",
    )
    row = await svc._ds.get_by_id("owner-1", created.id)
    assert row is not None
    row.oauth_client_info_json = '{"client_id":"cid"}'
    row.oauth_token_json = '{"access_token":"access"}'
    row.oauth_token_expires_at = 1780000000000
    updated = await svc._ds.update(row)

    await after_connector_oauth_authorized_hook(svc._ds.session, "owner-1", updated)

    assert hook.calls[-1] == (
        "oauth",
        {
            "user_id": "owner-1",
            "slug": "oauthy",
            "client_info_json": '{"client_id":"cid"}',
            "token_json": '{"access_token":"access"}',
            "token_expires_at": 1780000000000,
        },
    )
