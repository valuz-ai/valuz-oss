"""Read-only roots for the skill packages that ship with an install.

A bundled package is a release artifact — identical bytes for every user,
read-only, versioned with the release. Resolving it from one shared location is
what keeps a multi-user deployment from having to make N copies of immutable
content converge.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from valuz_agent.infra.fs_registry import fs_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    fs_registry.clear_system_skill_roots()
    yield
    fs_registry.clear_system_skill_roots()


def _tree(root: Path, *slugs: str) -> Path:
    for slug in slugs:
        (root / slug).mkdir(parents=True, exist_ok=True)
        (root / slug / "SKILL.md").write_text(f"---\nname: {slug}\n---\n", encoding="utf-8")
    return root


def test_empty_until_a_deployment_declares_one() -> None:
    """Declared, never inferred.

    A system root must be visible to every process that materializes a package,
    including a kernel inside a sandbox that mounts only the owner's subtrees.
    Nothing here can tell such a deployment apart from a desktop install, so an
    undeclared root stays empty and resolution falls back to the per-user root.
    """
    assert fs_registry.system_skill_roots() == ()
    assert fs_registry.find_system_skill("skill-creator") is None


def test_env_declares_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What a container image sets after composing its trees into one dir."""
    from valuz_agent.infra import fs_registry as fsr

    composed = _tree(tmp_path / "opt", "stock-analysis")
    monkeypatch.setattr(fsr.settings, "system_skills_dir", str(composed))

    assert fs_registry.system_skill_roots() == (composed.resolve(),)
    assert fs_registry.find_system_skill("stock-analysis") == composed / "stock-analysis"
    assert fs_registry.find_system_skill("skill-creator") is None


def test_env_accepts_several_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    a = _tree(tmp_path / "a", "citation")
    b = _tree(tmp_path / "b", "stock-analysis")
    monkeypatch.setattr(fsr.settings, "system_skills_dir", f"{a}{os.pathsep}{b}")

    assert fs_registry.system_skill_roots() == (a.resolve(), b.resolve())
    assert fs_registry.find_system_skill("stock-analysis") == b / "stock-analysis"


def test_registered_roots_are_appended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """How an overlay declares an edition's tree without owning the env var."""
    from valuz_agent.infra import fs_registry as fsr

    base = _tree(tmp_path / "base", "citation")
    edition = _tree(tmp_path / "edition", "stock-analysis")
    monkeypatch.setattr(fsr.settings, "system_skills_dir", str(base))

    fs_registry.register_system_skill_root(edition)
    fs_registry.register_system_skill_root(edition)  # idempotent

    assert fs_registry.system_skill_roots() == (base.resolve(), edition.resolve())
    assert fs_registry.find_system_skill("stock-analysis") == edition / "stock-analysis"


def test_a_declared_root_that_does_not_exist_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.infra import fs_registry as fsr

    present = _tree(tmp_path / "present", "citation")
    monkeypatch.setattr(
        fsr.settings, "system_skills_dir", f"{tmp_path / 'absent'}{os.pathsep}{present}"
    )

    assert fs_registry.system_skill_roots() == (present.resolve(),)


def test_unknown_slug_resolves_to_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "system_skills_dir", str(_tree(tmp_path / "r", "citation")))

    assert fs_registry.find_system_skill("no-such-package") is None
