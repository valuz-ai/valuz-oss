"""``skill_library`` tool — dispatch to ``SkillLibraryService`` (faked),
validation, and the two-phase import (preview → confirm) flow."""

# ruff: noqa: I001  (kernel bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

import valuz_agent.integrations.tools_skill_library as t


class FakeSkillService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def list_catalog(self, user_id: str, project_id: str, **_k: Any) -> Any:
        self.calls.append(("list", project_id))
        return SimpleNamespace(
            project_id=project_id,
            skills=[
                SimpleNamespace(id="user:a", name="A", library_enabled=True),
                SimpleNamespace(id="user:b", name="B", library_enabled=False),
            ],
        )

    async def get_skill_detail(
        self, user_id: str, skill_id: str, project_id: str | None = None
    ) -> Any:
        if skill_id == "user:missing":
            raise KeyError(skill_id)
        return SimpleNamespace(id=skill_id, name="A")

    async def create_skill(self, user_id: str, payload: Any) -> Any:
        self.calls.append(("create", payload))
        return SimpleNamespace(id=f"{payload.target_scope}:{payload.name}", name=payload.name)

    async def update_skill(
        self, user_id: str, skill_id: str, payload: Any, project_id: str | None = None
    ) -> Any:
        self.calls.append(("update", (skill_id, payload)))
        return SimpleNamespace(id=skill_id, name=payload.name or "A")

    async def set_library_enabled(self, user_id: str, skill_id: str, enabled: bool) -> Any:
        self.calls.append(("enabled", (skill_id, enabled)))
        return SimpleNamespace(id=skill_id, library_enabled=enabled)

    async def delete_skill(
        self, user_id: str, skill_id: str, project_id: str | None = None, mode: str = "dry_run"
    ) -> Any:
        self.calls.append(("delete", (skill_id, mode)))
        return None if mode == "confirm" else SimpleNamespace(affected_projects=["p1"], count=1)

    async def import_url_preview(
        self, user_id: str, *, url: str, target_scope: str, project_id: str | None
    ) -> Any:
        self.calls.append(("import_url", url))
        return SimpleNamespace(
            preview_id="pv-1",
            name="first",
            skills=[
                SimpleNamespace(preview_id="pv-1", name="first"),
                SimpleNamespace(preview_id="pv-2", name="second"),
            ],
        )

    async def confirm_url_import(self, user_id: str, payload: Any) -> Any:
        self.calls.append(("confirm_url", payload))
        return SimpleNamespace(id=f"{payload.target_scope}:{payload.preview_id}")

    async def list_versions(self, user_id: str, skill_id: str) -> Any:
        return SimpleNamespace(skill_id=skill_id, versions=[{"revision_id": "r1", "version_no": 1}])

    async def restore_version(self, user_id: str, skill_id: str, revision_id: str) -> Any:
        self.calls.append(("restore", (skill_id, revision_id)))
        return SimpleNamespace(revision_id=revision_id, version_no=2)


def _setup(monkeypatch) -> FakeSkillService:  # noqa: ANN001
    svc = FakeSkillService()

    async def _run_with(user_id: str, fn: Any) -> Any:
        return await fn(svc)

    monkeypatch.setattr(t, "run_with_skill_service", _run_with)
    return svc


def _run(args: dict[str, Any]) -> Any:
    return asyncio.run(t._handler(args, HostExecContext(session_id="s1", user_id="owner")))


def test_list_filters_by_library_switch(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    body = json.loads(_run({"action": "list"}).content)
    assert [s["id"] for s in body["skills"]] == ["user:a", "user:b"]
    body = json.loads(_run({"action": "list", "library_enabled": False}).content)
    assert [s["id"] for s in body["skills"]] == ["user:b"]


def test_validation_and_not_found(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    assert _run({"action": "frob"}).is_error
    assert _run({"action": "get"}).is_error  # skill_id required
    r = _run({"action": "get", "skill_id": "user:missing"})
    assert r.is_error and "not found" in r.content
    assert _run({"action": "create", "name": "x"}).is_error  # body required
    assert _run(
        {"action": "create", "name": "x", "instructions_markdown": "# x", "target_scope": "project"}
    ).is_error


def test_create_update_enable_disable(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    body = json.loads(
        _run(
            {
                "action": "create",
                "name": "summ",
                "instructions_markdown": "# summ",
                "description": "d",
            }
        ).content
    )
    assert body["skill"]["id"] == "user:summ"
    payload = next(c[1] for c in svc.calls if c[0] == "create")
    assert payload.target_scope == "user" and payload.instructions_markdown == "# summ"
    assert _run({"action": "update", "skill_id": "user:summ"}).is_error  # nothing to change
    _run({"action": "update", "skill_id": "user:summ", "description": "new"})
    _, (sid, upd) = next(c for c in svc.calls if c[0] == "update")
    assert sid == "user:summ" and upd.description == "new" and upd.name is None
    _run({"action": "disable", "skill_id": "user:summ"})
    _run({"action": "enable", "skill_id": "user:summ"})
    assert [c[1] for c in svc.calls if c[0] == "enabled"] == [
        ("user:summ", False),
        ("user:summ", True),
    ]


def test_delete_is_two_phase(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    body = json.loads(_run({"action": "delete", "skill_id": "user:a"}).content)
    assert body["would_affect"]["affected_projects"] == ["p1"]
    body = json.loads(_run({"action": "delete", "skill_id": "user:a", "confirm": True}).content)
    assert body["deleted"] == "user:a"
    assert [c[1] for c in svc.calls if c[0] == "delete"] == [
        ("user:a", "dry_run"),
        ("user:a", "confirm"),
    ]


def test_import_url_previews_then_confirms_every_candidate(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    body = json.loads(
        _run({"action": "import_url", "url": "https://github.com/o/r/tree/main/skills"}).content
    )
    assert [c["preview_id"] for c in body["candidates"]] == ["pv-1", "pv-2"]
    assert not any(c[0] == "confirm_url" for c in svc.calls)
    body = json.loads(
        _run(
            {
                "action": "import_url",
                "url": "https://github.com/o/r",
                "confirm": True,
                "add_to_project": True,
                "project_id": "p1",
            }
        ).content
    )
    assert [s["id"] for s in body["imported"]] == ["user:pv-1", "user:pv-2"]
    payloads = [c[1] for c in svc.calls if c[0] == "confirm_url"]
    assert all(p.add_to_project and p.project_id == "p1" for p in payloads)
    body = json.loads(
        _run({"action": "import_confirm", "preview_id": "pv-2", "name": "Second"}).content
    )
    assert body["skill"]["id"] == "user:pv-2"
    assert svc.calls[-1][1].name == "Second"


def test_versions_and_restore(monkeypatch) -> None:  # noqa: ANN001
    svc = _setup(monkeypatch)
    body = json.loads(_run({"action": "versions", "skill_id": "user:a"}).content)
    assert body["versions"]["versions"][0]["revision_id"] == "r1"
    body = json.loads(
        _run({"action": "restore", "skill_id": "user:a", "revision_id": "r1"}).content
    )
    assert body["restored"]["version_no"] == 2 and ("restore", ("user:a", "r1")) in svc.calls


def test_tool_def_shape() -> None:
    (td,) = t.build_skill_library_tool_defs()
    assert td.name == "skill_library" and td.parameters["required"] == ["action"]
    assert "import_url" in td.parameters["properties"]["action"]["enum"]
