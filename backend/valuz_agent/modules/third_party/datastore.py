"""Persistence of plugin storage and config (``valuz_extension_*`` tables).

Per user x plugin, owner-scoped on every query; the one cross-owner operation is
``purge`` (uninstall with ``purge_data`` removes the plugin's data for everyone on
this device, like the device-wide ``extensions-data`` directory).
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.third_party.models import ExtensionConfigRow, ExtensionStorageRow


class ExtensionDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- config --------------------------------------------------------------------

    async def get_config(self, user_id: str, extension_id: str) -> ExtensionConfigRow | None:
        return (
            (
                await self._db.execute(
                    select(ExtensionConfigRow).where(
                        ExtensionConfigRow.user_id == user_id,
                        ExtensionConfigRow.extension_id == extension_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def put_config(
        self, user_id: str, extension_id: str, values_json: str, now: int
    ) -> ExtensionConfigRow:
        row = await self.get_config(user_id, extension_id)
        if row is None:
            row = ExtensionConfigRow(
                user_id=user_id, extension_id=extension_id, values_json=values_json, updated_at=now
            )
            self._db.add(row)
        else:
            row.values_json = values_json
            row.updated_at = now
        await self._db.flush()
        return row

    # -- storage -------------------------------------------------------------------

    async def list_storage(
        self, user_id: str, extension_id: str, prefix: str = ""
    ) -> list[ExtensionStorageRow]:
        stmt = select(ExtensionStorageRow).where(
            ExtensionStorageRow.user_id == user_id,
            ExtensionStorageRow.extension_id == extension_id,
        )
        if prefix:
            stmt = stmt.where(ExtensionStorageRow.key.startswith(prefix, autoescape=True))
        stmt = stmt.order_by(ExtensionStorageRow.key)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_storage(
        self, user_id: str, extension_id: str, key: str
    ) -> ExtensionStorageRow | None:
        return (
            (
                await self._db.execute(
                    select(ExtensionStorageRow).where(
                        ExtensionStorageRow.user_id == user_id,
                        ExtensionStorageRow.extension_id == extension_id,
                        ExtensionStorageRow.key == key,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def storage_total(
        self, user_id: str, extension_id: str, *, excluding_key: str | None = None
    ) -> int:
        stmt = select(func.coalesce(func.sum(ExtensionStorageRow.size), 0)).where(
            ExtensionStorageRow.user_id == user_id,
            ExtensionStorageRow.extension_id == extension_id,
        )
        if excluding_key is not None:
            stmt = stmt.where(ExtensionStorageRow.key != excluding_key)
        return int((await self._db.execute(stmt)).scalar_one())

    async def put_storage(
        self, user_id: str, extension_id: str, key: str, value_json: str, size: int, now: int
    ) -> ExtensionStorageRow:
        row = await self.get_storage(user_id, extension_id, key)
        if row is None:
            row = ExtensionStorageRow(
                user_id=user_id,
                extension_id=extension_id,
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

    async def delete_storage(self, user_id: str, extension_id: str, key: str) -> bool:
        result = await self._db.execute(
            delete(ExtensionStorageRow).where(
                ExtensionStorageRow.user_id == user_id,
                ExtensionStorageRow.extension_id == extension_id,
                ExtensionStorageRow.key == key,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def purge(self, extension_id: str) -> None:
        await self._db.execute(
            delete(ExtensionStorageRow).where(ExtensionStorageRow.extension_id == extension_id)
        )
        await self._db.execute(
            delete(ExtensionConfigRow).where(ExtensionConfigRow.extension_id == extension_id)
        )


__all__ = ["ExtensionDatastore"]
