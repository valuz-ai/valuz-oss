"""Connector datastore — async SQLAlchemy ORM access.

DB methods are ``async``; the per-project filesystem helpers
(``get_project_connectors`` / ``set_project_connectors``) read/write
``.claude/project-config.json`` with no DB and stay plain ``def``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.connectors.models import ConnectorRow


class ConnectorDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_all(self, user_id: str) -> list[ConnectorRow]:
        return list(
            (
                await self._db.execute(
                    select(ConnectorRow)
                    .where(ConnectorRow.user_id == user_id)
                    .order_by(ConnectorRow.display_name)
                )
            )
            .scalars()
            .all()
        )

    async def list_enabled(self, user_id: str) -> list[ConnectorRow]:
        return list(
            (
                await self._db.execute(
                    select(ConnectorRow)
                    .where(ConnectorRow.user_id == user_id, ConnectorRow.enabled)
                    .order_by(ConnectorRow.display_name)
                )
            )
            .scalars()
            .all()
        )

    async def get_by_id(self, user_id: str, connector_id: str) -> ConnectorRow | None:
        return (
            (
                await self._db.execute(
                    select(ConnectorRow).where(
                        ConnectorRow.id == connector_id, ConnectorRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_by_slug(self, user_id: str, slug: str) -> ConnectorRow | None:
        return (
            (
                await self._db.execute(
                    select(ConnectorRow).where(
                        ConnectorRow.slug == slug, ConnectorRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def create(self, user_id: str, row: ConnectorRow) -> ConnectorRow:
        # Owner passed explicitly (no ContextVar write-stamp default).
        row.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def update(self, row: ConnectorRow) -> ConnectorRow:
        # ``row`` came from an owner-scoped read; merge preserves its user_id.
        merged = await self._db.merge(row)
        await self._db.commit()
        await self._db.refresh(merged)
        return merged

    async def delete(self, user_id: str, connector_id: str) -> bool:
        row = await self.get_by_id(user_id, connector_id)
        if row is None:
            return False
        await self._db.execute(
            delete(ConnectorRow).where(
                ConnectorRow.id == connector_id, ConnectorRow.user_id == user_id
            )
        )
        await self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Per-project connector selection (persisted in project-config.json)
    # ------------------------------------------------------------------

    def get_project_connectors(self, project: _ProjectLike) -> list[str]:
        if project.kind != "project" or not project.root_path:
            return []
        config_path = Path(project.root_path) / ".claude" / "project-config.json"
        if not config_path.exists():
            return []
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        value = raw.get("connectors", [])
        return value if isinstance(value, list) else []

    def set_project_connectors(self, project: _ProjectLike, slugs: list[str]) -> None:
        config_path = Path(project.root_path) / ".claude" / "project-config.json"  # type: ignore[arg-type]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
        data["connectors"] = slugs
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class _ProjectLike(Protocol):
    id: str
    kind: str
    root_path: str | None
