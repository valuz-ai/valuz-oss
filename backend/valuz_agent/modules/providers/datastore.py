from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.modules.providers.models import ProviderRow


class ProviderDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_providers(self, user_id: str) -> list[ProviderRow]:
        return list(
            (
                await self._db.execute(
                    select(ProviderRow)
                    .where(ProviderRow.user_id == user_id)
                    .order_by(ProviderRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def get_by_id(self, user_id: str, provider_id: str) -> ProviderRow | None:
        # Owner-scoped by id — never ``session.get`` (it bypasses the owner filter).
        return (
            (
                await self._db.execute(
                    select(ProviderRow).where(
                        ProviderRow.id == provider_id, ProviderRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_default(self, user_id: str) -> ProviderRow | None:
        return (
            (
                await self._db.execute(
                    select(ProviderRow).where(
                        ProviderRow.user_id == user_id,
                        ProviderRow.is_default,
                        ProviderRow.enabled,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def create(self, user_id: str, row: ProviderRow) -> ProviderRow:
        # Owner is passed explicitly (no ContextVar write-stamp default).
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="ProviderDatastore.create")
        return row

    async def update(self, row: ProviderRow) -> ProviderRow:
        # ``row`` came from an owner-scoped read, so its ``user_id`` is already
        # the caller's; merge preserves it.
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="ProviderDatastore.update")
        return row

    async def delete(self, user_id: str, provider_id: str) -> None:
        await self._db.execute(
            sa_delete(ProviderRow).where(
                ProviderRow.id == provider_id, ProviderRow.user_id == user_id
            )
        )
        await async_commit_with_retry(self._db, where="ProviderDatastore.delete")

    async def clear_default(self, user_id: str) -> None:
        await self._db.execute(
            update(ProviderRow)
            .where(ProviderRow.user_id == user_id, ProviderRow.is_default)
            .values(is_default=False)
        )
        await async_commit_with_retry(self._db, where="ProviderDatastore.clear_default")
