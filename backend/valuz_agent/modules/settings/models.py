from sqlalchemy import BigInteger, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin


class AppSettingRow(Base, OwnedMixin):
    __tablename__ = "valuz_app_setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[int] = mapped_column(BigInteger)


class ShortcutBindingRow(Base, OwnedMixin):
    __tablename__ = "valuz_shortcut_binding"

    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key_combo: Mapped[str] = mapped_column(String(128))
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class OnboardingStateRow(Base, OwnedMixin):
    __tablename__ = "valuz_onboarding_state"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
