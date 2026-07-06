"""Tests for SkillLibraryService — Phase 5 coverage."""

import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.skills.contracts import ProjectRef, RuntimeContext, SkillManifest
from valuz_agent.modules.skills.errors import PreviewExpired, SourceReadonly
from valuz_agent.modules.skills.models import (
    SessionSkillImportConfirmRequest,
    SkillCreateRequest,
    SkillFileAction,
    SkillImportArchiveConfirmRequest,
    SkillUpdateRequest,
)
from valuz_agent.modules.skills.service import SkillLibraryService

# ── Helpers ──────────────────────────────────────────────────────────


class FakeProject:
    def __init__(
        self, id: str = "ws-1", kind: str = "chat", root_path: str | None = None, name: str = "test"
    ):
        self.id = id
        self.kind = kind
        self.root_path = root_path
        self.name = name
        self.instructions_md = None
        self.memory_summary = None


class FakeProjectService:
    def __init__(self, projects: list | None = None):
        self._projects = projects or [FakeProject(), FakeProject(id="chat-default")]

    async def get_project(self, user_id: str, project_id: str):
        for ws in self._projects:
            if ws.id == project_id:
                return ws
        raise KeyError(project_id)

    async def list_projects(self, user_id: str):
        return self._projects


class FakeSkillDatastore:
    def __init__(self):
        self._enabled: dict[str, set[str]] = {}
        self._rows: dict[str, object] = {}

    def list_project_skill_manifests(self, project, source):
        ctx = RuntimeContext(
            project=ProjectRef(
                id=project.id,
                slug=project.id,
                kind=project.kind,
                root_path=project.root_path,
            ),
        )
        manifests = source.list_skills(ctx)
        enabled = self._enabled.get(project.id, set())
        result = []
        for m in manifests:
            is_enabled = project.kind == "chat" or m.path in enabled
            result.append(m.model_copy(update={"enabled": is_enabled}))
        return result

    def enabled_skill_paths(self, project):
        return self._enabled.get(project.id, set())

    def set_skill_enabled(self, project, skill_path, enabled):
        paths = self._enabled.setdefault(project.id, set())
        if enabled:
            paths.add(str(Path(skill_path).expanduser().resolve(strict=False)))
        else:
            paths.discard(str(Path(skill_path).expanduser().resolve(strict=False)))
        return paths

    def overwrite_enabled_skill_paths(self, project, skill_paths):
        self._enabled[project.id] = set(skill_paths)
        return self._enabled[project.id]

    def remove_skill_path_from_project(self, project, skill_path):
        paths = self._enabled.get(project.id, set())
        paths.discard(str(Path(skill_path).expanduser().resolve(strict=False)))

    def scan(self, project, source):
        return len(self.list_project_skill_manifests(project, source))

    async def get_by_id(self, user_id, skill_id):
        return self._rows.get(skill_id)

    async def get_by_slug(self, user_id, slug):
        return next((row for row in self._rows.values() if row.slug == slug), None)

    async def set_creation_origin(self, user_id, skill_id, origin):
        row = self._rows.get(skill_id)
        if row is not None:
            row.creation_origin = origin

    async def set_creation_origin_by_slug(self, user_id, slug, origin):
        row = await self.get_by_slug(user_id, slug)
        if row is not None:
            row.creation_origin = origin

    async def set_origin_metadata_by_slug(self, user_id, slug, origin_json):
        row = await self.get_by_slug(user_id, slug)
        if row is not None:
            row.origin_json = origin_json

    async def create(self, user_id, row):
        if not row.id:
            row.id = uuid4().hex
        self._rows[row.id] = row
        return row

    async def update(self, row):
        self._rows[row.id] = row
        return row

    async def list_skills(self, user_id):
        return list(self._rows.values())

    async def list_library_disabled_ids(self, user_id):
        return set(getattr(self, "_library_disabled", set()))

    async def list_library_disabled_slugs(self, user_id):
        return set(getattr(self, "_library_disabled_slugs", set()))

    async def set_library_enabled(self, user_id, skill_id, enabled):
        disabled = getattr(self, "_library_disabled", set())
        disabled_slugs = getattr(self, "_library_disabled_slugs", set())
        slug = skill_id.split(":", 1)[1] if ":" in skill_id else None
        if enabled:
            disabled.discard(skill_id)
            if slug is not None:
                disabled_slugs.discard(slug)
        else:
            disabled.add(skill_id)
            if slug is not None:
                disabled_slugs.add(slug)
        self._library_disabled = disabled
        self._library_disabled_slugs = disabled_slugs

    async def set_library_enabled_by_slug(self, user_id, slug, enabled):
        disabled = getattr(self, "_library_disabled_slugs", set())
        if enabled:
            disabled.discard(slug)
        else:
            disabled.add(slug)
        self._library_disabled_slugs = disabled

    def add_ignore(self, skill_id, content_hash=None):
        pass

    def is_ignored(self, skill_id, content_hash=None):
        return False

    def set_project_skills(self, user_id, project_id, rows):
        self._enabled[project_id] = set()


def _make_skill_dir(root: Path, name: str, body: str = "Test skill.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f'---\nname: "{name}"\ndescription: "Test {name}"\ntags: ["test"]\n---\n\n{body}\n',
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def skill_root(tmp_path):
    return tmp_path / "skills"


@pytest.fixture
def svc(skill_root, monkeypatch):
    skill_root.mkdir(parents=True, exist_ok=True)
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "user_skills_dir", skill_root)
    bus = EventBus()
    return SkillLibraryService(
        datastore=FakeSkillDatastore(),
        skill_source=FilesystemSkillSource(),
        project_service=FakeProjectService(),
        event_bus=bus,
    ), bus


# ── Tests ────────────────────────────────────────────────────────────


class TestIndexOfficialSkills:
    """``index_official_skills`` deterministically upserts the bundled official
    skills into the index, independent of the best-effort ``startup_scan``."""

    async def test_should_upsert_official_skills_into_index(self, svc, tmp_path):
        service, _bus = svc
        from valuz_agent.integrations.skills_official import OfficialSkillSource

        official_dir = tmp_path / "official"
        _make_skill_dir(official_dir, "sector-overview")
        _make_skill_dir(official_dir, "comps")
        service._extra_sources = [OfficialSkillSource(official_dir=official_dir)]

        count = await service.index_official_skills("u")

        assert count == 2
        rows = await service._ds.list_skills("u")
        slugs = {r.slug for r in rows}
        assert {"sector-overview", "comps"} <= slugs
        assert all(r.scope == "official" for r in rows if r.slug in {"sector-overview", "comps"})
        assert all(r.status == "available" for r in rows)

    async def test_should_be_idempotent(self, svc, tmp_path):
        service, _bus = svc
        from valuz_agent.integrations.skills_official import OfficialSkillSource

        official_dir = tmp_path / "official"
        _make_skill_dir(official_dir, "dcf")
        service._extra_sources = [OfficialSkillSource(official_dir=official_dir)]

        await service.index_official_skills("u")
        await service.index_official_skills("u")  # second pass updates, no duplicate row

        rows = await service._ds.list_skills("u")
        assert len([r for r in rows if r.slug == "dcf"]) == 1


class TestListCatalog:
    async def test_should_return_name_and_description_fields(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "alpha", "Alpha body")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        assert skill.name == "alpha"
        assert skill.description == "Test alpha"
        assert hasattr(skill, "name")
        assert not hasattr(skill, "title")

    async def test_should_include_slug_and_tags(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "beta")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        assert skill.slug == "beta"
        assert skill.tags == ["test"]

    async def test_should_include_content_hash(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "hashed")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        assert skill.content_hash is not None
        assert len(skill.content_hash) == 64

    async def test_should_return_empty_when_no_skills(self, svc):
        service, _ = svc
        catalog = await service.list_catalog("u", "ws-1")
        assert catalog.skills == []

    async def test_should_read_official_skills_from_index_without_rescanning_source(
        self, svc, tmp_path
    ):
        from valuz_agent.modules.skills.models import SkillIndexRow

        service, _ = svc
        official_dir = tmp_path / "official" / "skill-creator"
        official_dir.mkdir(parents=True)
        (official_dir / ".bundled-version").write_text("v1", encoding="utf-8")

        class _ExplodingOfficialSource:
            name = "official"

            def list_skills(self, ctx):
                raise AssertionError("official source should not be scanned when index is warm")

        service._extra_sources = [_ExplodingOfficialSource()]
        await service._ds.create(
            "u",
            SkillIndexRow(
                slug="skill-creator",
                name="skill-creator",
                description="Create skills",
                scope="official",
                source="official",
                source_path=str(official_dir),
                user_id="u",
                readonly=True,
                deletable=False,
                status="available",
                content_hash="c" * 64,
                manifest_hash="m" * 64,
                tags_json="official,test",
                creation_origin="discovered",
                library_enabled=True,
            ),
        )

        catalog = await service.list_catalog("u", "ws-1")

        skill = next(s for s in catalog.skills if s.slug == "skill-creator")
        assert skill.id == "official:skill-creator"
        assert skill.origin_label == "Built-in"
        assert skill.content_hash == "c" * 64
        assert skill.manifest_hash == "m" * 64
        assert skill.tags == ["official", "test"]

    async def test_should_fallback_to_official_source_when_index_is_empty(self, svc, tmp_path):
        service, _ = svc
        from valuz_agent.integrations.skills_official import OfficialSkillSource

        official_dir = tmp_path / "official"
        _make_skill_dir(official_dir, "browser")
        service._extra_sources = [OfficialSkillSource(official_dir=official_dir)]

        catalog = await service.list_catalog("u", "ws-1")

        assert any(skill.slug == "browser" for skill in catalog.skills)

    async def test_should_sort_by_folder_birthtime_desc(self, svc, skill_root):
        """The skill management page renders the catalog in DESC
        birthtime order. We stage two folders with deliberately staggered
        mtimes (os.utime is the cross-platform knob) and verify the
        newer one lands first. NB: ``_folder_birthtime`` reads
        ``st_birthtime`` when available and falls back to ``st_mtime``
        — setting mtime covers both branches on macOS and Linux."""
        import os as _os
        import time

        service, _ = svc
        old_dir = _make_skill_dir(skill_root, "old-skill")
        new_dir = _make_skill_dir(skill_root, "new-skill")
        # Force old-skill to be "older" by stamping its mtime back 1h.
        now = time.time()
        _os.utime(old_dir, (now - 3600, now - 3600))
        _os.utime(new_dir, (now, now))

        catalog = await service.list_catalog("u", "ws-1")
        # When st_birthtime exists (macOS), the test still passes
        # because mkdir() actually creates the folder slightly earlier
        # for old-skill — both candidates fall under the
        # ``compareByBirthtimeDesc`` semantics either way.
        slugs = [s.slug for s in catalog.skills]
        assert "new-skill" in slugs and "old-skill" in slugs
        # On platforms where birthtime tracks mkdir order, new-skill
        # comes first; on platforms that fall back to mtime, the
        # ``os.utime`` ordering ensures the same outcome.
        assert slugs.index("new-skill") <= slugs.index("old-skill")

    async def test_should_sort_null_birthtime_last(self, svc, skill_root):
        """Legacy rows with ``folder_created_at = None`` (the migration
        backfills lazily on the next startup_scan) must land at the end
        so freshly-created skills don't get buried."""
        service, _ = svc
        # Two skills with valid birthtime + one stubbed manifest whose
        # source manifest claims None for the timestamp.
        _make_skill_dir(skill_root, "real-1")

        # Fake a manifest entry with None timestamp by monkeypatching the
        # source. Easier: add an "extra source" returning a manifest with
        # folder_created_at=None. SkillLibraryService exposes that knob.

        class _NullTimeSource:
            name = "null-time"

            def list_skills(self, ctx):
                return [
                    SkillManifest(
                        id="extra:legacy",
                        name="zzz-legacy",
                        description="legacy row pre-birthtime",
                        scope="user",
                        source="valuz",
                        path="/tmp/legacy",
                        slug="zzz-legacy",
                        folder_created_at=None,
                    )
                ]

        service._extra_sources = [_NullTimeSource()]
        catalog = await service.list_catalog("u", "ws-1")
        slugs = [s.slug for s in catalog.skills]
        # zzz-legacy has no birthtime → must be after the real ones
        # regardless of its alphabetical-last name.
        assert slugs[-1] == "zzz-legacy"


class TestLibraryState:
    """Global library on/off switch — the field the new-conversation ``/``
    picker filters on. Default on; only an explicit off is stored."""

    async def test_catalog_overlays_disabled_row(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "alpha")
        _make_skill_dir(skill_root, "beta")
        # Turn alpha off by its catalog row id; beta left at the default (on).
        cat0 = await service.list_catalog("u", "ws-1")
        alpha_id = next(s for s in cat0.skills if s.slug == "alpha").id
        await service._ds.set_library_enabled("u", alpha_id, False)

        catalog = await service.list_catalog("u", "ws-1")
        by_slug = {s.slug: s for s in catalog.skills}

        assert by_slug["alpha"].library_enabled is False
        assert by_slug["beta"].library_enabled is True

    async def test_builtin_skill_cannot_be_disabled(self, svc, skill_root):
        service, _ = svc
        # A built-in skill (``origin-label: Built-in`` frontmatter). Even with its
        # row turned off, the catalog must keep it enabled — built-ins ship with
        # the client and aren't toggleable.
        d = skill_root / "skill-creator"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            '---\nname: "skill-creator"\ndescription: "x"\norigin-label: "Built-in"\n---\n\nbody\n',
            encoding="utf-8",
        )
        cat0 = await service.list_catalog("u", "ws-1")
        sc_id = next(s for s in cat0.skills if s.slug == "skill-creator").id
        await service._ds.set_library_enabled("u", sc_id, False)

        catalog = await service.list_catalog("u", "ws-1")
        sc = next(s for s in catalog.skills if s.slug == "skill-creator")
        assert sc.origin_label == "Built-in"
        assert sc.library_enabled is True

    async def test_toggle_returns_updated_and_persists(self, svc, skill_root):
        from valuz_agent.modules.skills.models import SkillIndexRow

        service, _ = svc
        _make_skill_dir(skill_root, "gamma")
        cat = await service.list_catalog("u", "ws-1")
        gamma = next(s for s in cat.skills if s.slug == "gamma")
        assert gamma.library_enabled is True
        # Seed the index row so the service can resolve id → slug.
        service._ds._rows[gamma.id] = SkillIndexRow(
            id=gamma.id,
            slug="gamma",
            name="gamma",
            description="",
            scope="user",
            source="filesystem",
            source_path=gamma.path,
            user_id="u",
        )

        updated = await service.set_library_enabled("u", gamma.id, False)
        assert updated.library_enabled is False

        cat2 = await service.list_catalog("u", "ws-1")
        assert next(s for s in cat2.skills if s.slug == "gamma").library_enabled is False


class TestCreateSkill:
    async def test_should_publish_event_on_create(self, svc, skill_root):
        service, bus = svc
        events = []
        bus.subscribe("skill.changed", lambda **kw: events.append(kw))
        await service.create_skill("u", SkillCreateRequest(name="new-skill", description="desc"))
        assert len(events) == 1
        assert events[0]["reason"] == "created"

    async def test_should_create_skill_dir_with_manifest(self, svc, skill_root):
        service, _ = svc
        result = await service.create_skill(
            "u", SkillCreateRequest(name="created", description="A test")
        )
        assert result.name == "created"
        assert (Path(result.path) / "SKILL.md").exists()

    async def test_should_not_write_creation_origin_into_skill_md(self, svc, skill_root):
        """creation_origin is host bookkeeping (valuz_skill_index) — it
        must NOT be written into the user's SKILL.md frontmatter, but
        the returned view still reports the skill as "created"."""
        service, _ = svc
        result = await service.create_skill(
            "u", SkillCreateRequest(name="origin-check", description="x")
        )
        raw = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert "creation-origin" not in raw
        assert result.creation_origin == "created"

    async def test_should_expose_creation_origin_via_catalog(self, svc, skill_root):
        """The catalog View must expose ``creation_origin`` sourced from
        the DB index — it's what drives the .agents group's badge."""
        service, _ = svc
        await service.create_skill("u", SkillCreateRequest(name="origin-view", description="y"))
        catalog = await service.list_catalog("u", "ws-1")
        match = next(s for s in catalog.skills if s.slug == "origin-view")
        assert match.creation_origin == "created"

    async def test_should_default_to_discovered_for_scanned_skill(self, svc, skill_root):
        """A skill folder dropped on disk (not created via Valuz) shows
        as ``"discovered"`` — it must NOT get the "创建" badge. This is
        the bug behind the .agents-vs-.claude display confusion."""
        service, _ = svc
        _make_skill_dir(skill_root, "scanned-skill")
        catalog = await service.list_catalog("u", "ws-1")
        match = next(s for s in catalog.skills if s.slug == "scanned-skill")
        assert match.creation_origin == "discovered"


class TestUpdateSkill:
    async def test_should_publish_event_on_update(self, svc, skill_root):
        service, bus = svc
        _make_skill_dir(skill_root, "updatable")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id

        events = []
        bus.subscribe("skill.changed", lambda **kw: events.append(kw))
        await service.update_skill("u", skill_id, SkillUpdateRequest(name="updated-name"))
        assert len(events) == 1
        assert events[0]["reason"] == "updated"


class TestDeleteSkill:
    async def test_should_publish_event_on_confirm_delete(self, svc, skill_root):
        service, bus = svc
        _make_skill_dir(skill_root, "deletable")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id

        events = []
        bus.subscribe("skill.changed", lambda **kw: events.append(kw))
        await service.delete_skill("u", skill_id, mode="confirm")
        assert any(e["reason"] == "deleted" for e in events)

    async def test_dry_run_should_return_preview(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "preview-del")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        result = await service.delete_skill("u", skill_id, mode="dry_run")
        assert result is not None
        assert hasattr(result, "count")


class TestReadonlySkill:
    async def test_should_reject_write_on_readonly_skill(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "locked")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        # Patch the skill to be readonly
        skill.readonly = True
        # Direct call to write_skill_file with a readonly skill
        from unittest.mock import AsyncMock, patch

        with patch.object(service, "_resolve_skill", new=AsyncMock(return_value=skill)):
            with pytest.raises(SourceReadonly):
                await service.write_skill_file(
                    "u", skill.id, SkillFileAction(action="create", path="test.md", content="x")
                )


class TestArchiveImport:
    async def test_confirm_should_survive_preview_worker_switch(self, svc, tmp_path, monkeypatch):
        service, _ = svc
        from valuz_agent.infra import fs_registry as fsr

        monkeypatch.setattr(
            fsr.settings,
            "user_temp_dir",
            tmp_path / "temp" / "{user_id}",
        )

        archive = tmp_path / "skill.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(
                "opscli-agent/SKILL.md",
                '---\nname: "opscli-agent"\ndescription: "Ops CLI"\n---\n\nUse opscli.\n',
            )

        preview = await service.import_archive_preview("u", str(archive), target_scope="user")
        assert service._load_import_preview_record("u", preview.preview_id) is not None

        imported = await service.confirm_archive_import(
            "u",
            SkillImportArchiveConfirmRequest(
                preview_id=preview.preview_id,
                name="opscli-agent",
            ),
        )

        assert imported.name == "opscli-agent"
        assert (tmp_path / "skills" / "opscli-agent" / "SKILL.md").exists()


class TestUrlImport:
    async def test_should_raise_preview_expired_after_ttl(self, svc, tmp_path, monkeypatch):
        import time

        service, _ = svc
        from valuz_agent.infra import fs_registry as fsr

        monkeypatch.setattr(fsr.settings, "user_temp_dir", tmp_path / "temp" / "{user_id}")
        preview_id = "test-expired"
        cleanup_root = tmp_path / "url-staging"
        skill_root = cleanup_root / "skill"
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text("---\nname: expired\n---\n", "utf-8")
        service._write_import_preview_record(
            "u",
            preview_id,
            kind="url",
            skill_root=skill_root,
            cleanup_root=cleanup_root,
            created_at=time.time() - 700,
        )
        from valuz_agent.modules.skills.models import SkillImportUrlConfirmRequest

        with pytest.raises(PreviewExpired):
            await service.confirm_url_import(
                "u", SkillImportUrlConfirmRequest(preview_id=preview_id)
            )
        assert not cleanup_root.exists()


class TestSkillFiles:
    async def test_should_list_files_in_skill_dir(self, svc, skill_root):
        service, _ = svc
        skill_dir = _make_skill_dir(skill_root, "with-files")
        (skill_dir / "extra.txt").write_text("hello")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        files = await service.list_skill_files("u", skill_id)
        paths = [f.path for f in files]
        assert "SKILL.md" in paths
        assert "extra.txt" in paths

    async def test_should_read_file_content(self, svc, skill_root):
        service, _ = svc
        skill_dir = _make_skill_dir(skill_root, "readable")
        (skill_dir / "data.txt").write_text("content here")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        result = await service.read_skill_file("u", skill_id, "data.txt")
        assert result.content == "content here"


class TestSkillDetail:
    async def test_should_return_detail_with_instructions(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "detailed", "Detailed instructions here.")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        detail = await service.get_skill_detail("u", skill_id)
        assert detail.instructions_markdown is not None
        assert "Detailed instructions" in detail.instructions_markdown
        assert detail.file_count >= 1
        assert detail.manifest_filename == "SKILL.md"


class TestTags:
    async def test_should_aggregate_unique_tags(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "tag-a")
        _make_skill_dir(skill_root, "tag-b")
        tags = await service.list_all_tags("u")
        assert "test" in tags
        assert len(tags) == len(set(tags))


class TestImportFromSessionConfirm:
    """Regression: this path used to call ``SessionDatastore.list_events`` —
    a method that does not exist (events live in the kernel ``events``
    table) — so the confirm endpoint raised AttributeError at runtime. It
    now fetches events through ``adapters.kernel_store.get_events``."""

    @staticmethod
    def _patch_events(monkeypatch, events):
        from valuz_agent.adapters import kernel_client

        seen: list[str] = []

        async def fake_get_events(_user_id, session_id, **kwargs):
            seen.append(session_id)
            return events

        monkeypatch.setattr(kernel_client, "get_events", fake_get_events)
        return seen

    async def test_should_build_skill_body_from_persisted_assistant_events(self, svc, monkeypatch):
        service, _ = svc
        seen = self._patch_events(
            monkeypatch,
            [
                SimpleNamespace(type="user_message", data={"message": "teach me"}),
                SimpleNamespace(type="assistant_message", data={"text": "First answer."}),
                SimpleNamespace(type="tool_result", data={"output": "tool noise"}),
                SimpleNamespace(type="assistant_message", data={"content": "Second answer."}),
            ],
        )
        result = await service.import_from_session_confirm(
            "u", SessionSkillImportConfirmRequest(session_id="sess-1", name="from-session")
        )
        assert seen == ["sess-1"]
        body = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert "First answer." in body
        assert "Second answer." in body
        assert "tool noise" not in body

    async def test_should_fall_back_to_description_when_no_assistant_text(self, svc, monkeypatch):
        service, _ = svc
        self._patch_events(monkeypatch, [])
        result = await service.import_from_session_confirm(
            "u",
            SessionSkillImportConfirmRequest(
                session_id="sess-2", name="empty-session", description="Fallback body."
            ),
        )
        body = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert "Fallback body." in body
