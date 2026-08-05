"""Factory model defaults: the ``ext.model_defaults`` port and its consumers.

Covers the three-layer contract: an explicit user KV always wins; unset KV
falls through to whatever port implementation is bound (OSS: Settings-backed,
env-overridable); a port returning out-of-enum garbage is clamped to the
module constants so a bad overlay can't 500 the settings page.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes import settings as settings_routes
from valuz_agent.infra.database import Base
from valuz_agent.modules.settings import preferences
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.model_defaults import ModelDefaults, SettingsModelDefaults

_OWNER = "local-test-owner"


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prefs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[AppSettingRow.__table__])
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


class _StubDefaults:
    def __init__(self, **overrides) -> None:
        self.value = ModelDefaults(
            default_runtime=overrides.get("default_runtime", "deepagents"),
            default_model=overrides.get("default_model", "stub-model"),
            default_provider_id=overrides.get("default_provider_id"),
            default_effort=overrides.get("default_effort", "medium"),
        )
        self.seen_user_ids: list[str | None] = []

    async def get(self, user_id: str | None = None) -> ModelDefaults:
        self.seen_user_ids.append(user_id)
        return self.value


@pytest.fixture
def stub_port():
    stub = _StubDefaults()
    previous = ext.model_defaults
    ext.model_defaults = stub
    try:
        yield stub
    finally:
        ext.model_defaults = previous


async def test_settings_port_should_read_env_overridable_settings(monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "default_runtime", "codex")
    monkeypatch.setattr(settings, "default_model", "gpt-5")
    monkeypatch.setattr(settings, "default_provider_id", "prov-x")
    monkeypatch.setattr(settings, "default_effort", "low")

    resolved = await SettingsModelDefaults().get()
    assert resolved == ModelDefaults(
        default_runtime="codex",
        default_model="gpt-5",
        default_provider_id="prov-x",
        default_effort="low",
    )


async def test_unset_runtime_should_fall_back_to_port_value(db, stub_port) -> None:
    assert await preferences.get_default_runtime(db, user_id=_OWNER) == "deepagents"
    assert stub_port.seen_user_ids == [_OWNER]


async def test_stored_runtime_should_win_over_port_value(db, stub_port) -> None:
    await preferences.set_default_runtime(db, "claude_agent", user_id=_OWNER)
    assert await preferences.get_default_runtime(db, user_id=_OWNER) == "claude_agent"


async def test_unset_effort_should_fall_back_to_port_value(db, stub_port) -> None:
    assert await preferences.get_default_effort(db, user_id=_OWNER) == "medium"


async def test_unset_model_and_provider_should_fall_back_to_port_values(
    db, stub_port
) -> None:
    stub_port.value = ModelDefaults(
        default_runtime="deepagents",
        default_model="stub-model",
        default_provider_id="prov-stub",
        default_effort="medium",
    )
    assert await preferences.get_default_model(db, user_id=_OWNER) == "stub-model"
    assert await preferences.get_default_provider_id(db, user_id=_OWNER) == "prov-stub"


async def test_out_of_enum_port_values_should_clamp_to_constants(db, stub_port) -> None:
    stub_port.value = ModelDefaults(
        default_runtime="gpt_agent",
        default_model="stub-model",
        default_provider_id=None,
        default_effort="ultra",
    )
    assert (
        await preferences.get_default_runtime(db, user_id=_OWNER)
        == preferences.FALLBACK_RUNTIME
    )
    assert (
        await preferences.get_default_effort(db, user_id=_OWNER)
        == preferences.FALLBACK_EFFORT
    )


async def test_conversation_trust_and_coverage_defaults(db) -> None:
    assert await preferences.get_conversation_citations_enabled(db, user_id=_OWNER) is True
    assert await preferences.get_conversation_verification_enabled(db, user_id=_OWNER) is False
    assert (
        await preferences.get_conversation_task_coverage_enabled(db, user_id=_OWNER)
        is True
    )


async def test_conversation_trust_preferences_round_trip(db) -> None:
    await preferences.set_conversation_citations_enabled(db, False, user_id=_OWNER)
    await preferences.set_conversation_verification_enabled(db, True, user_id=_OWNER)
    await preferences.set_conversation_task_coverage_enabled(db, False, user_id=_OWNER)

    assert await preferences.get_conversation_citations_enabled(db, user_id=_OWNER) is False
    assert await preferences.get_conversation_verification_enabled(db, user_id=_OWNER) is True
    assert (
        await preferences.get_conversation_task_coverage_enabled(db, user_id=_OWNER)
        is False
    )


async def test_preferences_route_disabling_citations_preserves_verification(
    db,
    monkeypatch,
) -> None:
    await preferences.set_conversation_verification_enabled(db, True, user_id=_OWNER)

    @asynccontextmanager
    async def unit_of_work(*, commit=True):
        del commit
        yield db

    monkeypatch.setattr(settings_routes, "async_unit_of_work", unit_of_work)

    result = await settings_routes.patch_preferences(
        settings_routes.PreferencesPatchPayload(
            conversation_citations_enabled=False,
        ),
        user_id=_OWNER,
    )

    assert result.conversation_citations_enabled is False
    assert result.conversation_verification_enabled is True
    assert result.conversation_task_coverage_enabled is True


async def test_preferences_route_enabling_verification_preserves_citations_setting(
    db,
    monkeypatch,
) -> None:
    await preferences.set_conversation_citations_enabled(db, False, user_id=_OWNER)

    @asynccontextmanager
    async def unit_of_work(*, commit=True):
        del commit
        yield db

    monkeypatch.setattr(settings_routes, "async_unit_of_work", unit_of_work)

    result = await settings_routes.patch_preferences(
        settings_routes.PreferencesPatchPayload(
            conversation_verification_enabled=True,
        ),
        user_id=_OWNER,
    )

    assert result.conversation_citations_enabled is False
    assert result.conversation_verification_enabled is True
    assert result.conversation_task_coverage_enabled is True


async def test_preferences_route_accepts_audit_only_combination(
    db,
    monkeypatch,
) -> None:
    @asynccontextmanager
    async def unit_of_work(*, commit=True):
        del commit
        yield db

    monkeypatch.setattr(settings_routes, "async_unit_of_work", unit_of_work)

    result = await settings_routes.patch_preferences(
        settings_routes.PreferencesPatchPayload(
            conversation_citations_enabled=False,
            conversation_verification_enabled=True,
        ),
        user_id=_OWNER,
    )

    assert result.conversation_citations_enabled is False
    assert result.conversation_verification_enabled is True
    assert result.conversation_task_coverage_enabled is True


async def test_preferences_route_toggles_task_coverage_independently(
    db,
    monkeypatch,
) -> None:
    @asynccontextmanager
    async def unit_of_work(*, commit=True):
        del commit
        yield db

    monkeypatch.setattr(settings_routes, "async_unit_of_work", unit_of_work)

    result = await settings_routes.patch_preferences(
        settings_routes.PreferencesPatchPayload(
            conversation_task_coverage_enabled=False,
        ),
        user_id=_OWNER,
    )

    assert result.conversation_citations_enabled is True
    assert result.conversation_verification_enabled is False
    assert result.conversation_task_coverage_enabled is False
