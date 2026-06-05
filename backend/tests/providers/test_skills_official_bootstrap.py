"""Tests for the bundled official-skills bootstrap sync."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from valuz_agent.integrations import skills_official_bootstrap as bootstrap
from valuz_agent.integrations.skills_official import OfficialSkillSource


@pytest.fixture(autouse=True)
def _isolated_official_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "official-skills"
    monkeypatch.setenv("VALUZ_OFFICIAL_SKILLS_DIR", str(target))
    return target


def test_sync_installs_bundled_skill_creator_on_first_run(_isolated_official_dir: Path) -> None:
    installed = bootstrap.sync_bundled_official_skills()

    assert "skill-creator" in installed
    skill_dir = _isolated_official_dir / "skill-creator"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "LICENSE.txt").is_file()
    assert (skill_dir / ".bundled-version").is_file()


def test_sync_is_idempotent_when_marker_matches(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills()
    second = bootstrap.sync_bundled_official_skills()
    assert second == []  # nothing reinstalled


def test_sync_reinstalls_when_marker_disagrees(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills()
    marker = _isolated_official_dir / "skill-creator" / ".bundled-version"
    marker.write_text("stale-hash", encoding="utf-8")

    second = bootstrap.sync_bundled_official_skills()
    assert "skill-creator" in second
    # marker should now be back to the real hash, i.e. != "stale-hash"
    assert marker.read_text(encoding="utf-8").strip() != "stale-hash"


def test_is_bundled_skill_detects_marker(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills()
    skill_dir = _isolated_official_dir / "skill-creator"
    assert bootstrap.is_bundled_skill(skill_dir)
    # A directory without the marker should be reported as not-bundled.
    other = _isolated_official_dir / "user-imported"
    other.mkdir()
    assert not bootstrap.is_bundled_skill(other)


def test_official_source_marks_bundled_skills_as_unlocked(
    _isolated_official_dir: Path,
) -> None:
    from valuz_agent.modules.skills.contracts import RuntimeContext

    bootstrap.sync_bundled_official_skills()
    manifests = OfficialSkillSource().list_skills(RuntimeContext())

    bundled = [m for m in manifests if m.slug == "skill-creator"]
    assert len(bundled) == 1
    m = bundled[0]
    assert m.is_locked is False
    assert m.lock_reason is None
    assert m.origin_label == "Built-in"
    assert m.readonly is True  # still read-only — users must Copy to edit


def test_official_source_keeps_non_bundled_skills_locked(
    _isolated_official_dir: Path,
) -> None:
    """A skill placed in the official dir without our marker stays locked."""
    from valuz_agent.modules.skills.contracts import RuntimeContext

    fake_skill = _isolated_official_dir / "third-party-skill"
    fake_skill.mkdir(parents=True)
    (fake_skill / "SKILL.md").write_text(
        "---\nname: third-party-skill\ndescription: External skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )

    manifests = OfficialSkillSource().list_skills(RuntimeContext())
    third = next((m for m in manifests if m.slug == "third-party-skill"), None)
    assert third is not None
    assert third.is_locked is True
    assert third.origin_label == "Official"


def test_environment_override_redirects_install_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-official"
    monkeypatch.setenv("VALUZ_OFFICIAL_SKILLS_DIR", str(custom))

    installed = bootstrap.sync_bundled_official_skills()
    assert "skill-creator" in installed
    assert (custom / "skill-creator" / "SKILL.md").is_file()
    # Ensure it didn't leak into the default location for this run.
    assert os.environ["VALUZ_OFFICIAL_SKILLS_DIR"] == str(custom)
