"""Regression: onboarding must deploy a team on the runtime the user chose.

The ConnectStep persists ``(default_runtime, default_provider_id,
default_model)`` together, but ``_resolve_deploy_target`` used to return only
``(provider_id, model)`` — so every onboarding-deployed agent silently fell
back to the hard-coded ``claude_agent`` ("Claude Code") regardless of the
runtime the user picked. These tests pin the runtime into the resolved triple.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.onboarding import _resolve_deploy_target
from valuz_agent.infra.database import Base
from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.modules.settings.preferences import (
    set_default_model,
    set_default_provider_id,
    set_default_runtime,
)


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    db_file = tmp_path / "onboarding_target.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AppSettingRow.__table__, ProviderRow.__table__],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def test_tier1_returns_user_chosen_runtime(db) -> None:
    """Explicit defaults: the stored runtime rides along, not claude_agent."""
    await set_default_runtime(db, "codex")
    await set_default_provider_id(db, "prov-openai")
    await set_default_model(db, "gpt-5-codex")

    runtime, provider_id, model = await _resolve_deploy_target(db)

    assert runtime == "codex"
    assert provider_id == "prov-openai"
    assert model == "gpt-5-codex"


async def test_tier2_derives_runtime_from_fallback_provider(db) -> None:
    """No explicit defaults: runtime is derived from the enabled provider's
    kind, so an OpenAI-shape channel lands on deepagents — never the old
    hard-coded claude_agent."""
    db.add(
        ProviderRow(
            user_id="local-test-owner",
            id="prov-1",
            name="My OpenAI",
            provider_kind="openai",
            source="user",
            credential_source="secret_ref",
            default_model="gpt-4o",
            enabled=True,
        )
    )
    await db.commit()

    runtime, provider_id, model = await _resolve_deploy_target(db)

    assert runtime == "deepagents"
    assert provider_id == "prov-1"
    assert model == "gpt-4o"


async def test_tier2_anthropic_fallback_uses_claude_agent(db) -> None:
    """An Anthropic fallback provider correctly resolves to claude_agent —
    the derivation is provider-driven, not a blanket default."""
    db.add(
        ProviderRow(
            user_id="local-test-owner",
            id="prov-2",
            name="My Claude",
            provider_kind="anthropic",
            source="user",
            credential_source="secret_ref",
            default_model="claude-sonnet-4-6",
            enabled=True,
        )
    )
    await db.commit()

    runtime, provider_id, model = await _resolve_deploy_target(db)

    assert runtime == "claude_agent"
    assert provider_id == "prov-2"
    assert model == "claude-sonnet-4-6"


async def test_no_provider_raises_422(db) -> None:
    """No defaults and no enabled provider → the authoritative 422 guard."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _resolve_deploy_target(db)
    assert exc.value.status_code == 422
