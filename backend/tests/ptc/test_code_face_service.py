"""Code-face selection + PTC skill assembly (discovery stubbed)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.modules.ptc import service


def _http(name: str, url: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, url=url, headers={})


# -- selection rule ---------------------------------------------------------


def test_code_face_selection_rule():
    servers = [
        _http("valuz-search", "https://data.valuz.cn/mcp/search"),
        _http("valuz-data-67b487", "https://data.valuz.cn/mcp"),  # manual copy → by host
        _http("valuz_docs", "http://127.0.0.1:8000/_internal/mcp/docs/mcp"),
        _http("harness", "http://127.0.0.1:8000/_internal/mcp/toolkit/base/mcp"),
        _http("github", "https://api.githubcopilot.com/mcp/"),
        SimpleNamespace(name="stdio-one", command="echo"),  # no url → skipped
    ]
    assert service.code_face_server_names(servers) == ["valuz-data-67b487", "valuz-search"]


def test_is_ptc_skill_path():
    assert service.is_ptc_skill_path("/x/ptc/skills/ptc-tools-ab12cd34ef")
    assert not service.is_ptc_skill_path("/x/skills/citation")


# -- skill assembly ---------------------------------------------------------


_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_data",
        "description": "Fetch one row.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "manage_things",
        "description": "Mutates.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {"type": "object", "properties": {}},
    },
]


@pytest.fixture()
def skill_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(service.fs_registry, "ptc_skill_root", lambda user_id: tmp_path)
    return tmp_path


@pytest.fixture()
def discovery(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"n": 0}

    async def _fake(cfg: Any) -> list[dict[str, Any]]:
        calls["n"] += 1
        return list(_SCHEMAS)

    monkeypatch.setattr(service, "_discover_tools", _fake)
    return calls


async def test_skill_tree_is_generated(skill_root: Path, discovery: dict[str, int]):
    built = await service.ensure_ptc_skill("u1", [_http("valuz-data-67b487", "https://x/mcp")])
    assert built is not None
    skill_md = (built / "SKILL.md").read_text(encoding="utf-8")
    # Frontmatter name is the STABLE materialized link name the executor's
    # PYTHONPATH candidates expect, independent of the hashed dir name.
    assert "name: ptc-tools" in skill_md
    assert built.name.startswith("ptc-tools-")
    module = (built / "tools" / "valuz_data_67b487.py").read_text(encoding="utf-8")
    assert "def get_data(" in module
    assert "manage_things" not in module  # readOnlyHint False → excluded
    assert (built / "tools" / "mcp_client.py").exists()
    assert (built / "tools" / "docs" / "valuz-data-67b487" / "get_data.md").exists()
    assert (built / "manifest.json").exists()


async def test_rebuild_is_skipped_while_codegen_version_holds(
    skill_root: Path, discovery: dict[str, int]
):
    cfg = [_http("valuz-data-67b487", "https://x/mcp")]
    first = await service.ensure_ptc_skill("u1", cfg)
    again = await service.ensure_ptc_skill("u1", cfg)
    assert first == again
    assert discovery["n"] == 1  # no per-turn network


async def test_codegen_version_move_regenerates(
    skill_root: Path, discovery: dict[str, int], monkeypatch: pytest.MonkeyPatch
):
    cfg = [_http("valuz-data-67b487", "https://x/mcp")]
    await service.ensure_ptc_skill("u1", cfg)
    monkeypatch.setattr(service, "codegen_version", lambda: "moved-version")
    await service.ensure_ptc_skill("u1", cfg)
    assert discovery["n"] == 2


async def test_discovery_failure_builds_nothing(skill_root: Path, monkeypatch):
    async def _boom(cfg: Any) -> list[dict[str, Any]]:
        raise RuntimeError("401")

    monkeypatch.setattr(service, "_discover_tools", _boom)
    built = await service.ensure_ptc_skill("u1", [_http("valuz-data-67b487", "https://x/mcp")])
    assert built is None


async def test_no_eligible_tools_builds_nothing(skill_root: Path, monkeypatch):
    async def _only_mutating(cfg: Any) -> list[dict[str, Any]]:
        return [_SCHEMAS[1]]

    monkeypatch.setattr(service, "_discover_tools", _only_mutating)
    built = await service.ensure_ptc_skill("u1", [_http("valuz-data-67b487", "https://x/mcp")])
    assert built is None
