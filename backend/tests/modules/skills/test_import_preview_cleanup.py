"""Multi-skill URL import: cleaning up one candidate must not delete siblings.

Regression for "selected 5, imported 1, 4 failed": every candidate of a
multi-skill URL import is a subdir of ONE shared staging tree. The old cleanup
did ``rmtree(skill_root.parent)``, so confirming the first skill deleted the
shared parent — wiping the source dirs of the other candidates. The fix
ref-counts the shared staging root and only removes it once the last candidate
is consumed.
"""

from __future__ import annotations

from pathlib import Path

import valuz_agent.modules.skills.service as svc_mod
from valuz_agent.modules.skills.service import SkillLibraryService


def _svc() -> SkillLibraryService:
    return SkillLibraryService.__new__(SkillLibraryService)


def _seed_multi(staging: Path, names: list[str]) -> list[tuple[str, Path]]:
    """Mimic _build_multi_preview: N candidates sharing one staging root."""
    skills_dir = staging / "skills"
    out: list[tuple[str, Path]] = []
    svc = _svc()
    for i, name in enumerate(names):
        root = skills_dir / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", "utf-8")
        pid = f"pid-{i}"
        svc_mod._import_previews[pid] = (root, staging, 0.0)
        svc._incref_cleanup_root(staging)
        out.append((pid, root))
    return out


def test_cleaning_one_candidate_keeps_siblings_until_last(tmp_path: Path) -> None:
    staging = tmp_path / "valuz-skill-url-x"
    entries = _seed_multi(staging, ["a", "b", "c"])
    svc = _svc()
    try:
        # Cleaning the first two candidates must NOT remove the shared tree, so
        # the remaining candidates' sources stay intact.
        svc._cleanup_preview(entries[0][0])
        assert entries[1][1].exists() and entries[2][1].exists()
        assert staging.exists()

        svc._cleanup_preview(entries[1][0])
        assert entries[2][1].exists()
        assert staging.exists()

        # Last candidate cleaned → the whole staging tree is reclaimed.
        svc._cleanup_preview(entries[2][0])
        assert not staging.exists()
        assert str(staging) not in svc_mod._import_cleanup_refs
    finally:
        for pid, _ in entries:
            svc_mod._import_previews.pop(pid, None)
        svc_mod._import_cleanup_refs.pop(str(staging), None)


def test_archive_preview_cleanup_unchanged(tmp_path: Path) -> None:
    # 2-tuple (archive) path still rmtree's the skill root's parent.
    extract = tmp_path / "extract"
    skill = extract / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x", "utf-8")
    svc_mod._import_previews["arch"] = (skill, True)
    try:
        _svc()._cleanup_preview("arch")
        assert not extract.exists()  # parent removed
    finally:
        svc_mod._import_previews.pop("arch", None)
