"""``/v1/extensions/third-party`` and ``/v1/ext-assets``: the HTTP surface of docs card 04 §D."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from tests.modules.third_party.conftest import data_root, db  # noqa: F401
from tests.modules.third_party.helpers import INDEX_JS, build_plugin, zip_dir
from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes.third_party import assets_router, router
from valuz_agent.api.third_party_middleware import PluginPermissionMiddleware
from valuz_agent.modules.third_party.service import third_party_service
from valuz_agent.ports.extensions import ext

BASE = "/v1/extensions/third-party"
PID = "acme.dashboard"
CONFIG = {
    "type": "object",
    "properties": {"region": {"type": "string", "enum": ["cn", "hk"], "default": "cn"}},
}
AUTOMATION = {"name": "run-me", "runtime": "python", "entry": "automations/run_me.py"}


@pytest.fixture
async def http(data_root: Path, db: None, monkeypatch: pytest.MonkeyPatch):  # noqa: F811, ANN201
    third_party_service._cache.clear()
    third_party_service._dev_sigs.clear()
    monkeypatch.setattr(ext.automation_runtime, "enqueue", AsyncMock())
    app = FastAPI()
    app.add_middleware(PluginPermissionMiddleware)
    app.include_router(router)
    app.include_router(assets_router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def plugin_src(tmp_path: Path, version: str = "1.0.0", name: str = "src", **over: Any) -> Path:
    over.setdefault("permissions", ["storage", "automations:run"])
    over.setdefault("config", CONFIG)
    over.setdefault("automations", [AUTOMATION])
    files = {
        "automations/run_me.py": "def run(ctx):\n    return {}\n",
        **over.pop("files", {}),
    }
    return build_plugin(tmp_path / name, PID, version, files=files, **over)


async def install(http: httpx.AsyncClient, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    response = await http.post(
        f"{BASE}/install", json={"source_path": str(plugin_src(tmp_path, **kwargs))}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ---- install, list, lifecycle ----------------------------------------------------------


async def test_install_list_and_shapes(http: httpx.AsyncClient, tmp_path: Path) -> None:
    body = await install(http, tmp_path)
    assert set(body) == {"plugin", "updated_from"} and body["updated_from"] is None
    item = body["plugin"]
    assert set(item) == {
        "id", "version", "name", "description", "publisher", "source", "status",
        "status_reason", "enabled", "permissions", "requires", "unmet_requires", "engines",
        "entry_url", "style_urls", "locales", "config_schema", "automations", "revision",
        "dev_path", "sha256", "installed_at",
    }  # fmt: skip
    listing = (await http.get(BASE)).json()
    assert set(listing) == {"api_version", "safe_mode", "safe_mode_reason", "generation", "plugins"}
    assert listing["api_version"] == "1.0.0" and listing["safe_mode"] is False
    assert [p["id"] for p in listing["plugins"]] == [PID]
    assert listing["plugins"][0]["status"] == "enabled"
    assert listing["plugins"][0]["automations"][0]["name"] == "run-me"


async def test_inspect_and_install_from_a_zip(http: httpx.AsyncClient, tmp_path: Path) -> None:
    zip_path = zip_dir(plugin_src(tmp_path), tmp_path / "p.zip")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    info = (await http.post(f"{BASE}/inspect", json={"source_path": str(zip_path)})).json()
    assert info["sha256"] == sha and info["existing"] is None and info["errors"] == []
    assert info["permissions"] == ["storage", "automations:run"]
    assert set(info) == {
        "manifest", "sha256", "size", "permissions", "requires", "unmet_requires", "errors",
        "warnings", "has_backend", "existing", "added_permissions",
    }  # fmt: skip
    ok = await http.post(
        f"{BASE}/install", json={"source_path": str(zip_path), "expected_sha256": sha}
    )
    assert ok.status_code == 200 and ok.json()["plugin"]["sha256"] == sha


async def test_error_shapes(http: httpx.AsyncClient, tmp_path: Path) -> None:
    await install(http, tmp_path)

    again = await http.post(
        f"{BASE}/install", json={"source_path": str(plugin_src(tmp_path, name="again"))}
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "version_not_newer"

    bad = build_plugin(tmp_path / "bad", "valuz.thing")
    invalid = await http.post(f"{BASE}/install", json={"source_path": str(bad)})
    assert invalid.status_code == 400
    detail = invalid.json()["detail"]
    assert detail["code"] == "invalid_manifest" and any("reserved" in e for e in detail["errors"])

    zip_path = zip_dir(plugin_src(tmp_path, "2.0.0", name="v2"), tmp_path / "v2.zip")
    mismatch = await http.post(
        f"{BASE}/install", json={"source_path": str(zip_path), "expected_sha256": "0" * 64}
    )
    assert mismatch.status_code == 422 and mismatch.json()["detail"]["code"] == "sha256_mismatch"

    assert (await http.post(f"{BASE}/nope.nope/enable")).status_code == 404
    missing = await http.post(f"{BASE}/install", json={"source_path": str(tmp_path / "x")})
    assert missing.status_code == 400 and missing.json()["detail"]["code"] == "invalid_source"
    both = await http.post(f"{BASE}/install", json={"url": "https://a/b.zip", "source_path": "x"})
    assert both.json()["detail"]["code"] == "invalid_source"


async def test_enable_disable_reload_dev_link_and_delete(
    http: httpx.AsyncClient, tmp_path: Path
) -> None:
    await install(http, tmp_path)
    off = (await http.post(f"{BASE}/{PID}/disable")).json()["plugin"]
    assert off["status"] == "disabled" and off["enabled"] is False
    on = (await http.post(f"{BASE}/{PID}/enable")).json()["plugin"]
    assert on["status"] == "enabled"

    not_dev = await http.post(f"{BASE}/{PID}/reload")
    assert not_dev.status_code == 400 and not_dev.json()["detail"]["code"] == "not_a_dev_plugin"
    linked = (
        await http.post(f"{BASE}/dev-link", json={"path": str(plugin_src(tmp_path, name="dev"))})
    ).json()["plugin"]
    assert linked["source"]["kind"] == "dev"
    reloaded = (await http.post(f"{BASE}/{PID}/reload")).json()["plugin"]
    assert reloaded["revision"] == linked["revision"] + 1

    deleted = await http.delete(f"{BASE}/{PID}", params={"purge_data": "true"})
    assert deleted.status_code == 200
    assert deleted.json() == {"removed": True, "automations_deleted": 1}
    assert (await http.get(BASE)).json()["plugins"] == []


async def test_watch_long_polls_for_a_generation_change(
    http: httpx.AsyncClient, tmp_path: Path
) -> None:
    generation = (await http.get(BASE)).json()["generation"]
    quiet = await http.get(f"{BASE}/watch", params={"since": generation, "timeout": 0.2})
    assert quiet.json() == {"generation": generation}
    waiter = asyncio.create_task(
        http.get(f"{BASE}/watch", params={"since": generation, "timeout": 5})
    )
    await asyncio.sleep(0.05)
    await install(http, tmp_path)
    woken = await asyncio.wait_for(waiter, 3)
    assert woken.json()["generation"] > generation


async def test_safe_mode(http: httpx.AsyncClient) -> None:
    assert (await http.post(f"{BASE}/safe-mode", json={"enabled": True, "reason": "x"})).json() == {
        "safe_mode": True
    }
    listing = (await http.get(BASE)).json()
    assert listing["safe_mode"] is True and listing["safe_mode_reason"] == "x"
    assert (await http.post(f"{BASE}/safe-mode", json={"enabled": False})).json() == {
        "safe_mode": False
    }


# ---- logs, config, storage -------------------------------------------------------------


async def test_logs(http: httpx.AsyncClient, tmp_path: Path) -> None:
    await install(http, tmp_path)
    for message in ("one", "two", "three"):
        response = await http.post(
            f"{BASE}/{PID}/logs",
            json={"level": "warn", "message": message},
            headers={"X-Valuz-Plugin-Id": PID},
        )
        assert response.status_code == 204 and response.content == b""
    entries = (await http.get(f"{BASE}/{PID}/logs", params={"limit": 2})).json()["entries"]
    assert [e["message"] for e in entries] == ["two", "three"]
    assert entries[0]["level"] == "warn" and entries[0]["source"] == "frontend"
    assert set(entries[0]) == {"ts", "level", "message", "source"}
    assert (await http.get(f"{BASE}/nope.nope/logs")).status_code == 404


async def test_config(http: httpx.AsyncClient, tmp_path: Path) -> None:
    await install(http, tmp_path)
    assert (await http.get(f"{BASE}/{PID}/config")).json() == {
        "values": {"region": "cn"},
        "schema": CONFIG,
    }
    saved = await http.put(f"{BASE}/{PID}/config", json={"values": {"region": "hk"}})
    assert saved.status_code == 200 and saved.json()["values"] == {"region": "hk"}
    bad = await http.put(f"{BASE}/{PID}/config", json={"values": {"region": "us"}})
    assert bad.status_code == 400
    detail = bad.json()["detail"]
    assert detail["code"] == "invalid_config" and detail["errors"]
    assert (await http.get(f"{BASE}/{PID}/config")).json()["values"] == {"region": "hk"}


async def test_storage(http: httpx.AsyncClient, tmp_path: Path) -> None:
    await install(http, tmp_path)
    put = await http.put(f"{BASE}/{PID}/storage/notes/today", json={"value": {"a": [1, 2]}})
    assert put.status_code == 200 and put.json() == {"key": "notes/today", "size": 11}
    await http.put(f"{BASE}/{PID}/storage/other", json={"value": "x"})
    assert (await http.get(f"{BASE}/{PID}/storage/notes/today")).json() == {
        "key": "notes/today",
        "value": {"a": [1, 2]},
    }
    listing = (await http.get(f"{BASE}/{PID}/storage", params={"prefix": "notes/"})).json()
    assert [i["key"] for i in listing["items"]] == ["notes/today"]
    assert set(listing["items"][0]) == {"key", "size", "updated_at"}
    deleted = await http.delete(f"{BASE}/{PID}/storage/notes/today")
    assert deleted.status_code == 204
    missing = await http.get(f"{BASE}/{PID}/storage/notes/today")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"
    too_big = await http.put(f"{BASE}/{PID}/storage/big", json={"value": "x" * (256 * 1024)})
    assert too_big.status_code == 413
    assert too_big.json()["detail"]["code"] == "storage_quota_exceeded"
    long_key = await http.put(f"{BASE}/{PID}/storage/{'k' * 201}", json={"value": 1})
    assert long_key.status_code == 400


# ---- automations -----------------------------------------------------------------------


async def test_automations(http: httpx.AsyncClient, tmp_path: Path) -> None:
    await install(http, tmp_path)
    listing = (await http.get(f"{BASE}/{PID}/automations")).json()["automations"]
    assert [a["name"] for a in listing] == ["run-me"]
    assert set(listing[0]) == {
        "name", "automation_id", "runtime", "trigger", "status", "latest_run"
    }  # fmt: skip
    assert listing[0]["automation_id"] and listing[0]["latest_run"] is None

    started = (await http.post(f"{BASE}/{PID}/automations/run-me/run", json={})).json()
    assert set(started) == {"automation_id", "run_id", "status"}
    busy = await http.post(f"{BASE}/{PID}/automations/run-me/run")
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "automation_already_running"
    latest = (await http.get(f"{BASE}/{PID}/automations/run-me/runs/latest")).json()
    assert latest["run_id"] == started["run_id"]
    run = await http.get(f"{BASE}/{PID}/automation-runs/{started['run_id']}")
    assert run.status_code == 200 and run.json()["run_id"] == started["run_id"]
    assert (await http.get(f"{BASE}/{PID}/automation-runs/nope")).status_code == 404
    unknown = await http.post(f"{BASE}/{PID}/automations/nope/run")
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "automation_not_found"


# ---- validate and pack -----------------------------------------------------------------


async def test_validate_and_pack(http: httpx.AsyncClient, tmp_path: Path) -> None:
    root = plugin_src(tmp_path)
    report = (await http.post(f"{BASE}/validate", json={"path": str(root)})).json()
    assert report["ok"] is True and report["manifest"]["id"] == PID
    packed = (
        await http.post(f"{BASE}/pack", json={"path": str(root), "out_dir": str(tmp_path / "o")})
    ).json()
    assert set(packed) >= {"path", "sha256", "size"} and Path(packed["path"]).is_file()
    (root / "frontend" / "index.js").unlink()
    bad = (await http.post(f"{BASE}/validate", json={"path": str(root)})).json()
    assert bad["ok"] is False and any("frontend.entry" in e for e in bad["errors"])
    refused = await http.post(f"{BASE}/pack", json={"path": str(root)})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "invalid_manifest"


# ---- assets ----------------------------------------------------------------------------


async def test_assets(http: httpx.AsyncClient, tmp_path: Path) -> None:
    item = (
        await install(
            http,
            tmp_path,
            icon="icon.svg",
            files={"icon.svg": "<svg/>", "frontend/font.woff2": "x", "frontend/data.json": "{}"},
        )
    )["plugin"]
    prefix = f"/v1/ext-assets/{PID}/{item['revision']}"
    js = await http.get(f"{prefix}/frontend/index.js")
    assert js.status_code == 200 and js.text == INDEX_JS
    assert js.headers["content-type"].startswith("text/javascript")
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert (await http.get(item["entry_url"])).text == INDEX_JS
    assert (await http.get(item["style_urls"][0])).headers["content-type"].startswith("text/css")
    for name, mime in (
        ("frontend/data.json", "application/json"),
        ("frontend/font.woff2", "font/woff2"),
        ("README.md", "text/markdown"),
    ):
        assert (await http.get(f"{prefix}/{name}")).headers["content-type"].startswith(mime), name

    # unknown revision / plugin / file, and no way out of the plugin directory
    assert (
        await http.get(f"/v1/ext-assets/{PID}/{item['revision'] + 1}/frontend/index.js")
    ).status_code == 404
    assert (await http.get("/v1/ext-assets/nope.nope/1/frontend/index.js")).status_code == 404
    assert (await http.get(f"{prefix}/frontend/missing.js")).status_code == 404
    assert (await http.get(f"{prefix}/frontend")).status_code == 404
    for escape in (
        "..%2f..%2finstalled.json",
        "frontend/..%2f..%2f..%2finstalled.json",
        "%2fetc%2fpasswd",
    ):
        assert (await http.get(f"{prefix}/{escape}")).status_code == 404, escape


async def test_dev_assets_are_not_cached_and_follow_the_directory(
    http: httpx.AsyncClient, tmp_path: Path
) -> None:
    dev = plugin_src(tmp_path, name="dev")
    item = (await http.post(f"{BASE}/dev-link", json={"path": str(dev)})).json()["plugin"]
    response = await http.get(item["entry_url"])
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    assert response.text == INDEX_JS


# ---- cloud deployments -----------------------------------------------------------------


async def test_every_route_is_refused_on_a_cloud_deployment(
    http: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "deployment_type", "cloud")
    calls = [
        ("GET", BASE, None),
        ("GET", f"{BASE}/watch", None),
        ("POST", f"{BASE}/safe-mode", {"enabled": True}),
        ("POST", f"{BASE}/inspect", {"source_path": "x"}),
        ("POST", f"{BASE}/install", {"source_path": "x"}),
        ("POST", f"{BASE}/dev-link", {"path": "x"}),
        ("POST", f"{BASE}/validate", {"path": "x"}),
        ("POST", f"{BASE}/pack", {"path": "x"}),
        ("POST", f"{BASE}/{PID}/reload", None),
        ("POST", f"{BASE}/{PID}/enable", None),
        ("DELETE", f"{BASE}/{PID}", None),
        ("GET", f"{BASE}/{PID}/logs", None),
        ("POST", f"{BASE}/{PID}/logs", {"level": "info", "message": "x"}),
        ("GET", f"{BASE}/{PID}/config", None),
        ("PUT", f"{BASE}/{PID}/config", {"values": {}}),
        ("GET", f"{BASE}/{PID}/storage", None),
        ("PUT", f"{BASE}/{PID}/storage/k", {"value": 1}),
        ("GET", f"{BASE}/{PID}/automations", None),
        ("POST", f"{BASE}/{PID}/automations/x/run", {}),
        ("GET", f"/v1/ext-assets/{PID}/1/frontend/index.js", None),
    ]
    for method, path, body in calls:
        response = await http.request(method, path, json=body)
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"]["code"] == "third_party_unavailable", (method, path)
