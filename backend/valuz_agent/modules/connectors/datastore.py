"""Connector datastore — async SQLAlchemy ORM access.

DB methods are ``async``; the per-workspace filesystem helpers
(``get_workspace_connectors`` / ``set_workspace_connectors``) read/write
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

    async def list_all(self) -> list[ConnectorRow]:
        return list(
            (await self._db.execute(select(ConnectorRow).order_by(ConnectorRow.display_name)))
            .scalars()
            .all()
        )

    async def list_enabled(self) -> list[ConnectorRow]:
        return list(
            (
                await self._db.execute(
                    select(ConnectorRow).filter_by(enabled=True).order_by(ConnectorRow.display_name)
                )
            )
            .scalars()
            .all()
        )

    async def get_by_id(self, connector_id: str) -> ConnectorRow | None:
        return (
            (await self._db.execute(select(ConnectorRow).filter_by(id=connector_id)))
            .scalars()
            .first()
        )

    async def get_by_slug(self, slug: str) -> ConnectorRow | None:
        return (await self._db.execute(select(ConnectorRow).filter_by(slug=slug))).scalars().first()

    async def create(self, row: ConnectorRow) -> ConnectorRow:
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def update(self, row: ConnectorRow) -> ConnectorRow:
        merged = await self._db.merge(row)
        await self._db.commit()
        await self._db.refresh(merged)
        return merged

    async def delete(self, connector_id: str) -> bool:
        row = await self.get_by_id(connector_id)
        if row is None:
            return False
        await self._db.execute(delete(ConnectorRow).where(ConnectorRow.id == connector_id))
        await self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Per-workspace connector selection (persisted in project-config.json)
    # ------------------------------------------------------------------

    def get_workspace_connectors(self, workspace: _WorkspaceLike) -> list[str]:
        if workspace.kind != "project" or not workspace.root_path:
            return []
        config_path = Path(workspace.root_path) / ".claude" / "project-config.json"
        if not config_path.exists():
            return []
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        value = raw.get("connectors", [])
        return value if isinstance(value, list) else []

    def set_workspace_connectors(self, workspace: _WorkspaceLike, slugs: list[str]) -> None:
        config_path = Path(workspace.root_path) / ".claude" / "project-config.json"  # type: ignore[arg-type]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
        data["connectors"] = slugs
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class _WorkspaceLike(Protocol):
    id: str
    kind: str
    root_path: str | None
