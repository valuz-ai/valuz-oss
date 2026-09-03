"""memory tool — the management half (list / clear / settings), mirroring the
Memory settings page. The store and the settings accessors are faked; the
tool's own dispatch, validation and gating are under test."""

# ruff: noqa: I001  (kernel bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

import valuz_agent.infra.db as infra_db
import valuz_agent.modules.memory.tools as t
import valuz_agent.modules.settings.preferences as prefs


class FakeStore:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str | None], list[str]] = {
            ("user", None): ["likes concise answers"],
            ("global", None): ["prefers zh-CN"],
            ("project", "p1"): ["deadline friday"],
        }
        self.cleared: list[tuple[str, str | None]] = []

    def read_entries(
        self, user_id: str, target: str, *, project_id: str | None = None
    ) -> list[str]:
        return list(self.entries.get((target, project_id), []))

    def usage_for(self, entries: list[str], target: str) -> str:
        return f"{len(entries)} entries"

    def clear(self, user_id: str, target: str, *, project_id: str | None = None) -> None:
        self.cleared.append((target, project_id))
        self.entries[(target, project_id)] = []


def _const(value: Any):  # noqa: ANN202
    async def _f(*_a: Any, **_k: Any) -> Any:
        return value

    return _f


def _setup(monkeypatch, project_id: str | None = "p1") -> FakeStore:  # noqa: ANN001
    store = FakeStore()
    monkeypatch.setattr(t, "memory_store", store)
    monkeypatch.setattr(t, "_resolve_project_id", _const(project_id))

    @asynccontextmanager
    async def _uow(**_k: Any):
        yield object()

    settings = {"enabled": True, "auto_extract": False, "custom_instructions": "focus on deadlines"}
    monkeypatch.setattr(infra_db, "async_unit_of_work", _uow)
    monkeypatch.setattr(prefs, "get_memory_enabled", _const(settings["enabled"]))
    monkeypatch.setattr(prefs, "get_memory_auto_extract", _const(settings["auto_extract"]))
    monkeypatch.setattr(
        prefs, "get_memory_custom_instructions", _const(settings["custom_instructions"])
    )
    writes: list[tuple[str, Any]] = []

    async def _set_enabled(db: Any, value: bool, *, user_id: str) -> None:
        writes.append(("enabled", value))

    async def _set_auto(db: Any, value: bool, *, user_id: str) -> None:
        writes.append(("auto_extract", value))

    async def _set_custom(db: Any, value: str, *, user_id: str) -> None:
        writes.append(("custom_instructions", value))

    monkeypatch.setattr(prefs, "set_memory_enabled", _set_enabled)
    monkeypatch.setattr(prefs, "set_memory_auto_extract", _set_auto)
    monkeypatch.setattr(prefs, "set_memory_custom_instructions", _set_custom)
    store.writes = writes  # type: ignore[attr-defined]
    return store


def _run(args: dict[str, Any]) -> Any:
    ctx = HostExecContext(session_id="s1", user_id="owner")
    return asyncio.run(t._memory_handler(args, ctx))


def test_list_returns_every_scope_and_the_settings(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    r = _run({"action": "list"})
    assert not r.is_error, r.content
    body = json.loads(r.content)
    assert body["entries"] == {
        "user": ["likes concise answers"],
        "global": ["prefers zh-CN"],
        "project": ["deadline friday"],
    }
    assert body["settings"]["custom_instructions"] == "focus on deadlines"
    assert body["usage"]["project"] == "1 entries"


def test_list_skips_project_scope_outside_a_project(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch, project_id=None)
    body = json.loads(_run({"action": "list"}).content)
    assert set(body["entries"]) == {"user", "global"}
    r = _run({"action": "list", "target": "project"})
    assert r.is_error and "no project" in r.content


def test_clear_wipes_one_scope(monkeypatch) -> None:  # noqa: ANN001
    store = _setup(monkeypatch)
    r = _run({"action": "clear", "target": "project"})
    assert not r.is_error, r.content
    assert store.cleared == [("project", "p1")]
    assert json.loads(r.content)["entry_count"] == 0
    assert _run({"action": "clear"}).is_error  # target required


def test_settings_reads_and_patches_only_given_fields(monkeypatch) -> None:  # noqa: ANN001
    store = _setup(monkeypatch)
    body = json.loads(_run({"action": "settings"}).content)
    assert body["settings"]["enabled"] is True
    assert store.writes == []  # type: ignore[attr-defined]
    r = _run({"action": "settings", "auto_extract": True, "custom_instructions": ""})
    assert not r.is_error, r.content
    assert store.writes == [("auto_extract", True), ("custom_instructions", "")]  # type: ignore[attr-defined]
    assert _run({"action": "settings", "enabled": "yes"}).is_error


def test_tool_def_declares_the_management_actions() -> None:
    (td,) = t.build_memory_tool_defs()
    assert set(td.parameters["properties"]["action"]["enum"]) == {
        "add",
        "replace",
        "remove",
        "list",
        "clear",
        "settings",
    }
    assert td.parameters["required"] == ["action"]
    assert "action=list" in td.description and "action=settings" in td.description
