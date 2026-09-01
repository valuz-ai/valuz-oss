"""PluginService — install / link / conflict / reference-counted uninstall /
enable-disable / update / export against a real SQLite DB, the real skill
library service (filesystem source + index datastore) and the real connector
service. Only the project service is a stub (a chat project is all the skill
catalog needs)."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.modules.plugins.helpers import (
    build_agent_plugin,
    build_legacy_plugin,
    write_skill,
    zip_dir,
)
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import ConnectorAttrRow, ConnectorOAuthRow, ConnectorRow
from valuz_agent.modules.connectors.service import ConnectorService
from valuz_agent.modules.marketplace.install_store import (
    MarketplaceInstallRow,
    MarketplaceInstallStore,
)
from valuz_agent.modules.marketplace.market_index import MarketIndexUnavailableError
from valuz_agent.modules.plugins.datastore import PluginDatastore
from valuz_agent.modules.plugins.errors import (
    PluginConflict,
    PluginFetchFailed,
    PluginInstallFailed,
    PluginInvalid,
    PluginNotFound,
    PluginSourceUnavailable,
)
from valuz_agent.modules.plugins.manifest import MCP_SCHEMA_ID, hash_directory, load_plugin_dir
from valuz_agent.modules.plugins.models import PluginComponentRow, PluginRow
from valuz_agent.modules.plugins.service import PluginService
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import ProjectSkillConfigRow, SkillIndexRow
from valuz_agent.modules.skills.service import SkillLibraryService

USER = "plugin-user"


class _Project:
    def __init__(self, id: str = "chat-default", kind: str = "chat") -> None:
        self.id = id
        self.kind = kind
        self.root_path: str | None = None
        self.name = id


class _FakeProjectService:
    def __init__(self) -> None:
        self._projects = [_Project()]

    async def get_project(self, user_id: str, project_id: str) -> _Project:
        for p in self._projects:
            if p.id == project_id:
                return p
        raise KeyError(project_id)

    async def list_projects(self, user_id: str) -> list[_Project]:
        return self._projects


class _FakeMarket:
    channel = "oss"

    def __init__(self) -> None:
        self.details: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    async def item_detail(self, item_id: str, locale: str) -> dict[str, Any]:
        self.calls.append(item_id)
        if item_id not in self.details:
            raise MarketIndexUnavailableError("down")
        return self.details[item_id]


class Env:
    def __init__(self, tmp_path: Path, session: Any) -> None:
        self.tmp_path = tmp_path
        self.session = session
        self.skill_ds = SkillDatastore(session)
        self.skills = SkillLibraryService(
            datastore=self.skill_ds,
            skill_source=FilesystemSkillSource(),
            project_service=_FakeProjectService(),  # type: ignore[arg-type]
            extra_sources=[],
        )
        self.connectors = ConnectorService(ConnectorDatastore(session))
        self.plugin_ds = PluginDatastore(session)
        self.market = _FakeMarket()
        self.installs = MarketplaceInstallStore(session)
        self.svc = PluginService(
            datastore=self.plugin_ds,
            skill_service=self.skills,
            connector_service=self.connectors,
            market=self.market,
            installs=self.installs,
        )

    @property
    def skill_root(self) -> Path:
        return fs_registry.user_skill_root(user_id=USER)

    async def skill_row(self, slug: str) -> SkillIndexRow | None:
        target = (self.skill_root / slug).resolve()
        for row in await self.skill_ds.list_skills(USER):
            if row.source_path and Path(row.source_path).resolve() == target:
                return row
        return None

    async def connector(self, slug: str) -> Any:
        return next(
            (v for v in await self.connectors.list_connectors(USER) if v.slug == slug), None
        )

    async def rows(self) -> tuple[list[PluginRow], list[PluginComponentRow]]:
        return await self.plugin_ds.list_plugins(USER), await self.plugin_ds.list_all_components(
            USER
        )


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "user_skills_dir", tmp_path / "skills")
    # Cloud mode: the filesystem source scans ONLY the configured user root
    # (never the developer's real ~/.agents / ~/.claude skill folders).
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plugins.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                PluginRow.__table__,
                PluginComponentRow.__table__,
                SkillIndexRow.__table__,
                ProjectSkillConfigRow.__table__,
                ProjectRow.__table__,
                ConnectorRow.__table__,
                ConnectorAttrRow.__table__,
                ConnectorOAuthRow.__table__,
                MarketplaceInstallRow.__table__,
            ],
        )
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield Env(tmp_path, session)
    finally:
        await session.close()
        await engine.dispose()


def _full_plugin(root: Path, name: str = "reports") -> Path:
    return build_agent_plugin(
        root,
        name=name,
        skills={
            "summarize": {
                "extra": {"scripts/run.sh": "echo run\n"},
                "metadata": {"version": "1.2"},
            },
            "deploy": {},
        },
        servers={
            "local-validator": {
                "type": "stdio",
                "command": "npx",
                "args": ["--data", "${PLUGIN_DATA}/validator"],
                "env": {"CONFIG": "${PLUGIN_ROOT}/config.json"},
            },
            "deploy-api": {
                "type": "streamable-http",
                "url": "https://deploy.example.com/mcp",
                "headers": {"X-Tenant": "public"},
            },
        },
    )


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


async def test_install_from_zip_materializes_root_skills_and_connectors(env: Env) -> None:
    src = _full_plugin(env.tmp_path / "src")
    result = await env.svc.install(USER, zip_bytes=zip_dir(src))
    assert result.status == "installed"
    assert result.conflicts == [] and result.skipped == []
    view = result.plugin
    assert view.name == "reports" and view.version == "1.0.0"
    assert view.source == "zip" and view.source_ref is None
    assert view.composition == "with_connectors"
    assert view.skill_count == 2 and view.connector_count == 2
    assert all(m.installed and not m.content_differs for m in view.members)
    assert view.installed_at.endswith("Z")

    # PLUGIN_ROOT holds the package verbatim; PLUGIN_DATA exists and is writable.
    root = Path(view.root_path)
    assert root == fs_registry.plugin_root(USER, "reports")
    assert (root / "plugin.json").is_file() and (root / "mcp.json").is_file()
    data_dir = fs_registry.plugins_data_root(USER) / "reports"
    assert data_dir.is_dir()
    (data_dir / "probe").write_text("ok")

    # Skills landed in the user skill root with their EXACT files, indexed,
    # switched on and marked as imported.
    for slug in ("summarize", "deploy"):
        lib = env.skill_root / slug
        assert (lib / "SKILL.md").read_bytes() == (src / "skills" / slug / "SKILL.md").read_bytes()
        assert hash_directory(lib) == hash_directory(src / "skills" / slug)
        row = await env.skill_row(slug)
        assert row is not None and row.library_enabled and row.creation_origin == "imported"
    assert (env.skill_root / "summarize" / "scripts" / "run.sh").read_text() == "echo run\n"
    summarize = next(m for m in view.members if m.slug == "summarize")
    assert summarize.meta_version == "1.2"

    # Connectors: stdio with placeholders expanded + reserved env injected;
    # streamable-http → http with the fixed headers.
    local = await env.connector("local-validator")
    assert local is not None and local.transport == "stdio" and local.command == "npx"
    assert local.args == ["--data", str(data_dir / "validator")]
    assert local.working_dir == str(root)
    row = await ConnectorDatastore(env.session).get_by_slug(USER, "local-validator")
    assert row is not None
    env_json = json.loads(row.env_json or "{}")
    assert env_json["CONFIG"] == f"{root}/config.json"
    assert env_json["PLUGIN_ROOT"] == str(root) and env_json["PLUGIN_DATA"] == str(data_dir)
    remote = await env.connector("deploy-api")
    assert remote is not None and remote.transport == "http"
    assert remote.url == "https://deploy.example.com/mcp"
    assert [(h.key, h.value) for h in remote.headers] == [("X-Tenant", "public")]

    # Component rows: everything this plugin brought in is ``installed``.
    plugins, comps = await env.rows()
    assert len(plugins) == 1 and len(comps) == 4
    assert {c.origin for c in comps} == {"installed"}


async def test_reinstalling_the_same_zip_is_idempotent(env: Env) -> None:
    src = _full_plugin(env.tmp_path / "src")
    first = await env.svc.install(USER, zip_bytes=zip_dir(src))
    second = await env.svc.install(USER, zip_bytes=zip_dir(src))
    assert second.status == "already_installed"
    assert second.plugin.id == first.plugin.id
    plugins, comps = await env.rows()
    assert len(plugins) == 1 and len(comps) == 4
    assert len(await env.svc.list_plugins(USER)) == 1


async def test_install_from_directory_and_get(env: Env) -> None:
    src = build_agent_plugin(env.tmp_path / "src", name="dir-plugin", skills={"alpha": {}})
    result = await env.svc.install(USER, path=str(src))
    assert result.plugin.source == "local_dir" and result.plugin.source_ref == str(src.resolve())
    assert result.plugin.composition == "skills_only"
    fetched = await env.svc.get_plugin(USER, result.plugin.id)
    assert fetched.name == "dir-plugin" and [m.slug for m in fetched.members] == ["alpha"]
    with pytest.raises(PluginNotFound):
        await env.svc.get_plugin(USER, "nope")


async def test_install_rejects_bad_sources(env: Env) -> None:
    with pytest.raises(PluginInstallFailed):
        await env.svc.install(USER, zip_bytes=b"not a zip")
    with pytest.raises(PluginInstallFailed):
        await env.svc.install(USER, path=str(env.tmp_path / "missing"))
    with pytest.raises(PluginInstallFailed):
        await env.svc.install(USER)  # no source
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("README.md", "nothing")
    with pytest.raises(PluginInvalid):
        await env.svc.install(USER, zip_bytes=buffer.getvalue())
    bad = build_agent_plugin(env.tmp_path / "bad", name="bad")
    (bad / "plugin.json").write_text(json.dumps({"name": "bad"}))  # no $schema → fatal
    with pytest.raises(PluginInvalid):
        await env.svc.install(USER, path=str(bad))
    assert await env.svc.list_plugins(USER) == []


async def test_component_failures_are_isolated_and_reported(env: Env) -> None:
    src = build_agent_plugin(
        env.tmp_path / "src",
        name="partial",
        skills={"good": {}},
        servers={
            "ok": {"type": "stdio", "command": "x"},
            "bad": {"type": "stdio", "command": "x y"},
            "escape": {"type": "stdio", "command": "x", "cwd": "./../out"},
        },
    )
    (src / "skills" / "Broken").mkdir()
    (src / "skills" / "Broken" / "SKILL.md").write_text("---\nname: b\ndescription: d\n---\n")
    result = await env.svc.install(USER, zip_bytes=zip_dir(src))
    assert result.status == "installed"
    assert sorted(m.slug for m in result.plugin.members) == ["good", "ok"]
    skipped = {(s.kind, s.slug): s.reason for s in result.skipped}
    assert ("skill", "Broken") in skipped
    assert ("connector", "bad") in skipped
    assert ("connector", "escape") in skipped and "outside" in skipped[("connector", "escape")]
    assert await env.connector("escape") is None


async def test_same_name_from_another_source_conflicts(env: Env) -> None:
    src = build_agent_plugin(env.tmp_path / "src", name="dup", skills={"alpha": {}})
    await env.svc.install(USER, path=str(src))
    with pytest.raises(PluginConflict):
        await env.svc.install(USER, zip_bytes=zip_dir(src))
    preview = await env.svc.preview(USER, zip_bytes=zip_dir(src))
    assert preview.existing == "other_source"
    assert (await env.svc.preview(USER, path=str(src))).existing == "same_source"


# ---------------------------------------------------------------------------
# One slug = one copy: link / conflict / overwrite / reference counting
# ---------------------------------------------------------------------------


async def test_shared_skill_is_linked_and_survives_until_the_last_reference(env: Env) -> None:
    a = build_agent_plugin(env.tmp_path / "a", name="plug-a", skills={"shared": {}, "only-a": {}})
    b = build_agent_plugin(env.tmp_path / "b", name="plug-b", skills={"shared": {}, "only-b": {}})
    ra = await env.svc.install(USER, path=str(a))
    rb = await env.svc.install(USER, path=str(b))
    assert rb.conflicts == []
    shared_b = next(m for m in rb.plugin.members if m.slug == "shared")
    assert shared_b.installed and not shared_b.content_differs
    _plugins, comps = await env.rows()
    origins = {(c.plugin_id, c.slug): c.origin for c in comps}
    assert origins[(ra.plugin.id, "shared")] == "installed"
    assert origins[(rb.plugin.id, "shared")] == "linked"

    badges = await env.svc.memberships(USER, "skill", ["shared", "only-a", "nope"])
    assert sorted(p.name for p in badges["shared"]) == ["plug-a", "plug-b"]
    assert [p.name for p in badges["only-a"]] == ["plug-a"]
    assert badges["nope"] == []

    # Uninstall A: ``shared`` is still referenced by B → kept; ``only-a`` removed.
    out = await env.svc.uninstall(USER, ra.plugin.id)
    assert [(m.kind, m.slug) for m in out.removed_members] == [("skill", "only-a")]
    assert [(m.slug, m.reason) for m in out.kept_members] == [
        ("shared", "referenced_by_other_plugin")
    ]
    assert (env.skill_root / "shared" / "SKILL.md").is_file()
    assert not (env.skill_root / "only-a").exists()
    assert not fs_registry.plugin_root(USER, "plug-a").exists()
    # Ownership moved to B, so uninstalling B reclaims the skill.
    out = await env.svc.uninstall(USER, rb.plugin.id)
    assert sorted(m.slug for m in out.removed_members) == ["only-b", "shared"]
    assert not (env.skill_root / "shared").exists()
    assert await env.svc.list_plugins(USER) == []
    assert (await env.rows()) == ([], [])


async def test_conflicting_skill_is_skipped_by_default_and_overwritten_on_request(env: Env) -> None:
    # A standalone skill the user already has (different content).
    write_skill(env.skill_root, "alpha", body="# user version\n")
    await env.skills.startup_scan(USER)
    user_hash = hash_directory(env.skill_root / "alpha")
    src = build_agent_plugin(
        env.tmp_path / "src", name="conf", skills={"alpha": {"body": "# plugin version\n"}}
    )
    preview = await env.svc.preview(USER, path=str(src))
    assert [c.slug for c in preview.conflicts] == ["alpha"]
    assert preview.members[0].installed and preview.members[0].content_differs
    assert await env.svc.list_plugins(USER) == []  # preview has no side effects

    result = await env.svc.install(USER, path=str(src))
    assert result.status == "installed"
    assert [(c.kind, c.slug) for c in result.conflicts] == [("skill", "alpha")]
    member = result.plugin.members[0]
    assert member.installed and member.content_differs
    assert hash_directory(env.skill_root / "alpha") == user_hash  # untouched
    _plugins, comps = await env.rows()
    assert comps[0].origin == "linked" and comps[0].content_differs

    # Same source again with overwrite → the library copy is replaced.
    result = await env.svc.install(USER, path=str(src), on_conflict="overwrite")
    assert result.conflicts == []
    assert (env.skill_root / "alpha" / "SKILL.md").read_text().endswith("# plugin version\n")
    assert not result.plugin.members[0].content_differs
    # The skill existed before the plugin → still standalone on uninstall.
    out = await env.svc.uninstall(USER, result.plugin.id)
    assert [(m.slug, m.reason) for m in out.kept_members] == [("alpha", "standalone")]
    assert (env.skill_root / "alpha" / "SKILL.md").is_file()


async def test_conflicting_connector_follows_the_same_policy(env: Env) -> None:
    await env.connectors.create_connector(
        USER,
        slug="deploy-api",
        display_name="mine",
        transport="http",
        url="https://mine.example/mcp",
    )
    src = build_agent_plugin(
        env.tmp_path / "src",
        name="conn",
        skills={},
        servers={
            "deploy-api": {"type": "streamable-http", "url": "https://deploy.example.com/mcp"}
        },
    )
    result = await env.svc.install(USER, path=str(src))
    assert [(c.kind, c.slug) for c in result.conflicts] == [("connector", "deploy-api")]
    assert (await env.connector("deploy-api")).url == "https://mine.example/mcp"
    result = await env.svc.install(USER, path=str(src), on_conflict="overwrite")
    assert result.conflicts == []
    assert (await env.connector("deploy-api")).url == "https://deploy.example.com/mcp"
    out = await env.svc.uninstall(USER, result.plugin.id)
    assert [(m.slug, m.reason) for m in out.kept_members] == [("deploy-api", "standalone")]
    assert await env.connector("deploy-api") is not None


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


async def test_enable_disable_propagates_but_respects_user_disabled_members(env: Env) -> None:
    src = _full_plugin(env.tmp_path / "src", name="toggle")
    installed = await env.svc.install(USER, path=str(src))
    # The user disables one skill on their own.
    await env.skills.set_library_enabled(USER, "user:deploy", False)

    view = await env.svc.set_enabled(USER, installed.plugin.id, False)
    assert view.enabled is False
    for slug in ("summarize", "deploy"):
        row = await env.skill_row(slug)
        assert row is not None and row.library_enabled is False
    assert (await env.connector("local-validator")).enabled is False
    assert (await env.connector("deploy-api")).enabled is False

    view = await env.svc.set_enabled(USER, installed.plugin.id, True)
    assert view.enabled is True
    assert (await env.skill_row("summarize")).library_enabled is True
    assert (await env.skill_row("deploy")).library_enabled is False  # stays as the user left it
    assert (await env.connector("local-validator")).enabled is True


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_from_source_dir_diffs_members(env: Env) -> None:
    src = build_agent_plugin(
        env.tmp_path / "src",
        name="upd",
        skills={
            "keep": {},
            "refresh": {"body": "# v1\n"},
            "gone": {},
            "touched": {"body": "# v1\n"},
        },
    )
    first = await env.svc.install(USER, path=str(src))
    assert first.status == "installed"
    # Plugin changes: ``refresh`` + ``touched`` change, ``gone`` is dropped, ``added`` appears.
    (src / "skills" / "refresh" / "SKILL.md").write_text(
        "---\nname: refresh\ndescription: Does a thing. Use when asked.\n---\n\n# v2\n"
    )
    (src / "skills" / "touched" / "SKILL.md").write_text(
        "---\nname: touched\ndescription: Does a thing. Use when asked.\n---\n\n# v2\n"
    )
    shutil.rmtree(src / "skills" / "gone")
    write_skill(src / "skills", "added")
    (src / "plugin.json").write_text(
        json.dumps({**json.loads((src / "plugin.json").read_text()), "version": "1.1.0"})
    )
    # The user edited THEIR copy of ``touched`` meanwhile.
    (env.skill_root / "touched" / "notes.md").write_text("my notes")

    result = await env.svc.update(USER, first.plugin.id)
    assert result.status == "updated"
    assert result.plugin.version == "1.1.0"
    slugs = sorted(m.slug for m in result.plugin.members)
    assert slugs == ["added", "keep", "refresh", "touched"]
    # Plugin-owned & unmodified → refreshed silently.
    assert (env.skill_root / "refresh" / "SKILL.md").read_text().endswith("# v2\n")
    # User-modified → conflict, kept as-is and flagged.
    assert [(c.kind, c.slug) for c in result.conflicts] == [("skill", "touched")]
    assert (env.skill_root / "touched" / "SKILL.md").read_text().endswith("# v1\n")
    assert next(m for m in result.plugin.members if m.slug == "touched").content_differs
    # Dropped member released (it was plugin-installed and unreferenced → removed).
    assert not (env.skill_root / "gone").exists()
    assert any("gone" in w for w in result.warnings)
    # Added member installed.
    assert (env.skill_root / "added" / "SKILL.md").is_file()

    # Overwrite resolves the conflict.
    result = await env.svc.update(USER, first.plugin.id, on_conflict="overwrite")
    assert result.conflicts == []
    assert (env.skill_root / "touched" / "SKILL.md").read_text().endswith("# v2\n")
    # Nothing changed → already_installed.
    assert (await env.svc.update(USER, first.plugin.id)).status == "already_installed"


async def test_update_needs_a_refetchable_source(env: Env) -> None:
    src = build_agent_plugin(env.tmp_path / "src", name="ziponly", skills={"alpha": {}})
    result = await env.svc.install(USER, zip_bytes=zip_dir(src))
    with pytest.raises(PluginSourceUnavailable):
        await env.svc.update(USER, result.plugin.id)


async def test_update_preserves_plugin_data_dir(env: Env) -> None:
    src = build_agent_plugin(env.tmp_path / "src", name="keepdata", skills={"alpha": {}})
    result = await env.svc.install(USER, path=str(src))
    data_dir = fs_registry.plugins_data_root(USER) / "keepdata"
    (data_dir / "state.json").write_text("{}")
    root = Path(result.plugin.root_path)
    (root / "scratch.txt").write_text("gone on update")
    write_skill(src / "skills", "beta")
    await env.svc.update(USER, result.plugin.id)
    assert (data_dir / "state.json").is_file()
    assert not (root / "scratch.txt").exists() and (root / "skills" / "beta").is_dir()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_export_is_a_straight_zip_of_the_plugin_root(env: Env) -> None:
    src = _full_plugin(env.tmp_path / "src", name="exp")
    result = await env.svc.install(USER, path=str(src))
    # Local edits to the LIBRARY copy do not leak into the export — the package
    # is what was installed (PLUGIN_ROOT).
    (env.skill_root / "deploy" / "extra.md").write_text("local edit")
    filename, data = await env.svc.export_zip(USER, result.plugin.id)
    assert filename == "exp-1.0.0.zip"
    root = Path(result.plugin.root_path)
    expected = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == expected
        names = set(zf.namelist())
        assert {"plugin.json", "mcp.json", "skills/deploy/SKILL.md"} <= names
        assert "skills/summarize/scripts/run.sh" in names
        assert "skills/deploy/extra.md" not in names
        mcp = json.loads(zf.read("mcp.json"))
        assert mcp["$schema"] == MCP_SCHEMA_ID
        assert set(mcp["mcpServers"]) == {"local-validator", "deploy-api"}
        # Portable form: placeholders are NOT expanded in the export.
        assert mcp["mcpServers"]["local-validator"]["args"] == [
            "--data",
            "${PLUGIN_DATA}/validator",
        ]
    out = env.tmp_path / "re"
    out.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(out)
    reloaded = load_plugin_dir(out)
    assert reloaded.manifest.name == "exp" and reloaded.composition == "with_connectors"


async def test_frontmatter_names_are_corrected_in_root_and_library(env: Env) -> None:
    src = build_agent_plugin(
        env.tmp_path / "src", name="names", skills={"my-skill": {"name": "My Skill"}, "ok": {}}
    )
    result = await env.svc.install(USER, path=str(src))
    assert result.status == "installed"
    assert any("'My Skill' rewritten to 'my-skill'" in w for w in result.warnings)
    member = next(m for m in result.plugin.members if m.slug == "my-skill")
    assert member.name == "my-skill" and member.installed and not member.content_differs
    root = Path(result.plugin.root_path)
    root_copy = (root / "skills" / "my-skill" / "SKILL.md").read_text()
    lib_copy = (env.skill_root / "my-skill" / "SKILL.md").read_text()
    assert "name: my-skill" in root_copy and root_copy == lib_copy
    assert "name: My Skill" in (src / "skills" / "my-skill" / "SKILL.md").read_text()  # source kept
    row = await env.skill_row("my-skill")
    assert row is not None and row.name == "my-skill"
    # Stable: re-installing sees identical content (no spurious conflict / update).
    again = await env.svc.install(USER, path=str(src))
    assert again.status == "already_installed" and again.conflicts == []
    # Export carries the corrected name.
    _name, data = await env.svc.export_zip(USER, result.plugin.id)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert b"name: my-skill" in zf.read("skills/my-skill/SKILL.md")


# ---------------------------------------------------------------------------
# Legacy layouts
# ---------------------------------------------------------------------------


async def test_install_legacy_codebuddy_plugin(env: Env) -> None:
    src = build_legacy_plugin(
        env.tmp_path / "wb",
        fmt="codebuddy_plugin",
        name="agent-browser",
        root_skill=True,
        mcp_json={
            "browser": {
                "command": "${CODEBUDDY_PLUGIN_ROOT}/bin/run",
                "args": ["${CODEBUDDY_PLUGIN_ROOT}/dist/index.js"],
                "env": {"MODE": "x"},
                "defer_loading": True,
            }
        },
    )
    (src / "bin").mkdir()
    (src / "bin" / "run").write_text("#!/bin/sh\n")
    result = await env.svc.install(USER, zip_bytes=zip_dir(src, wrap="agent-browser"))
    view = result.plugin
    assert view.source == "codebuddy_plugin" and view.composition == "with_connectors"
    assert [m.slug for m in view.members if m.kind == "skill"] == ["agent-browser"]
    # PLUGIN_ROOT holds the NORMALIZED Agent Plugins layout, not the raw legacy tree.
    root = Path(view.root_path)
    on_disk = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert on_disk == [
        # Root files stay because the MCP server resolves ./bin/run against PLUGIN_ROOT.
        "bin/run",
        "io.valuz.agent/legacy/.codebuddy-plugin/plugin.json",
        "io.valuz.agent/legacy/.mcp.json",
        "mcp.json",
        "plugin.json",
        "scripts/run.sh",
        "skills/agent-browser/SKILL.md",
        "skills/agent-browser/bin/run",
        "skills/agent-browser/scripts/run.sh",
    ]
    manifest = json.loads((root / "plugin.json").read_text())
    assert manifest["name"] == "agent-browser"
    assert manifest["extensions"]["io.valuz.agent"]["legacy_format"] == "codebuddy_plugin"
    assert load_plugin_dir(root).format == "agent_plugins"
    (plugins, _comps) = await env.rows()
    assert plugins[0].format == "codebuddy_plugin"  # provenance keeps the original layout
    # Root skill: the library copy is the skill's own files minus the format dirs.
    lib = env.skill_root / "agent-browser"
    assert (lib / "SKILL.md").is_file() and (lib / "scripts" / "run.sh").is_file()
    assert not (lib / ".codebuddy-plugin").exists()
    conn = await env.connector("browser")
    assert conn is not None and conn.command == str(root / "bin" / "run")
    assert conn.args == [f"{root}/dist/index.js"]
    assert any("defer_loading" in w for w in result.warnings)
    # Export = straight zip of the (normalized) root.
    _name, data = await env.svc.export_zip(USER, view.id)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == on_disk
        manifest = json.loads(zf.read("plugin.json"))
        assert manifest["extensions"]["io.valuz.agent"]["legacy_format"] == "codebuddy_plugin"


async def test_install_legacy_skills_only_root_skill_has_no_root_leftovers(env: Env) -> None:
    src = build_legacy_plugin(
        env.tmp_path / "docx", fmt="claude_plugin", name="docx", root_skill=True
    )
    (src / "README.md").write_text("readme")
    result = await env.svc.install(USER, path=str(src))
    assert result.plugin.source == "claude_plugin"
    assert result.plugin.composition == "skills_only"
    root = Path(result.plugin.root_path)
    on_disk = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert on_disk == [
        "io.valuz.agent/legacy/.claude-plugin/plugin.json",
        "plugin.json",
        "skills/docx/README.md",
        "skills/docx/SKILL.md",
        "skills/docx/scripts/run.sh",
    ]
    assert (env.skill_root / "docx" / "README.md").read_text() == "readme"


# ---------------------------------------------------------------------------
# Marketplace source
# ---------------------------------------------------------------------------


async def test_install_from_market_downloads_via_install_manifest(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = build_agent_plugin(env.tmp_path / "src", name="market-plugin", skills={"alpha": {}})
    payload = zip_dir(src)
    env.market.details["market:plugin:market-plugin"] = {
        "type": "plugin",
        "version": "3.2.1",
        "install_manifest": {"download_url": "https://cdn.example.com/p.zip"},
    }
    downloaded: list[str] = []

    async def _fake_download(self: PluginService, url: str) -> bytes:
        downloaded.append(url)
        return payload

    monkeypatch.setattr(PluginService, "_download", _fake_download)
    result = await env.svc.install(USER, market_item_id="market:plugin:market-plugin")
    assert result.status == "installed"
    assert result.plugin.source == "market"
    assert result.plugin.source_ref == "market:plugin:market-plugin"
    assert downloaded == ["https://cdn.example.com/p.zip"]
    rows = await MarketplaceInstallStore(env.session)._db.execute(  # noqa: SLF001
        __import__("sqlalchemy").select(MarketplaceInstallRow)
    )
    row = rows.scalars().one()
    assert row.item_type == "plugin" and row.installed_ref == "market-plugin"
    assert row.version == "3.2.1" and row.source_channel == "oss"
    # ``update`` re-fetches through the same item id.
    assert (await env.svc.update(USER, result.plugin.id)).status == "already_installed"
    assert env.market.calls == ["market:plugin:market-plugin"] * 2


async def test_market_failures_surface_as_fetch_errors(env: Env) -> None:
    with pytest.raises(PluginFetchFailed):
        await env.svc.install(USER, market_item_id="market:plugin:missing")
    env.market.details["market:plugin:nourl"] = {"type": "plugin", "install_manifest": {}}
    with pytest.raises(PluginFetchFailed):
        await env.svc.install(USER, market_item_id="market:plugin:nourl")
    env.market.details["market:skill:notaplugin"] = {
        "type": "skill",
        "install_manifest": {"download_url": "x"},
    }
    with pytest.raises(PluginInvalid):
        await env.svc.install(USER, market_item_id="market:skill:notaplugin")


async def test_uninstall_unknown_plugin(env: Env) -> None:
    with pytest.raises(PluginNotFound):
        await env.svc.uninstall(USER, "missing")


async def test_owner_isolation(env: Env) -> None:
    src = build_agent_plugin(env.tmp_path / "src", name="mine", skills={"alpha": {}})
    result = await env.svc.install(USER, path=str(src))
    assert await env.svc.list_plugins("someone-else") == []
    with pytest.raises(PluginNotFound):
        await env.svc.get_plugin("someone-else", result.plugin.id)
    assert (await env.svc.memberships("someone-else", "skill", ["alpha"]))["alpha"] == []
    _ = SimpleNamespace  # keep the import used for future stubs


async def test_plugin_view_reports_protected_when_a_member_skill_is(env: Env) -> None:
    """A plugin is exactly as protected as the strictest thing inside it.

    The client needs this to stop offering ``export`` (which the service refuses
    with 403 for these) and to badge the plugin. It must be DERIVED per request:
    a member that becomes protected later must not leave a stale ``False`` on the
    parent, which is why it is not stored on the plugin row.
    """
    src = build_agent_plugin(env.tmp_path / "src", name="guarded", skills={"alpha": {}, "beta": {}})
    result = await env.svc.install(USER, path=str(src))
    assert result.plugin.protected is False

    rows = await env.skill_ds.list_skills(USER)
    target = next(r for r in rows if r.slug == "alpha")
    target.protected = True
    await env.skill_ds.update(target)

    fetched = await env.svc.get_plugin(USER, result.plugin.id)
    assert fetched.protected is True, "one protected member must protect the plugin"
    # The plugin itself stays fully visible — only actions change.
    assert fetched.name == "guarded"
    assert sorted(m.slug for m in fetched.members) == ["alpha", "beta"]

    listed = await env.svc.list_plugins(USER)
    assert [p.protected for p in listed if p.id == result.plugin.id] == [True]


async def test_plugin_view_protected_matches_what_export_refuses(env: Env) -> None:
    """The badge and the 403 must agree — otherwise the UI hides an action that
    would have worked, or offers one that cannot."""
    from valuz_agent.modules.skills.errors import SkillProtected

    src = build_agent_plugin(env.tmp_path / "src", name="guarded2", skills={"alpha": {}})
    result = await env.svc.install(USER, path=str(src))
    await env.svc.export_zip(USER, result.plugin.id)  # not protected yet → fine

    rows = await env.skill_ds.list_skills(USER)
    target = next(r for r in rows if r.slug == "alpha")
    target.protected = True
    await env.skill_ds.update(target)

    assert (await env.svc.get_plugin(USER, result.plugin.id)).protected is True
    with pytest.raises(SkillProtected):
        await env.svc.export_zip(USER, result.plugin.id)
