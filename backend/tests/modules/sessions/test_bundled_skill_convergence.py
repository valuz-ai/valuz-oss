"""Regression: a bundled package that lands mid-session must still reach it.

``resolve_session_capabilities`` injects every bundled official package into
every session, but it only runs at create time. The packages themselves are
written out of band — a release that adds one, or a managed deployment landing
an owner's official-skills tree for the first time. A session created in that
window would otherwise be the only one on the installation that cannot see the
package, for its whole life.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.schemas import (  # type: ignore[import-not-found]
    AgentConfigSchema,
    SessionData,
)

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.modules.sessions import capabilities

BUNDLED_VERSION_FILE = ".bundled-version"


def _make_session(*, skills: list[str], status: str = "idle") -> SessionData:
    return SessionData(
        id="sess-1",
        agent_config=AgentConfigSchema(id="agent-1", name="a"),
        cwd="/tmp/bundled-convergence",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        instructions="",
        skills=list(skills),
        mcp_servers=[],
        permission_mode="full_access",
        status=status,
        created_at=0,
        user_id="owner-1",
        metadata={},
    )


def _bundled(root: Path, slug: str) -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {slug}\n---\n", encoding="utf-8")
    (skill_dir / BUNDLED_VERSION_FILE).write_text("hash", encoding="utf-8")
    return skill_dir


@pytest.fixture(autouse=True)
def _clean_cache():
    capabilities.reset_bundled_paths_cache()
    yield
    capabilities.reset_bundled_paths_cache()


@pytest.fixture
def official_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from valuz_agent.infra.fs_registry import fs_registry

    root = tmp_path / "official-skills"
    root.mkdir()
    monkeypatch.setattr(fs_registry, "official_skill_root", lambda *, user_id: root)
    return root


@pytest.fixture
def patched_kernel(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"session": None, "updates": []}

    async def _get_session(user_id: str, session_id: str) -> Any:
        return state["session"]

    async def _update_session(user_id: str, session_id: str, request: Any) -> None:
        state["updates"].append(request)

    monkeypatch.setattr(capabilities.kernel_client, "get_session", _get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", _update_session)
    return state


@pytest.mark.asyncio
async def test_package_that_landed_after_creation_is_attached(
    official_root: Path, patched_kernel: dict[str, Any]
) -> None:
    already = _bundled(official_root, "skill-creator")
    patched_kernel["session"] = _make_session(skills=[str(already.resolve())])
    landed = _bundled(official_root, "6-step-valuation-research")

    assert await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1") is True

    skills = patched_kernel["updates"][0].skills
    assert str(landed.resolve()) in skills
    assert str(already.resolve()) in skills


@pytest.mark.asyncio
async def test_no_write_when_the_session_already_has_every_package(
    official_root: Path, patched_kernel: dict[str, Any]
) -> None:
    a = _bundled(official_root, "skill-creator")
    b = _bundled(official_root, "citation")
    patched_kernel["session"] = _make_session(skills=[str(a.resolve()), str(b.resolve())])

    assert await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1") is False
    assert patched_kernel["updates"] == []


@pytest.mark.asyncio
async def test_only_bundled_directories_are_attached(
    official_root: Path, patched_kernel: dict[str, Any]
) -> None:
    """A directory without the marker is not an App-managed package."""
    _bundled(official_root, "skill-creator")
    stray = official_root / "hand-placed"
    stray.mkdir()
    (stray / "SKILL.md").write_text("---\nname: hand-placed\n---\n", encoding="utf-8")
    patched_kernel["session"] = _make_session(skills=[])

    await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1")

    skills = patched_kernel["updates"][0].skills
    assert str(stray.resolve()) not in skills


@pytest.mark.asyncio
async def test_entries_the_session_carries_are_never_removed(
    official_root: Path, patched_kernel: dict[str, Any]
) -> None:
    _bundled(official_root, "skill-creator")
    attached = "/somewhere/else/a-user-skill"
    patched_kernel["session"] = _make_session(skills=[attached])

    await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1")

    assert attached in patched_kernel["updates"][0].skills


@pytest.mark.asyncio
async def test_terminated_session_is_left_alone(
    official_root: Path, patched_kernel: dict[str, Any]
) -> None:
    _bundled(official_root, "skill-creator")
    patched_kernel["session"] = _make_session(skills=[], status="terminated")

    assert await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1") is False
    assert patched_kernel["updates"] == []


@pytest.mark.asyncio
async def test_missing_official_root_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patched_kernel: dict[str, Any]
) -> None:
    from valuz_agent.infra.fs_registry import fs_registry

    monkeypatch.setattr(fs_registry, "official_skill_root", lambda *, user_id: tmp_path / "absent")
    patched_kernel["session"] = _make_session(skills=[])

    assert await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1") is False


@pytest.mark.asyncio
async def test_the_package_listing_is_not_walked_on_every_turn(
    official_root: Path, patched_kernel: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is a network mount where it matters — 600 ms per walk on valuz-prod."""
    a = _bundled(official_root, "skill-creator")
    patched_kernel["session"] = _make_session(skills=[str(a.resolve())])

    walks = {"n": 0}
    real_iterdir = Path.iterdir

    def _counted(self: Path):  # type: ignore[no-untyped-def]
        if self == official_root:
            walks["n"] += 1
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _counted)

    for _ in range(5):
        await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1")

    assert walks["n"] == 1, "the root was re-walked on a later turn"


@pytest.mark.asyncio
async def test_a_package_that_lands_mid_session_invalidates_the_listing(
    official_root: Path, patched_kernel: dict[str, Any]
) -> None:
    a = _bundled(official_root, "skill-creator")
    patched_kernel["session"] = _make_session(skills=[str(a.resolve())])
    assert await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1") is False

    landed = _bundled(official_root, "6-step-valuation-research")  # changes root mtime

    assert await capabilities.refresh_bundled_skills_for_session("sess-1", "owner-1") is True
    assert str(landed.resolve()) in patched_kernel["updates"][0].skills
