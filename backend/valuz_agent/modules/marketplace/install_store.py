"""Marketplace install provenance — ``marketplace_install`` table.

Records what a user installed from the market index: the item id, its type,
the resulting local reference (skill slug / agent slug / team pack id / plugin
name), the
item's version at install time, and (for skills) a content hash of the
installed file tree. Phase 1 (this module) is **write-only** — installs
call :meth:`MarketplaceInstallStore.record`, and resource-deletion hooks in
``modules/skills/service.py`` / ``modules/agents/service.py`` call
:meth:`MarketplaceInstallStore.remove_by_ref` to keep provenance from going
stale. No read/update API is exposed yet; the update-check flow (★ phase 2)
is a documented follow-up — see ``docs/cloud-marketplace/design/oss.md``.
"""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import Boolean, String, UniqueConstraint, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

logger = logging.getLogger(__name__)

MarketplaceInstallType = Literal[
    "skill", "agent_template", "agent_team_template", "connector", "plugin"
]


class MarketplaceInstallRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "marketplace_install"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", name="uq_marketplace_install_owner_item"),
    )

    # ``market:{kind}:{slug}`` — the market index item id.
    item_id: Mapped[str] = mapped_column(String(255), index=True)
    item_type: Mapped[str] = mapped_column(String(32))
    # skill slug / agent slug / team pack id — the local resource this
    # install produced. Indexed so a resource-deletion hook can find its
    # provenance row(s) by ref without scanning the whole table.
    installed_ref: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[str] = mapped_column(String(64))
    # Skills only — the installed file tree's stable hash (``hash_skill_directory``),
    # captured at install time. Phase 2 uses it to detect local edits before an
    # automatic update; phase 1 just persists it.
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    # Phase-2 flag column, always False in phase 1 — no code path flips it yet.
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)
    # The index channel active at install time (diagnostics — e.g. "oss").
    source_channel: Mapped[str] = mapped_column(String(64))


class MarketplaceInstallStore:
    """Persistence for ``marketplace_install`` — write path only in phase 1."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        user_id: str,
        *,
        item_id: str,
        item_type: MarketplaceInstallType,
        installed_ref: str,
        version: str,
        source_channel: str,
        content_hash: str | None = None,
    ) -> None:
        """Upsert the provenance row for one ``(user_id, item_id)``.

        Re-installing (or, later, updating) the same item overwrites the
        previous row instead of accumulating history — phase 1 only needs
        the latest install state. This is also how a pre-existing
        (pre-marketplace_install-era) install "catches up": reinstalling the
        same item establishes fresh provenance (see design doc's "historical
        installs are not backfilled" decision).
        """
        existing = await self._db.scalar(
            select(MarketplaceInstallRow).where(
                MarketplaceInstallRow.user_id == user_id,
                MarketplaceInstallRow.item_id == item_id,
            )
        )
        if existing is not None:
            existing.item_type = item_type
            existing.installed_ref = installed_ref
            existing.version = version
            existing.content_hash = content_hash
            existing.source_channel = source_channel
        else:
            self._db.add(
                MarketplaceInstallRow(
                    user_id=user_id,
                    item_id=item_id,
                    item_type=item_type,
                    installed_ref=installed_ref,
                    version=version,
                    content_hash=content_hash,
                    source_channel=source_channel,
                )
            )
        await self._db.flush()

    async def remove_by_ref(self, user_id: str, installed_ref: str) -> None:
        """Delete provenance row(s) for a resource removed from the user's
        library (the skill/agent deletion hooks) — ``installed_ref`` is the
        skill slug or agent slug that was deleted."""
        await self._db.execute(
            delete(MarketplaceInstallRow).where(
                MarketplaceInstallRow.user_id == user_id,
                MarketplaceInstallRow.installed_ref == installed_ref,
            )
        )
        await self._db.flush()
