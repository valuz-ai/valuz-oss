from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.modules.providers.models import ProviderRow


class ProviderDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_providers(self) -> list[ProviderRow]:
        return list(
            (await self._db.execute(select(ProviderRow).order_by(ProviderRow.created_at)))
            .scalars()
            .all()
        )

    async def get_by_id(self, provider_id: str) -> ProviderRow | None:
        return await self._db.get(ProviderRow, provider_id)

    async def get_default(self) -> ProviderRow | None:
        return (
            (await self._db.execute(select(ProviderRow).filter_by(is_default=True, enabled=True)))
            .scalars()
            .first()
        )

    async def create(self, row: ProviderRow) -> ProviderRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="ProviderDatastore.create")
        return row

    async def update(self, row: ProviderRow) -> ProviderRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="ProviderDatastore.update")
        return row

    async def delete(self, provider_id: str) -> None:
        await self._db.execute(sa_delete(ProviderRow).where(ProviderRow.id == provider_id))
        await async_commit_with_retry(self._db, where="ProviderDatastore.delete")

    async def clear_default(self) -> None:
        await self._db.execute(
            update(ProviderRow).where(ProviderRow.is_default).values(is_default=False)
        )
        await async_commit_with_retry(self._db, where="ProviderDatastore.clear_default")
