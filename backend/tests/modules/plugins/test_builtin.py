"""Builtin (app-managed) plugins — install semantics, official-root member
landing, the not-deletable guard, and D6 (a disable survives a re-sync)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.modules.plugins.helpers import build_agent_plugin, write_skill
from tests.modules.plugins.test_service import USER, Env
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.marketplace.install_store import MarketplaceInstallRow
from valuz_agent.modules.plugins.errors import PluginConflict, PluginNotDeletable
from valuz_agent.modules.plugins.models import PluginComponentRow, PluginRow
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.skills.models import ProjectSkillConfigRow, SkillIndexRow


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "user_skills_dir", tmp_path / "skills")
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
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield Env(tmp_path, session)
    await engine.dispose()


def _office_like(tmp_path: Path) -> Path:
    root = build_agent_plugin(tmp_path / "office-src", name="office")
    write_skill(root / "skills", "docx", body="# docx skill")
    write_skill(root / "skills", "xlsx", body="# xlsx skill")
    return root


async def test_builtin_install_lands_members_in_official_root(
    env: Env, tmp_path: Path
) -> None:
    root = _office_like(tmp_path)
    result = await env.svc.install(USER, path=str(root), builtin=True)
    assert result.plugin.source == "builtin"
    assert result.plugin.deletable is False
    official = fs_registry.official_skill_root(user_id=USER)
    assert (official / "docx" / "SKILL.md").is_file()
    assert (official / "docx" / ".bundled-version").is_file()
    # NOT in the user library — the official root is the landing zone (D3).
    assert not (env.skill_root / "docx").exists()
    # The view reports the official copies as installed.
    assert all(m.installed for m in result.plugin.members if m.kind == "skill")


async def test_builtin_resync_is_idempotent_byte_for_byte(env: Env, tmp_path: Path) -> None:
    root = _office_like(tmp_path)
    await env.svc.install(USER, path=str(root), builtin=True)
    official = fs_registry.official_skill_root(user_id=USER)
    marker = official / "docx" / ".bundled-version"
    before = (marker.read_text(), marker.stat().st_mtime_ns)
    result = await env.svc.install(USER, path=str(root), builtin=True)
    assert result.status == "already_installed"
    assert (marker.read_text(), marker.stat().st_mtime_ns) == before


async def test_builtin_uninstall_returns_conflict(env: Env, tmp_path: Path) -> None:
    root = _office_like(tmp_path)
    result = await env.svc.install(USER, path=str(root), builtin=True)
    with pytest.raises(PluginNotDeletable):
        await env.svc.uninstall(USER, result.plugin.id)


async def test_builtin_resync_preserves_user_disable(env: Env, tmp_path: Path) -> None:
    root = _office_like(tmp_path)
    result = await env.svc.install(USER, path=str(root), builtin=True)
    await env.svc.set_enabled(USER, result.plugin.id, False)
    resynced = await env.svc.install(USER, path=str(root), builtin=True)
    assert resynced.plugin.enabled is False  # D6 — the sync never re-enables


async def test_builtin_never_clobbers_a_user_install(env: Env, tmp_path: Path) -> None:
    root = _office_like(tmp_path)
    await env.svc.install(USER, path=str(root))  # ordinary local_dir install
    with pytest.raises(PluginConflict):
        await env.svc.install(USER, path=str(root), builtin=True)
