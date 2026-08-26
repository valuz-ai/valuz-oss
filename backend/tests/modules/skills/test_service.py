"""Tests for SkillLibraryService — Phase 5 coverage."""

import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.skills.contracts import ProjectRef, RuntimeContext, SkillManifest
from valuz_agent.modules.skills.errors import PreviewExpired, SourceReadonly
from valuz_agent.modules.skills.models import (
    SessionSkillImportConfirmRequest,
    SkillCreateRequest,
    SkillFileAction,
    SkillImportArchiveConfirmRequest,
    SkillImportUrlConfirmRequest,
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
    session = object()

    def __init__(self):
        self._enabled: dict[str, set[str]] = {}
        self._rows: dict[str, object] = {}

    def list_project_skill_manifests(self, project, source, *, compute_content_hash=True):
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
        # Mirror the real datastore: same-slug rows can coexist across scopes, so
        # resolve the highest-priority one (official > project > user).
        matches = [row for row in self._rows.values() if row.slug == slug]
        if not matches:
            return None
        rank = {"official": 0, "project": 1}
        matches.sort(
            key=lambda r: (
                rank.get(getattr(r, "scope", ""), 2),
                getattr(r, "source_path", "") or "",
            )
        )
        return matches[0]

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

    async def mark_unavailable_by_slug(self, user_id, slug):
        row = await self.get_by_slug(user_id, slug)
        if row is not None:
            row.status = "unavailable"

    async def create(self, user_id, row):
        if not row.id:
            row.id = uuid4().hex
        row.user_id = user_id
        self._rows[row.id] = row
        return row

    async def update(self, row):
        self._rows[row.id] = row
        return row

    async def list_skills(self, user_id):
        return list(self._rows.values())

    async def list_library_disabled_ids(self, user_id):
        row_ids = {
            row.id
            for row in self._rows.values()
            if getattr(row, "user_id", user_id) == user_id
            and getattr(row, "library_enabled", True) is False
        }
        return row_ids | set(getattr(self, "_library_disabled", set()))

    async def list_library_disabled_slugs(self, user_id):
        slugs = {
            row.slug
            for row in self._rows.values()
            if getattr(row, "user_id", user_id) == user_id
            and getattr(row, "library_enabled", True) is False
        }
        return slugs | set(getattr(self, "_library_disabled_slugs", set()))

    async def set_library_enabled(self, user_id, skill_id, enabled):
        row = self._rows.get(skill_id)
        if row is None:
            # Catalog view ids are composite ``{scope}:{slug}``; resolve to the
            # underlying row by slug so the row's ``library_enabled`` (what the
            # path-keyed catalog overlay reads) actually flips.
            slug_part = skill_id.split(":", 1)[1] if ":" in skill_id else skill_id
            row = next(
                (r for r in self._rows.values() if getattr(r, "slug", None) == slug_part),
                None,
            )
        if row is not None:
            row.library_enabled = enabled
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
        row = await self.get_by_slug(user_id, slug)
        if row is not None:
            row.library_enabled = enabled
        disabled = getattr(self, "_library_disabled_slugs", set())
        if enabled:
            disabled.discard(slug)
        else:
            disabled.add(slug)
        self._library_disabled_slugs = disabled

    async def get_by_source_path(self, user_id, source_path):
        return next(
            (
                row
                for row in self._rows.values()
                if getattr(row, "source_path", None) == source_path
            ),
            None,
        )

    async def set_creation_origin_by_path(self, user_id, source_path, origin):
        row = await self.get_by_source_path(user_id, source_path)
        if row is not None:
            row.creation_origin = origin

    async def set_origin_metadata_by_path(self, user_id, source_path, origin_json):
        row = await self.get_by_source_path(user_id, source_path)
        if row is not None:
            row.origin_json = origin_json

    async def mark_unavailable_by_path(self, user_id, source_path):
        row = await self.get_by_source_path(user_id, source_path)
        if row is not None:
            row.status = "unavailable"

    async def list_library_disabled_paths(self, user_id):
        paths = {
            row.source_path
            for row in self._rows.values()
            if getattr(row, "user_id", user_id) == user_id
            and getattr(row, "library_enabled", True) is False
        }
        return paths | set(getattr(self, "_library_disabled_paths", set()))

    async def set_library_enabled_by_path(self, user_id, source_path, enabled):
        row = await self.get_by_source_path(user_id, source_path)
        if row is not None:
            row.library_enabled = enabled
        disabled = getattr(self, "_library_disabled_paths", set())
        if enabled:
            disabled.discard(source_path)
        else:
            disabled.add(source_path)
        self._library_disabled_paths = disabled

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
def svc(skill_root, tmp_path, monkeypatch):
    skill_root.mkdir(parents=True, exist_ok=True)
    test_home = tmp_path / ".test-home"
    test_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: test_home)
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "user_skills_dir", skill_root)
    return SkillLibraryService(
        datastore=FakeSkillDatastore(),
        skill_source=FilesystemSkillSource(),
        project_service=FakeProjectService(),
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestIndexOfficialSkills:
    """``index_official_skills`` deterministically upserts the bundled official
    skills into the index, independent of the best-effort ``startup_scan``."""

    async def test_should_upsert_official_skills_into_index(self, svc, tmp_path):
        service = svc
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
        service = svc
        from valuz_agent.integrations.skills_official import OfficialSkillSource

        official_dir = tmp_path / "official"
        _make_skill_dir(official_dir, "dcf")
        service._extra_sources = [OfficialSkillSource(official_dir=official_dir)]

        await service.index_official_skills("u")
        await service.index_official_skills("u")  # second pass updates, no duplicate row

        rows = await service._ds.list_skills("u")
        assert len([r for r in rows if r.slug == "dcf"]) == 1


class TestSameSlugCoexistence:
    """A bundled ``official`` copy and a ``user`` copy of the same slug are
    distinct skills (different on-disk dirs) and must coexist — the scan indexes
    both and the catalog shows each in its own source group, rather than one
    shadowing the other in the ``(user_id, slug)``-keyed index."""

    @staticmethod
    def _seed_official_row(source_path: str, *, library_enabled: bool = True):
        from valuz_agent.modules.skills.models import SkillIndexRow

        return SkillIndexRow(
            slug="shared",
            name="shared",
            description="official copy",
            scope="official",
            source="official",
            source_path=source_path,
            user_id="u",
            readonly=True,
            deletable=False,
            status="available",
            creation_origin="discovered",
            library_enabled=library_enabled,
        )

    @staticmethod
    def _seed_user_row(source_path: str, *, library_enabled: bool = False):
        from valuz_agent.modules.skills.models import SkillIndexRow

        return SkillIndexRow(
            slug="shared",
            name="shared",
            description="user copy",
            scope="user",
            source="valuz",
            source_path=source_path,
            user_id="u",
            status="available",
            creation_origin="discovered",
            library_enabled=library_enabled,
        )

    class _ExplodingOfficialSource:
        name = "official"

        def list_skills(self, ctx, *, compute_content_hash=True):
            raise AssertionError("official source should not be scanned when index is warm")

    async def test_startup_scan_indexes_both_copies_without_shadowing(
        self, svc, skill_root, tmp_path
    ):
        from valuz_agent.integrations.skills_official import OfficialSkillSource

        # A user copy under the user skills root...
        _make_skill_dir(skill_root, "shared")
        # ...and an official copy of the SAME slug under the official-skills root.
        official_dir = tmp_path / "official"
        official_copy = _make_skill_dir(official_dir, "shared")
        (official_copy / ".bundled-version").write_text("v1", encoding="utf-8")
        svc._extra_sources = [OfficialSkillSource(official_dir=official_dir)]

        await svc.startup_scan("u")

        rows = [r for r in await svc._ds.list_skills("u") if r.slug == "shared"]
        # Both rows exist — the user source (scanned first) no longer claims the
        # slug and drops the official one.
        assert {r.scope for r in rows} == {"user", "official"}
        assert len({r.source_path for r in rows}) == 2
        # ``get_by_slug`` resolves the effective copy: official outranks user.
        effective = await svc._ds.get_by_slug("u", "shared")
        assert effective is not None and effective.scope == "official"

    async def test_catalog_shows_both_copies_in_their_groups(self, svc, tmp_path):
        official_dir = tmp_path / "official" / "shared"
        official_dir.mkdir(parents=True)
        (official_dir / ".bundled-version").write_text("v1", encoding="utf-8")
        await svc._ds.create("u", self._seed_official_row(str(official_dir)))
        await svc._ds.create("u", self._seed_user_row(str(tmp_path / "user" / "shared")))
        svc._extra_sources = [self._ExplodingOfficialSource()]

        catalog = await svc.list_catalog("u", "ws-1")

        shared = [s for s in catalog.skills if s.slug == "shared"]
        assert {s.id for s in shared} == {"official:shared", "user:shared"}
        official = next(s for s in shared if s.scope == "official")
        user = next(s for s in shared if s.scope == "user")
        assert official.origin_label == "Built-in"
        assert user.origin_label != "Built-in"

    async def test_disabling_user_copy_does_not_disable_official(self, svc, tmp_path):
        official_dir = tmp_path / "official" / "shared"
        official_dir.mkdir(parents=True)
        (official_dir / ".bundled-version").write_text("v1", encoding="utf-8")
        await svc._ds.create(
            "u", self._seed_official_row(str(official_dir), library_enabled=True)
        )
        # The user copy is turned OFF; a slug-keyed overlay would wrongly hide the
        # enabled official copy too. Path-keying isolates them.
        await svc._ds.create(
            "u", self._seed_user_row(str(tmp_path / "user" / "shared"), library_enabled=False)
        )
        svc._extra_sources = [self._ExplodingOfficialSource()]

        catalog = await svc.list_catalog("u", "ws-1")

        official = next(
            s for s in catalog.skills if s.slug == "shared" and s.scope == "official"
        )
        user = next(s for s in catalog.skills if s.slug == "shared" and s.scope == "user")
        assert official.library_enabled is True
        assert user.library_enabled is False


class TestListCatalog:
    async def test_should_return_name_and_description_fields(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "alpha", "Alpha body")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        assert skill.name == "alpha"
        assert skill.description == "Test alpha"
        assert hasattr(skill, "name")
        assert not hasattr(skill, "title")

    async def test_should_include_slug_and_tags(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "beta")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        assert skill.slug == "beta"
        assert skill.tags == ["test"]

    async def test_should_include_content_hash(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "hashed")
        catalog = await service.list_catalog("u", "ws-1")
        skill = catalog.skills[0]
        assert skill.content_hash is not None
        assert len(skill.content_hash) == 64

    async def test_should_return_empty_when_no_skills(self, svc):
        service = svc
        catalog = await service.list_catalog("u", "ws-1")
        assert catalog.skills == []

    async def test_should_read_official_skills_from_index_without_rescanning_source(
        self, svc, tmp_path
    ):
        from valuz_agent.modules.skills.models import SkillIndexRow

        service = svc
        official_dir = tmp_path / "official" / "skill-creator"
        official_dir.mkdir(parents=True)
        (official_dir / ".bundled-version").write_text("v1", encoding="utf-8")

        class _ExplodingOfficialSource:
            name = "official"

            def list_skills(self, ctx, *, compute_content_hash=True):
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
        service = svc
        from valuz_agent.integrations.skills_official import OfficialSkillSource

        official_dir = tmp_path / "official"
        _make_skill_dir(official_dir, "browser")
        service._extra_sources = [OfficialSkillSource(official_dir=official_dir)]

        catalog = await service.list_catalog("u", "ws-1")

        assert any(skill.slug == "browser" for skill in catalog.skills)

    async def test_should_read_user_skills_from_index_without_rescanning_source(
        self, svc, tmp_path
    ):
        from valuz_agent.modules.skills.models import SkillIndexRow

        service = svc
        user_dir = tmp_path / "user" / "my-skill"
        user_dir.mkdir(parents=True)

        class _ExplodingSource:
            name = "filesystem"

            def list_skills(self, ctx, *, compute_content_hash=True):
                raise AssertionError(
                    "the filesystem source must not be scanned when the index has user skills"
                )

        service._source = _ExplodingSource()
        await service._ds.create(
            "u",
            SkillIndexRow(
                slug="my-skill",
                name="My Skill",
                description="Desc",
                scope="user",
                source="valuz",
                source_path=str(user_dir),
                user_id="u",
                status="available",
                readonly=False,
                deletable=True,
                content_hash="c" * 64,
                manifest_hash="m" * 64,
                tags_json="a,b",
                creation_origin="created",
                library_enabled=True,
            ),
        )

        catalog = await service.list_catalog("u", "ws-1")

        skill = next(s for s in catalog.skills if s.slug == "my-skill")
        assert skill.id == "user:my-skill"
        assert skill.name == "My Skill"
        assert skill.creation_origin == "created"
        assert skill.tags == ["a", "b"]
        assert skill.readonly is False
        assert skill.deletable is True

    async def test_should_fallback_to_filesystem_scan_when_user_index_is_empty(
        self, svc, skill_root
    ):
        # No user rows in the index → fall back to a filesystem scan so a
        # freshly-created (not-yet-indexed) skill is never missing from the catalog.
        service = svc
        _make_skill_dir(skill_root, "scanned-skill")

        catalog = await service.list_catalog("u", "ws-1")

        assert any(s.slug == "scanned-skill" for s in catalog.skills)

    async def test_should_sort_by_folder_birthtime_desc(self, svc, skill_root):
        """The skill management page renders the catalog in DESC
        birthtime order. We stage two folders with deliberately staggered
        mtimes (os.utime is the cross-platform knob) and verify the
        newer one lands first. NB: ``_folder_birthtime`` reads
        ``st_birthtime`` when available and falls back to ``st_mtime``
        — setting mtime covers both branches on macOS and Linux."""
        import os as _os
        import time

        service = svc
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
        service = svc
        # Two skills with valid birthtime + one stubbed manifest whose
        # source manifest claims None for the timestamp.
        _make_skill_dir(skill_root, "real-1")

        # Fake a manifest entry with None timestamp by monkeypatching the
        # source. Easier: add an "extra source" returning a manifest with
        # folder_created_at=None. SkillLibraryService exposes that knob.

        class _NullTimeSource:
            name = "null-time"

            def list_skills(self, ctx, *, compute_content_hash=True):
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
    picker filters on. Scanned user skills default off; deliberate create/import
    flows opt skills back in."""

    async def test_scanned_skills_default_off_and_preserve_enabled_row(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "alpha")
        _make_skill_dir(skill_root, "beta")
        await service.startup_scan("u")
        await service._ds.set_library_enabled_by_slug("u", "beta", True)

        catalog = await service.list_catalog("u", "ws-1")
        by_slug = {s.slug: s for s in catalog.skills}

        assert by_slug["alpha"].library_enabled is False
        assert by_slug["beta"].library_enabled is True

    async def test_builtin_skill_can_be_disabled(self, svc, skill_root):
        service = svc
        # A built-in skill (``origin-label: Built-in`` frontmatter) defaults on,
        # but still honors the library switch when a user hides it.
        d = skill_root / "skill-creator"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            '---\nname: "skill-creator"\ndescription: "x"\norigin-label: "Built-in"\n---\n\nbody\n',
            encoding="utf-8",
        )
        # The pre-index (fallback scan) view carries the Built-in label from the
        # SKILL.md frontmatter.
        cat0 = await service.list_catalog("u", "ws-1")
        sc0 = next(s for s in cat0.skills if s.slug == "skill-creator")
        assert sc0.origin_label == "Built-in"

        # Index it (the production read path) and hide it through the real
        # library toggle, which resolves the row by its on-disk path.
        await service.startup_scan("u")
        cat1 = await service.list_catalog("u", "ws-1")
        sc_id = next(s for s in cat1.skills if s.slug == "skill-creator").id
        await service.set_library_enabled("u", sc_id, False)

        catalog = await service.list_catalog("u", "ws-1")
        sc = next(s for s in catalog.skills if s.slug == "skill-creator")
        assert sc.library_enabled is False

    async def test_toggle_returns_updated_and_persists(self, svc, skill_root):
        from valuz_agent.modules.skills.models import SkillIndexRow

        service = svc
        _make_skill_dir(skill_root, "gamma")
        cat = await service.list_catalog("u", "ws-1")
        gamma = next(s for s in cat.skills if s.slug == "gamma")
        assert gamma.library_enabled is False
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
    async def test_should_create_skill_dir_with_manifest(self, svc, skill_root):
        service = svc
        result = await service.create_skill(
            "u", SkillCreateRequest(name="created", description="A test")
        )
        assert result.name == "created"
        assert result.library_enabled is True
        assert (Path(result.path) / "SKILL.md").exists()

    async def test_should_not_write_creation_origin_into_skill_md(self, svc, skill_root):
        """creation_origin is host bookkeeping (valuz_skill_index) — it
        must NOT be written into the user's SKILL.md frontmatter, but
        the returned view still reports the skill as "created"."""
        service = svc
        result = await service.create_skill(
            "u", SkillCreateRequest(name="origin-check", description="x")
        )
        raw = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert "creation-origin" not in raw
        assert result.creation_origin == "created"

    async def test_should_expose_creation_origin_via_catalog(self, svc, skill_root):
        """The catalog View must expose ``creation_origin`` sourced from
        the DB index — it's what drives the .agents group's badge."""
        service = svc
        await service.create_skill("u", SkillCreateRequest(name="origin-view", description="y"))
        catalog = await service.list_catalog("u", "ws-1")
        match = next(s for s in catalog.skills if s.slug == "origin-view")
        assert match.creation_origin == "created"

    async def test_should_keep_special_characters_out_of_name_and_slug(self, svc, skill_root):
        """The frontmatter name becomes a *directory* name when the kernel
        materializes the skill into a session cwd, so a namespaced name like
        ``react:components`` would be uncreatable on Windows (WinError 267).
        Both the directory and the manifest name are sanitized at creation."""
        service = svc
        result = await service.create_skill(
            "u", SkillCreateRequest(name="react:components", description="A test")
        )
        assert Path(result.path).name == "react-components"
        raw = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert 'name: "react-components"' in raw
        assert "react:components" not in raw

    async def test_should_dodge_windows_device_name_for_slug(self, svc, skill_root):
        """``con`` / ``com1`` are reserved device names on Windows — a
        directory can't carry one even though the charset is plain ASCII."""
        service = svc
        result = await service.create_skill("u", SkillCreateRequest(name="con", description="x"))
        assert Path(result.path).name == "con-skill"

    async def test_should_default_to_discovered_for_scanned_skill(self, svc, skill_root):
        """A skill folder dropped on disk (not created via Valuz) shows
        as ``"discovered"`` — it must NOT get the "创建" badge. This is
        the bug behind the .agents-vs-.claude display confusion."""
        service = svc
        _make_skill_dir(skill_root, "scanned-skill")
        catalog = await service.list_catalog("u", "ws-1")
        match = next(s for s in catalog.skills if s.slug == "scanned-skill")
        assert match.creation_origin == "discovered"
        assert match.library_enabled is False


class TestUpdateSkill:
    async def test_should_rename_skill(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "updatable")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id

        result = await service.update_skill(
            "u", skill_id, SkillUpdateRequest(name="updated-name")
        )
        assert result.name == "updated-name"


class TestDeleteSkill:
    async def test_confirm_delete_marks_unavailable(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "deletable")
        await service.startup_scan("u")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id

        await service.delete_skill("u", skill_id, mode="confirm")
        row = await service._ds.get_by_slug("u", "deletable")
        assert row is not None and row.status == "unavailable"

    async def test_confirm_delete_cleans_up_marketplace_install_row(
        self, svc, skill_root, monkeypatch
    ):
        """Wiring check for the marketplace-provenance cleanup hook: a real
        session-backed removal is covered end-to-end in
        tests/modules/marketplace/test_marketplace_install_cleanup.py; this
        only asserts delete_skill calls it with the deleted skill's slug.
        The datastore fake has no real DB session, so the hook's own
        best-effort try/except must not swallow this monkeypatched call."""
        _make_skill_dir(skill_root, "market-installed")
        await svc.startup_scan("u")
        catalog = await svc.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id

        calls: list[tuple[str, str]] = []

        class _StubInstallStore:
            def __init__(self, session):
                pass

            async def remove_by_ref(self, user_id: str, installed_ref: str) -> None:
                calls.append((user_id, installed_ref))

        svc._ds.session = object()
        monkeypatch.setattr(
            "valuz_agent.modules.marketplace.install_store.MarketplaceInstallStore",
            _StubInstallStore,
        )

        await svc.delete_skill("u", skill_id, mode="confirm")

        assert calls == [("u", "market-installed")]

    async def test_dry_run_should_return_preview(self, svc, skill_root):
        service = svc
        _make_skill_dir(skill_root, "preview-del")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        result = await service.delete_skill("u", skill_id, mode="dry_run")
        assert result is not None
        assert hasattr(result, "count")


class TestReadonlySkill:
    async def test_should_reject_write_on_readonly_skill(self, svc, skill_root):
        service = svc
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
        service = svc
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
    async def test_should_extract_archive_body_when_url_has_no_archive_suffix(
        self, svc, skill_root, tmp_path, monkeypatch
    ):
        import io
        import urllib.request

        service = svc
        from valuz_agent.infra import fs_registry as fsr

        monkeypatch.setattr(fsr.settings, "user_temp_dir", tmp_path / "temp" / "{user_id}")

        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as zf:
            zf.writestr(
                "ima-skills/SKILL.md",
                '---\nname: "ima-skills"\ndescription: "IMA skill"\n---\n\nUse IMA.\n',
            )
            zf.writestr("ima-skills/references/api.md", "API docs")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return archive_bytes.getvalue()

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: FakeResponse())

        preview = await service.import_url_preview(
            "u",
            "https://api.skillhub.cn/api/v1/download?slug=ima-skills",
        )
        assert preview.name == "ima-skills"

        imported = await service.confirm_url_import(
            "u",
            SkillImportUrlConfirmRequest(preview_id=preview.preview_id, name="ima-skills"),
        )

        assert imported.slug == "ima-skills"
        manifest = skill_root / imported.slug / "SKILL.md"
        assert manifest.read_bytes().startswith(b"---")
        assert "Use IMA." in manifest.read_text(encoding="utf-8")

    async def test_should_raise_preview_expired_after_ttl(self, svc, tmp_path, monkeypatch):
        import time

        service = svc
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
        with pytest.raises(PreviewExpired):
            await service.confirm_url_import(
                "u", SkillImportUrlConfirmRequest(preview_id=preview_id)
            )
        assert not cleanup_root.exists()


class TestSkillFiles:
    async def test_should_list_files_in_skill_dir(self, svc, skill_root):
        service = svc
        skill_dir = _make_skill_dir(skill_root, "with-files")
        (skill_dir / "extra.txt").write_text("hello")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        files = await service.list_skill_files("u", skill_id)
        paths = [f.path for f in files]
        assert "SKILL.md" in paths
        assert "extra.txt" in paths

    async def test_should_read_file_content(self, svc, skill_root):
        service = svc
        skill_dir = _make_skill_dir(skill_root, "readable")
        (skill_dir / "data.txt").write_text("content here")
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        result = await service.read_skill_file("u", skill_id, "data.txt")
        assert result.content == "content here"

    async def test_should_read_non_utf8_imported_file_content(self, svc, skill_root):
        service = svc
        skill_dir = _make_skill_dir(skill_root, "gbk-file")
        (skill_dir / "api.md").write_bytes("接口说明".encode("gb18030"))
        catalog = await service.list_catalog("u", "ws-1")
        skill_id = catalog.skills[0].id
        result = await service.read_skill_file("u", skill_id, "api.md")
        assert result.content == "接口说明"


class TestSkillDetail:
    async def test_should_return_detail_with_instructions(self, svc, skill_root):
        service = svc
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
        service = svc
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
        service = svc
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
        service = svc
        self._patch_events(monkeypatch, [])
        result = await service.import_from_session_confirm(
            "u",
            SessionSkillImportConfirmRequest(
                session_id="sess-2", name="empty-session", description="Fallback body."
            ),
        )
        body = (Path(result.path) / "SKILL.md").read_text(encoding="utf-8")
        assert "Fallback body." in body
