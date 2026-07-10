"""Library-switch defaults for scanned vs Valuz-originated skills.

A user-scope row inserted by the filesystem scan was merely FOUND on disk
(~/.claude/skills, ~/.codex/skills, or a folder in the shared library root) —
it must insert with ``library_enabled = False`` so it doesn't auto-ride into
every chat session's prompt. Official / project-scope rows keep the default
ON. Creating or importing a skill through Valuz is an explicit opt-in: the
``set_creation_origin*`` stamp flips the switch ON. The upsert (update)
branch never rewrites a stored value, so an explicit user toggle survives
rescans in both directions.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.skills.contracts import SkillManifest
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillIndexRow
from valuz_agent.modules.skills.service import _upsert_skill_row

USER = "u1"


def _manifest(slug: str, *, source: str, scope: str = "user") -> SkillManifest:
    return SkillManifest(
        id=f"{scope}:{slug}",
        name=slug,
        description="test",
        scope=scope,
        source=source,
        path=f"/tmp/{slug}",
        slug=slug,
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


@pytest.mark.parametrize("source", ["claude", "codex", "valuz"])
async def test_scanned_user_rows_default_off(session_ds, source: str) -> None:  # type: ignore[no-untyped-def]
    _session, ds = session_ds
    await _upsert_skill_row(USER, ds, _manifest("scanned", source=source))

    row = await ds.get_by_slug(USER, "scanned")
    assert row is not None
    assert row.library_enabled is False


@pytest.mark.parametrize(
    ("source", "scope"), [("official", "official"), ("project", "project")]
)
async def test_non_user_scope_rows_default_on(session_ds, source: str, scope: str) -> None:  # type: ignore[no-untyped-def]
    _session, ds = session_ds
    await _upsert_skill_row(USER, ds, _manifest("native", source=source, scope=scope))

    row = await ds.get_by_slug(USER, "native")
    assert row is not None
    assert row.library_enabled is True


@pytest.mark.parametrize("origin", ["created", "imported"])
async def test_creation_origin_stamp_enables_library_switch(session_ds, origin: str) -> None:  # type: ignore[no-untyped-def]
    """Create / import flows scan first (row lands OFF), then stamp the
    origin — the stamp is the explicit opt-in that turns the switch ON."""
    _session, ds = session_ds
    await _upsert_skill_row(USER, ds, _manifest("mine", source="valuz"))
    row = await ds.get_by_slug(USER, "mine")
    assert row is not None
    assert row.library_enabled is False

    await ds.set_creation_origin_by_slug(USER, "mine", origin)
    row = await ds.get_by_slug(USER, "mine")
    assert row is not None
    assert row.creation_origin == origin
    assert row.library_enabled is True


async def test_rescan_preserves_explicit_toggle(session_ds) -> None:  # type: ignore[no-untyped-def]
    """The upsert (update) branch never rewrites ``library_enabled`` — a user
    re-enabling a scanned skill keeps it on across rescans, and disabling a
    created skill keeps it off."""
    _session, ds = session_ds

    await _upsert_skill_row(USER, ds, _manifest("scanned", source="claude"))
    await ds.set_library_enabled_by_slug(USER, "scanned", True)
    await _upsert_skill_row(USER, ds, _manifest("scanned", source="claude"))
    row = await ds.get_by_slug(USER, "scanned")
    assert row is not None
    assert row.library_enabled is True

    await _upsert_skill_row(USER, ds, _manifest("mine", source="valuz"))
    await ds.set_creation_origin_by_slug(USER, "mine", "created")
    await ds.set_library_enabled_by_slug(USER, "mine", False)
    await _upsert_skill_row(USER, ds, _manifest("mine", source="valuz"))
    row = await ds.get_by_slug(USER, "mine")
    assert row is not None
    assert row.library_enabled is False
