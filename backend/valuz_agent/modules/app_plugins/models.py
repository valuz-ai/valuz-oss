"""Third-party plugin tables (docs task card 04 §A).

``valuz_app_plugin_storage`` — the plugin key-value store, one row per
(user, plugin, key); ``value_json`` is the JSON-encoded value and ``size`` its
encoded byte length (what the per-plugin quota adds up).

``valuz_app_plugin_config`` — the user's settings of one plugin, validated
against the manifest ``config`` schema before they are written.

The tables are per user x plugin; the plugin *package* is per device and lives on
disk (``installed.json``, see ``store.py``).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base


class AppPluginStorageRow(Base):
    __tablename__ = "valuz_app_plugin_storage"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_plugin_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class AppPluginConfigRow(Base):
    __tablename__ = "valuz_app_plugin_config"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_plugin_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    values_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


# Deprecated import aliases use the canonical implementation.
ExtensionStorageRow = AppPluginStorageRow
ExtensionConfigRow = AppPluginConfigRow
