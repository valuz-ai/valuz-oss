"""``project`` tool — validation, THIS-project gating and dispatch to the
project service (faked), mirroring the Projects page operations."""

# ruff: noqa: I001  (kernel bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

import valuz_agent.infra.db as infra_db
import valuz_agent.modules.projects.tools as t


class FakeProjectService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.projects = {"p1": SimpleNamespace(id="p1", name="Research", kind="project")}

    async def list_projects(self, user_id: str) -> list[Any]:
        self.calls.append(("list", user_id))
        return list(self.projects.values())

    async def get_project(self, user_id: str, project_id: str) -> Any:
        self.calls.append(("get", project_id))
        return self.projects[project_id]  # KeyError → not found, like the service

    async def create_project(self, user_id: str, name: str, root_path: str | None = None) -> Any:
        self.calls.append(("create", (name, root_path)))
        return SimpleNamespace(id="p2", name=name, kind="project")

    async def rename_project(self, user_id: str, project_id: str, name: str) -> Any:
        self.calls.append(("rename", (project_id, name)))
        return SimpleNamespace(id=project_id, name=name, kind="project")

    async def preview_delete(self, user_id: str, project_id: str) -> Any:
        self.calls.append(("preview", project_id))
        return {
            "session_count": 3,
            "doc_binding_count": 1,
            "schedule_count": 0,
            "skill_config_count": 2,
        }

    async def delete_project(self, user_id: str, project_id: str) -> None:
        self.calls.append(("delete", project_id))

    async def set_default_lead(self, user_id: str, project_id: str, agent_slug: str | None) -> Any:
        self.calls.append(("lead", (project_id, agent_slug)))
        return SimpleNamespace(
            id=project_id, name="Research", kind="project", default_lead=agent_slug
        )


def _const(value: Any):  # noqa: ANN202
    async def _f(*_a: Any, **_k: Any) -> Any:
        return value

    return _f


def _setup(monkeypatch, this_project: str | None = "p1") -> FakeProjectService:  # noqa: ANN001
    svc = FakeProjectService()

    @asynccontextmanager
    async def _uow(**_k: Any):
        yield object()

    monkeypatch.setattr(infra_db, "async_unit_of_work", _uow)
    monkeypatch.setattr(t, "_full_project_service", lambda _db: svc)
    monkeypatch.setattr(t, "_resolve_real_project_id", _const(this_project))
    return svc


def _run(args: dict[str, Any], workspace: str = "") -> Any:
    ctx = HostExecContext(session_id="s1", user_id="owner", workspace=workspace)
    return asyncio.run(t._project_handler(args, ctx))


def test_list_and_create(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    body = json.loads(_run({"action": "list"}).content)
    assert [p["id"] for p in body["projects"]] == ["p1"]
    r = _run({"action": "create", "name": "  New  ", "root_path": "/tmp/x"})
    assert not r.is_error, r.content
    assert ("create", ("New", "/tmp/x")) in svc.calls
    assert _run({"action": "create"}).is_error


def test_this_project_is_the_default_target(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    r = _run({"action": "rename", "name": "Renamed"})
    assert not r.is_error, r.content
    assert ("rename", ("p1", "Renamed")) in svc.calls
    body = json.loads(_run({"action": "delete_preview"}).content)
    assert body["would_remove"]["session_count"] == 3


def test_mutations_refuse_chat_sessions_without_explicit_id(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch, this_project=None)
    r = _run({"action": "rename", "name": "x"})
    assert r.is_error and "no project" in r.content
    assert not any(c[0] == "rename" for c in svc.calls)
    # An explicit id still works (the user named a project from list).
    r = _run({"action": "get", "project_id": "p1"})
    assert not r.is_error


def test_delete_needs_an_explicit_id_and_reports_not_found(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    r = _run({"action": "delete"})
    assert r.is_error and "explicit" in r.content
    r = _run({"action": "delete", "project_id": "p1"})
    assert not r.is_error and ("delete", "p1") in svc.calls
    r = _run({"action": "get", "project_id": "missing"})
    assert r.is_error and "not found" in r.content


def test_set_default_lead_clears_when_slug_omitted(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    _run({"action": "set_default_lead", "agent_slug": "analyst"})
    _run({"action": "set_default_lead"})
    assert [c for c in svc.calls if c[0] == "lead"] == [
        ("lead", ("p1", "analyst")),
        ("lead", ("p1", None)),
    ]


def test_export_writes_the_pack_into_the_workspace(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    monkeypatch.setattr(t, "_export_project", _const(b"PK\x03\x04pack"))
    body = json.loads(_run({"action": "export"}, workspace=str(tmp_path)).content)
    assert body["ok"] is True and body["bytes"] == 8
    assert Path(body["path"]).read_bytes() == b"PK\x03\x04pack"
    assert Path(body["path"]).name == "p1.valuzpack"


def test_tool_def_shape() -> None:
    (td,) = t.build_project_tool_defs()
    assert td.name == "project" and td.parameters["required"] == ["action"]
    assert "delete_preview" in td.parameters["properties"]["action"]["enum"]
