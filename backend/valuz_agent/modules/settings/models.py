from sqlalchemy import BigInteger, Boolean, PrimaryKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, UserMixin


class AppSettingRow(Base, UserMixin):
    __tablename__ = "valuz_app_setting"
    # Composite PK ``(key, user_id)``: settings are per-owner, so two users can
    # each hold the same key without clobbering each other on upsert.
    __table_args__ = (PrimaryKeyConstraint("key", "user_id"),)

    key: Mapped[str] = mapped_column(String(128))
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[int] = mapped_column(BigInteger)


class ShortcutBindingRow(Base, UserMixin):
    __tablename__ = "valuz_shortcut_binding"
    __table_args__ = (PrimaryKeyConstraint("action_id", "user_id"),)

    action_id: Mapped[str] = mapped_column(String(128))
    key_combo: Mapped[str] = mapped_column(String(128))
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class OnboardingStateRow(Base, UserMixin):
    __tablename__ = "valuz_onboarding_state"
    __table_args__ = (PrimaryKeyConstraint("step_id", "user_id"),)

    step_id: Mapped[str] = mapped_column(String(64))
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
