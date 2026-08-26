"""Tests for FilesystemSkillSource — frontmatter parsing and discovery."""

import os
from pathlib import Path
from unittest.mock import patch

from valuz_agent.infra.config import settings
from valuz_agent.integrations.skills_filesystem import (
    FilesystemSkillSource,
    _extract_frontmatter,
    _folder_birthtime,
)
from valuz_agent.modules.skills.contracts import RuntimeContext


def _set_user_skills_dir(monkeypatch, path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "user_skills_dir", path)
    home = path.parent / ".test-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)


def _write_skill(root: Path, slug: str, name: str | None = None) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    skill_name = name or slug
    (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n\nBody\n")


class TestExtractFrontmatter:
    def test_should_parse_standard_frontmatter(self):
        raw = '---\nname: "Test"\ndescription: "A test"\ntags: ["a", "b"]\n---\n\nBody here.'
        meta, body = _extract_frontmatter(raw)
        assert meta["name"] == "Test"
        assert meta["description"] == "A test"
        assert meta["tags"] == ["a", "b"]
        assert body.strip() == "Body here."

    def test_should_return_empty_when_no_frontmatter(self):
        raw = "Just a body with no frontmatter."
        meta, body = _extract_frontmatter(raw)
        assert meta == {}
        assert body == raw

    def test_should_handle_missing_closing_delimiter(self):
        raw = "---\nname: Test\nNo closing delimiter"
        meta, body = _extract_frontmatter(raw)
        assert meta == {}
        assert body == raw

    def test_should_strip_quotes_from_values(self):
        raw = "---\nname: \"Quoted\"\nother: 'single'\n---\n\nBody"
        meta, _ = _extract_frontmatter(raw)
        assert meta["name"] == "Quoted"
        assert meta["other"] == "single"

    def test_should_handle_empty_values(self):
        raw = '---\nname:\ndescription: "has value"\n---\n\nBody'
        meta, _ = _extract_frontmatter(raw)
        assert meta["name"] == ""
        assert meta["description"] == "has value"

    def test_should_parse_unknown_keys(self):
        raw = '---\nname: "Test"\ncustom-key: "custom-value"\n---\n\nBody'
        meta, _ = _extract_frontmatter(raw)
        assert meta["custom-key"] == "custom-value"

    def test_should_fold_block_scalar_description(self):
        """The reported bug: ``description: >`` surfaced a literal ``>`` as
        the library-card description. The folded scalar must be joined into
        the full text — including continuation lines that contain colons."""
        raw = (
            "---\n"
            "name: python-backend-craft\n"
            "description: >\n"
            '  This skill should be used when the user asks to "build a backend",\n'
            '  "create an API", or any task involving: design, testing, deploys.\n'
            "tags: [python, backend]\n"
            "---\n"
            "\nBody"
        )
        meta, body = _extract_frontmatter(raw)
        desc = str(meta["description"])
        assert desc.startswith("This skill should be used")
        assert "create an API" in desc and desc.endswith("design, testing, deploys.")
        assert ">" != desc
        assert meta["tags"] == ["python", "backend"]
        assert body.strip() == "Body"

    def test_should_keep_literal_block_scalar_text(self):
        raw = "---\ndescription: |\n  line one\n  line two\n---\n\nBody"
        meta, _ = _extract_frontmatter(raw)
        assert meta["description"] == "line one\nline two"

    def test_should_keep_nested_mapping_out_of_top_level(self):
        raw = "---\nname: x\nmetadata:\n  short-description: short\n---\n\nBody"
        meta, _ = _extract_frontmatter(raw)
        # The nested key must not leak into the top level (the old parser
        # flattened it); it stays under the ``metadata`` mapping.
        assert "short-description" not in meta
        assert meta["metadata"] == {"short-description": "short"}

    def test_should_fall_back_to_line_parser_on_invalid_yaml(self):
        raw = "---\nname: Test\nbroken: @not yaml\n---\n\nBody"
        meta, _ = _extract_frontmatter(raw)
        assert meta["name"] == "Test"


class TestFilesystemSkillSource:
    def test_should_discover_skills_in_directory(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\nname: "My Skill"\ndescription: "Desc"\ntags: ["test"]\n---\n\nBody\n'
        )

        source = FilesystemSkillSource()
        ctx = RuntimeContext()
        manifests = source.list_skills(ctx)
        assert len(manifests) == 1
        assert manifests[0].name == "My Skill"
        assert manifests[0].description == "Desc"
        assert manifests[0].slug == "my-skill"
        assert manifests[0].tags == ["test"]

    def test_should_skip_dirs_without_manifest(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path)
        (tmp_path / "no-manifest").mkdir()
        (tmp_path / "no-manifest" / "readme.txt").write_text("not a skill")

        source = FilesystemSkillSource()
        manifests = source.list_skills(RuntimeContext())
        assert len(manifests) == 0

    def test_should_detect_lowercase_skill_md(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "lower"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text('---\nname: "Lower"\n---\n\nBody\n')

        source = FilesystemSkillSource()
        manifests = source.list_skills(RuntimeContext())
        assert len(manifests) == 1
        assert manifests[0].name == "Lower"

    def test_should_compute_content_hash(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "hashed"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\nname: "H"\n---\n\nBody\n')

        source = FilesystemSkillSource()
        manifests = source.list_skills(RuntimeContext())
        assert manifests[0].content_hash is not None
        assert len(manifests[0].content_hash) == 64
        assert manifests[0].manifest_hash is not None

    def test_should_skip_content_hash_when_disabled(self, tmp_path, monkeypatch):
        # The display/catalog list passes compute_content_hash=False so it never
        # reads every file in the skill dir (slow on a network filesystem). Only
        # ``content_hash`` is skipped; ``manifest_hash`` (from the SKILL.md that
        # was read anyway) stays populated.
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "nohash"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\nname: "N"\n---\n\nBody\n')

        manifests = FilesystemSkillSource().list_skills(
            RuntimeContext(), compute_content_hash=False
        )
        assert manifests[0].content_hash is None
        assert manifests[0].manifest_hash is not None

    def test_manifest_cache_refreshes_on_edit(self, tmp_path, monkeypatch):
        # The parsed-manifest cache is validated by a stat signature, so an edit
        # is picked up on the next list — it must never serve stale metadata.
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "cached"
        skill_dir.mkdir()
        md = skill_dir / "SKILL.md"
        md.write_text('---\nname: "First"\n---\n\nBody\n')

        source = FilesystemSkillSource()
        assert source.list_skills(RuntimeContext())[0].name == "First"

        # Different length → different stat size → cache signature changes even if
        # the filesystem's mtime resolution is coarse.
        md.write_text('---\nname: "Second-longer"\n---\n\nBody\n')
        assert source.list_skills(RuntimeContext())[0].name == "Second-longer"

    def test_should_parse_extended_frontmatter_fields(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "extended"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\nname: "Ext"\ndescription: "D"\nicon: "rocket"\n'
            'argument-hint: "query"\ncontext: "project"\n'
            'origin-label: "Custom"\n---\n\nBody\n'
        )

        source = FilesystemSkillSource()
        manifests = source.list_skills(RuntimeContext())
        m = manifests[0]
        assert m.icon == "rocket"
        assert m.argument_hint == "query"
        assert m.context == "project"
        assert m.origin_label == "Custom"

    def test_should_return_empty_when_dir_missing(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path / "nonexistent")
        source = FilesystemSkillSource()
        manifests = source.list_skills(RuntimeContext())
        assert manifests == []

    def test_should_discover_configured_agents_claude_and_codex_roots(
        self, tmp_path, monkeypatch
    ):
        custom_root = tmp_path / "configured-skills"
        fake_home = tmp_path / "home"
        monkeypatch.setattr(settings, "user_skills_dir", custom_root)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        _write_skill(custom_root, "configured")
        _write_skill(fake_home / ".agents" / "skills", "agents")
        _write_skill(fake_home / ".claude" / "skills", "claude")
        _write_skill(fake_home / ".codex" / "skills", "codex")

        manifests = FilesystemSkillSource().list_skills(RuntimeContext())
        by_slug = {manifest.slug: manifest for manifest in manifests}

        assert set(by_slug) == {"configured", "agents", "claude", "codex"}
        assert by_slug["configured"].source == "valuz"
        assert by_slug["agents"].source == "valuz"
        assert by_slug["claude"].source == "claude"
        assert by_slug["codex"].source == "codex"

    def test_should_dedupe_when_configured_root_is_agents_root(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        agents_root = fake_home / ".agents" / "skills"
        monkeypatch.setattr(settings, "user_skills_dir", agents_root)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        _write_skill(agents_root, "shared")

        manifests = FilesystemSkillSource().list_skills(RuntimeContext())
        assert [manifest.slug for manifest in manifests] == ["shared"]

    def test_should_skip_host_compatibility_roots_in_cloud(self, tmp_path, monkeypatch):
        custom_root = tmp_path / "cloud-user-skills" / "{user_id}"
        fake_home = tmp_path / "host-home"
        monkeypatch.setattr(settings, "deployment_type", "cloud")
        monkeypatch.setattr(settings, "user_skills_dir", custom_root)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        _write_skill(tmp_path / "cloud-user-skills" / "owner-1", "cloud-owned")
        _write_skill(fake_home / ".agents" / "skills", "host-agents")
        _write_skill(fake_home / ".claude" / "skills", "host-claude")
        _write_skill(fake_home / ".codex" / "skills", "host-codex")

        manifests = FilesystemSkillSource().list_skills(
            RuntimeContext(user_id="owner-1")
        )

        assert [manifest.slug for manifest in manifests] == ["cloud-owned"]


class TestFolderBirthtime:
    """The DESC sort on the skill management page hangs off birthtime,
    so the helper must (a) prefer ``st_birthtime`` when present,
    (b) fall back to ``st_mtime`` cleanly, (c) never raise."""

    def test_should_return_epoch_ms_int(self, tmp_path):
        result = _folder_birthtime(tmp_path)
        assert result is not None
        # Instants are Unix epoch ms (UTC) ints now — not datetimes.
        assert isinstance(result, int)
        assert result > 0

    def test_should_fall_back_to_mtime_when_birthtime_missing(self, tmp_path):
        """Simulates Linux on a filesystem without statx support: the
        ``stat_result`` lacks ``st_birthtime`` entirely. We monkey-patch
        the helper's stat() call to drop the attribute."""

        original_stat = os.stat

        class _NoBirthtimeStat:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                if name == "st_birthtime":
                    raise AttributeError("simulated missing st_birthtime")
                return getattr(self._real, name)

        def _fake_stat(path):
            return _NoBirthtimeStat(original_stat(path))

        from pathlib import Path as _Path

        with patch.object(_Path, "stat", lambda self: _fake_stat(self)):
            result = _folder_birthtime(tmp_path)
        # The fallback path should still produce a datetime; never None
        # for an existing directory.
        assert result is not None

    def test_should_return_none_for_nonexistent_path(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        assert _folder_birthtime(missing) is None


class TestFilesystemSourceFolderBirthtime:
    """End-to-end: folder birthtime should always be populated on the
    SkillManifest — it drives the DESC sort on the skill management
    page. (``creation_origin`` is host bookkeeping kept in the DB, not
    on the filesystem-scanned manifest — covered in test_service.py.)"""

    def test_should_populate_folder_created_at(self, tmp_path, monkeypatch):
        _set_user_skills_dir(monkeypatch, tmp_path)
        skill_dir = tmp_path / "timed"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\nname: "T"\n---\n\nBody\n')
        manifests = FilesystemSkillSource().list_skills(RuntimeContext())
        assert manifests[0].folder_created_at is not None
        # Epoch ms (UTC) int — drives the DESC sort on the skill page.
        assert isinstance(manifests[0].folder_created_at, int)
        assert manifests[0].folder_created_at > 0
