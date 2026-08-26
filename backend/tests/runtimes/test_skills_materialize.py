"""Skill materialization must work without privileged symlinks.

On Windows ``os.symlink`` needs Developer Mode / admin (``WinError 1314``);
ordinary users hit a permission error. The materializer must therefore fall
back to a directory junction (or a copy when even junctions are unsupported)
while keeping its manifest-driven, never-destroy-user-data cleanup correct.

These tests drive the platform-specific link primitive on POSIX too, by
monkeypatching ``os.name``/``os.symlink``, so the Windows path is exercised in
CI without a Windows runner.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/integrations/test_sandbox_seatbelt.py.
import kernel  # noqa: F401

from src.runtimes import skills_materialize as sm


def _make_skill(tmp_path: Path, name: str) -> str:
    src = tmp_path / "sources" / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return str(src)


def _manifest(root: Path, manifest_rel: str) -> dict:
    return json.loads((root / manifest_rel).read_text(encoding="utf-8"))


# -- POSIX happy path (symlink) --------------------------------------------


def test_symlink_materialization_records_kind(tmp_path: Path) -> None:
    src = _make_skill(tmp_path, "alpha")
    root = Path(sm.prepare_deepagents_skills(str(tmp_path / "cwd"), [src]))

    link = root / "alpha"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "# alpha\n"

    manifest = _manifest(Path(tmp_path / "cwd"), sm.AGENTS_MANIFEST)
    assert manifest == {"managed": [{"name": "alpha", "kind": "symlink"}]}


def test_symlink_reflects_live_source_edits(tmp_path: Path) -> None:
    src = _make_skill(tmp_path, "alpha")
    root = Path(sm.prepare_deepagents_skills(str(tmp_path / "cwd"), [src]))

    (Path(src) / "SKILL.md").write_text("# edited\n", encoding="utf-8")
    assert (root / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "# edited\n"


# -- Cyclic / self-referential sources are skipped, not linked --------------


def test_skips_source_living_inside_skills_root(tmp_path: Path) -> None:
    """A skill authored in-place under ``.agents/skills`` must not be linked to
    itself. ``os.symlink(abspath(src), src)`` would create a self-referential
    link whose ``SKILL.md`` then raises ``OSError(ELOOP)`` on read."""
    cwd = tmp_path / "cwd"
    skills_root = cwd / ".agents" / "skills"
    in_place = skills_root / "autoplan"
    in_place.mkdir(parents=True)
    (in_place / "SKILL.md").write_text("# autoplan\n", encoding="utf-8")

    root = Path(sm.prepare_deepagents_skills(str(cwd), [str(in_place)]))

    # The real directory is left exactly where it sits — readable, not a link.
    assert not (root / "autoplan").is_symlink()
    assert (root / "autoplan" / "SKILL.md").read_text(encoding="utf-8") == "# autoplan\n"
    # ...and it is not recorded as a managed entry.
    manifest = _manifest(cwd, sm.AGENTS_MANIFEST)
    assert manifest == {"managed": []}


def test_skips_source_reaching_skills_root_through_symlink(tmp_path: Path) -> None:
    """Indirect cycles (the user-library entry is itself a symlink back into the
    project skills root) are caught on the real, resolved path."""
    cwd = tmp_path / "cwd"
    skills_root = cwd / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    materialized = skills_root / "autoplan"
    materialized.mkdir()
    (materialized / "SKILL.md").write_text("# autoplan\n", encoding="utf-8")

    # A user-library path that resolves (via symlink) back into the skills root.
    lib = tmp_path / "lib" / "autoplan"
    lib.parent.mkdir(parents=True)
    lib.symlink_to(materialized, target_is_directory=True)

    root = Path(sm.prepare_deepagents_skills(str(cwd), [str(lib)]))

    assert not (root / "autoplan").is_symlink()
    manifest = _manifest(cwd, sm.AGENTS_MANIFEST)
    assert manifest == {"managed": []}


def test_in_place_skill_skipped_at_debug_not_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A skill already sitting at its skills-root location is benign — discovered
    in place. It must be skipped quietly (debug), not warned, so the log stays
    clean on the common case."""
    cwd = tmp_path / "cwd"
    in_place = cwd / ".agents" / "skills" / "autoplan"
    in_place.mkdir(parents=True)
    (in_place / "SKILL.md").write_text("# autoplan\n", encoding="utf-8")

    with caplog.at_level("DEBUG", logger=sm.__name__):
        sm.prepare_deepagents_skills(str(cwd), [str(in_place)])

    records = [r for r in caplog.records if "autoplan" in r.getMessage()]
    assert records, "expected the skip to be logged"
    assert all(r.levelname == "DEBUG" for r in records)
    assert all("in place" in r.getMessage() for r in records)


def test_source_containing_skills_root_warns_with_cause(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A skill source that is an *ancestor* of the skills root (e.g. resolved to
    the project cwd) is a real misconfiguration: it's skipped with a WARNING that
    names the cause, so the loop is self-diagnosing."""
    cwd = tmp_path / "cwd"
    cwd.mkdir(parents=True)
    (cwd / "SKILL.md").write_text("# bogus\n", encoding="utf-8")

    # Source == cwd, which is an ancestor of <cwd>/.agents/skills.
    with caplog.at_level("DEBUG", logger=sm.__name__):
        root = Path(sm.prepare_deepagents_skills(str(cwd), [str(cwd)]))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a warning for the ancestor source"
    assert any("ancestor of the skills root" in r.getMessage() for r in warnings)
    # nothing materialized
    assert _manifest(cwd, sm.AGENTS_MANIFEST) == {"managed": []}
    assert not (root / "cwd").exists()


# -- Windows fallback: copy when the link primitive is unavailable ----------


def _force_copy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the Windows branch on POSIX. Forcing ``_on_windows`` True is enough:
    ``_winapi`` is a Windows-only module, so the materializer's ``import _winapi``
    raises ``ModuleNotFoundError`` (an ``ImportError``) here, which it catches and
    falls back to a copy — exactly the FAT/exFAT/network-share case. (We do NOT
    flip the global ``os.name``, which would switch ``pathlib`` into
    un-instantiable ``WindowsPath`` mode.)"""
    monkeypatch.setattr(sm, "_on_windows", lambda: True)


def test_copy_fallback_when_no_link_primitive(tmp_path: Path, monkeypatch) -> None:
    src = _make_skill(tmp_path, "beta")
    _force_copy_fallback(monkeypatch)

    cwd = tmp_path / "cwd"
    root = Path(sm.prepare_deepagents_skills(str(cwd), [src]))

    entry = root / "beta"
    assert not entry.is_symlink()
    assert entry.is_dir()
    assert (entry / "SKILL.md").read_text(encoding="utf-8") == "# beta\n"

    manifest = _manifest(cwd, sm.AGENTS_MANIFEST)
    assert manifest == {"managed": [{"name": "beta", "kind": "copy"}]}


def test_copy_entry_is_cleaned_up_on_rematerialize(tmp_path: Path, monkeypatch) -> None:
    """A previously-copied entry (real dir) must be removed when the skill set
    changes — the manifest kind drives ``rmtree`` so it doesn't leak or collide."""
    src_beta = _make_skill(tmp_path, "beta")
    src_gamma = _make_skill(tmp_path, "gamma")
    _force_copy_fallback(monkeypatch)

    cwd = tmp_path / "cwd"
    root = Path(sm.prepare_deepagents_skills(str(cwd), [src_beta]))
    assert (root / "beta").is_dir()

    # Re-materialize with a different skill: the old copy must be gone.
    sm.prepare_deepagents_skills(str(cwd), [src_gamma])
    assert not (root / "beta").exists()
    assert (root / "gamma").is_dir()
    manifest = _manifest(cwd, sm.AGENTS_MANIFEST)
    assert manifest == {"managed": [{"name": "gamma", "kind": "copy"}]}


# -- Cleanup safety: never destroy what we didn't write ---------------------


def test_user_placed_dir_is_never_destroyed(tmp_path: Path) -> None:
    src = _make_skill(tmp_path, "alpha")
    cwd = tmp_path / "cwd"
    root = Path(sm.prepare_deepagents_skills(str(cwd), [src]))

    # User hand-places a real directory with the same basename a future skill
    # would use. It is NOT in our manifest, so it must survive + force a loud error.
    sm.prepare_deepagents_skills(str(cwd), [])  # clears our managed 'alpha'
    user_dir = root / "alpha"
    user_dir.mkdir()
    (user_dir / "precious.txt").write_text("keep me", encoding="utf-8")

    src_again = _make_skill(tmp_path / "again", "alpha")
    with pytest.raises(FileExistsError):
        sm.prepare_deepagents_skills(str(cwd), [src_again])

    # The user's file is untouched.
    assert (user_dir / "precious.txt").read_text(encoding="utf-8") == "keep me"


def test_legacy_string_manifest_is_read_and_cleaned(tmp_path: Path) -> None:
    """A manifest written by an older build stored bare name strings; cleanup
    must still recognise and remove those (as symlinks)."""
    src = _make_skill(tmp_path, "alpha")
    cwd = tmp_path / "cwd"
    root = Path(sm.prepare_deepagents_skills(str(cwd), [src]))

    # Rewrite the manifest in the legacy bare-string shape.
    manifest_path = cwd / sm.AGENTS_MANIFEST
    manifest_path.write_text(json.dumps({"managed": ["alpha"]}), encoding="utf-8")
    assert sm._read_manifest(str(manifest_path)) == [("alpha", "symlink")]

    # Re-materialize empty: the legacy symlink entry must be cleaned up.
    sm.prepare_deepagents_skills(str(cwd), [])
    assert not (root / "alpha").exists()


def test_broken_symlink_is_cleaned_up(tmp_path: Path) -> None:
    src = _make_skill(tmp_path / "src", "alpha")
    cwd = tmp_path / "cwd"
    root = Path(sm.prepare_deepagents_skills(str(cwd), [src]))

    # Delete the source -> broken symlink under the skills root.
    os.remove(Path(src) / "SKILL.md")
    os.rmdir(src)
    assert (root / "alpha").is_symlink()  # broken, but still a link

    # Re-materialize empty: the broken link must be removed idempotently.
    sm.prepare_deepagents_skills(str(cwd), [])
    assert not os.path.islink(root / "alpha")
    assert not (root / "alpha").exists()


def test_materialize_uses_frontmatter_name_over_versioned_dirname(tmp_path: Path) -> None:
    """Versioned source dirs (slug-collision suffixes) materialize under the
    Agent Skills spec name from SKILL.md, so directory name == name and the
    deepagents middleware stops warning on every session assembly."""
    src = tmp_path / "sources" / "weekly-report-v4"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: weekly-report\ndescription: demo\n---\n# Weekly\n",
        encoding="utf-8",
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    root = Path(sm.prepare_deepagents_skills(str(cwd), [str(src)]))

    assert (root / "weekly-report" / "SKILL.md").exists()
    assert not (root / "weekly-report-v4").exists()


def test_materialize_same_name_versions_last_wins(tmp_path: Path) -> None:
    """Two versioned copies of one skill collapse to a single spec-named
    entry (the later source wins) instead of two mismatched dirs."""
    entries = []
    for version in ("v4", "v5"):
        src = tmp_path / "sources" / f"weekly-report-{version}"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text(
            f"---\nname: weekly-report\n---\n# {version}\n",
            encoding="utf-8",
        )
        entries.append(str(src))
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    root = Path(sm.prepare_deepagents_skills(str(cwd), entries))

    assert (root / "weekly-report" / "SKILL.md").read_text(encoding="utf-8").endswith("# v5\n")
    assert not (root / "weekly-report-v4").exists()
    assert not (root / "weekly-report-v5").exists()


def test_materialize_falls_back_to_basename_without_frontmatter(tmp_path: Path) -> None:
    src = _make_skill(tmp_path, "plain-skill")  # SKILL.md without frontmatter
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    root = Path(sm.prepare_deepagents_skills(str(cwd), [src]))

    assert (root / "plain-skill" / "SKILL.md").exists()


# -- Frontmatter names that can't be directories ----------------------------


@pytest.mark.parametrize(
    "declared",
    [
        "react:components",  # plugin-namespaced name; ':' = NTFS data stream
        'quote"name',
        "pipe|name",
        "star*name",
        "question?name",
        "less<greater>",
        "trailing-dot.",
        "con",  # Windows device name
        "COM1.md",
        "",
    ],
)
def test_unportable_frontmatter_name_falls_back_to_basename(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, declared: str
) -> None:
    """A frontmatter ``name:`` that can't be a directory component on Windows
    must not become one. ``react:components`` crashed materialization with
    ``WinError 267`` ("The directory name is invalid") because NTFS reads
    ``dir:stream`` as an alternate data stream; the source basename — which
    already exists on disk — is used instead, on every platform."""
    src = tmp_path / "sources" / "react-components"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {declared}\ndescription: demo\n---\n# hi\n", encoding="utf-8"
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    with caplog.at_level("WARNING", logger=sm.__name__):
        root = Path(sm.prepare_deepagents_skills(str(cwd), [str(src)]))

    assert (root / "react-components" / "SKILL.md").exists()
    assert _manifest(cwd, sm.AGENTS_MANIFEST) == {
        "managed": [{"name": "react-components", "kind": "symlink"}]
    }
    if declared:  # an empty name never reaches the frontmatter branch quietly
        assert any(declared in r.getMessage() for r in caplog.records)


def test_portable_frontmatter_names_are_still_honoured(tmp_path: Path) -> None:
    """The guard rejects only what Windows forbids — spaces, dots, unicode and
    embedded device names stay usable as directory names."""
    for declared in ("weekly report", "v1.2.3-report", "周报", "console"):
        assert sm._is_portable_segment(declared), declared
    for declared in ("trailing-space ", "nul", "a/b", "a\\b", ".", ".."):
        assert not sm._is_portable_segment(declared), declared
