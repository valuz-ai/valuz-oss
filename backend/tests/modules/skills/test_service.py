"""Tests for SkillLibraryService — Phase 5 coverage."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest, WorkspaceRef
from valuz_agent.modules.skills.errors import PreviewExpired, SourceReadonly
from valuz_agent.modules.skills.models import (
    SkillCreateRequest,
    SkillFileAction,
    SkillUpdateRequest,
)
from valuz_agent.modules.skills.service import SkillLibraryService
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource


# ── Helpers ──────────────────────────────────────────────────────────


class FakeWorkspace:
    def __init__(
        self, id: str = "ws-1", kind: str = "chat", root_path: str | None = None, name: str = "test"
    ):
        self.id = id
        self.kind = kind
        self.root_path = root_path
        self.name = name
        self.instructions_md = None
        self.memory_summary = None


class FakeWorkspaceService:
    def __init__(self, workspaces: list | None = None):
        self._workspaces = workspaces or [FakeWorkspace(), FakeWorkspace(id="chat-default")]

    async def get_workspace(self, workspace_id: str):
        for ws in self._workspaces:
            if ws.id == workspace_id:
                return ws
        raise KeyError(workspace_id)

    async def list_workspaces(self):
        return self._workspaces


class FakeSessionDatastore:
    async def list_events(self, session_id: str):
        return []


class FakeSkillDatastore:
    def __init__(self):
        self._enabled: dict[str, set[str]] = {}
        self._rows: dict[str, object] = {}

    def list_workspace_skills(self, workspace, source):
        ctx = RuntimeContext(
            workspace=WorkspaceRef(
                id=workspace.id,
                slug=workspace.id,
                kind=workspace.kind,
                root_path=workspace.root_path,
            ),
        )
        manifests = source.list_skills(ctx)
        enabled = self._enabled.get(workspace.id, set())
        result = []
        for m in manifests:
            is_enabled = workspace.kind == "chat" or m.path in enabled
            result.append(m.model_copy(update={"enabled": is_enabled}))
        return result

    def enabled_skill_paths(self, workspace):
        return self._enabled.get(workspace.id, set())

    def set_skill_enabled(self, workspace, skill_path, enabled):
        paths = self._enabled.setdefault(workspace.id, set())
        if enabled:
            paths.add(str(Path(skill_path).expanduser().resolve(strict=False)))
        else:
            paths.discard(str(Path(skill_path).expanduser().resolve(strict=False)))
        return paths

    def overwrite_enabled_skill_paths(self, workspace, skill_paths):
        self._enabled[workspace.id] = set(skill_paths)
        return self._enabled[workspace.id]

    def remove_skill_path_from_workspace(self, workspace, skill_path):
        paths = self._enabled.get(workspace.id, set())
        paths.discard(str(Path(skill_path).expanduser().resolve(strict=False)))

    def scan(self, workspace, source):
        return len(self.list_workspace_skills(workspace, source))

    async def get_by_id(self, skill_id):
        return self._rows.get(skill_id)

    async def set_creation_origin(self, skill_id, origin):
        row = self._rows.get(skill_id)
        if row is not None:
            row.creation_origin = origin

    async def create(self, row):
        self._rows[row.id] = row
        return row

    async def update(self, row):
        self._rows[row.id] = row
        return row

    async def list_skills(self):
        return list(self._rows.values())

    def add_ignore(self, skill_id, content_hash=None):
        pass

    def is_ignored(self, skill_id, content_hash=None):
        return False

    def set_project_skills(self, workspace_id, rows):
        self._enabled[workspace_id] = set()


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
    monkeypatch.setenv("VALUZ_USER_SKILLS_DIR", str(skill_root))
    bus = EventBus()
    return SkillLibraryService(
        datastore=FakeSkillDatastore(),
        skill_source=FilesystemSkillSource(),
        workspace_service=FakeWorkspaceService(),
        session_datastore=FakeSessionDatastore(),
        event_bus=bus,
    ), bus


# ── Tests ────────────────────────────────────────────────────────────


class TestListCatalog:
    async def test_should_return_name_and_description_fields(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "alpha", "Alpha body")
        catalog = await service.list_catalog("ws-1")
        skill = catalog.skills[0]
        assert skill.name == "alpha"
        assert skill.description == "Test alpha"
        assert hasattr(skill, "name")
        assert not hasattr(skill, "title")

    async def test_should_include_slug_and_tags(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "beta")
        catalog = await service.list_catalog("ws-1")
        skill = catalog.skills[0]
        assert skill.slug == "beta"
        assert skill.tags == ["test"]

    async def test_should_include_content_hash(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "hashed")
        catalog = await service.list_catalog("ws-1")
        skill = catalog.skills[0]
        assert skill.content_hash is not None
        assert len(skill.content_hash) == 64

    async def test_should_return_empty_when_no_skills(self, svc):
        service, _ = svc
        catalog = await service.list_catalog("ws-1")
        assert catalog.skills == []

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

        catalog = await service.list_catalog("ws-1")
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
        from valuz_agent.modules.skills.contracts import SkillManifest

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
        catalog = await service.list_catalog("ws-1")
        slugs = [s.slug for s in catalog.skills]
        # zzz-legacy has no birthtime → must be after the real ones
        # regardless of its alphabetical-last name.
        assert slugs[-1] == "zzz-legacy"


class TestCreateSkill:
    async def test_should_publish_event_on_create(self, svc, skill_root):
        service, bus = svc
        events = []
        bus.subscribe("skill.changed", lambda **kw: events.append(kw))
        await service.create_skill(SkillCreateRequest(name="new-skill", description="desc"))
        assert len(events) == 1
        assert events[0]["reason"] == "created"

    async def test_should_create_skill_dir_with_manifest(self, svc, skill_root):
        service, _ = svc
        result = await service.create_skill(
            SkillCreateRequest(name="created", description="A test")
        )
        assert result.name == "created"
        assert (Path(result.path) / "SKILL.md").exists()

    async def test_should_not_write_creation_origin_into_skill_md(self, svc, skill_root):
        """creation_origin is host bookkeeping (valuz_skill_index) — it
        must NOT be written into the user's SKILL.md frontmatter, but
        the returned view still reports the skill as "created"."""
        service, _ = svc
        result = await service.create_skill(
            SkillCreateRequest(name="origin-check", description="x")
        )
        raw = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert "creation-origin" not in raw
        assert result.creation_origin == "created"

    async def test_should_expose_creation_origin_via_catalog(self, svc, skill_root):
        """The catalog View must expose ``creation_origin`` sourced from
        the DB index — it's what drives the .agents group's badge."""
        service, _ = svc
        await service.create_skill(SkillCreateRequest(name="origin-view", description="y"))
        catalog = await service.list_catalog("ws-1")
        match = next(s for s in catalog.skills if s.slug == "origin-view")
        assert match.creation_origin == "created"

    async def test_should_default_to_discovered_for_scanned_skill(self, svc, skill_root):
        """A skill folder dropped on disk (not created via Valuz) shows
        as ``"discovered"`` — it must NOT get the "创建" badge. This is
        the bug behind the .agents-vs-.claude display confusion."""
        service, _ = svc
        _make_skill_dir(skill_root, "scanned-skill")
        catalog = await service.list_catalog("ws-1")
        match = next(s for s in catalog.skills if s.slug == "scanned-skill")
        assert match.creation_origin == "discovered"


class TestUpdateSkill:
    async def test_should_publish_event_on_update(self, svc, skill_root):
        service, bus = svc
        _make_skill_dir(skill_root, "updatable")
        catalog = await service.list_catalog("ws-1")
        skill_id = catalog.skills[0].id

        events = []
        bus.subscribe("skill.changed", lambda **kw: events.append(kw))
        await service.update_skill(skill_id, SkillUpdateRequest(name="updated-name"))
        assert len(events) == 1
        assert events[0]["reason"] == "updated"


class TestDeleteSkill:
    async def test_should_publish_event_on_confirm_delete(self, svc, skill_root):
        service, bus = svc
        _make_skill_dir(skill_root, "deletable")
        catalog = await service.list_catalog("ws-1")
        skill_id = catalog.skills[0].id

        events = []
        bus.subscribe("skill.changed", lambda **kw: events.append(kw))
        await service.delete_skill(skill_id, mode="confirm")
        assert any(e["reason"] == "deleted" for e in events)

    async def test_dry_run_should_return_preview(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "preview-del")
        catalog = await service.list_catalog("ws-1")
        skill_id = catalog.skills[0].id
        result = await service.delete_skill(skill_id, mode="dry_run")
        assert result is not None
        assert hasattr(result, "count")


class TestReadonlySkill:
    async def test_should_reject_write_on_readonly_skill(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "locked")
        catalog = await service.list_catalog("ws-1")
        skill = catalog.skills[0]
        # Patch the skill to be readonly
        skill.readonly = True
        # Direct call to write_skill_file with a readonly skill
        from unittest.mock import AsyncMock, patch

        with patch.object(service, "_resolve_skill", new=AsyncMock(return_value=skill)):
            with pytest.raises(SourceReadonly):
                await service.write_skill_file(
                    skill.id, SkillFileAction(action="create", path="test.md", content="x")
                )


class TestUrlImport:
    async def test_should_raise_preview_expired_after_ttl(self, svc):
        import time

        service, _ = svc
        from valuz_agent.modules.skills.service import _import_previews

        preview_id = "test-expired"
        # URL preview shape: (skill_root, cleanup_root, created_at). Stamp it 700s
        # ago so confirm trips the 600s TTL.
        _import_previews[preview_id] = (
            Path("/tmp/nonexistent/skill"),
            Path("/tmp/nonexistent"),
            time.time() - 700,
        )
        from valuz_agent.modules.skills.models import SkillImportUrlConfirmRequest

        with pytest.raises(PreviewExpired):
            await service.confirm_url_import(SkillImportUrlConfirmRequest(preview_id=preview_id))
        _import_previews.pop(preview_id, None)


class TestSkillFiles:
    async def test_should_list_files_in_skill_dir(self, svc, skill_root):
        service, _ = svc
        skill_dir = _make_skill_dir(skill_root, "with-files")
        (skill_dir / "extra.txt").write_text("hello")
        catalog = await service.list_catalog("ws-1")
        skill_id = catalog.skills[0].id
        files = await service.list_skill_files(skill_id)
        paths = [f.path for f in files]
        assert "SKILL.md" in paths
        assert "extra.txt" in paths

    async def test_should_read_file_content(self, svc, skill_root):
        service, _ = svc
        skill_dir = _make_skill_dir(skill_root, "readable")
        (skill_dir / "data.txt").write_text("content here")
        catalog = await service.list_catalog("ws-1")
        skill_id = catalog.skills[0].id
        result = await service.read_skill_file(skill_id, "data.txt")
        assert result.content == "content here"


class TestSkillDetail:
    async def test_should_return_detail_with_instructions(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "detailed", "Detailed instructions here.")
        catalog = await service.list_catalog("ws-1")
        skill_id = catalog.skills[0].id
        detail = await service.get_skill_detail(skill_id)
        assert detail.instructions_markdown is not None
        assert "Detailed instructions" in detail.instructions_markdown
        assert detail.file_count >= 1
        assert detail.manifest_filename == "SKILL.md"


class TestTags:
    async def test_should_aggregate_unique_tags(self, svc, skill_root):
        service, _ = svc
        _make_skill_dir(skill_root, "tag-a")
        _make_skill_dir(skill_root, "tag-b")
        tags = await service.list_all_tags()
        assert "test" in tags
        assert len(tags) == len(set(tags))
