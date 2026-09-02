"""A saved skill must not point at the directory it was written in.

The agent authors under ``.skill-staging/<slug>/`` and documents its own
helpers by the path it can see. Saving the skill moves the package and DELETES
staging, so those commands name a directory that no longer exists — while the
files themselves sit right there in the package. Six such lines shipped in one
qa manifest on 2026-09-02, each a runtime failure waiting for the skill to be
used.
"""

from __future__ import annotations

from pathlib import Path

from valuz_agent.modules.skills.staging import strip_staging_paths

SLUG = "daily-weather-forecast"


def _skill(root: Path, body: str) -> Path:
    d = root / SLUG
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {SLUG}\n---\n\n{body}", encoding="utf-8")
    (d / "scripts" / "fetch.py").write_text("print(1)\n", encoding="utf-8")
    return d


def test_relative_staging_prefix_becomes_a_package_relative_path(tmp_path: Path) -> None:
    staging = tmp_path / "staging" / SLUG
    d = _skill(tmp_path / "lib", f"run: python3 .skill-staging/{SLUG}/scripts/fetch.py --all\n")

    changed = strip_staging_paths(d, SLUG, staging)

    assert changed == ["SKILL.md"]
    assert (
        (d / "SKILL.md")
        .read_text(encoding="utf-8")
        .endswith("run: python3 scripts/fetch.py --all\n")
    )


def test_the_dot_slash_spelling_is_consumed_whole(tmp_path: Path) -> None:
    staging = tmp_path / "staging" / SLUG
    d = _skill(tmp_path / "lib", f"open ./.skill-staging/{SLUG}/assets/board.html\n")

    strip_staging_paths(d, SLUG, staging)

    assert "open assets/board.html" in (d / "SKILL.md").read_text(encoding="utf-8")


def test_the_absolute_staging_path_is_rewritten_too(tmp_path: Path) -> None:
    staging = tmp_path / "staging" / SLUG
    staging.mkdir(parents=True)
    d = _skill(tmp_path / "lib", f"python3 {staging}/scripts/fetch.py\n")

    strip_staging_paths(d, SLUG, staging)

    assert "python3 scripts/fetch.py" in (d / "SKILL.md").read_text(encoding="utf-8")


def test_another_package_s_staging_path_is_left_alone(tmp_path: Path) -> None:
    """Only this package's own prefix. A manifest that legitimately mentions a
    different skill is documentation, not a broken self-reference."""
    staging = tmp_path / "staging" / SLUG
    d = _skill(tmp_path / "lib", "see .skill-staging/some-other-skill/scripts/x.py\n")

    assert strip_staging_paths(d, SLUG, staging) == []
    assert ".skill-staging/some-other-skill/" in (d / "SKILL.md").read_text(encoding="utf-8")


def test_binary_and_oversized_files_are_not_touched(tmp_path: Path) -> None:
    staging = tmp_path / "staging" / SLUG
    d = _skill(tmp_path / "lib", "clean\n")
    blob = d / "assets" / "logo.png"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"\x89PNG\r\n\x1a\n" + f".skill-staging/{SLUG}/".encode())

    assert strip_staging_paths(d, SLUG, staging) == []
    assert blob.read_bytes().startswith(b"\x89PNG")


def test_a_package_with_nothing_to_rewrite_is_left_byte_for_byte(tmp_path: Path) -> None:
    staging = tmp_path / "staging" / SLUG
    d = _skill(tmp_path / "lib", "python3 scripts/fetch.py\n")
    before = {p: p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}

    assert strip_staging_paths(d, SLUG, staging) == []
    assert {p: p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()} == before
