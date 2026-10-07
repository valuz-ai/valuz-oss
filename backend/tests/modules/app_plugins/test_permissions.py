"""Plugin request permissions: the route map and the middleware matrix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from tests.modules.app_plugins.helpers import build_plugin
from valuz_agent.api.app_plugin_middleware import PluginPermissionMiddleware
from valuz_agent.modules.app_plugins import logs
from valuz_agent.modules.app_plugins.permissions import Rule, api_path, classify
from valuz_agent.modules.app_plugins.service import AppPluginService, app_plugin_service

ME = "acme.dashboard"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/v1/projects", Rule("projects:read")),
        ("GET", "/v1/projects/p1/files", Rule("projects:read")),
        ("POST", "/v1/projects", Rule("projects:write", write=True)),
        ("DELETE", "/v1/projects/p1", Rule("projects:write", write=True)),
        ("GET", "/v1/artifacts", Rule("artifacts:read")),
        ("GET", "/v1/artifacts/revisions/r1/content", Rule("artifacts:read")),
        ("PUT", "/v1/artifacts/x", Rule("artifacts:write", write=True)),
        ("POST", "/v1/docs/search", Rule("knowledge:read")),
        ("GET", "/v1/docs/d1", Rule("knowledge:read")),
        ("DELETE", "/v1/docs/d1", None),
        ("GET", "/v1/docs/d1/chunks", None),
        ("GET", "/v1/sessions/s1/events", Rule("conversations:read")),
        ("POST", "/v1/sessions", Rule("conversations:write", write=True)),
        ("POST", "/v1/sessions/s1/messages", Rule("conversations:write", write=True)),
        ("GET", "/v1/sessions", None),
        ("DELETE", "/v1/sessions/s1", None),
        ("GET", "/v1/connectors", Rule("connectors:read")),
        ("GET", "/v1/connectors/c1/tools", Rule("connectors:read")),
        ("POST", "/v1/connectors/c1/tools/search/call", Rule("connectors:call", True, False, True)),
        ("DELETE", "/v1/connectors/c1", None),
        ("POST", "/v1/connectors", None),
        ("POST", "/v1/notifications", Rule("notifications", write=True)),
        ("GET", "/v1/notifications", None),
        ("GET", f"/v1/app-plugins/{ME}/storage", Rule("storage")),
        ("PUT", f"/v1/app-plugins/{ME}/storage/a/b", Rule("storage", write=True)),
        ("DELETE", f"/v1/app-plugins/{ME}/storage/a", Rule("storage", write=True)),
        ("GET", f"/v1/app-plugins/{ME}/automations", Rule("automations:run")),
        (
            "POST",
            f"/v1/app-plugins/{ME}/automations/x/run",
            Rule("automations:run", write=True),
        ),
        ("GET", f"/v1/app-plugins/{ME}/automation-runs/r1", Rule("automations:run")),
        ("GET", f"/v1/app-plugins/{ME}/logs", Rule(None, always=True)),
        ("POST", f"/v1/app-plugins/{ME}/logs", Rule(None, always=True)),
        ("GET", f"/v1/app-plugins/{ME}/config", Rule(None, always=True)),
        ("PUT", f"/v1/app-plugins/{ME}/config", Rule(None, write=True, always=True)),
        # never someone else's plugin, never the management surface
        ("GET", "/v1/app-plugins/other.plugin/storage", None),
        ("GET", "/v1/app-plugins", None),
        ("POST", f"/v1/app-plugins/{ME}/disable", None),
        ("DELETE", f"/v1/app-plugins/{ME}", None),
        ("POST", "/v1/app-plugins/install", None),
        ("GET", "/v1/app-plugin-assets/acme.dashboard/1/frontend/index.js", None),
        ("GET", "/v1/providers", None),
        ("GET", "/v1/settings", None),
        ("GET", "/healthz", None),
        ("GET", "/kernel/v1/sessions", None),
        ("GET", "/_internal/mcp/docs/v1/x", None),
        ("GET", "/valuz-backend/kernel/v1/sessions", None),
    ],
)
def test_classify(method: str, path: str, expected: Rule | None) -> None:
    assert classify(method, path, ME) == expected


def test_paths_match_from_v1_on_whatever_the_prefix() -> None:
    for prefix in ("", "/valuz-backend", "/a/b"):
        assert classify("GET", f"{prefix}/v1/projects", ME) == Rule("projects:read")
        assert api_path(f"{prefix}/v1/projects") == "/v1/projects"
    assert api_path("/healthz") is None
    assert api_path("/v1") is None


# ---- middleware ------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(PluginPermissionMiddleware)

    async def ok(request: Request) -> dict[str, Any]:
        return {"ok": True, "path": request.url.path}

    async def echo(request: Request) -> dict[str, Any]:
        return {"ok": True, "body": await request.json()}

    for path in (
        "/v1/projects",
        "/valuz-backend/v1/projects",
        "/v1/artifacts",
        "/healthz",
        "/kernel/v1/sessions",
        "/v1/providers",
        f"/v1/app-plugins/{ME}/storage/k",
        "/v1/app-plugins/other.plugin/storage/k",
        f"/v1/app-plugins/{ME}/logs",
        f"/v1/app-plugins/{ME}/config",
        f"/v1/app-plugins/{ME}/automations",
    ):
        app.add_api_route(path, ok, methods=["GET", "PUT", "POST"])
    app.add_api_route("/v1/sessions", ok, methods=["POST"])
    app.add_api_route("/v1/notifications", ok, methods=["POST"])
    app.add_api_route("/v1/connectors/{cid}/tools/{tool}/call", echo, methods=["POST"])
    return app


@pytest.fixture
async def installed(data_root: Path, tmp_path: Path) -> AppPluginService:
    """The singleton the middleware reads, with a plugin holding three permissions."""
    app_plugin_service._cache.clear()
    app_plugin_service._dev_sigs.clear()
    root = build_plugin(
        tmp_path / "src", permissions=["projects:read", "connectors:call", "storage"]
    )
    await app_plugin_service.install("user-1", {"source_path": str(root)})
    return app_plugin_service


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def headers(plugin_id: str = ME) -> dict[str, str]:
    return {"X-Valuz-App-Plugin-Id": plugin_id}


def denial(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 403
    assert response.headers["access-control-allow-origin"] == "*"
    detail: dict[str, Any] = response.json()["detail"]
    assert detail["code"] == "plugin_permission_denied"
    return detail


async def test_requests_without_the_header_pass_untouched(
    app: FastAPI, installed: AppPluginService
) -> None:
    async with client(app) as http:
        for method, path in (
            ("GET", "/v1/providers"),
            ("POST", "/v1/sessions"),
            ("GET", "/kernel/v1/sessions"),
            ("GET", "/healthz"),
        ):
            assert (await http.request(method, path)).status_code in (200, 404)
        assert (await http.get("/v1/providers")).json() == {"ok": True, "path": "/v1/providers"}


async def test_allowed_and_denied(app: FastAPI, installed: AppPluginService) -> None:
    async with client(app) as http:
        assert (await http.get("/v1/projects", headers=headers())).status_code == 200
        # the same route behind an ingress prefix
        assert (await http.get("/valuz-backend/v1/projects", headers=headers())).status_code == 200

        detail = denial(await http.get("/v1/artifacts", headers=headers()))
        assert detail["permission"] == "artifacts:read" and detail["plugin_id"] == ME
        assert denial(await http.post("/v1/sessions", headers=headers()))["permission"] == (
            "conversations:write"
        )
        assert denial(await http.post("/v1/notifications", headers=headers()))["permission"] == (
            "notifications"
        )
        # nothing outside the public map is reachable, whatever the plugin declares
        for path in ("/healthz", "/kernel/v1/sessions", "/v1/providers"):
            detail = denial(await http.get(path, headers=headers()))
            assert detail["permission"] is None and detail["reason"] == "not_available"


async def test_unknown_and_disabled_plugins_are_refused(
    app: FastAPI, installed: AppPluginService
) -> None:
    async with client(app) as http:
        detail = denial(await http.get("/v1/projects", headers=headers("ghost.plugin")))
        assert detail["reason"] == "plugin_not_enabled"
        await installed.set_enabled("user-1", ME, False)
        assert denial(await http.get("/v1/projects", headers=headers()))["reason"] == (
            "plugin_not_enabled"
        )
        await installed.set_enabled("user-1", ME, True)
        assert (await http.get("/v1/projects", headers=headers())).status_code == 200


async def test_the_plugins_own_storage_logs_and_config(
    app: FastAPI, installed: AppPluginService
) -> None:
    base = f"/v1/app-plugins/{ME}"
    async with client(app) as http:
        assert (await http.put(f"{base}/storage/k", headers=headers())).status_code == 200
        # logs and config need no declared permission; automations need automations:run
        assert (await http.get(f"{base}/logs", headers=headers())).status_code == 200
        assert (await http.put(f"{base}/config", headers=headers())).status_code == 200
        assert denial(await http.get(f"{base}/automations", headers=headers()))["permission"] == (
            "automations:run"
        )
        # another plugin's storage is not reachable
        other = "/v1/app-plugins/other.plugin/storage/k"
        assert denial(await http.get(other, headers=headers()))["reason"] == "not_available"


async def test_connector_calls_need_write_permission_for_allow_write(
    app: FastAPI, installed: AppPluginService
) -> None:
    call = "/v1/connectors/c1/tools/search/call"
    async with client(app) as http:
        read_only = await http.post(call, headers=headers(), json={"arguments": {"q": 1}})
        assert read_only.status_code == 200
        assert read_only.json()["body"] == {"arguments": {"q": 1}}  # the body reached the route
        denied = await http.post(
            call, headers=headers(), json={"arguments": {}, "allow_write": True}
        )
        assert denial(denied)["permission"] == "connectors:write"


async def test_connector_write_is_allowed_with_the_permission(
    app: FastAPI, data_root: Path, tmp_path: Path
) -> None:
    app_plugin_service._cache.clear()
    root = build_plugin(tmp_path / "w", permissions=["connectors:call", "connectors:write"])
    await app_plugin_service.install("user-1", {"source_path": str(root)})
    async with client(app) as http:
        ok = await http.post(
            "/v1/connectors/c1/tools/t/call",
            headers=headers(),
            json={"arguments": {}, "allow_write": True},
        )
        assert ok.status_code == 200 and ok.json()["body"]["allow_write"] is True


async def test_denials_and_writes_are_audited(app: FastAPI, installed: AppPluginService) -> None:
    async with client(app) as http:
        await http.get("/v1/artifacts", headers=headers())  # denied
        await http.get("/v1/projects", headers=headers())  # a read: not audited
        await http.put(f"/v1/app-plugins/{ME}/storage/k", headers=headers())  # a write
    entries = [e for e in logs.read_log(ME) if e["source"] == "audit"]
    assert [e["level"] for e in entries] == ["warn", "info"]
    assert "denied GET /v1/artifacts" in entries[0]["message"]
    assert "artifacts:read" in entries[0]["message"]
    assert entries[1]["message"] == f"PUT /v1/app-plugins/{ME}/storage/k"


async def test_preflight_is_never_blocked(app: FastAPI, installed: AppPluginService) -> None:
    async with client(app) as http:
        response = await http.options("/v1/artifacts", headers=headers())
        assert response.status_code != 403
