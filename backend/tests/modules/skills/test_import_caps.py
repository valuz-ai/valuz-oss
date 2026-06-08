"""``_enforce_import_caps`` — per-skill size/count guardrails on URL import.

A URL can point at an arbitrarily large repo; these caps bound what a single
import copies into the library. Caps are monkeypatched small so the test stays
fast and doesn't write multi-MiB fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import valuz_agent.modules.skills.service as svc_mod
from valuz_agent.modules.skills.errors import SkillImportFailed
from valuz_agent.modules.skills.service import SkillLibraryService


def _svc() -> SkillLibraryService:
    return SkillLibraryService.__new__(SkillLibraryService)


def _skill_tree(root: Path, files: dict[str, int]) -> None:
    (root).mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text("---\nname: x\n---\n", "utf-8")
    for rel, size in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)


def test_under_caps_passes(tmp_path: Path) -> None:
    _skill_tree(tmp_path / "s", {"a.txt": 10, "sub/b.txt": 20})
    _svc()._enforce_import_caps(tmp_path / "s")  # no raise


def test_file_count_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc_mod, "_MAX_IMPORT_FILE_COUNT", 2)
    _skill_tree(tmp_path / "s", {"a": 1, "b": 1, "c": 1})  # 4 files incl SKILL.md
    with pytest.raises(SkillImportFailed, match="2-file limit"):
        _svc()._enforce_import_caps(tmp_path / "s")


def test_per_file_byte_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc_mod, "_MAX_IMPORT_FILE_BYTES", 1024 * 1024)  # 1 MiB
    _skill_tree(tmp_path / "s", {"big.bin": 1024 * 1024 + 1})
    with pytest.raises(SkillImportFailed, match="per-file limit"):
        _svc()._enforce_import_caps(tmp_path / "s")


def test_total_bundle_byte_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc_mod, "_MAX_IMPORT_FILE_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(svc_mod, "_MAX_IMPORT_TOTAL_BYTES", 2 * 1024 * 1024)  # 2 MiB
    _skill_tree(tmp_path / "s", {"a.bin": 1024 * 1024, "b.bin": 1024 * 1024 + 1})
    with pytest.raises(SkillImportFailed, match="MiB limit"):
        _svc()._enforce_import_caps(tmp_path / "s")
