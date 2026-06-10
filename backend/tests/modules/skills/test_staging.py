"""Unit / integration tests for the staging module."""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.modules.skills import staging
from valuz_agent.modules.skills.staging import (
    StagingMeta,
    hash_skill_directory,
    prepare_optimize,
    read_staging_meta,
    scan_staging,
    sync_slug,
    write_staging_meta,
)


@pytest.fixture
def staging_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect both staging dir and user skills dir into the tmp tree."""
    staging_dir = tmp_path / "staging"
    user_skills = tmp_path / "user-skills"
    staging_dir.mkdir(parents=True)
    user_skills.mkdir(parents=True)

    # ``skill_staging_dir`` is a property that consults ``skill_staging_dir_override``
    # on every read, so patching that field redirects the legacy staging root.
    #
    # IMPORTANT — patch the *exact* settings object ``staging`` holds, not a
    # freshly imported one. ``tests/modules/sessions/test_session_approval_e2e.py``
    # pops + reimports ``valuz_agent.infra.config`` to rebind the kernel env,
    # which swaps the module-level ``settings`` singleton for a NEW instance.
    # ``staging`` was imported earlier in the suite via ``from …config import
    # settings`` so it keeps the OLD object. A bare ``from …config import
    # settings`` here would patch the NEW one and miss the object ``staging``
    # actually reads — leaking the resolved path back to the real ``~/.valuz``
    # staging dir. Patching ``staging.settings`` is hermetic against that.
    monkeypatch.setattr(staging.settings, "skill_staging_dir_override", staging_dir)
    monkeypatch.setenv("VALUZ_USER_SKILLS_DIR", str(user_skills))
    return tmp_path


def _write_skill(
    dir_path: Path,
    *,
    name: str,
    description: str = "",
    body: str = "Body.\n",
    version: int | None = None,
) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if version is not None:
        lines.append(f"version: {version}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    (dir_path / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def test_scan_returns_no_slugs_when_session_dir_missing(staging_root: Path) -> None:
    result = await scan_staging("never-seen")
    assert result.slugs == []
    assert result.session_id == "never-seen"


async def test_scan_skips_dirs_without_skill_md(staging_root: Path) -> None:
    session_dir = await staging.staging_dir_for_session("sess-1", mkdir=True)
    (session_dir / "garbage").mkdir()
    (session_dir / "garbage" / "notes.txt").write_text("hi", encoding="utf-8")

    assert (await scan_staging("sess-1")).slugs == []


async def test_scan_reports_no_conflict_when_target_absent(staging_root: Path) -> None:
    session_dir = await staging.staging_dir_for_session("sess-1", mkdir=True)
    _write_skill(session_dir / "weekly-report", name="weekly-report", description="Weekly report.")

    result = await scan_staging("sess-1")
    assert len(result.slugs) == 1
    s = result.slugs[0]
    assert s.slug == "weekly-report"
    assert s.name == "weekly-report"
    assert s.description == "Weekly report."
    assert s.conflict_kind == "none"
    assert s.suggested_strategy == "overwrite"
    assert s.suggested_new_slug is None


async def test_scan_detects_diverged_and_suggests_fork(staging_root: Path, tmp_path: Path) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-2", mkdir=True)

    # User already has a skill at the target slug, with different content.
    _write_skill(user_skills / "weekly-report", name="weekly-report", description="Old version.")
    _write_skill(session_dir / "weekly-report", name="weekly-report", description="New rewrite.")

    result = await scan_staging("sess-2")
    s = result.slugs[0]
    assert s.conflict_kind == "diverged"
    assert s.suggested_strategy == "fork"
    assert s.suggested_new_slug == "weekly-report-v2"


async def test_scan_reports_same_source_when_meta_hash_matches(
    staging_root: Path, tmp_path: Path
) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-3", mkdir=True)

    _write_skill(user_skills / "weekly-report", name="weekly-report", description="Original.")
    source_hash = hash_skill_directory(user_skills / "weekly-report")

    _write_skill(session_dir / "weekly-report", name="weekly-report", description="Tweaked desc.")
    write_staging_meta(
        session_dir / "weekly-report",
        StagingMeta(
            source_skill_id="user:weekly-report",
            source_path=str(user_skills / "weekly-report"),
            source_content_hash=source_hash,
            intent="optimize",
        ),
    )

    s = (await scan_staging("sess-3")).slugs[0]
    assert s.conflict_kind == "same_source"
    assert s.suggested_strategy == "overwrite"
    assert s.source_skill_id == "user:weekly-report"


async def test_sync_slug_overwrite_writes_to_user_skill_root(
    staging_root: Path, tmp_path: Path
) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-4", mkdir=True)
    _write_skill(session_dir / "weekly-report", name="weekly-report")

    result = await sync_slug("sess-4", "weekly-report", "overwrite")
    assert result.skipped is False
    assert result.written_path == str(user_skills / "weekly-report")
    assert (user_skills / "weekly-report" / "SKILL.md").is_file()


async def test_sync_slug_fork_auto_picks_v2_and_bumps_frontmatter(
    staging_root: Path, tmp_path: Path
) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-5", mkdir=True)
    # Original v1 already in library
    _write_skill(user_skills / "weekly-report", name="weekly-report", version=1)
    # New variant in staging — same slug
    _write_skill(session_dir / "weekly-report", name="weekly-report")

    result = await sync_slug("sess-5", "weekly-report", "fork")
    assert result.new_slug == "weekly-report-v2"
    assert result.written_path == str(user_skills / "weekly-report-v2")

    forked_md = (user_skills / "weekly-report-v2" / "SKILL.md").read_text("utf-8")
    assert "version: 2" in forked_md


async def test_sync_slug_fork_auto_picks_v3_when_v2_exists(
    staging_root: Path, tmp_path: Path
) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-6", mkdir=True)
    _write_skill(user_skills / "weekly-report", name="weekly-report", version=1)
    _write_skill(user_skills / "weekly-report-v2", name="weekly-report", version=2)
    _write_skill(session_dir / "weekly-report", name="weekly-report")

    result = await sync_slug("sess-6", "weekly-report", "fork")
    assert result.new_slug == "weekly-report-v3"
    assert "version: 3" in (user_skills / "weekly-report-v3" / "SKILL.md").read_text("utf-8")


async def test_sync_slug_fork_with_explicit_new_slug(staging_root: Path, tmp_path: Path) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-7", mkdir=True)
    _write_skill(user_skills / "weekly-report", name="weekly-report", version=1)
    _write_skill(session_dir / "weekly-report", name="weekly-report")

    result = await sync_slug(
        "sess-7", "weekly-report", "fork", new_slug="weekly-report-experimental"
    )
    assert result.new_slug == "weekly-report-experimental"
    assert (user_skills / "weekly-report-experimental" / "SKILL.md").is_file()


async def test_sync_slug_fork_rejects_existing_target(staging_root: Path, tmp_path: Path) -> None:
    user_skills = tmp_path / "user-skills"
    session_dir = await staging.staging_dir_for_session("sess-8", mkdir=True)
    _write_skill(user_skills / "weekly-report-v2", name="anything")
    _write_skill(session_dir / "weekly-report", name="weekly-report")

    with pytest.raises(FileExistsError):
        await sync_slug("sess-8", "weekly-report", "fork", new_slug="weekly-report-v2")


async def test_sync_slug_abort_returns_skipped_marker(staging_root: Path) -> None:
    session_dir = await staging.staging_dir_for_session("sess-9", mkdir=True)
    _write_skill(session_dir / "weekly-report", name="weekly-report")

    result = await sync_slug("sess-9", "weekly-report", "abort")
    assert result.skipped is True
    assert result.written_path is None


async def test_sync_slug_raises_when_staging_slug_missing(staging_root: Path) -> None:
    await staging.staging_dir_for_session("sess-10", mkdir=True)
    with pytest.raises(FileNotFoundError):
        await sync_slug("sess-10", "weekly-report", "overwrite")


async def test_prepare_optimize_copies_and_writes_meta(staging_root: Path, tmp_path: Path) -> None:
    source = tmp_path / "external-skill"
    _write_skill(source, name="external-skill", description="Pre-existing.")

    dest = await prepare_optimize("sess-11", source, "user:external-skill")
    assert dest.is_dir()
    assert (dest / "SKILL.md").is_file()

    meta = read_staging_meta(dest)
    assert meta is not None
    assert meta.intent == "optimize"
    assert meta.source_skill_id == "user:external-skill"
    assert meta.source_content_hash is not None


async def test_prepare_optimize_then_scan_reports_same_source(
    staging_root: Path, tmp_path: Path
) -> None:
    user_skills = tmp_path / "user-skills"
    _write_skill(user_skills / "external-skill", name="external-skill")

    await prepare_optimize("sess-12", user_skills / "external-skill", "user:external-skill")
    s = (await scan_staging("sess-12")).slugs[0]
    assert s.conflict_kind == "same_source"
    assert s.source_skill_id == "user:external-skill"


async def test_scan_reports_version_from_frontmatter(staging_root: Path) -> None:
    session_dir = await staging.staging_dir_for_session("sess-13", mkdir=True)
    _write_skill(session_dir / "weekly-report", name="weekly-report", version=4)

    s = (await scan_staging("sess-13")).slugs[0]
    assert s.version == 4


async def test_invalid_session_id_rejected(staging_root: Path) -> None:
    with pytest.raises(ValueError):
        await staging.staging_dir_for_session("../escape")
    with pytest.raises(ValueError):
        await staging.staging_dir_for_session(".dotfile")


async def test_remove_slug_and_session(staging_root: Path) -> None:
    session_dir = await staging.staging_dir_for_session("sess-14", mkdir=True)
    _write_skill(session_dir / "alpha", name="alpha")
    _write_skill(session_dir / "beta", name="beta")

    await staging.remove_slug("sess-14", "alpha")
    assert not (session_dir / "alpha").exists()
    assert (session_dir / "beta").exists()

    await staging.remove_session_staging("sess-14")
    assert not session_dir.exists()
