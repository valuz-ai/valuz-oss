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

    async def get_setting(self, user_id: str, key: str) -> AppSettingRow | None:
        return (
            (
                await self._db.execute(
                    select(AppSettingRow).where(
                        AppSettingRow.key == key, AppSettingRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def list_settings(self, user_id: str) -> list[AppSettingRow]:
        return list(
            (await self._db.execute(select(AppSettingRow).where(AppSettingRow.user_id == user_id)))
            .scalars()
            .all()
        )

    async def upsert_setting(self, user_id: str, row: AppSettingRow) -> AppSettingRow:
        # Owner passed explicitly; the composite PK ``(key, user_id)`` makes the
        # merge per-owner, so one user's write never clobbers another's.
        row.user_id = user_id
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SettingsDatastore.upsert_setting")
        return row

    async def list_shortcuts(self, user_id: str) -> list[ShortcutBindingRow]:
        return list(
            (
                await self._db.execute(
                    select(ShortcutBindingRow).where(ShortcutBindingRow.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

    async def upsert_shortcut(self, user_id: str, row: ShortcutBindingRow) -> ShortcutBindingRow:
        row.user_id = user_id
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SettingsDatastore.upsert_shortcut")
        return row

    async def get_onboarding_state(self, user_id: str) -> list[OnboardingStateRow]:
        return list(
            (
                await self._db.execute(
                    select(OnboardingStateRow).where(OnboardingStateRow.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

    async def upsert_onboarding_step(
        self, user_id: str, row: OnboardingStateRow
    ) -> OnboardingStateRow:
        row.user_id = user_id
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SettingsDatastore.upsert_onboarding_step")
        return row
