"""SkillDatastore global-library-switch round-trip against real SQLite.

Exercises the ``valuz_skill_index.library_enabled`` column via the datastore:
fresh rows default off, enabling clears them from the disabled set, disabling is
idempotent, and an unknown id is a no-op. Per-user isolation is checked too.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillIndexRow


def _row(skill_id: str, user_id: str, slug: str) -> SkillIndexRow:
    return SkillIndexRow(
        id=skill_id,
        slug=slug,
        name=slug,
        description="",
        scope="user",
        source="filesystem",
        source_path=f"/tmp/{slug}",
        user_id=user_id,
    )


@pytest.fixture
async def session_ds(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'idx.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[SkillIndexRow.__table__])
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session, SkillDatastore(session)
    finally:
        await session.close()
        await engine.dispose()


async def _seed(session, *rows: SkillIndexRow) -> None:  # type: ignore[no-untyped-def]
    session.add_all(rows)
    await session.commit()


async def test_default_disabled_and_toggle(session_ds) -> None:  # type: ignore[no-untyped-def]
    session, ds = session_ds
    await _seed(session, _row("user:alpha", "u1", "alpha"))

    # Fresh row defaults disabled.
    assert await ds.list_library_disabled_ids("u1") == {"user:alpha"}

    # Idempotent.
    await ds.set_library_enabled("u1", "user:alpha", False)
    assert await ds.list_library_disabled_ids("u1") == {"user:alpha"}

    # Re-enable clears it.
    await ds.set_library_enabled("u1", "user:alpha", True)
    assert await ds.list_library_disabled_ids("u1") == set()


async def test_unknown_id_is_noop(session_ds) -> None:  # type: ignore[no-untyped-def]
    _session, ds = session_ds
    await ds.set_library_enabled("u1", "user:ghost", False)  # no such row
    assert await ds.list_library_disabled_ids("u1") == set()


async def test_per_user_isolation(session_ds) -> None:  # type: ignore[no-untyped-def]
    session, ds = session_ds
    await _seed(
        session,
        _row("user:alpha", "u1", "alpha"),
        _row("user:beta", "u2", "beta"),
    )
    await ds.set_library_enabled("u1", "user:alpha", False)
    await ds.set_library_enabled("u2", "user:beta", False)
    assert await ds.list_library_disabled_ids("u1") == {"user:alpha"}
    assert await ds.list_library_disabled_ids("u2") == {"user:beta"}


def test_rootless_project_has_no_configured_skills(session_ds) -> None:
    from types import SimpleNamespace

    _session, ds = session_ds
    project = SimpleNamespace(id="rootless", kind="project", root_path=None)
    assert ds.enabled_skill_paths(project) == set()
    assert ds.set_skill_enabled(project, "/some/skill", True) == set()
    assert ds.overwrite_enabled_skill_paths(project, ["relative-skill"]) == set()


def test_project_skill_config_rejects_non_object_json(session_ds, tmp_path) -> None:
    from types import SimpleNamespace

    _session, ds = session_ds
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "project-config.json").write_text("[]", encoding="utf-8")
    project = SimpleNamespace(id="project", kind="project", root_path=str(tmp_path))
    with pytest.raises(ValueError, match="must be an object"):
        ds.set_mcp_servers(project, ["server"])
