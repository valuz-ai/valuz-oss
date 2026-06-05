"""Settings helpers for parser routing (PR-2)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.modules.settings.parser_routing import (
    DEFAULT_FALLBACK_ON_ERROR,
    DEFAULT_PRIMARY_PLUGIN_ID,
    LOCKED_LOCAL_KINDS,
    get_by_kind,
    get_fallback_to_local_on_error,
    get_plugin_config,
    get_primary_plugin_id,
    set_by_kind,
    set_fallback_to_local_on_error,
    set_primary_plugin_id,
    update_plugin_config,
)


@pytest_asyncio.fixture
async def db():
    # ``parser`` module models must be registered before create_all.
    import valuz_agent.modules.parser  # noqa: F401
    import valuz_agent.modules.settings.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestPrimaryPlugin:
    async def test_returns_default_when_unset(self, db):
        assert await get_primary_plugin_id(db) == DEFAULT_PRIMARY_PLUGIN_ID

    async def test_round_trips_after_set(self, db):
        await set_primary_plugin_id(db, "mineru")
        assert await get_primary_plugin_id(db) == "mineru"

    async def test_empty_value_rejected(self, db):
        with pytest.raises(ValueError):
            await set_primary_plugin_id(db, "")


class TestByKind:
    async def test_returns_empty_when_unset(self, db):
        assert await get_by_kind(db) == {}

    async def test_round_trips_mapping(self, db):
        await set_by_kind(db, {"pdf": "mineru", "image": "paddleocr"})
        assert await get_by_kind(db) == {"pdf": "mineru", "image": "paddleocr"}

    async def test_strips_locked_kinds(self, db):
        # ``text`` is locked-local; the helper must scrub it on write so
        # round-tripping preserves the invariant the router enforces.
        await set_by_kind(db, {"text": "mineru", "pdf": "mineru"})
        assert "text" not in await get_by_kind(db)
        assert await get_by_kind(db) == {"pdf": "mineru"}

    async def test_drops_non_string_values(self, db):
        await set_by_kind(db, {"pdf": "mineru", "image": ""})
        assert await get_by_kind(db) == {"pdf": "mineru"}


class TestFallbackFlag:
    async def test_default_is_true(self, db):
        assert await get_fallback_to_local_on_error(db) is DEFAULT_FALLBACK_ON_ERROR is True

    async def test_round_trips_false(self, db):
        await set_fallback_to_local_on_error(db, False)
        assert await get_fallback_to_local_on_error(db) is False


class TestPluginConfigs:
    async def test_default_config_for_unknown_plugin_id(self, db):
        cfg = await get_plugin_config(db, "paddleocr")
        assert cfg == {"enabled": False, "secret_ref": None, "options": {}}

    async def test_update_sets_enabled_only(self, db):
        cfg = await update_plugin_config(db, "paddleocr", enabled=True)
        assert cfg["enabled"] is True
        assert cfg["secret_ref"] is None

    async def test_secret_ref_change_set_and_clear(self, db):
        await update_plugin_config(db, "mineru", secret_ref_change=("ref-1",))
        assert (await get_plugin_config(db, "mineru"))["secret_ref"] == "ref-1"
        await update_plugin_config(db, "mineru", secret_ref_change=(None,))
        assert (await get_plugin_config(db, "mineru"))["secret_ref"] is None

    async def test_options_replace_whole_dict(self, db):
        await update_plugin_config(db, "mineru", options={"a": 1, "b": 2})
        await update_plugin_config(db, "mineru", options={"c": 3})
        assert (await get_plugin_config(db, "mineru"))["options"] == {"c": 3}

    def test_text_is_locked_kind(self):
        # Defensive assert on the global so future kind additions don't
        # accidentally unlock text without flagging this test.
        assert "text" in LOCKED_LOCAL_KINDS
