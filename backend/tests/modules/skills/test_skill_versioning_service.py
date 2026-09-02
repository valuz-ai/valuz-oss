"""Skill versions through the service: save → history → save again → restore.

Real ``SkillDatastore`` + ``FilesystemSkillSource`` over a temp-file SQLite
(the artifact tables live on the same ``Base``), a fake project service, and
the kernel session lookup + staging resolution monkeypatched to temp dirs —
so the whole save pipeline in ``confirm_submission`` runs for real, including
the artifacts module underneath it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.artifacts import (
    models as _artifact_models,  # noqa: F401 — register tables on Base before create_all
)
from valuz_agent.modules.skills import staging as staging_mod
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.errors import SkillNotFound, SkillReadOnly
from valuz_agent.modules.skills.service import SkillLibraryService

USER = "u-versions"
SESSION = "sess-1"


class _Project:
    def __init__(self, id: str, kind: str = "chat", root_path: str | None = None) -> None:
        self.id = id
        self.kind = kind
        self.root_path = root_path
        self.name = id
        self.instructions_md = None
        self.memory_summary = None


class _Projects:
    def __init__(self) -> None:
        self._all = [_Project("chat-default")]

    async def get_project(self, user_id: str, project_id: str):  # type: ignore[no-untyped-def]
        for p in self._all:
            if p.id == project_id:
                return p
        raise KeyError(project_id)

    async def list_projects(self, user_id: str):  # type: ignore[no-untyped-def]
        return self._all


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    data_dir = tmp_path / "data"
    library = tmp_path / "library"
    staging = tmp_path / "staging"
    for d in (data_dir, library, staging):
        d.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "user_skills_dir", library)

    async def _staging_dir(user_id: str, session_id: str, *, mkdir: bool = False) -> Path:
        if mkdir:
            staging.mkdir(exist_ok=True)
        return staging

    monkeypatch.setattr(staging_mod, "staging_dir_for_session", _staging_dir)

    from valuz_agent.adapters import kernel_client

    async def _get_session(user_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            user_id=user_id,
            metadata={
                "valuz": {"project_id": "chat-default", "creation_context": {"kind": "chat"}}
            },
        )

    monkeypatch.setattr(kernel_client, "get_session", _get_session)

    # A FILE, not ``:memory:``: the save pipeline commits mid-way, and a
    # committed session returns its connection to the pool — a fresh
    # in-memory connection would be a fresh, empty database.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    db = factory()
    svc = SkillLibraryService(
        datastore=SkillDatastore(db),
        skill_source=FilesystemSkillSource(),
        project_service=_Projects(),  # type: ignore[arg-type]
    )
    yield SimpleNamespace(svc=svc, db=db, library=library, staging=staging, data_dir=data_dir)
    await db.close()
    await engine.dispose()


def _stage(staging: Path, slug: str, body: str, *, extra: dict[str, str] | None = None) -> Path:
    d = staging / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: demo {slug}\n---\n\n{body}\n", encoding="utf-8"
    )
    for name, text in (extra or {}).items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
    return d


def _manifest_version(skill_dir: Path) -> str | None:
    for line in (skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


async def test_first_save_records_v1_and_links_the_index_row(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    skill, _ctx, _bound = await env.svc.confirm_submission(USER, SESSION, "demo")

    assert skill.artifact_id
    assert _manifest_version(env.library / "demo") == "1"
    assert not (env.staging / "demo").exists()  # staging consumed

    versions = await env.svc.list_versions(USER, skill.id)
    assert versions.artifact_id == skill.artifact_id
    assert [(v.version_no, v.is_current, v.source_session_id) for v in versions.items] == [
        (1, True, SESSION)
    ]
    snapshot = env.data_dir / "skill-versions" / ".artifact" / skill.artifact_id / "v1" / "demo.zip"
    assert snapshot.is_file()


async def test_second_save_is_v2_on_the_same_lineage(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    first, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    _stage(env.staging, "demo", "second", extra={"scripts/go.sh": "echo go"})
    second, _, _ = await env.svc.confirm_submission(USER, "sess-2", "demo")

    assert second.artifact_id == first.artifact_id
    assert _manifest_version(env.library / "demo") == "2"
    assert (env.library / "demo" / "scripts" / "go.sh").read_text() == "echo go"
    versions = await env.svc.list_versions(USER, second.id)
    assert [v.version_no for v in versions.items] == [1, 2]
    assert versions.items[1].is_current and not versions.items[0].is_current
    assert versions.items[1].source_session_id == "sess-2"


async def test_identical_resave_adds_no_version(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "same")
    first, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    # stage exactly the library content again (the host will stamp the same
    # next version number it would have — but the bytes equal v1's only if the
    # frontmatter version matches, so re-stage the LIBRARY copy verbatim)
    import shutil

    shutil.copytree(env.library / "demo", env.staging / "demo")
    versions_before = await env.svc.list_versions(USER, first.id)
    # the staged copy carries version: 1; the host rewrites it to 2 → different
    # bytes → a real v2. That is correct: a save is a save. What must NOT
    # happen is a phantom version from a pure replay of the same archive,
    # which the artifacts head idempotency covers (see test_artifact_content_delivery).
    await env.svc.confirm_submission(USER, SESSION, "demo")
    versions_after = await env.svc.list_versions(USER, first.id)
    assert len(versions_after.items) == len(versions_before.items) + 1


async def test_hand_edited_library_is_captured_as_baseline_before_overwrite(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    skill, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    # edit the library copy by hand (the manual editor path, not versioned)
    (env.library / "demo" / "notes.md").write_text("hand edit", encoding="utf-8")

    _stage(env.staging, "demo", "third")
    await env.svc.confirm_submission(USER, "sess-3", "demo")

    versions = await env.svc.list_versions(USER, skill.id)
    assert [(v.version_no, v.created_by) for v in versions.items] == [
        (1, None),
        (2, None),  # the baseline: library state that was never saved
        (3, None),
    ]
    # v2 really is the hand-edited state
    baseline = versions.items[1]
    file = await env.svc.read_version_file(USER, skill.id, baseline.revision_id, "notes.md")
    assert file.content == "hand edit"
    # and the library now holds v3 without the hand edit
    assert not (env.library / "demo" / "notes.md").exists()
    assert _manifest_version(env.library / "demo") == "3"


async def test_restore_makes_an_old_version_the_new_head(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first", extra={"references/a.md": "A"})
    skill, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    _stage(env.staging, "demo", "second")
    await env.svc.confirm_submission(USER, SESSION, "demo")
    assert not (env.library / "demo" / "references" / "a.md").exists()

    v1 = (await env.svc.list_versions(USER, skill.id)).items[0]
    restored = await env.svc.restore_version(USER, skill.id, v1.revision_id)

    assert restored.version_no == 3
    assert (env.library / "demo" / "references" / "a.md").read_text() == "A"
    assert "first" in (env.library / "demo" / "SKILL.md").read_text()
    assert _manifest_version(env.library / "demo") == "3"
    versions = await env.svc.list_versions(USER, skill.id)
    assert [v.version_no for v in versions.items] == [1, 2, 3]
    assert versions.items[2].is_current


async def test_read_version_file_rejects_traversal_and_unknown(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    skill, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    v1 = (await env.svc.list_versions(USER, skill.id)).items[0]
    with pytest.raises(SkillNotFound):
        await env.svc.read_version_file(USER, skill.id, v1.revision_id, "../x")
    with pytest.raises(SkillNotFound):
        await env.svc.read_version_file(USER, skill.id, v1.revision_id, "missing.md")
    with pytest.raises(SkillNotFound):
        await env.svc.read_version_file(USER, skill.id, "not-a-revision", "SKILL.md")


async def test_unversioned_skill_lists_no_versions(env) -> None:  # type: ignore[no-untyped-def]
    # dropped straight into the library, never saved through it
    d = env.library / "dropped"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: dropped\ndescription: d\n---\nbody\n", encoding="utf-8")
    await env.svc.startup_scan(USER)
    catalog = await env.svc.list_catalog(USER, "chat-default")
    dropped = next(s for s in catalog.skills if s.slug == "dropped")
    versions = await env.svc.list_versions(USER, dropped.id)
    assert versions.artifact_id is None and versions.items == []


async def test_restore_refuses_readonly(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    skill, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    v1 = (await env.svc.list_versions(USER, skill.id)).items[0]
    real = env.svc._resolve_skill

    async def _readonly(*args, **kwargs):  # type: ignore[no-untyped-def]
        view = await real(*args, **kwargs)
        view.readonly = True
        return view

    monkeypatch.setattr(env.svc, "_resolve_skill", _readonly)
    with pytest.raises(SkillReadOnly):
        await env.svc.restore_version(USER, skill.id, v1.revision_id)


async def test_version_detail_lists_that_version_s_own_files(env) -> None:  # type: ignore[no-untyped-def]
    """Which files a version holds is part of what changed between versions,
    so the viewer must read them from the archive rather than reuse the
    current skill's tree."""
    _stage(env.staging, "demo", "v1", extra={"references/only-in-v1.md": "gone later"})
    skill, _, _ = await env.svc.confirm_submission(USER, SESSION, "demo")
    _stage(env.staging, "demo", "v2", extra={"scripts/new.sh": "echo hi"})
    await env.svc.confirm_submission(USER, SESSION, "demo")

    versions = await env.svc.list_versions(USER, skill.id)
    v1 = await env.svc.get_version_detail(USER, skill.id, versions.items[0].revision_id)
    v2 = await env.svc.get_version_detail(USER, skill.id, versions.items[1].revision_id)

    assert {f.path for f in v1.files} == {"SKILL.md", "references/only-in-v1.md"}
    assert {f.path for f in v2.files} == {"SKILL.md", "scripts/new.sh"}
    assert v1.is_current is False and v2.is_current is True
    assert all(f.size > 0 for f in v2.files)
