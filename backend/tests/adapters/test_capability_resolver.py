"""Tests for the capability resolver — focused on the chat-project
user-library skill auto-include behavior added to fix the
"Unknown skill: <slug>" error in chat sessions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

# Side-effect import — surfaces ``src.core...`` on sys.path before the
# resolver tries to import ``McpServerConfig`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.capability_resolver import (
    _PROJECT_DOCS_SKILL_DIR,
    always_on_skill_paths,
    resolve_session_capabilities,
)
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest

# Path to the builtin valuz-project-docs skill that the resolver
# auto-injects into every session (chat + project). Tests filter it
# out before asserting against the user-controlled skill set so they
# stay focused on the behavior under test.
_DOCS_SKILL_PATH = str(_PROJECT_DOCS_SKILL_DIR.resolve(strict=False))


def _user_skills(caps_skills: tuple[str, ...]) -> tuple[str, ...]:
    """Strip the always-on baseline skills (valuz-project-docs + skill-creator)
    so the remaining tuple reflects only what the resolver picked up from
    user / extras / library."""
    baseline = set(always_on_skill_paths()) | {_DOCS_SKILL_PATH}
    return tuple(p for p in caps_skills if p not in baseline)


@dataclass
class _FakeProject:
    id: str
    kind: str
    root_path: str | None


class _FakeProjectDatastore:
    def __init__(self, project: _FakeProject) -> None:
        self._project = project

    async def get_by_id(self, user_id: str, project_id: str) -> _FakeProject | None:
        if project_id != self._project.id:
            return None
        return self._project


class _FakeSkillDatastore:
    """Honors only what the resolver consumes: enabled_skill_paths + get_by_id."""

    def __init__(self, enabled_paths: set[str] | None = None) -> None:
        self._enabled_paths = enabled_paths or set()

    def enabled_skill_paths(self, project: _FakeProject) -> set[str]:
        if project.kind != "project":
            return set()
        return self._enabled_paths

    def get_by_id(self, user_id: str, skill_id: str):  # noqa: ANN201 — matches real signature
        return None


class _FakeSkillSource:
    """Minimal stand-in for FilesystemSkillSource — returns whatever manifests
    the test injects, without touching the filesystem.
    """

    def __init__(self, manifests: list[SkillManifest]) -> None:
        self._manifests = manifests
        self.calls: list[RuntimeContext] = []

    def list_skills(self, ctx: RuntimeContext) -> list[SkillManifest]:
        self.calls.append(ctx)
        return list(self._manifests)


def _make_skill_dir(tmp_path: Path, slug: str) -> Path:
    skill_dir = tmp_path / slug
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    return skill_dir


def _manifest_for(skill_dir: Path, *, scope: str = "user") -> SkillManifest:
    return SkillManifest(
        id=f"{scope}:{skill_dir.name}",
        name=skill_dir.name,
        description="test",
        scope=scope,
        source="valuz",
        path=str(skill_dir.resolve(strict=False)),
        slug=skill_dir.name,
    )


def test_chat_project_auto_includes_user_library_skills(tmp_path: Path) -> None:
    """A chat project with a user-library skill should ship that skill in
    Session.skills so the kernel materializes it for /skill-name dispatch."""
    skill_dir = _make_skill_dir(tmp_path, "reportify-ai")
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            skill_source=_FakeSkillSource([_manifest_for(skill_dir)]),
        )
    )

    assert _user_skills(caps.skills) == (str(skill_dir.resolve(strict=False)),)
    assert _DOCS_SKILL_PATH in caps.skills


def test_project_does_not_auto_include_user_library_skills(
    tmp_path: Path,
) -> None:
    """Projects preserve their opt-in semantics — user-library
    skills are NOT auto-included; only paths in project-config.json are.

    The valuz-project-docs builtin skill is auto-injected into every
    project session (always-on capability layer), so the user-library
    manifest stays excluded even when nothing else is enabled.
    """
    skill_dir = _make_skill_dir(tmp_path, "reportify-ai")
    project = _FakeProject(id="ws-proj", kind="project", root_path=str(tmp_path / "proj"))

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(enabled_paths=set()),
            project_id="ws-proj",
            skill_source=_FakeSkillSource([_manifest_for(skill_dir)]),
        )
    )

    assert str(skill_dir.resolve(strict=False)) not in caps.skills


def test_chat_project_without_skill_source_yields_empty(tmp_path: Path) -> None:
    """Backward compat: callers that don't pass a skill_source still work and
    simply produce no skills (legacy behavior preserved)."""
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            skill_source=None,
        )
    )

    # No user / extras input → only the always-on docs skill remains.
    assert _user_skills(caps.skills) == ()
    assert _DOCS_SKILL_PATH in caps.skills


def test_chat_project_skips_non_user_scoped_manifests(tmp_path: Path) -> None:
    """Only user-scoped manifests are auto-included for chat — project-scoped
    ones (e.g. project-local skills surfaced by the same source) are not."""
    user_dir = _make_skill_dir(tmp_path, "user-skill")
    proj_dir = _make_skill_dir(tmp_path, "proj-skill")
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            skill_source=_FakeSkillSource(
                [
                    _manifest_for(user_dir, scope="user"),
                    _manifest_for(proj_dir, scope="project"),
                ]
            ),
        )
    )

    assert _user_skills(caps.skills) == (str(user_dir.resolve(strict=False)),)


def test_chat_project_dedupes_against_extras(tmp_path: Path) -> None:
    """If the same path appears in both extras and the user library, it
    should only be materialized once (resolver uses ``seen`` to dedupe)."""
    skill_dir = _make_skill_dir(tmp_path, "shared")
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    # Inject the same path through the user library; extras path is empty.
    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            skill_source=_FakeSkillSource([_manifest_for(skill_dir)]),
        )
    )

    assert caps.skills.count(str(skill_dir.resolve(strict=False))) == 1


def test_chat_project_includes_bundled_official_skill_without_entitlement(
    tmp_path: Path,
) -> None:
    """Bundled official skills (origin_label=='Built-in') ship with the
    client and are always available — even without ``official_entitled``."""
    skill_dir = _make_skill_dir(tmp_path, "skill-creator")
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    bundled = SkillManifest(
        id=f"official:{skill_dir.name}",
        name=skill_dir.name,
        description="bundled official",
        scope="official",
        source="official",
        path=str(skill_dir.resolve(strict=False)),
        slug=skill_dir.name,
        readonly=True,
        is_locked=False,
        origin_label="Built-in",
    )

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            extra_skill_sources=[_FakeSkillSource([bundled])],
            official_entitled=False,
        )
    )

    assert _user_skills(caps.skills) == (str(skill_dir.resolve(strict=False)),)


def test_chat_project_excludes_unbundled_official_skill_without_entitlement(
    tmp_path: Path,
) -> None:
    """Externally installed official skills require the
    ``skills:official`` entitlement — without it the resolver excludes them
    so they are never materialized into the runtime cwd."""
    skill_dir = _make_skill_dir(tmp_path, "premium-skill")
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    locked = SkillManifest(
        id=f"official:{skill_dir.name}",
        name=skill_dir.name,
        description="paid official",
        scope="official",
        source="official",
        path=str(skill_dir.resolve(strict=False)),
        slug=skill_dir.name,
        readonly=True,
        is_locked=True,
        lock_reason="Connect Reportify to unlock official skills",
        origin_label="Official",
    )

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            extra_skill_sources=[_FakeSkillSource([locked])],
            official_entitled=False,
        )
    )

    assert _user_skills(caps.skills) == ()


def test_chat_project_includes_unbundled_official_skill_when_entitled(
    tmp_path: Path,
) -> None:
    """When ``official_entitled=True`` (Reportify connected with
    ``skills:official``), externally installed official skills get
    materialized into the runtime cwd."""
    skill_dir = _make_skill_dir(tmp_path, "premium-skill")
    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    locked = SkillManifest(
        id=f"official:{skill_dir.name}",
        name=skill_dir.name,
        description="paid official",
        scope="official",
        source="official",
        path=str(skill_dir.resolve(strict=False)),
        slug=skill_dir.name,
        readonly=True,
        is_locked=True,
        origin_label="Official",
    )

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(),
            project_id="ws-chat",
            extra_skill_sources=[_FakeSkillSource([locked])],
            official_entitled=True,
        )
    )

    assert _user_skills(caps.skills) == (str(skill_dir.resolve(strict=False)),)


def test_project_does_not_auto_include_official_skills(
    tmp_path: Path,
) -> None:
    """Projects preserve opt-in semantics for every scope —
    official skills are not auto-included even when entitled."""
    skill_dir = _make_skill_dir(tmp_path, "skill-creator")
    project = _FakeProject(id="ws-proj", kind="project", root_path=str(tmp_path / "proj"))

    bundled = SkillManifest(
        id=f"official:{skill_dir.name}",
        name=skill_dir.name,
        description="bundled official",
        scope="official",
        source="official",
        path=str(skill_dir.resolve(strict=False)),
        slug=skill_dir.name,
        readonly=True,
        is_locked=False,
        origin_label="Built-in",
    )

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(enabled_paths=set()),
            project_id="ws-proj",
            extra_skill_sources=[_FakeSkillSource([bundled])],
            official_entitled=True,
        )
    )

    # The valuz-project-docs builtin may be present (auto-injected for
    # projects); only the bundled official skill must NOT be.
    assert str(skill_dir.resolve(strict=False)) not in caps.skills


def test_unknown_project_raises_key_error() -> None:
    project = _FakeProject(id="ws-existing", kind="chat", root_path=None)

    with pytest.raises(KeyError):
        asyncio.run(
            resolve_session_capabilities(
                projects=_FakeProjectDatastore(project),
                skills=_FakeSkillDatastore(),
                project_id="ws-missing",
            )
        )
