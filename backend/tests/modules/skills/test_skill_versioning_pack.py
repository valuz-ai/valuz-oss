"""Packing + frontmatter mechanics behind skill versions (pure filesystem)."""

from __future__ import annotations

import os
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from valuz_agent.modules.skills import versioning
from valuz_agent.modules.skills.staging import STAGING_META_FILENAME


def _skill(root: Path, *, body: str = "do the thing", extra: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: a demo\n---\n\n{body}\n", encoding="utf-8"
    )
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    os.chmod(root / "scripts" / "run.sh", 0o755)
    for name, text in (extra or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def test_pack_is_deterministic_across_runs_and_mtimes(tmp_path: Path) -> None:
    a = _skill(tmp_path / "a")
    first = versioning.pack_skill_dir(a)
    # touch every file: a real mtime must not leak into the bytes
    for p in a.rglob("*"):
        if p.is_file():
            os.utime(p, (1_700_000_000, 1_700_000_000))
    second = versioning.pack_skill_dir(a)
    assert first == second
    # an identical tree elsewhere packs identically too
    b = _skill(tmp_path / "b")
    assert versioning.pack_skill_dir(b) == first


def test_pack_changes_when_content_name_or_mode_changes(tmp_path: Path) -> None:
    base = versioning.pack_skill_dir(_skill(tmp_path / "base"))

    changed = _skill(tmp_path / "changed", body="do the other thing")
    assert versioning.pack_skill_dir(changed) != base

    renamed = _skill(tmp_path / "renamed")
    (renamed / "scripts" / "run.sh").rename(renamed / "scripts" / "go.sh")
    assert versioning.pack_skill_dir(renamed) != base

    demoted = _skill(tmp_path / "demoted")
    os.chmod(demoted / "scripts" / "run.sh", 0o644)
    assert versioning.pack_skill_dir(demoted) != base


def test_pack_excludes_bookkeeping_and_litter(tmp_path: Path) -> None:
    root = _skill(
        tmp_path / "s",
        extra={
            STAGING_META_FILENAME: "{}",
            ".DS_Store": "x",
            "__pycache__/m.cpython-313.pyc": "x",
            "scripts/helper.pyc": "x",
            "references/notes.md": "keep me",
        },
    )
    names = {name for name, _ in versioning.list_archive_members(versioning.pack_skill_dir(root))}
    assert names == {"SKILL.md", "scripts/run.sh", "references/notes.md"}


def test_pack_refuses_oversized_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _skill(tmp_path / "big", extra={"blob.bin": "x" * 2048})
    monkeypatch.setattr(versioning, "MAX_SKILL_FILE_BYTES", 1024)
    with pytest.raises(versioning.SkillTooLargeError):
        versioning.pack_skill_dir(root)
    monkeypatch.setattr(versioning, "MAX_SKILL_FILE_BYTES", 1 << 20)
    monkeypatch.setattr(versioning, "MAX_SKILL_TOTAL_BYTES", 1024)
    with pytest.raises(versioning.SkillTooLargeError):
        versioning.pack_skill_dir(root)


def test_unpack_round_trips_content_and_executable_bit(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src", extra={"references/a.md": "A"})
    data = versioning.pack_skill_dir(src)
    dest = tmp_path / "out"
    versioning.unpack_skill_archive(data, dest)
    assert (dest / "SKILL.md").read_text() == (src / "SKILL.md").read_text()
    assert (dest / "references" / "a.md").read_text() == "A"
    assert os.access(dest / "scripts" / "run.sh", os.X_OK)
    assert versioning.pack_skill_dir(dest) == data


def test_unpack_refuses_escaping_members(tmp_path: Path) -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "no")
    with pytest.raises(ValueError):
        versioning.unpack_skill_archive(buf.getvalue(), tmp_path / "out")


def test_read_archive_member(tmp_path: Path) -> None:
    data = versioning.pack_skill_dir(_skill(tmp_path / "s"))
    assert versioning.read_archive_member(data, "scripts/run.sh") == b"#!/bin/sh\necho hi\n"
    assert versioning.read_archive_member(data, "nope.md") is None


@pytest.mark.parametrize(
    ("raw", "expected_head"),
    [
        ("---\nname: demo\nversion: 3\n---\nbody\n", "---\nname: demo\nversion: 7\n---\nbody\n"),
        ("---\nname: demo\n---\nbody\n", "---\nversion: 7\nname: demo\n---\nbody\n"),
        ("just a body\n", "---\nversion: 7\n---\n\njust a body\n"),
    ],
)
def test_set_manifest_version(tmp_path: Path, raw: str, expected_head: str) -> None:
    manifest = tmp_path / "SKILL.md"
    manifest.write_text(raw, encoding="utf-8")
    versioning.set_manifest_version(manifest, 7)
    assert manifest.read_text(encoding="utf-8") == expected_head


def test_content_hash_matches_the_artifacts_spelling() -> None:
    assert versioning.content_hash_of(b"abc").startswith("sha256:")
    assert len(versioning.content_hash_of(b"abc")) == len("sha256:") + 64
