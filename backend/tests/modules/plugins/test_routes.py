"""``/v1/plugins`` routes — request parsing (multipart vs JSON), response
shapes and error mapping, with the service replaced by an in-memory fake."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.middleware import ErrorHandlerMiddleware
from valuz_agent.api.routes import plugins as plugins_routes
from valuz_agent.modules.plugins.errors import PluginConflict, PluginNotFound
from valuz_agent.modules.plugins.models import (
    PluginInstallResult,
    PluginMember,
    PluginMembershipRef,
    PluginPreview,
    PluginUninstallResult,
    PluginView,
)


def _view(name: str = "demo") -> PluginView:
    return PluginView(
        id="p1",
        name=name,
        version="1.0.0",
        source="zip",
        composition="skills_only",
        enabled=True,
        members=[
            PluginMember(kind="skill", slug="alpha", name="alpha", content_hash="h", installed=True)
        ],
        skill_count=1,
        connector_count=0,
        root_path="/tmp/plugins/demo",
        installed_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:00:00Z",
    )


class FakePluginService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.plugins = {"p1": _view()}

    async def list_plugins(self, user_id: str) -> list[PluginView]:
        return list(self.plugins.values())

    async def get_plugin(self, user_id: str, plugin_id: str) -> PluginView:
        if plugin_id not in self.plugins:
            raise PluginNotFound()
        return self.plugins[plugin_id]

    async def memberships(
        self, user_id: str, kind: str, slugs: list[str]
    ) -> dict[str, list[PluginMembershipRef]]:
        self.calls.append(("memberships", {"kind": kind, "slugs": slugs}))
        return {
            s: ([PluginMembershipRef(id="p1", name="demo")] if s == "alpha" else []) for s in slugs
        }

    async def preview(self, user_id: str, **kwargs: Any) -> PluginPreview:
        self.calls.append(("preview", kwargs))
        return PluginPreview(
            manifest={"name": "demo"}, format="agent_plugins", composition="skills_only"
        )

    async def install(self, user_id: str, **kwargs: Any) -> PluginInstallResult:
        self.calls.append(("install", kwargs))
        if kwargs.get("path") == "/conflict":
            raise PluginConflict()
        return PluginInstallResult(plugin=_view(), status="installed")

    async def update(self, user_id: str, plugin_id: str, **kwargs: Any) -> PluginInstallResult:
        self.calls.append(("update", {"plugin_id": plugin_id, **kwargs}))
        return PluginInstallResult(plugin=_view(), status="updated")

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> PluginView:
        self.calls.append(("set_enabled", {"plugin_id": plugin_id, "enabled": enabled}))
        view = _view()
        view.enabled = enabled
        return view

    async def uninstall(self, user_id: str, plugin_id: str) -> PluginUninstallResult:
        self.calls.append(("uninstall", {"plugin_id": plugin_id}))
        return PluginUninstallResult(
            removed_members=[{"kind": "skill", "slug": "alpha"}],  # type: ignore[list-item]
            kept_members=[{"kind": "skill", "slug": "beta", "reason": "standalone"}],  # type: ignore[list-item]
        )

    async def export_zip(self, user_id: str, plugin_id: str) -> tuple[str, bytes]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("plugin.json", "{}")
        return "demo-1.0.0.zip", buffer.getvalue()


@pytest.fixture
def client() -> tuple[TestClient, FakePluginService]:
    fake = FakePluginService()
    app = FastAPI()
    app.add_middleware(ErrorHandlerMiddleware)
    app.include_router(plugins_routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    app.dependency_overrides[plugins_routes._get_plugin_service] = lambda: fake  # noqa: SLF001
    return TestClient(app), fake


def test_list_and_get(client: tuple[TestClient, FakePluginService]) -> None:
    tc, _fake = client
    resp = tc.get("/v1/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["name"] for p in body["items"]] == ["demo"]
    assert body["items"][0]["members"][0]["slug"] == "alpha"
    assert tc.get("/v1/plugins/p1").json()["name"] == "demo"
    missing = tc.get("/v1/plugins/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == 404_751


def test_memberships_query(client: tuple[TestClient, FakePluginService]) -> None:
    tc, fake = client
    resp = tc.get("/v1/plugins/memberships", params={"kind": "skill", "slugs": "alpha, beta,"})
    assert resp.status_code == 200
    assert resp.json() == {"alpha": [{"id": "p1", "name": "demo"}], "beta": []}
    assert fake.calls[-1] == ("memberships", {"kind": "skill", "slugs": ["alpha", "beta"]})
    assert tc.get("/v1/plugins/memberships", params={"kind": "agent"}).status_code == 422


def test_install_multipart_zip(client: tuple[TestClient, FakePluginService]) -> None:
    tc, fake = client
    resp = tc.post(
        "/v1/plugins/install",
        files={"file": ("p.zip", b"PK\x05\x06zipbytes", "application/zip")},
        data={"on_conflict": "overwrite"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "installed"
    name, kwargs = fake.calls[-1]
    assert name == "install"
    assert kwargs["zip_bytes"] == b"PK\x05\x06zipbytes"
    assert kwargs["on_conflict"] == "overwrite"
    assert kwargs["path"] is None and kwargs["url"] is None and kwargs["market_item_id"] is None


def test_install_json_body(client: tuple[TestClient, FakePluginService]) -> None:
    tc, fake = client
    resp = tc.post("/v1/plugins/install", json={"market_item_id": "market:plugin:x"})
    assert resp.status_code == 200
    _name, kwargs = fake.calls[-1]
    assert kwargs["market_item_id"] == "market:plugin:x" and kwargs["zip_bytes"] is None
    assert kwargs["on_conflict"] == "skip"
    resp = tc.post("/v1/plugins/install", json={"path": "/conflict"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == 409_751
    assert tc.post("/v1/plugins/install", json={"on_conflict": "maybe"}).status_code == 422
    assert (
        tc.post(
            "/v1/plugins/install", content=b"[1]", headers={"content-type": "application/json"}
        ).status_code
        == 422
    )


def test_preview_json_and_multipart(client: tuple[TestClient, FakePluginService]) -> None:
    tc, fake = client
    resp = tc.post("/v1/plugins/preview", json={"url": "https://x.example/p.zip"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "agent_plugins"
    assert fake.calls[-1][1]["url"] == "https://x.example/p.zip"
    resp = tc.post("/v1/plugins/preview", files={"file": ("p.zip", b"zip", "application/zip")})
    assert resp.status_code == 200
    assert fake.calls[-1][1]["zip_bytes"] == b"zip"


def test_enable_disable_update_delete(client: tuple[TestClient, FakePluginService]) -> None:
    tc, fake = client
    assert tc.post("/v1/plugins/p1/disable").json()["enabled"] is False
    assert tc.post("/v1/plugins/p1/enable").json()["enabled"] is True
    assert tc.post("/v1/plugins/p1/update").json()["status"] == "updated"
    assert fake.calls[-1] == ("update", {"plugin_id": "p1", "on_conflict": "skip"})
    tc.post("/v1/plugins/p1/update", json={"on_conflict": "overwrite"})
    assert fake.calls[-1][1]["on_conflict"] == "overwrite"
    resp = tc.delete("/v1/plugins/p1")
    assert resp.status_code == 200
    assert resp.json() == {
        "removed_members": [{"kind": "skill", "slug": "alpha"}],
        "kept_members": [{"kind": "skill", "slug": "beta", "reason": "standalone"}],
    }


def test_export_streams_a_zip(client: tuple[TestClient, FakePluginService]) -> None:
    tc, _fake = client
    resp = tc.get("/v1/plugins/p1/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert 'filename="demo-1.0.0.zip"' in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.namelist() == ["plugin.json"]
