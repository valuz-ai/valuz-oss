"""Tests for the capability resolver — focused on the chat-project
user-library skill auto-include behavior added to fix the
"Unknown skill: <slug>" error in chat sessions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

# Side-effect import — surfaces ``src.core...`` on sys.path before the
# resolver tries to import ``McpServerConfig`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.capability_resolver import (
    always_on_skill_paths,
    browser_skill_dir,
    project_docs_skill_dir,
    resolve_session_capabilities,
)
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest

USER = "test-user"

# Path to the builtin valuz-project-docs skill that the resolver auto-injects
# into every session (chat + project). Now resolves under the per-user
# official-skills dir (materialized by ``sync_bundled_official_skills``), NOT the
# package source. Tests filter it out before asserting against the user-controlled
# skill set so they stay focused on the behavior under test.
def _docs_skill_path() -> str:
    """Live path to the materialized valuz-project-docs skill (reads current data_dir)."""
    return str(project_docs_skill_dir(USER).resolve(strict=False))


@pytest.fixture(autouse=True)
def _materialize_baseline_skills(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point ``data_dir`` at a hermetic tmp root and materialize the bundled
    baseline skills there.

    ``always_on_skill_paths`` now returns the MATERIALIZED per-user
    official-skills paths (valuz-project-docs / browser / skill-creator), each
    gated on ``is_dir()``. Without a real ``sync_bundled_official_skills`` these
    dirs wouldn't exist and the always-on baseline would be silently empty — so
    every test in this module runs the sync into an isolated data root.
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.integrations.skills_official_bootstrap import sync_bundled_official_skills

    root = tmp_path_factory.mktemp("caps-data")
    monkeypatch.setattr(settings, "data_dir", root / "{user_id}")
    sync_bundled_official_skills(USER)


def _user_skills(caps_skills: tuple[str, ...]) -> tuple[str, ...]:
    """Strip the always-on baseline skills (valuz-project-docs + skill-creator)
    so the remaining tuple reflects only what the resolver picked up from
    user / extras / library."""
    baseline = set(always_on_skill_paths(user_id=USER)) | {_docs_skill_path()}
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

    def __init__(
        self,
        enabled_paths: set[str] | None = None,
        rows_by_id: dict[str, object] | None = None,
        rows_by_slug: dict[str, object] | None = None,
    ) -> None:
        self._enabled_paths = enabled_paths or set()
        self._rows_by_id = rows_by_id or {}
        self._rows_by_slug = rows_by_slug or {}

    def enabled_skill_paths(self, project: _FakeProject) -> set[str]:
        if project.kind != "project":
            return set()
        return self._enabled_paths

    async def get_by_id(self, user_id: str, skill_id: str):  # noqa: ANN201 — matches real signature
        return self._rows_by_id.get(skill_id)

    async def get_by_slug(self, user_id: str, slug: str):  # noqa: ANN201 — matches real signature
        return self._rows_by_slug.get(slug)


class _FakeSkillSource:
    """Minimal stand-in for FilesystemSkillSource — returns whatever manifests
    the test injects, without touching the filesystem.
    """

    def __init__(self, manifests: list[SkillManifest]) -> None:
        self._manifests = manifests
        self.calls: list[RuntimeContext] = []

    def list_skills(
        self, ctx: RuntimeContext, *, compute_content_hash: bool = True
    ) -> list[SkillManifest]:
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
            user_id=USER,
            skill_source=_FakeSkillSource([_manifest_for(skill_dir)]),
        )
    )

    assert _user_skills(caps.skills) == (str(skill_dir.resolve(strict=False)),)
    assert _docs_skill_path() in caps.skills


def test_extra_skill_id_resolves_manifest_id_by_slug(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(tmp_path, "skill-creator")
    project = _FakeProject(id="ws-project", kind="project", root_path=str(tmp_path))
    row = SimpleNamespace(source_path=str(skill_dir.resolve(strict=False)))

    caps = asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore(rows_by_slug={"skill-creator": row}),
            project_id="ws-project",
            user_id=USER,
            extra_skill_ids=["official:skill-creator"],
        )
    )

    assert str(skill_dir.resolve(strict=False)) in caps.skills
    assert caps.skill_resolution_warnings == ()


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
            user_id=USER,
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
            user_id=USER,
            skill_source=None,
        )
    )

    # No user / extras input → only the always-on docs skill remains.
    assert _user_skills(caps.skills) == ()
    assert _docs_skill_path() in caps.skills


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
            user_id=USER,
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
            user_id=USER,
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
            user_id=USER,
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
            user_id=USER,
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
            user_id=USER,
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
            user_id=USER,
            extra_skill_sources=[_FakeSkillSource([bundled])],
            official_entitled=True,
        )
    )

    # The valuz-project-docs builtin may be present (auto-injected for
    # projects); only the bundled official skill must NOT be.
    assert str(skill_dir.resolve(strict=False)) not in caps.skills


def test_browser_skill_gated_on_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The always-on browser skill is injected only when the browser engine
    (Node + chrome-devtools-mcp) is available, so headless/TUI hosts without
    Node don't advertise a dead skill. See docs/design/browser-feature.md §8."""
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.browser import service as browser_service

    # always_on now returns the MATERIALIZED path under official_skill_root
    # (produced by ``sync_bundled_official_skills``); create it under a tmp data
    # root so the presence assertion is meaningful and hermetic.
    monkeypatch.setattr(settings, "data_dir", tmp_path / "{user_id}")
    (fs_registry.official_skill_root(user_id=USER) / "browser").mkdir(parents=True, exist_ok=True)
    browser_path = str(browser_skill_dir(USER).resolve(strict=False))

    monkeypatch.setattr(browser_service, "node_available", lambda: True)
    assert browser_path in always_on_skill_paths(user_id=USER)

    monkeypatch.setattr(browser_service, "node_available", lambda: False)
    assert browser_path not in always_on_skill_paths(user_id=USER)


def test_unknown_project_raises_key_error() -> None:
    project = _FakeProject(id="ws-existing", kind="chat", root_path=None)

    with pytest.raises(KeyError):
        asyncio.run(
            resolve_session_capabilities(
                projects=_FakeProjectDatastore(project),
                skills=_FakeSkillDatastore(),
                project_id="ws-missing",
                user_id=USER,
            )
        )


def test_the_resolver_never_asks_for_a_content_hash(tmp_path: Path) -> None:
    """It reads path / scope / origin_label and nothing else.

    Computing the hash reads every file of every package — 21-28 s on a managed
    deployment's network mount, paid at session creation.
    """
    asked: list[bool] = []

    class _Recording:
        def list_skills(
            self, ctx: RuntimeContext, *, compute_content_hash: bool = True
        ) -> list[SkillManifest]:
            asked.append(compute_content_hash)
            return []

    project = _FakeProject(id="ws-chat", kind="chat", root_path=None)

    asyncio.run(
        resolve_session_capabilities(
            projects=_FakeProjectDatastore(project),
            skills=_FakeSkillDatastore([]),
            project_id="ws-chat",
            user_id="u1",
            skill_source=_Recording(),
            extra_skill_sources=[_Recording()],
        )
    )

    assert asked == [False, False]
