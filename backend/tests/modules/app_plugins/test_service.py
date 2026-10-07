"""AppPluginService: install paths, version rules, statuses, dev link, watch, uninstall."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.modules.app_plugins.helpers import INDEX_JS, build_plugin, zip_dir
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.app_plugins import archive, logs
from valuz_agent.modules.app_plugins import service as service_mod
from valuz_agent.modules.app_plugins.errors import (
    AppPluginsUnavailable,
    InvalidManifest,
    InvalidSource,
    NotDevPlugin,
    PluginNotFound,
    Sha256Mismatch,
    VersionNotNewer,
)
from valuz_agent.modules.app_plugins.service import AppPluginService
from valuz_agent.ports.app_plugins import PolicyVerdict
from valuz_agent.ports.extensions import ext

USER = "user-1"
Svc = AppPluginService


def src(tmp_path: Path, version: str = "1.0.0", name: str = "src", **over: Any) -> Path:
    return build_plugin(tmp_path / name, version=version, **over)


async def plugin(svc: Svc, plugin_id: str = "acme.dashboard") -> dict[str, Any]:
    listing = await svc.list(USER)
    return next(p for p in listing["plugins"] if p["id"] == plugin_id)


# ---- install ---------------------------------------------------------------------------


async def test_install_a_directory(svc: Svc, tmp_path: Path, data_root: Path) -> None:
    root = src(tmp_path)
    result = await svc.install(USER, {"source_path": str(root)})
    item = result["plugin"]
    assert result["updated_from"] is None
    assert item["id"] == "acme.dashboard" and item["version"] == "1.0.0"
    assert item["status"] == "enabled" and item["enabled"] is True and item["status_reason"] is None
    assert item["name"] == {"en-US": "Acme Dashboard", "zh-CN": "Acme 看板"}
    assert item["publisher"]["name"] == "Acme"
    assert item["permissions"] == ["projects:read", "storage"]
    assert item["engines"] == {"valuz-plugin-api": "^1.0.0"}
    assert item["source"] == {"kind": "file", "path": str(root.resolve())}
    revision = item["revision"]
    assert revision >= 1
    assert item["entry_url"] == f"/v1/app-plugin-assets/acme.dashboard/{revision}/frontend/index.js"
    assert item["style_urls"] == [
        f"/v1/app-plugin-assets/acme.dashboard/{revision}/frontend/index.css"
    ]
    assert item["locales"] == {"en-US": {"title": "Dash"}, "zh-CN": {"title": "看板"}}
    assert item["dev_path"] is None and item["config_schema"] is None
    assert isinstance(item["installed_at"], int)
    installed = data_root / "app-plugins" / "acme.dashboard" / "1.0.0"
    assert (installed / "frontend" / "index.js").read_text(encoding="utf-8") == INDEX_JS
    # the sha256 is that of the deterministic zip ``pack`` produces for the directory
    packed = svc.pack(str(root), str(tmp_path / "out"))
    assert item["sha256"] == packed["sha256"]
    registry = json.loads((data_root / "app-plugins" / "installed.json").read_text())
    assert registry["version"] == 1 and registry["generation"] >= 1
    assert registry["plugins"]["acme.dashboard"]["source"]["kind"] == "file"


async def test_install_a_zip_file_and_a_wrapped_zip(svc: Svc, tmp_path: Path) -> None:
    root = src(tmp_path)
    plain = zip_dir(root, tmp_path / "plain.zip")
    result = await svc.install(USER, {"source_path": str(plain)})
    assert result["plugin"]["sha256"] == hashlib.sha256(plain.read_bytes()).hexdigest()
    wrapped_src = build_plugin(tmp_path / "w", "acme.wrapped", "1.0.0")
    wrapped = zip_dir(wrapped_src, tmp_path / "wrapped.zip", wrapper="acme-wrapped")
    assert (await svc.install(USER, {"source_path": str(wrapped)}))["plugin"]["id"] == (
        "acme.wrapped"
    )


async def test_install_from_an_https_url(
    svc: Svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = zip_dir(src(tmp_path), tmp_path / "p.zip").read_bytes()
    monkeypatch.setattr(
        archive,
        "make_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        ),
    )
    result = await svc.install(USER, {"url": "https://example.com/acme.zip"})
    assert result["plugin"]["source"] == {"kind": "url", "url": "https://example.com/acme.zip"}
    assert result["plugin"]["sha256"] == hashlib.sha256(body).hexdigest()


async def test_install_needs_exactly_one_source(svc: Svc, tmp_path: Path) -> None:
    with pytest.raises(InvalidSource):
        await svc.install(USER, {})
    with pytest.raises(InvalidSource):
        await svc.install(USER, {"source_path": "a", "url": "https://x/y.zip"})
    with pytest.raises(InvalidSource, match="not found"):
        await svc.install(USER, {"source_path": str(tmp_path / "nope")})


async def test_invalid_packages_are_refused_with_the_list_of_problems(
    svc: Svc, tmp_path: Path
) -> None:
    root = build_plugin(tmp_path / "bad")
    manifest = json.loads((root / "valuz-plugin.json").read_text())
    manifest["id"] = "valuz.x"
    manifest["backend"] = {"runtime": "node"}
    (root / "valuz-plugin.json").write_text(json.dumps(manifest))
    with pytest.raises(InvalidManifest) as raised:
        await svc.install(USER, {"source_path": str(root)})
    assert raised.value.code == "invalid_manifest" and raised.value.status_code == 400
    assert any("reserved" in e for e in raised.value.errors)
    assert any("B-level" in e for e in raised.value.errors)
    assert (await svc.list(USER))["plugins"] == []
    # nothing is left in the scratch area
    assert not any((fs_registry.app_plugins_root() / ".tmp").iterdir())


async def test_expected_sha256(svc: Svc, tmp_path: Path) -> None:
    zip_path = zip_dir(src(tmp_path), tmp_path / "p.zip")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with pytest.raises(Sha256Mismatch) as raised:
        await svc.install(USER, {"source_path": str(zip_path)}, expected_sha256="0" * 64)
    assert raised.value.status_code == 422 and raised.value.code == "sha256_mismatch"
    assert (await svc.list(USER))["plugins"] == []
    await svc.install(USER, {"source_path": str(zip_path)}, expected_sha256=f"sha256:{sha.upper()}")
    assert (await plugin(svc))["sha256"] == sha


async def test_install_archive_records_the_catalog_source(svc: Svc, tmp_path: Path) -> None:
    zip_path = zip_dir(src(tmp_path), tmp_path / "p.zip")
    source = {"kind": "catalog", "scope": "org", "item_id": "item-1"}
    result = await svc.install_archive(USER, str(zip_path), source=source)
    assert result["plugin"]["source"] == source


# ---- versions --------------------------------------------------------------------------


async def test_same_id_installs_only_a_higher_version(
    svc: Svc, tmp_path: Path, data_root: Path
) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path, "1.0.0"))})
    first = await plugin(svc)
    for version in ("1.0.0", "0.9.0"):
        with pytest.raises(VersionNotNewer) as raised:
            await svc.install(USER, {"source_path": str(src(tmp_path, version, name="again"))})
        assert raised.value.status_code == 409 and raised.value.code == "version_not_newer"
    result = await svc.install(USER, {"source_path": str(src(tmp_path, "1.1.0", name="newer"))})
    assert result["updated_from"] == "1.0.0"
    assert result["plugin"]["version"] == "1.1.0"
    assert result["plugin"]["revision"] > first["revision"]
    # the superseded directory stays until the next start ...
    plugin_dir = data_root / "app-plugins" / "acme.dashboard"
    assert sorted(p.name for p in plugin_dir.iterdir()) == ["1.0.0", "1.1.0"]
    # ... and the start-up cleanup removes it
    from valuz_agent.modules.app_plugins.maintenance import cleanup_superseded_versions

    old_scratch = data_root / "app-plugins" / ".tmp" / "stale"
    new_scratch = data_root / "app-plugins" / ".tmp" / "running"
    old_scratch.mkdir(parents=True)
    new_scratch.mkdir(parents=True)
    os.utime(old_scratch, (1, 1))
    cleanup_superseded_versions()
    assert sorted(p.name for p in plugin_dir.iterdir()) == ["1.1.0"]
    assert not old_scratch.exists() and new_scratch.exists()  # only dead scratch trees go


async def test_inspect_reports_the_existing_version_and_added_permissions(
    svc: Svc, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path, "1.0.0"))})
    newer = src(
        tmp_path, "1.2.0", name="newer", permissions=["projects:read", "storage", "notifications"]
    )
    info = await svc.inspect({"source_path": str(newer)}, user_id=USER)
    assert info["existing"] == {"version": "1.0.0"}
    assert info["added_permissions"] == ["notifications"]
    assert info["permissions"] == ["projects:read", "storage", "notifications"]
    assert info["errors"] == [] and info["has_backend"] is False
    assert info["manifest"]["version"] == "1.2.0" and len(info["sha256"]) == 64
    fresh = await svc.inspect({"source_path": str(src(tmp_path, name="fresh", plugin_id="acme.b"))})
    assert fresh["existing"] is None and fresh["added_permissions"] == []


async def test_inspect_lists_validation_errors_without_failing(svc: Svc, tmp_path: Path) -> None:
    root = build_plugin(tmp_path / "bad")
    (root / "frontend" / "index.js").unlink()
    info = await svc.inspect({"source_path": str(root)})
    assert any("frontend.entry" in e for e in info["errors"])


# ---- statuses --------------------------------------------------------------------------


async def test_incompatible_engines(svc: Svc, tmp_path: Path) -> None:
    root = src(tmp_path, engines={"valuz-plugin-api": "^2.0.0"})
    await svc.install(USER, {"source_path": str(root)})
    item = await plugin(svc)
    assert item["status"] == "incompatible"
    assert "^2.0.0" in item["status_reason"] and "1.0.0" in item["status_reason"]


async def test_requires_unmet(svc: Svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_mod, "running_edition", lambda: "oss")
    monkeypatch.setattr(service_mod, "deployment_type", lambda: "local")
    monkeypatch.setattr(service_mod, "active_capabilities", lambda: frozenset({"oss.knowledge"}))

    async def connectors(user_id: str) -> set[str]:
        assert user_id == USER
        return {"other"}

    monkeypatch.setattr(service_mod, "enabled_connector_slugs", connectors)
    requires = [
        "edition:finance",
        "deployment:cloud",
        "connector:acme-data",
        "capability:automations",
        "capability:knowledge",
        "deployment:local",
        "edition:oss",
    ]
    await svc.install(USER, {"source_path": str(src(tmp_path, requires=requires))})
    item = await plugin(svc)
    assert item["status"] == "requires-unmet"
    assert item["requires"] == requires
    assert item["unmet_requires"] == [
        "edition:finance",
        "deployment:cloud",
        "connector:acme-data",
        "capability:automations",
    ]
    assert "edition:finance" in item["status_reason"]

    async def with_connector(user_id: str) -> set[str]:
        return {"acme-data"}

    monkeypatch.setattr(service_mod, "enabled_connector_slugs", with_connector)
    monkeypatch.setattr(service_mod, "running_edition", lambda: "finance")
    monkeypatch.setattr(service_mod, "deployment_type", lambda: "local")
    monkeypatch.setattr(service_mod, "active_capabilities", lambda: frozenset({"oss.automations"}))
    item = await plugin(svc)
    assert item["unmet_requires"] == ["deployment:cloud", "capability:knowledge", "edition:oss"]


async def test_blocked_by_policy(svc: Svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    class Deny:
        async def evaluate(self, user_id: str, plugin: Any) -> PolicyVerdict:
            seen.append(dict(plugin))
            return PolicyVerdict(allowed=False, reason="blocked by org policy")

    monkeypatch.setattr(ext, "app_plugin_policy", Deny())
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    item = await plugin(svc)
    assert item["status"] == "blocked" and item["status_reason"] == "blocked by org policy"
    assert seen and seen[0]["id"] == "acme.dashboard" and seen[0]["source"]["kind"] == "file"


async def test_a_failing_policy_fails_open(
    svc: Svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        async def evaluate(self, user_id: str, plugin: Any) -> PolicyVerdict:
            raise RuntimeError("control plane down")

    monkeypatch.setattr(ext, "app_plugin_policy", Broken())
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    assert (await plugin(svc))["status"] == "enabled"


async def test_enable_and_disable(svc: Svc, tmp_path: Path, data_root: Path) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    before = svc._store.generation()
    off = (await svc.set_enabled(USER, "acme.dashboard", False))["plugin"]
    assert off["status"] == "disabled" and off["enabled"] is False
    assert svc._store.generation() > before
    assert json.loads((data_root / "plugins.json").read_text())["disabled"] == ["acme.dashboard"]
    on = (await svc.set_enabled(USER, "acme.dashboard", True))["plugin"]
    assert on["status"] == "enabled" and on["enabled"] is True
    with pytest.raises(PluginNotFound):
        await svc.set_enabled(USER, "nope.nope", True)
    await svc.install(USER, {"source_path": str(src(tmp_path, "1.1.0", name="n"))}, enable=False)
    assert (await plugin(svc))["status"] == "disabled"


async def test_a_damaged_install_is_broken(svc: Svc, tmp_path: Path, data_root: Path) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    (data_root / "app-plugins" / "acme.dashboard" / "1.0.0" / "frontend" / "index.js").unlink()
    item = await plugin(svc)
    assert item["status"] == "broken" and "frontend.entry" in item["status_reason"]
    # a broken install can be repaired by installing the same version again
    again = await svc.install(USER, {"source_path": str(src(tmp_path, name="again"))})
    assert again["plugin"]["status"] == "enabled"


async def test_safe_mode(svc: Svc, monkeypatch: pytest.MonkeyPatch) -> None:
    listing = await svc.list(USER)
    assert listing["safe_mode"] is False and listing["safe_mode_reason"] is None
    assert listing["api_version"] == "1.0.0"
    before = listing["generation"]
    assert await svc.set_safe_mode(True, "crashed twice") == {"safe_mode": True}
    listing = await svc.list(USER)
    assert listing["safe_mode"] is True and listing["safe_mode_reason"] == "crashed twice"
    assert listing["generation"] > before
    assert await svc.set_safe_mode(False) == {"safe_mode": False}
    monkeypatch.setenv("VALUZ_APP_PLUGINS_SAFE_MODE", "1")
    assert (await svc.list(USER))["safe_mode"] is True


async def test_cloud_deployments_are_refused(
    svc: Svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "deployment_type", "cloud")
    for call in (
        svc.list(USER),
        svc.install(USER, {"source_path": str(tmp_path)}),
        svc.inspect({"source_path": str(tmp_path)}),
        svc.dev_link(USER, str(tmp_path)),
        svc.watch(0, 0),
        svc.uninstall(USER, "x.y"),
    ):
        with pytest.raises(AppPluginsUnavailable) as raised:
            await call
        assert raised.value.status_code == 403 and raised.value.code == "app_plugins_unavailable"
    with pytest.raises(AppPluginsUnavailable):
        svc.asset_path("x.y", 1, "a.js")


# ---- dev link --------------------------------------------------------------------------


def touch(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


async def test_dev_link_replaces_an_install_and_follows_file_changes(
    svc: Svc, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path, "0.5.0", name="released"))})
    released = await plugin(svc)
    dev = src(tmp_path, "0.1.0", name="dev")
    linked = (await svc.dev_link(USER, str(dev)))["plugin"]
    assert linked["source"] == {"kind": "dev", "path": str(dev.resolve())}
    assert linked["dev_path"] == str(dev.resolve()) and linked["sha256"] is None
    assert linked["version"] == "0.1.0" and linked["revision"] > released["revision"]
    assert linked["status"] == "enabled"

    # nothing changed -> same revision, same generation
    generation = svc._store.generation()
    assert (await plugin(svc))["revision"] == linked["revision"]
    assert svc._store.generation() == generation

    touch(dev / "frontend" / "index.js", INDEX_JS + "// v2\n")
    changed = await plugin(svc)
    assert changed["revision"] == linked["revision"] + 1
    assert changed["entry_url"].split("/")[4] == str(changed["revision"])
    assert svc._store.generation() == generation + 1
    # listing again does not bump a second time
    assert (await plugin(svc))["revision"] == changed["revision"]

    touch(dev / "frontend" / "index.css", "a{}")
    assert (await plugin(svc))["revision"] == changed["revision"] + 1
    touch(
        dev / "valuz-plugin.json", (dev / "valuz-plugin.json").read_text().replace("0.1.0", "0.2.0")
    )
    item = await plugin(svc)
    assert item["version"] == "0.2.0"


async def test_reload_bumps_the_revision(svc: Svc, tmp_path: Path) -> None:
    dev = src(tmp_path, name="dev")
    linked = (await svc.dev_link(USER, str(dev)))["plugin"]
    reloaded = (await svc.reload("acme.dashboard", user_id=USER))["plugin"]
    assert reloaded["revision"] == linked["revision"] + 1
    released = src(tmp_path, "2.0.0", name="rel", plugin_id="acme.released")
    await svc.install(USER, {"source_path": str(released)})
    with pytest.raises(NotDevPlugin):
        await svc.reload("acme.released")
    with pytest.raises(PluginNotFound):
        await svc.reload("nope.nope")


async def test_dev_link_rejects_a_bad_directory(svc: Svc, tmp_path: Path) -> None:
    with pytest.raises(InvalidSource):
        await svc.dev_link(USER, str(tmp_path / "missing"))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InvalidManifest):
        await svc.dev_link(USER, str(empty))


# ---- watch -----------------------------------------------------------------------------


async def test_watch_times_out_with_the_current_generation(svc: Svc) -> None:
    generation = svc._store.generation()
    started = time.monotonic()
    assert await svc.watch(generation, 0.3) == {"generation": generation}
    assert time.monotonic() - started >= 0.25
    # an old ``since`` answers immediately
    assert (await svc.watch(generation - 1, 5))["generation"] == generation


async def test_watch_wakes_on_install_and_on_a_dev_file_change(svc: Svc, tmp_path: Path) -> None:
    since = svc._store.generation()
    waiter = asyncio.create_task(svc.watch(since, 5))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    dev = src(tmp_path, name="dev")
    await svc.dev_link(USER, str(dev))
    assert (await asyncio.wait_for(waiter, 3))["generation"] > since

    since = svc._store.generation()
    await plugin(svc)  # records the dev file signature
    waiter = asyncio.create_task(svc.watch(since, 5))
    await asyncio.sleep(0.05)
    touch(dev / "frontend" / "index.js", INDEX_JS + "// changed\n")
    result = await asyncio.wait_for(waiter, 4)  # picked up by the ~1s poll
    assert result["generation"] > since


# ---- uninstall -------------------------------------------------------------------------


async def test_uninstall_removes_the_package_but_keeps_data(
    svc: Svc, tmp_path: Path, data_root: Path
) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    fs_registry.app_plugin_data_dir("acme.dashboard").joinpath("keep.txt").write_text("x")
    logs.append_log("acme.dashboard", "info", "hello", "backend")
    await svc.set_enabled(USER, "acme.dashboard", False)
    result = await svc.uninstall(USER, "acme.dashboard")
    assert result == {"removed": True, "automations_deleted": 0}
    assert (await svc.list(USER))["plugins"] == []
    assert not (data_root / "app-plugins" / "acme.dashboard").exists()
    assert (data_root / "app-plugin-data" / "acme.dashboard" / "keep.txt").is_file()
    assert (data_root / "logs" / "app-plugins" / "acme.dashboard.log").is_file()
    assert "acme.dashboard" not in json.loads((data_root / "plugins.json").read_text())["disabled"]
    with pytest.raises(PluginNotFound):
        await svc.uninstall(USER, "acme.dashboard")


async def test_uninstalling_a_dev_link_leaves_the_directory(svc: Svc, tmp_path: Path) -> None:
    dev = src(tmp_path, name="dev")
    await svc.dev_link(USER, str(dev))
    await svc.uninstall(USER, "acme.dashboard")
    assert (dev / "valuz-plugin.json").is_file()
    assert (await svc.list(USER))["plugins"] == []


# ---- validate / pack -------------------------------------------------------------------


def test_validate_and_pack(svc: Svc, tmp_path: Path) -> None:
    root = src(tmp_path)
    report = svc.validate(str(root))
    assert report["ok"] is True and report["errors"] == []
    assert report["manifest"]["id"] == "acme.dashboard"
    packed = svc.pack(str(root))
    assert packed["path"] == str(root.resolve() / "dist" / "acme.dashboard-1.0.0.zip")
    assert packed["size"] == Path(packed["path"]).stat().st_size
    assert packed["sha256"] == hashlib.sha256(Path(packed["path"]).read_bytes()).hexdigest()
    assert svc.pack(str(root), str(tmp_path / "o"))["sha256"] == packed["sha256"]
    assert "valuz-plugin.json" in zipfile.ZipFile(packed["path"]).namelist()


def test_validate_fails_without_the_entry_or_a_manifest(svc: Svc, tmp_path: Path) -> None:
    root = src(tmp_path)
    (root / "frontend" / "index.js").unlink()
    report = svc.validate(str(root))
    assert report["ok"] is False and any("frontend.entry" in e for e in report["errors"])
    assert svc.validate(str(tmp_path / "missing"))["ok"] is False
    with pytest.raises(InvalidManifest):
        svc.pack(str(root))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert svc.validate(str(empty))["manifest"] is None


async def test_files_outside_the_packed_set_warn_in_validate_and_refuse_in_pack(
    svc: Svc, tmp_path: Path
) -> None:
    root = build_plugin(
        tmp_path / "p",
        frontend={"entry": "build/index.js"},
        files={"build/index.js": INDEX_JS},
    )
    report = svc.validate(str(root))
    assert report["ok"] is True
    assert any("outside the packed file set" in w for w in report["warnings"])
    with pytest.raises(InvalidManifest) as raised:
        svc.pack(str(root))
    assert "packed file set is incomplete" in raised.value.message
    assert any("frontend.entry" in e for e in raised.value.errors)
    assert not (root / "dist").exists() or not list((root / "dist").iterdir())
    # installing the directory ships the same packed set, so it is refused too
    with pytest.raises(InvalidManifest):
        await svc.install(USER, {"source_path": str(root)})
    # a dev link reads the directory as is, so it works
    assert (await svc.dev_link(USER, str(root)))["plugin"]["status"] == "enabled"


def test_pack_reports_the_file_list(svc: Svc, tmp_path: Path) -> None:
    packed = svc.pack(str(src(tmp_path)))
    assert packed["id"] == "acme.dashboard" and packed["version"] == "1.0.0"
    assert packed["files"] == [
        "README.md",
        "frontend/index.css",
        "frontend/index.js",
        "locales/en-US.json",
        "locales/zh-CN.json",
        "valuz-plugin.json",
    ]
    assert isinstance(packed["warnings"], list)


def test_validate_and_pack_work_on_a_cloud_deployment(
    svc: Svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "deployment_type", "cloud")
    root = src(tmp_path)
    assert svc.validate(str(root))["ok"] is True
    assert svc.pack(str(root), str(tmp_path / "out"))["path"].endswith("acme.dashboard-1.0.0.zip")


# ---- assets and access -----------------------------------------------------------------


async def test_asset_paths_are_confined_to_the_current_revision(svc: Svc, tmp_path: Path) -> None:
    item = (await svc.install(USER, {"source_path": str(src(tmp_path))}))["plugin"]
    revision = item["revision"]
    file, dev = svc.asset_path("acme.dashboard", revision, "frontend/index.js")
    assert file.read_text(encoding="utf-8") == INDEX_JS and dev is False
    for bad in ("../../installed.json", "frontend/../../x", "/etc/passwd", "frontend", "nope.js"):
        with pytest.raises(PluginNotFound):
            svc.asset_path("acme.dashboard", revision, bad)
    with pytest.raises(PluginNotFound):
        svc.asset_path("acme.dashboard", revision + 1, "frontend/index.js")
    with pytest.raises(PluginNotFound):
        svc.asset_path("nope.nope", 1, "frontend/index.js")


async def test_a_symlink_out_of_a_dev_directory_is_not_served(svc: Svc, tmp_path: Path) -> None:
    dev = src(tmp_path, name="dev")
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    (dev / "frontend" / "leak.txt").symlink_to(secret)
    item = (await svc.dev_link(USER, str(dev)))["plugin"]
    _, is_dev = svc.asset_path("acme.dashboard", item["revision"], "frontend/index.js")
    assert is_dev is True
    with pytest.raises(PluginNotFound):
        svc.asset_path("acme.dashboard", item["revision"], "frontend/leak.txt")


async def test_plugin_access(svc: Svc, tmp_path: Path) -> None:
    assert svc.plugin_access("acme.dashboard") is None
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    assert svc.plugin_access("acme.dashboard") == {"projects:read", "storage"}
    await svc.set_enabled(USER, "acme.dashboard", False)
    assert svc.plugin_access("acme.dashboard") is None
    await svc.set_enabled(USER, "acme.dashboard", True)
    other = src(tmp_path, name="inc", plugin_id="acme.inc", engines={"valuz-plugin-api": "^9.0.0"})
    await svc.install(USER, {"source_path": str(other)})
    assert svc.plugin_access("acme.inc") is None


# ---- logs ------------------------------------------------------------------------------


async def test_logs_newest_last_and_trimmed(svc: Svc, tmp_path: Path, data_root: Path) -> None:
    await svc.install(USER, {"source_path": str(src(tmp_path))})
    for i in range(5):
        svc.write_log("acme.dashboard", "warning" if i == 0 else "info", f"line {i}")
    entries = svc.read_logs("acme.dashboard", 3)["entries"]
    assert [e["message"] for e in entries] == ["line 2", "line 3", "line 4"]
    first = svc.read_logs("acme.dashboard")["entries"][0]
    assert first["level"] == "warn" and first["source"] == "frontend"
    assert isinstance(first["ts"], int)
    with pytest.raises(PluginNotFound):
        svc.write_log("nope.nope", "info", "x")
    # past ~1 MiB the file is cut back to its newest part
    blob = "x" * 4000
    for _ in range(400):
        logs.append_log("acme.dashboard", "info", blob, "backend")
    path = data_root / "logs" / "app-plugins" / "acme.dashboard.log"
    assert path.stat().st_size < 1.3 * logs.MAX_LOG_BYTES
    logs.append_log("acme.dashboard", "info", "last", "audit")
    assert logs.read_log("acme.dashboard", 1)[0] == {
        **logs.read_log("acme.dashboard", 1)[0],
        "message": "last",
        "source": "audit",
    }
