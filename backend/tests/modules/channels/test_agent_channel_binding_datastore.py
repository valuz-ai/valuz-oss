from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.channels.datastore import AgentChannelBindingDatastore
from valuz_agent.modules.channels.models import AgentChannelBindingRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "channels.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[AgentChannelBindingRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def test_upsert_wecom_aibot_binding_is_unique_per_agent(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = AgentChannelBindingDatastore(db)
        first = await ds.upsert(
            user_id="u1",
            platform="wecom_aibot",
            agent_slug="developer",
            channel_instance_id="wecom-aibot-main",
            bot_id="bot-1",
            secret_ref="channel/wecom-aibot/developer",
            enabled=True,
        )
        second = await ds.upsert(
            user_id="u1",
            platform="wecom_aibot",
            agent_slug="developer",
            channel_instance_id="wecom-aibot-main",
            bot_id="bot-2",
            secret_ref="channel/wecom-aibot/developer",
            enabled=False,
        )

        rows = (await db.execute(select(AgentChannelBindingRow))).scalars().all()

    assert second.id == first.id
    assert len(rows) == 1
    assert rows[0].agent_slug == "developer"
    assert rows[0].bot_id == "bot-2"
    assert rows[0].enabled is False


async def test_list_enabled_wecom_aibot_bindings_is_owner_scoped(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = AgentChannelBindingDatastore(db)
        await ds.upsert(
            user_id="u1",
            platform="wecom_aibot",
            agent_slug="developer",
            channel_instance_id="wecom-aibot-main",
            bot_id="bot-1",
            secret_ref="channel/wecom-aibot/developer",
            enabled=True,
        )
        await ds.upsert(
            user_id="u1",
            platform="wecom_aibot",
            agent_slug="reviewer",
            channel_instance_id="wecom-aibot-main",
            bot_id="bot-2",
            secret_ref="channel/wecom-aibot/reviewer",
            enabled=False,
        )
        await ds.upsert(
            user_id="u2",
            platform="wecom_aibot",
            agent_slug="developer",
            channel_instance_id="wecom-aibot-main",
            bot_id="bot-3",
            secret_ref="channel/wecom-aibot/developer",
            enabled=True,
        )

        rows = await ds.list_enabled(user_id="u1", platform="wecom_aibot")

    assert [row.agent_slug for row in rows] == ["developer"]
    assert rows[0].bot_id == "bot-1"


async def test_list_enabled_without_owner_crosses_users(sessionmaker_) -> None:
    """The long-connection supervisors load bindings ownerless — each row
    carries its own owner, so bindings created under an edition-specific
    user id (not the device-fingerprint local id) still connect."""
    async with sessionmaker_() as db:
        ds = AgentChannelBindingDatastore(db)
        await ds.upsert(
            user_id="commercial-user-1",
            platform="feishu",
            agent_slug="valuz-helper",
            channel_instance_id="feishu-main",
            bot_id="cli_app_1",
            secret_ref="channel/feishu/valuz-helper",
            enabled=True,
        )
        await ds.upsert(
            user_id="local-fingerprint",
            platform="feishu",
            agent_slug="developer",
            channel_instance_id="feishu-main",
            bot_id="cli_app_2",
            secret_ref="channel/feishu/developer",
            enabled=False,
        )

        rows = await ds.list_enabled(platform="feishu")

    assert [(row.owner_user_id, row.agent_slug) for row in rows] == [
        ("commercial-user-1", "valuz-helper")
    ]


async def test_get_enabled_binding_by_channel_instance_returns_owner(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = AgentChannelBindingDatastore(db)
        await ds.upsert(
            user_id="u1",
            platform="feishu",
            agent_slug="developer",
            channel_instance_id="feishu-main",
            bot_id="cli_app_1",
            secret_ref="channel/feishu/developer",
            enabled=True,
        )
        await ds.upsert(
            user_id="u2",
            platform="feishu",
            agent_slug="reviewer",
            channel_instance_id="feishu-disabled",
            bot_id="cli_app_2",
            secret_ref="channel/feishu/reviewer",
            enabled=False,
        )

        active = await ds.get_enabled_by_channel_instance(
            platform="feishu",
            channel_instance_id="feishu-main",
        )
        disabled = await ds.get_enabled_by_channel_instance(
            platform="feishu",
            channel_instance_id="feishu-disabled",
        )

    assert active is not None
    assert active.owner_user_id == "u1"
    assert active.agent_slug == "developer"
    assert active.bot_id == "cli_app_1"
    assert disabled is None
