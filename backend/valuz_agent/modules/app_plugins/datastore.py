"""Persistence of plugin storage and config (``valuz_app_plugin_*`` tables).

Per user x plugin, owner-scoped on every query; the one cross-owner operation is
``purge`` (uninstall with ``purge_data`` removes the plugin's data for everyone on
this device, like the device-wide ``app-plugin-data`` directory).
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.app_plugins.models import AppPluginConfigRow, AppPluginStorageRow


class AppPluginDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- config --------------------------------------------------------------------

    async def get_config(self, user_id: str, app_plugin_id: str) -> AppPluginConfigRow | None:
        return (
            (
                await self._db.execute(
                    select(AppPluginConfigRow).where(
                        AppPluginConfigRow.user_id == user_id,
                        AppPluginConfigRow.app_plugin_id == app_plugin_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def put_config(
        self, user_id: str, app_plugin_id: str, values_json: str, now: int
    ) -> AppPluginConfigRow:
        row = await self.get_config(user_id, app_plugin_id)
        if row is None:
            row = AppPluginConfigRow(
                user_id=user_id,
                app_plugin_id=app_plugin_id,
                values_json=values_json,
                updated_at=now,
            )
            self._db.add(row)
        else:
            row.values_json = values_json
            row.updated_at = now
        await self._db.flush()
        return row

    # -- storage -------------------------------------------------------------------

    async def list_storage(
        self, user_id: str, app_plugin_id: str, prefix: str = ""
    ) -> list[AppPluginStorageRow]:
        stmt = select(AppPluginStorageRow).where(
            AppPluginStorageRow.user_id == user_id,
            AppPluginStorageRow.app_plugin_id == app_plugin_id,
        )
        if prefix:
            stmt = stmt.where(AppPluginStorageRow.key.startswith(prefix, autoescape=True))
        stmt = stmt.order_by(AppPluginStorageRow.key)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_storage(
        self, user_id: str, app_plugin_id: str, key: str
    ) -> AppPluginStorageRow | None:
        return (
            (
                await self._db.execute(
                    select(AppPluginStorageRow).where(
                        AppPluginStorageRow.user_id == user_id,
                        AppPluginStorageRow.app_plugin_id == app_plugin_id,
                        AppPluginStorageRow.key == key,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def storage_total(
        self, user_id: str, app_plugin_id: str, *, excluding_key: str | None = None
    ) -> int:
        stmt = select(func.coalesce(func.sum(AppPluginStorageRow.size), 0)).where(
            AppPluginStorageRow.user_id == user_id,
            AppPluginStorageRow.app_plugin_id == app_plugin_id,
        )
        if excluding_key is not None:
            stmt = stmt.where(AppPluginStorageRow.key != excluding_key)
        return int((await self._db.execute(stmt)).scalar_one())

    async def put_storage(
        self, user_id: str, app_plugin_id: str, key: str, value_json: str, size: int, now: int
    ) -> AppPluginStorageRow:
        row = await self.get_storage(user_id, app_plugin_id, key)
        if row is None:
            row = AppPluginStorageRow(
                user_id=user_id,
                app_plugin_id=app_plugin_id,
                key=key,
                value_json=value_json,
                size=size,
                updated_at=now,
            )
            self._db.add(row)
        else:
            row.value_json = value_json
            row.size = size
            row.updated_at = now
        await self._db.flush()
        return row

    async def delete_storage(self, user_id: str, app_plugin_id: str, key: str) -> bool:
        result = await self._db.execute(
            delete(AppPluginStorageRow).where(
                AppPluginStorageRow.user_id == user_id,
                AppPluginStorageRow.app_plugin_id == app_plugin_id,
                AppPluginStorageRow.key == key,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def purge(self, app_plugin_id: str) -> None:
        await self._db.execute(
            delete(AppPluginStorageRow).where(AppPluginStorageRow.app_plugin_id == app_plugin_id)
        )
        await self._db.execute(
            delete(AppPluginConfigRow).where(AppPluginConfigRow.app_plugin_id == app_plugin_id)
        )


__all__ = ["AppPluginDatastore"]
