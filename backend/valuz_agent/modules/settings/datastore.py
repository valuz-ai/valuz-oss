from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.modules.settings.models import (
    AppSettingRow,
    OnboardingStateRow,
    ShortcutBindingRow,
)


class SettingsDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_setting(self, key: str) -> AppSettingRow | None:
        return await self._db.get(AppSettingRow, key)

    async def list_settings(self) -> list[AppSettingRow]:
        return list((await self._db.execute(select(AppSettingRow))).scalars().all())

    async def upsert_setting(self, row: AppSettingRow) -> AppSettingRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SettingsDatastore.upsert_setting")
        return row

    async def list_shortcuts(self) -> list[ShortcutBindingRow]:
        return list((await self._db.execute(select(ShortcutBindingRow))).scalars().all())

    async def upsert_shortcut(self, row: ShortcutBindingRow) -> ShortcutBindingRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SettingsDatastore.upsert_shortcut")
        return row

    async def get_onboarding_state(self) -> list[OnboardingStateRow]:
        return list((await self._db.execute(select(OnboardingStateRow))).scalars().all())

    async def upsert_onboarding_step(self, row: OnboardingStateRow) -> OnboardingStateRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SettingsDatastore.upsert_onboarding_step")
        return row
