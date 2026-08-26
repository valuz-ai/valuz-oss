"""Datastore for channel thread bindings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.channels.models import (
    AgentChannelBindingRow,
    ChannelChatBindingRow,
    ChannelThreadBindingRow,
)
from valuz_agent.modules.channels.schemas import (
    AgentChannelBinding,
    ChannelChatBinding,
    ChannelRouteKey,
    ChannelThreadBinding,
)


class AgentChannelBindingDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
    ) -> AgentChannelBinding | None:
        row = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow).where(
                        AgentChannelBindingRow.user_id == user_id,
                        AgentChannelBindingRow.platform == platform,
                        AgentChannelBindingRow.agent_slug == agent_slug,
                    )
                )
            )
            .scalars()
            .first()
        )
        return _agent_binding_to_schema(row) if row is not None else None

    async def list_enabled(
        self,
        *,
        platform: str,
        user_id: str | None = None,
    ) -> list[AgentChannelBinding]:
        """Enabled bindings for a platform.

        ``user_id=None`` lists across owners — the long-connection supervisors
        use it because a background loader has no request identity to filter
        by; each row carries its own owner (the supervisor must never guess one
        from ambient process identity).
        """
        conditions = [
            AgentChannelBindingRow.platform == platform,
            AgentChannelBindingRow.enabled.is_(True),
        ]
        if user_id is not None:
            conditions.append(AgentChannelBindingRow.user_id == user_id)
        rows = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow)
                    .where(*conditions)
                    .order_by(AgentChannelBindingRow.updated_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_agent_binding_to_schema(row) for row in rows]

    async def get_enabled_by_channel_instance(
        self,
        *,
        platform: str,
        channel_instance_id: str,
    ) -> AgentChannelBinding | None:
        row = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow)
                    .where(
                        AgentChannelBindingRow.platform == platform,
                        AgentChannelBindingRow.channel_instance_id == channel_instance_id,
                        AgentChannelBindingRow.enabled.is_(True),
                    )
                    .order_by(AgentChannelBindingRow.updated_at.desc())
                )
            )
            .scalars()
            .first()
        )
        return _agent_binding_to_schema(row) if row is not None else None

    async def upsert(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
        channel_instance_id: str,
        bot_id: str,
        secret_ref: str | None,
        enabled: bool,
        bot_name: str | None = None,
        ws_url: str | None = None,
    ) -> AgentChannelBinding:
        row = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow).where(
                        AgentChannelBindingRow.user_id == user_id,
                        AgentChannelBindingRow.platform == platform,
                        AgentChannelBindingRow.agent_slug == agent_slug,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = AgentChannelBindingRow(
                user_id=user_id,
                platform=platform,
                agent_slug=agent_slug,
                channel_instance_id=channel_instance_id,
                bot_id=bot_id,
                secret_ref=secret_ref,
                enabled=enabled,
                bot_name=bot_name,
                ws_url=ws_url,
            )
            self._db.add(row)
        else:
            row.channel_instance_id = channel_instance_id
            row.bot_id = bot_id
            row.secret_ref = secret_ref
            row.enabled = enabled
            row.bot_name = bot_name
            row.ws_url = ws_url
        await self._db.commit()
        await self._db.refresh(row)
        return _agent_binding_to_schema(row)


class ChannelThreadBindingDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_for_thread(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
        external_thread_id: str,
        agent_slug: str,
    ) -> ChannelThreadBinding | None:
        row = (
            (
                await self._db.execute(
                    select(ChannelThreadBindingRow)
                    .where(
                        ChannelThreadBindingRow.user_id == user_id,
                        ChannelThreadBindingRow.channel_instance_id == channel_instance_id,
                        ChannelThreadBindingRow.external_chat_id == external_chat_id,
                        ChannelThreadBindingRow.external_thread_id == external_thread_id,
                        ChannelThreadBindingRow.agent_slug == agent_slug,
                    )
                    .order_by(ChannelThreadBindingRow.updated_at.desc())
                )
            )
            .scalars()
            .first()
        )
        return _row_to_binding(row) if row is not None else None

    async def upsert(self, *, user_id: str, key: ChannelRouteKey, session_id: str) -> None:
        row = (
            (
                await self._db.execute(
                    select(ChannelThreadBindingRow).where(
                        ChannelThreadBindingRow.user_id == user_id,
                        ChannelThreadBindingRow.channel_instance_id == key.channel_instance_id,
                        ChannelThreadBindingRow.external_chat_id == key.external_chat_id,
                        ChannelThreadBindingRow.external_thread_id == key.external_thread_id,
                        ChannelThreadBindingRow.agent_slug == key.agent_slug,
                        ChannelThreadBindingRow.project_id == key.project_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = ChannelThreadBindingRow(
                user_id=user_id,
                channel_instance_id=key.channel_instance_id,
                external_chat_id=key.external_chat_id,
                external_thread_id=key.external_thread_id,
                agent_slug=key.agent_slug,
                project_id=key.project_id,
                session_id=session_id,
            )
            self._db.add(row)
        else:
            row.session_id = session_id
        await self._db.commit()


def _row_to_binding(row: ChannelThreadBindingRow) -> ChannelThreadBinding:
    return ChannelThreadBinding(
        channel_instance_id=row.channel_instance_id,
        external_chat_id=row.external_chat_id,
        external_thread_id=row.external_thread_id,
        agent_slug=row.agent_slug,
        project_id=row.project_id,
        session_id=row.session_id,
    )


class ChannelChatBindingDatastore:
    """Persistent "this chat is that project" mapping.

    Replaces inferring a chat's project from whichever session lineage was
    touched last: that guess could not be inspected, changed, or reasoned about
    (docs/design/channel-project-binding-and-default-lead.md §2.2).
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
    ) -> ChannelChatBinding | None:
        row = await self._row(
            user_id=user_id,
            channel_instance_id=channel_instance_id,
            external_chat_id=external_chat_id,
        )
        return _chat_binding_to_schema(row) if row is not None else None

    async def list_all(self, *, user_id: str) -> list[ChannelChatBinding]:
        rows = (
            (
                await self._db.execute(
                    select(ChannelChatBindingRow)
                    .where(ChannelChatBindingRow.user_id == user_id)
                    .order_by(ChannelChatBindingRow.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_chat_binding_to_schema(row) for row in rows]

    async def list_for_project(
        self, *, user_id: str, project_id: str
    ) -> list[ChannelChatBinding]:
        rows = (
            (
                await self._db.execute(
                    select(ChannelChatBindingRow)
                    .where(
                        ChannelChatBindingRow.user_id == user_id,
                        ChannelChatBindingRow.project_id == project_id,
                    )
                    .order_by(ChannelChatBindingRow.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_chat_binding_to_schema(row) for row in rows]

    async def upsert(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
        project_id: str,
        default_agent_slug: str | None = None,
        external_chat_name: str | None = None,
        bound_by_external_user: str | None = None,
        created_by_valuz: bool | None = None,
    ) -> ChannelChatBinding:
        """Bind (or rebind) the chat. One chat holds one project, so rebinding
        overwrites rather than accumulating — see §3.2."""
        row = await self._row(
            user_id=user_id,
            channel_instance_id=channel_instance_id,
            external_chat_id=external_chat_id,
        )
        if row is None:
            row = ChannelChatBindingRow(
                user_id=user_id,
                channel_instance_id=channel_instance_id,
                external_chat_id=external_chat_id,
                project_id=project_id,
            )
            self._db.add(row)
        row.project_id = project_id
        row.default_agent_slug = default_agent_slug
        # Keep the last known name/binder when the caller has nothing fresher.
        if external_chat_name is not None:
            row.external_chat_name = external_chat_name
        if bound_by_external_user is not None:
            row.bound_by_external_user = bound_by_external_user
        if created_by_valuz is not None:
            row.created_by_valuz = created_by_valuz
        await self._db.flush()
        return _chat_binding_to_schema(row)

    async def delete(
        self, *, user_id: str, channel_instance_id: str, external_chat_id: str
    ) -> bool:
        row = await self._row(
            user_id=user_id,
            channel_instance_id=channel_instance_id,
            external_chat_id=external_chat_id,
        )
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.flush()
        return True

    async def _row(
        self, *, user_id: str, channel_instance_id: str, external_chat_id: str
    ) -> ChannelChatBindingRow | None:
        return (
            (
                await self._db.execute(
                    select(ChannelChatBindingRow).where(
                        ChannelChatBindingRow.user_id == user_id,
                        ChannelChatBindingRow.channel_instance_id == channel_instance_id,
                        ChannelChatBindingRow.external_chat_id == external_chat_id,
                    )
                )
            )
            .scalars()
            .first()
        )


def _chat_binding_to_schema(row: ChannelChatBindingRow) -> ChannelChatBinding:
    return ChannelChatBinding(
        channel_instance_id=row.channel_instance_id,
        external_chat_id=row.external_chat_id,
        project_id=row.project_id,
        default_agent_slug=row.default_agent_slug,
        external_chat_name=row.external_chat_name,
        bound_by_external_user=row.bound_by_external_user,
        created_by_valuz=bool(row.created_by_valuz),
    )


def _agent_binding_to_schema(row: AgentChannelBindingRow) -> AgentChannelBinding:
    return AgentChannelBinding(
        id=row.id,
        owner_user_id=row.user_id,
        platform=row.platform,
        channel_instance_id=row.channel_instance_id,
        agent_slug=row.agent_slug,
        bot_id=row.bot_id,
        secret_ref=row.secret_ref,
        enabled=row.enabled,
        bot_name=row.bot_name,
        ws_url=row.ws_url,
    )


__all__ = [
    "AgentChannelBindingDatastore",
    "ChannelChatBindingDatastore",
    "ChannelThreadBindingDatastore",
]
