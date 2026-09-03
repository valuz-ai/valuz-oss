"""``plugin`` tool — source resolution (exactly one; GitHub addresses are
fetched and packaged locally), dispatch to ``PluginService`` (faked) and the
page's rules."""

# ruff: noqa: I001  (kernel bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

import valuz_agent.integrations.tools_plugin as t


class FakePluginService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def list_plugins(self, user_id: str) -> list[Any]:
        return [SimpleNamespace(id="pl1", name="demo", enabled=True)]

    async def get_plugin(self, user_id: str, plugin_id: str) -> Any:
        return SimpleNamespace(id=plugin_id, name="demo", enabled=True)

    async def preview(self, user_id: str, **source: Any) -> Any:
        self.calls.append(("preview", source))
        return SimpleNamespace(manifest={"name": "demo"}, members=[], conflicts=[], existing=None)

    async def install(self, user_id: str, *, on_conflict: str = "skip", **source: Any) -> Any:
        self.calls.append(("install", (on_conflict, source)))
        return SimpleNamespace(plugin=SimpleNamespace(id="pl2", name="demo"), status="installed")

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> Any:
        self.calls.append(("enabled", (plugin_id, enabled)))
        return SimpleNamespace(id=plugin_id, enabled=enabled)

    async def update(self, user_id: str, plugin_id: str, *, on_conflict: str = "skip") -> Any:
        self.calls.append(("update", (plugin_id, on_conflict)))
        return SimpleNamespace(status="updated")

    async def uninstall(self, user_id: str, plugin_id: str) -> Any:
        self.calls.append(("uninstall", plugin_id))
        return SimpleNamespace(removed_members=["demo-skill"], kept_members=[])

    async def export_zip(self, user_id: str, plugin_id: str) -> tuple[str, bytes]:
        return f"{plugin_id}.zip", b"PK\x03\x04zip"

    async def memberships(self, user_id: str, kind: str, slugs: list[str]) -> dict[str, list[Any]]:
        self.calls.append(("memberships", (kind, slugs)))
        return {s: [SimpleNamespace(id="pl1", name="demo")] for s in slugs}


class FakeSkills:
    """Only the two importer helpers the plugin tool borrows."""

    def _is_github_url(self, url: str) -> bool:
        return "github.com" in url

    def _fetch_github_tree(self, url: str, staging_dir: Path) -> Path:  # pragma: no cover
        raise AssertionError("network fetch must be mocked")


def _setup(monkeypatch) -> FakePluginService:  # noqa: ANN001
    svc = FakePluginService()

    async def _with(user_id: str, fn: Any) -> Any:
        return await fn(svc, FakeSkills())

    monkeypatch.setattr(t, "_with_plugin_service", _with)
    return svc


def _run(args: dict[str, Any], workspace: str = "") -> Any:
    ctx = HostExecContext(session_id="s1", user_id="owner", workspace=workspace)
    return asyncio.run(t._handler(args, ctx))


def test_install_requires_exactly_one_source(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    assert _run({"action": "install"}).is_error
    r = _run({"action": "install", "path": "/tmp/a.zip", "url": "https://x/a.zip"})
    assert r.is_error and "exactly one" in r.content
    assert svc.calls == []


def test_install_from_path_url_and_market(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    _run({"action": "install", "path": "/tmp/plugin-dir", "on_conflict": "overwrite"})
    _run({"action": "install", "url": "https://files.example/a.zip"})
    _run({"action": "install", "market_item_id": "market:plugin:demo"})
    assert [c[1] for c in svc.calls if c[0] == "install"] == [
        ("overwrite", {"path": "/tmp/plugin-dir"}),
        ("skip", {"url": "https://files.example/a.zip"}),
        ("skip", {"market_item_id": "market:plugin:demo"}),
    ]
    assert _run({"action": "install", "path": "/x", "on_conflict": "ask"}).is_error


def test_github_address_is_fetched_and_packaged_locally(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    fetched: list[str] = []

    def _fetch(skills: Any, url: str) -> bytes:
        assert isinstance(skills, FakeSkills)
        fetched.append(url)
        return b"PK\x03\x04github"

    monkeypatch.setattr(t, "_fetch_github_plugin_zip", _fetch)
    body = json.loads(
        _run({"action": "preview", "url": "https://github.com/o/r/tree/main/plugin"}).content
    )
    assert body["source"].startswith("github:")
    assert fetched == ["https://github.com/o/r/tree/main/plugin"]
    assert svc.calls == [("preview", {"zip_bytes": b"PK\x03\x04github"})]
    body = json.loads(_run({"action": "install", "url": "https://github.com/o/r"}).content)
    assert body["result"]["status"] == "installed"
    assert svc.calls[-1] == ("install", ("skip", {"zip_bytes": b"PK\x03\x04github"}))


def test_switches_update_uninstall_and_memberships(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    assert _run({"action": "enable"}).is_error  # plugin_id required
    _run({"action": "disable", "plugin_id": "pl1"})
    _run({"action": "enable", "plugin_id": "pl1"})
    _run({"action": "update", "plugin_id": "pl1", "on_conflict": "overwrite"})
    body = json.loads(_run({"action": "uninstall", "plugin_id": "pl1"}).content)
    assert body["result"]["removed_members"] == ["demo-skill"]
    assert _run({"action": "memberships", "kind": "skill"}).is_error
    body = json.loads(
        _run({"action": "memberships", "kind": "skill", "slugs": ["demo-skill"]}).content
    )
    assert body["memberships"]["demo-skill"][0]["id"] == "pl1"
    assert [c for c in svc.calls if c[0] in ("enabled", "update", "uninstall")] == [
        ("enabled", ("pl1", False)),
        ("enabled", ("pl1", True)),
        ("update", ("pl1", "overwrite")),
        ("uninstall", "pl1"),
    ]


def test_export_writes_into_the_workspace(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    body = json.loads(
        _run({"action": "export", "plugin_id": "pl1"}, workspace=str(tmp_path)).content
    )
    assert (
        Path(body["path"]).name == "pl1.zip" and Path(body["path"]).read_bytes() == b"PK\x03\x04zip"
    )


def test_tool_def_shape() -> None:
    (td,) = t.build_plugin_tool_defs()
    assert td.name == "plugin" and td.parameters["required"] == ["action"]
    assert td.parameters["properties"]["slugs"]["type"] == "array"
