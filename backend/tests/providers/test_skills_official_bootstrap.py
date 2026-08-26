"""Tests for the bundled official-skills bootstrap sync."""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.integrations import skills_official_bootstrap as bootstrap
from valuz_agent.integrations.skills_official import OfficialSkillSource

USER = "test-user"


@pytest.fixture(autouse=True)
def _isolated_official_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from valuz_agent.infra import fs_registry as fsr

    data_dir = tmp_path / "data"
    monkeypatch.setattr(fsr.settings, "data_dir", data_dir)
    return data_dir / "official-skills"


def test_sync_installs_bundled_skill_creator_on_first_run(_isolated_official_dir: Path) -> None:
    installed = bootstrap.sync_bundled_official_skills(USER)

    assert "skill-creator" in installed
    skill_dir = _isolated_official_dir / "skill-creator"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "LICENSE.txt").is_file()
    assert (skill_dir / ".bundled-version").is_file()


def test_sync_installs_builtin_skills_alongside_official(_isolated_official_dir: Path) -> None:
    """Builtin skills (valuz-project-docs, citation, browser) land in the SAME per-user
    official-skills dir — no separate directory — so an install that predates
    system skill roots keeps resolving them from the mounted subtree.
    """
    installed = bootstrap.sync_bundled_official_skills(USER)

    assert "valuz-project-docs" in installed
    assert "citation" in installed
    assert "browser" in installed
    docs_dir = _isolated_official_dir / "valuz-project-docs"
    assert (docs_dir / "SKILL.md").is_file()
    assert (docs_dir / ".bundled-version").is_file()
    assert (_isolated_official_dir / "browser" / "SKILL.md").is_file()
    assert (_isolated_official_dir / "citation" / "SKILL.md").is_file()
    assert (_isolated_official_dir / "citation" / "references" / "protocol.md").is_file()


def test_accessors_prefer_a_declared_shipped_package_over_a_per_user_copy(
    _isolated_official_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy is not the authority once a deployment declares a system root.

    An install that ran the old bootstrap keeps its copies, and they must not
    shadow the version the release actually carries.
    """
    from valuz_agent.adapters.capability_resolver import citation_skill_dir
    from valuz_agent.infra import fs_registry as fsr
    from valuz_agent.infra.fs_registry import fs_registry

    bootstrap.sync_bundled_official_skills(USER)
    shipped = tmp_path / "opt" / "citation"
    shipped.mkdir(parents=True)
    (shipped / "SKILL.md").write_text("---\nname: citation\n---\n", encoding="utf-8")
    monkeypatch.setattr(fsr.settings, "system_skills_dir", str(tmp_path / "opt"))

    assert fs_registry.system_skill_roots() == ((tmp_path / "opt").resolve(),)
    assert citation_skill_dir(USER).resolve(strict=False) == shipped.resolve()


def test_accessor_uses_the_per_user_copy_when_no_root_is_declared(
    _isolated_official_dir: Path,
) -> None:
    """The default. Nothing declared → today's behaviour, unchanged."""
    from valuz_agent.adapters.capability_resolver import citation_skill_dir

    bootstrap.sync_bundled_official_skills(USER)

    assert citation_skill_dir(USER).resolve(strict=False) == (
        _isolated_official_dir / "citation"
    ).resolve(strict=False)


def test_sync_with_user_id_installs_into_templated_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path / "data" / "{user_id}")

    installed = bootstrap.sync_bundled_official_skills(user_id="org/user-A")

    assert "skill-creator" in installed
    skill_dir = tmp_path / "data" / "org__user-A" / "official-skills" / "skill-creator"
    assert (skill_dir / "SKILL.md").is_file()
    assert not (tmp_path / "data" / "official-skills" / "skill-creator").exists()


def test_sync_is_idempotent_when_marker_matches(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills(USER)
    second = bootstrap.sync_bundled_official_skills(USER)
    assert second == []  # nothing reinstalled


def test_sync_reinstalls_when_marker_disagrees(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills(USER)
    marker = _isolated_official_dir / "skill-creator" / ".bundled-version"
    marker.write_text("stale-hash", encoding="utf-8")

    second = bootstrap.sync_bundled_official_skills(USER)
    assert "skill-creator" in second
    # marker should now be back to the real hash, i.e. != "stale-hash"
    assert marker.read_text(encoding="utf-8").strip() != "stale-hash"


def test_is_bundled_skill_detects_marker(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills(USER)
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

    bootstrap.sync_bundled_official_skills(USER)
    manifests = OfficialSkillSource().list_skills(RuntimeContext(user_id=USER))

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

    manifests = OfficialSkillSource().list_skills(RuntimeContext(user_id=USER))
    third = next((m for m in manifests if m.slug == "third-party-skill"), None)
    assert third is not None
    assert third.is_locked is True
    assert third.origin_label == "Official"


def test_data_dir_controls_install_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    data_dir = tmp_path / "custom-data"
    monkeypatch.setattr(fsr.settings, "data_dir", data_dir)

    installed = bootstrap.sync_bundled_official_skills(USER)
    assert "skill-creator" in installed
    assert (data_dir / "official-skills" / "skill-creator" / "SKILL.md").is_file()


def test_copy_never_deletes_before_it_writes(
    _isolated_official_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The corruption this replaced: a concurrent writer's delete phase erased a
    finished copy while the marker survived, certifying a package that was gone."""
    bootstrap.sync_bundled_official_skills(USER)
    landed = _isolated_official_dir / "skill-creator" / "SKILL.md"
    assert landed.is_file()

    observed: list[bool] = []
    real_copytree = bootstrap.shutil.copytree

    def _watch(*args: object, **kwargs: object) -> object:
        observed.append(landed.is_file())
        return real_copytree(*args, **kwargs)

    marker = _isolated_official_dir / "skill-creator" / bootstrap.BUNDLED_VERSION_FILE
    marker.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(bootstrap.shutil, "copytree", _watch)

    bootstrap.sync_bundled_official_skills(USER)

    assert observed and all(observed), "the package vanished while it was being replaced"
    assert landed.is_file()


def test_a_package_that_lost_its_manifest_is_re_landed(_isolated_official_dir: Path) -> None:
    """A valid marker must not certify a damaged package forever."""
    bootstrap.sync_bundled_official_skills(USER)
    skill_dir = _isolated_official_dir / "skill-creator"
    (skill_dir / "SKILL.md").unlink()
    assert (skill_dir / bootstrap.BUNDLED_VERSION_FILE).is_file()  # marker still valid

    second = bootstrap.sync_bundled_official_skills(USER)

    assert "skill-creator" in second
    assert (skill_dir / "SKILL.md").is_file()


def test_a_file_the_package_dropped_is_removed(_isolated_official_dir: Path) -> None:
    bootstrap.sync_bundled_official_skills(USER)
    stray = _isolated_official_dir / "skill-creator" / "left-over.md"
    stray.write_text("from an older version", encoding="utf-8")
    (_isolated_official_dir / "skill-creator" / bootstrap.BUNDLED_VERSION_FILE).write_text(
        "stale", encoding="utf-8"
    )

    bootstrap.sync_bundled_official_skills(USER)

    assert not stray.exists()
    assert (_isolated_official_dir / "skill-creator" / "SKILL.md").is_file()


def test_copy_retries_a_transient_filesystem_error(
    _isolated_official_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = {"n": 0}
    real_copytree = bootstrap.shutil.copytree

    def _flaky(*args: object, **kwargs: object) -> object:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError(5, "Input/output error")
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(bootstrap.shutil, "copytree", _flaky)

    installed = bootstrap.sync_bundled_official_skills(USER)

    assert "skill-creator" in installed
    assert (_isolated_official_dir / "skill-creator" / "SKILL.md").is_file()
