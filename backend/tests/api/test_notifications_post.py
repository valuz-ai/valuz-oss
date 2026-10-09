"""``POST /v1/notifications`` — a plugin (or any client) posts to the caller's ledger."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401
import valuz_agent.infra.db as db_mod
from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes import notifications as routes
from valuz_agent.infra.database import Base
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.service import notification_service

USER = "user-A"


@pytest.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    db_file = tmp_path / "notifications.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[NotificationRow.__table__])
    sync_engine.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod, "AsyncSessionLocal", async_sessionmaker(bind=engine, expire_on_commit=False)
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    await engine.dispose()


async def test_plugin_post_is_an_app_plugin_entry(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/v1/notifications",
        json={"title": "Report ready", "body": "Q3 risk summary", "link": "/x/acme.dash/report"},
        headers={"X-Valuz-App-Plugin-Id": "acme.dash"},
    )
    assert res.status_code == 200, res.text
    notification_id = res.json()["id"]

    entries, unread = await notification_service.snapshot(USER)
    assert unread == 1
    (entry,) = entries
    assert entry.id == notification_id
    assert entry.kind == "app_plugin"
    assert entry.title == "Report ready" and entry.body == "Q3 risk summary"
    assert entry.route == "/x/acme.dash/report"
    assert entry.urgency == "info" and entry.action == "none"
    assert entry.payload == {"app_plugin_id": "acme.dash", "link": "/x/acme.dash/report"}


async def test_every_plugin_post_is_its_own_entry_with_an_app_plugin_dedup_key(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Valuz-App-Plugin-Id": "acme.dash"}
    first = await client.post("/v1/notifications", json={"title": "same"}, headers=headers)
    second = await client.post("/v1/notifications", json={"title": "same"}, headers=headers)
    assert first.json()["id"] != second.json()["id"]

    entries, _ = await notification_service.snapshot(USER)
    assert len(entries) == 2
    from sqlalchemy import select

    from valuz_agent.infra.db import async_unit_of_work

    async with async_unit_of_work(commit=False) as db:
        keys = list((await db.scalars(select(NotificationRow.dedup_key))).all())
    assert len(set(keys)) == 2
    assert all(k.startswith("app-plugin:acme.dash:") for k in keys)


async def test_without_a_plugin_header_it_is_a_generic_entry(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/v1/notifications",
        json={"title": "Heads up", "urgency": "actionable", "link": "https://example.com/x"},
    )
    assert res.status_code == 200, res.text
    (entry,), _ = await notification_service.snapshot(USER)
    assert entry.kind == "custom" and len(entry.kind) <= 32
    assert entry.urgency == "actionable"
    assert entry.route is None, "an external URL is not an in-app route"
    assert entry.payload == {"link": "https://example.com/x"}
    assert "plugin_id" not in entry.payload


@pytest.mark.parametrize(
    "link", ["//evil.example/x", "javascript:alert(1)", "relative/path", "/has space", "/a\\b"]
)
async def test_only_in_app_paths_become_a_route(client: httpx.AsyncClient, link: str) -> None:
    res = await client.post("/v1/notifications", json={"title": "t", "link": link})
    assert res.status_code == 200, res.text
    (entry,), _ = await notification_service.snapshot(USER)
    assert entry.route is None


async def test_validation(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/notifications", json={})).status_code == 422
    assert (await client.post("/v1/notifications", json={"title": ""})).status_code == 422
    bad_urgency = await client.post("/v1/notifications", json={"title": "t", "urgency": "loud"})
    assert bad_urgency.status_code == 422
    bad_plugin = await client.post(
        "/v1/notifications", json={"title": "t"}, headers={"X-Valuz-App-Plugin-Id": "Not A Plugin!"}
    )
    assert bad_plugin.status_code == 400
    assert bad_plugin.json()["detail"]["code"] == "invalid_plugin_id"


async def test_a_long_plugin_id_still_fits_the_dedup_key_column(client: httpx.AsyncClient) -> None:
    plugin_id = f"{'a' * 49}.{'b' * 50}"  # 100 chars: the manifest maximum
    res = await client.post(
        "/v1/notifications", json={"title": "t"}, headers={"X-Valuz-App-Plugin-Id": plugin_id}
    )
    assert res.status_code == 200, res.text
    from sqlalchemy import select

    from valuz_agent.infra.db import async_unit_of_work

    async with async_unit_of_work(commit=False) as db:
        key = (await db.scalars(select(NotificationRow.dedup_key))).one()
    assert len(key) <= 128 and key.startswith("app-plugin:")
