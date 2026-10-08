"""Memory isolation must not depend on deployment DATA_DIR templating."""

from pathlib import Path

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.modules.memory.service import MemoryStore


@pytest.mark.parametrize("templated", [False, True])
def test_memory_is_owner_scoped_without_moving_other_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, templated: bool
) -> None:
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", root / "{user_id}" if templated else root)
    fs = FsRegistry()
    store = MemoryStore(fs)

    assert store.add("owner-a", "user", "Answer in Chinese.")["success"]
    assert store.add("owner-a", "global", "Prefer concise answers.")["success"]
    assert store.add("owner-a", "project", "Project A context.", project_id="p")["success"]
    assert store.read_entries("owner-b", "user") == []
    assert store.read_entries("owner-b", "global") == []
    assert store.read_entries("owner-b", "project", project_id="p") == []
    assert store.render_for_injection("owner-b", project_id="p") == ""

    store.clear("owner-b", "global")
    store.drop_project("owner-b", "p")
    assert store.read_entries("owner-a", "global") == ["Prefer concise answers."]
    assert store.read_entries("owner-a", "project", project_id="p") == ["Project A context."]
    assert fs.memory_dir("owner-a", "global") != fs.memory_dir("owner-b", "global")
    assert fs.data_dir("owner-a") == (root / "owner-a" if templated else root)


def test_unattributed_legacy_memory_is_not_adopted_or_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    legacy = tmp_path / "memories"
    (legacy / "projects" / "p").mkdir(parents=True)
    (legacy / "USER.md").write_text("Old private profile.")
    (legacy / "MEMORY.md").write_text("Old private note.")
    project_file = legacy / "projects" / "p" / "MEMORY.md"
    project_file.write_text("Old project note.")
    store = MemoryStore(FsRegistry())

    assert store.render_for_injection("owner-a", project_id="p") == ""
    store.clear("owner-a", "user")
    store.drop_project("owner-a", "p")
    assert (legacy / "USER.md").read_text() == "Old private profile."
    assert project_file.read_text() == "Old project note."


def test_memory_owner_names_cannot_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    fs = FsRegistry()
    assert fs.memory_dir("a/b", "global") != fs.memory_dir("a__b", "global")
    assert fs.memory_dir("a\\b", "global") != fs.memory_dir("a__b", "global")
    first = fs.memory_dir("a/b", "global")
    assert FsRegistry().memory_dir("a/b", "global") == first
    assert first.is_relative_to(tmp_path)


def test_memory_requires_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with pytest.raises(ValueError, match="user_id"):
        FsRegistry().memory_dir("", "global")


@pytest.mark.parametrize("project_id", ["../outside", "a/b", "a\\b"])
def test_project_memory_rejects_path_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_id: str
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with pytest.raises(ValueError, match="project_id"):
        FsRegistry().memory_dir("owner-a", "project", project_id=project_id)
