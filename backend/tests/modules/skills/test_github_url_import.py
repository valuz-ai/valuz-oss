"""GitHub URL parsing + ref/path resolution for skill imports.

Covers three concerns, all without network:

1. ``_parse_github_url`` — owner/repo/segments extraction for bare, tree, blob,
   and raw URLs (the parser does NOT split the ref out of the segments).
2. ``_fetch_github_tree`` ref/path resolution — a ``/tree/<ref>/<path>`` URL is
   ambiguous when the ref contains ``/`` (e.g. ``release/v2``). We try each
   split shortest-ref-first against codeload and accept the first whose ref
   resolves AND whose sub-path exists.
3. ``_download_repo_zipball`` — bare repo default-branch fallback main → master
   → REST API, all via codeload (no API call for the common case).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.modules.skills.service import SkillLibraryService


def _svc() -> SkillLibraryService:
    return SkillLibraryService.__new__(SkillLibraryService)  # github helpers need no deps


# ── _parse_github_url ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/o/r", ("o", "r", None)),
        ("https://github.com/o/r.git", ("o", "r", None)),
        ("https://github.com/o/r/", ("o", "r", None)),
        ("https://github.com/o/r/tree/main", ("o", "r", ["main"])),
        ("https://github.com/o/r/tree/main/skills/foo", ("o", "r", ["main", "skills", "foo"])),
        # ref with '/' is NOT pre-split — the whole tail is segments
        (
            "https://github.com/o/r/tree/release/v2/skills/foo",
            ("o", "r", ["release", "v2", "skills", "foo"]),
        ),
        # blob/raw drop the trailing filename (its parent dir is the skill dir)
        (
            "https://github.com/o/r/blob/main/skills/foo/SKILL.md",
            ("o", "r", ["main", "skills", "foo"]),
        ),
        (
            "https://raw.githubusercontent.com/o/r/main/skills/foo/SKILL.md",
            ("o", "r", ["main", "skills", "foo"]),
        ),
        # url-escaped segments are decoded
        ("https://github.com/o/r/tree/main/my%20skill", ("o", "r", ["main", "my skill"])),
        ("https://example.com/not/github", None),
    ],
)
def test_parse_github_url(url: str, expected: object) -> None:
    assert _svc()._parse_github_url(url) == expected


# ── _fetch_github_tree ref/path split resolution ─────────────────────


def _wire_resolution(
    svc: SkillLibraryService, *, refs_that_exist: set[str], valid_subdirs: set[str]
):
    """Stub codeload download + extract so the split loop runs offline.

    ``refs_that_exist`` decides which refs return a zip (others 404). For a ref
    that exists, extraction returns a sentinel path iff its ``subdir`` is in
    ``valid_subdirs``; otherwise it raises FileNotFoundError (wrong split).
    """
    tried_refs: list[str] = []

    def fake_download(owner: str, repo: str, ref: str, target: Path) -> bool:
        tried_refs.append(ref)
        return ref in refs_that_exist

    def fake_extract(zip_path: Path, subdir: str) -> Path:
        if subdir in valid_subdirs:
            return Path(f"/resolved/{subdir or 'ROOT'}")
        raise FileNotFoundError(subdir)

    svc._try_download_codeload = fake_download  # type: ignore[method-assign]
    svc._extract_to_subdir = fake_extract  # type: ignore[method-assign]
    return tried_refs


def test_single_segment_ref_one_download(tmp_path: Path) -> None:
    svc = _svc()
    tried = _wire_resolution(svc, refs_that_exist={"main"}, valid_subdirs={"skills/foo"})
    result = svc._fetch_github_tree("https://github.com/o/r/tree/main/skills/foo", tmp_path)
    assert result == Path("/resolved/skills/foo")
    assert tried == ["main"]  # resolved on the first (shortest) split


def test_slash_ref_when_short_prefix_absent(tmp_path: Path) -> None:
    svc = _svc()
    tried = _wire_resolution(svc, refs_that_exist={"release/v2"}, valid_subdirs={"skills/foo"})
    result = svc._fetch_github_tree("https://github.com/o/r/tree/release/v2/skills/foo", tmp_path)
    assert result == Path("/resolved/skills/foo")
    assert tried == ["release", "release/v2"]  # 'release' 404s, then 'release/v2' wins


def test_slash_ref_when_short_prefix_is_a_real_but_wrong_branch(tmp_path: Path) -> None:
    # Pathological: BOTH 'release' and 'release/v2' exist as refs. The shorter
    # 'release' downloads but 'v2/skills/foo' isn't in it → fall through to the
    # longer ref whose 'skills/foo' subdir exists.
    svc = _svc()
    tried = _wire_resolution(
        svc,
        refs_that_exist={"release", "release/v2"},
        valid_subdirs={"skills/foo"},  # only valid under release/v2
    )
    result = svc._fetch_github_tree("https://github.com/o/r/tree/release/v2/skills/foo", tmp_path)
    assert result == Path("/resolved/skills/foo")
    assert tried == ["release", "release/v2"]


def test_unresolvable_ref_raises_listing_candidates(tmp_path: Path) -> None:
    svc = _svc()
    _wire_resolution(svc, refs_that_exist=set(), valid_subdirs=set())
    with pytest.raises(ValueError, match="release, release/v2, release/v2/skills"):
        svc._fetch_github_tree("https://github.com/o/r/tree/release/v2/skills", tmp_path)


# ── _download_repo_zipball (bare repo default-branch fallback) ────────


def _wire_bare(svc: SkillLibraryService, *, exist: set[str], api_branch: str | None = None):
    tried: list[str] = []

    def fake_download(owner: str, repo: str, ref: str, target: Path) -> bool:
        tried.append(ref)
        return ref in exist

    def fake_api(owner: str, repo: str) -> str:
        if api_branch is None:
            raise AssertionError("default-branch API must not be called")
        return api_branch

    svc._try_download_codeload = fake_download  # type: ignore[method-assign]
    svc._github_default_branch = fake_api  # type: ignore[method-assign]
    return tried


def test_bare_repo_prefers_main_without_api(tmp_path: Path) -> None:
    svc = _svc()
    tried = _wire_bare(svc, exist={"main"})  # api_branch None → must not be called
    assert svc._download_repo_zipball("o", "r", tmp_path / "z.zip") == "main"
    assert tried == ["main"]


def test_bare_repo_falls_back_to_master(tmp_path: Path) -> None:
    svc = _svc()
    tried = _wire_bare(svc, exist={"master"})
    assert svc._download_repo_zipball("o", "r", tmp_path / "z.zip") == "master"
    assert tried == ["main", "master"]


def test_bare_repo_consults_api_for_nonstandard_default(tmp_path: Path) -> None:
    svc = _svc()
    tried = _wire_bare(svc, exist={"trunk"}, api_branch="trunk")
    assert svc._download_repo_zipball("o", "r", tmp_path / "z.zip") == "trunk"
    assert tried == ["main", "master", "trunk"]


def test_bare_repo_raises_when_api_branch_also_missing(tmp_path: Path) -> None:
    svc = _svc()
    _wire_bare(svc, exist=set(), api_branch="trunk")
    with pytest.raises(ValueError, match="default branch 'trunk'"):
        svc._download_repo_zipball("o", "r", tmp_path / "z.zip")
