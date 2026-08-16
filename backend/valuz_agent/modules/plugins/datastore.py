"""Persistence for ``valuz_plugin`` / ``valuz_plugin_component``.

Owner-scoped like every other business table (``user_id`` stamped explicitly
on create). Writes commit through ``async_commit_with_retry`` (same as the
skill / connector datastores) so plugin bookkeeping is durable even if the
enclosing request unit of work later fails.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.modules.plugins.models import PluginComponentRow, PluginRow


class PluginDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db

    # -- plugins ------------------------------------------------------------

    async def list_plugins(self, user_id: str) -> list[PluginRow]:
        stmt = select(PluginRow).where(PluginRow.user_id == user_id).order_by(PluginRow.name)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_id(self, user_id: str, plugin_id: str) -> PluginRow | None:
        stmt = select(PluginRow).where(PluginRow.user_id == user_id, PluginRow.id == plugin_id)
        return (await self._db.execute(stmt)).scalars().first()

    async def get_by_name(self, user_id: str, name: str) -> PluginRow | None:
        stmt = select(PluginRow).where(PluginRow.user_id == user_id, PluginRow.name == name)
        return (await self._db.execute(stmt)).scalars().first()

    async def create_plugin(self, user_id: str, row: PluginRow) -> PluginRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="PluginDatastore.create_plugin")
        return row

    async def update_plugin(self, row: PluginRow) -> PluginRow:
        merged = await self._db.merge(row)
        await async_commit_with_retry(self._db, where="PluginDatastore.update_plugin")
        return merged

    async def delete_plugin(self, user_id: str, plugin_id: str) -> None:
        await self._db.execute(
            delete(PluginComponentRow).where(
                PluginComponentRow.user_id == user_id,
                PluginComponentRow.plugin_id == plugin_id,
            )
        )
        await self._db.execute(
            delete(PluginRow).where(PluginRow.user_id == user_id, PluginRow.id == plugin_id)
        )
        await async_commit_with_retry(self._db, where="PluginDatastore.delete_plugin")

    # -- components ---------------------------------------------------------

    async def list_components(self, user_id: str, plugin_id: str) -> list[PluginComponentRow]:
        stmt = (
            select(PluginComponentRow)
            .where(
                PluginComponentRow.user_id == user_id,
                PluginComponentRow.plugin_id == plugin_id,
            )
            .order_by(PluginComponentRow.kind, PluginComponentRow.slug)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_all_components(self, user_id: str) -> list[PluginComponentRow]:
        stmt = (
            select(PluginComponentRow)
            .where(PluginComponentRow.user_id == user_id)
            .order_by(PluginComponentRow.kind, PluginComponentRow.slug)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_components_by_member(
        self, user_id: str, kind: str, slug: str
    ) -> list[PluginComponentRow]:
        stmt = select(PluginComponentRow).where(
            PluginComponentRow.user_id == user_id,
            PluginComponentRow.kind == kind,
            PluginComponentRow.slug == slug,
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_component(
        self, user_id: str, plugin_id: str, kind: str, slug: str
    ) -> PluginComponentRow | None:
        stmt = select(PluginComponentRow).where(
            PluginComponentRow.user_id == user_id,
            PluginComponentRow.plugin_id == plugin_id,
            PluginComponentRow.kind == kind,
            PluginComponentRow.slug == slug,
        )
        return (await self._db.execute(stmt)).scalars().first()

    async def create_component(self, user_id: str, row: PluginComponentRow) -> PluginComponentRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="PluginDatastore.create_component")
        return row

    async def update_component(self, row: PluginComponentRow) -> PluginComponentRow:
        merged = await self._db.merge(row)
        await async_commit_with_retry(self._db, where="PluginDatastore.update_component")
        return merged

    async def delete_component(self, user_id: str, component_id: str) -> None:
        await self._db.execute(
            delete(PluginComponentRow).where(
                PluginComponentRow.user_id == user_id, PluginComponentRow.id == component_id
            )
        )
        await async_commit_with_retry(self._db, where="PluginDatastore.delete_component")


__all__ = ["PluginDatastore"]
