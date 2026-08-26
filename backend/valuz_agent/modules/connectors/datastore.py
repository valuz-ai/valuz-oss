"""Connector datastore — async SQLAlchemy ORM access.

All connector state lives in the host DB: connector rows (including their secret
columns), and the per-project connector selection (``valuz_project_connector``,
formerly ``<project>/.claude/project-config.json``). A shared multi-client
backend has no per-user local filesystem, so nothing here touches disk.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
    ProjectConnectorRow,
)


class ConnectorDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        """The unit-of-work session shared with transaction-aware ports."""
        return self._db

    async def list_all(self, user_id: str) -> list[ConnectorRow]:
        rows = list(
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
        await self._hydrate(rows)
        return rows

    async def list_enabled(self, user_id: str) -> list[ConnectorRow]:
        rows = list(
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
        await self._hydrate(rows)
        return rows

    async def get_by_id(self, user_id: str, connector_id: str) -> ConnectorRow | None:
        row = (
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
        if row is not None:
            await self._hydrate([row])
        return row

    async def get_by_slug(self, user_id: str, slug: str) -> ConnectorRow | None:
        row = (
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
        if row is not None:
            await self._hydrate([row])
        return row

    async def create(self, user_id: str, row: ConnectorRow) -> ConnectorRow:
        # Owner passed explicitly (no ContextVar write-stamp default). The side
        # data (attrs + oauth) was built into the row's holders via the property
        # setters; persist it explicitly after the row gets its id.
        row.user_id = user_id
        attrs = row._attr_store()
        oauth = row._oauth_store()
        self._db.add(row)
        await self._db.flush()  # assigns row.id (+ client-side default columns)
        await self._persist_children(row.id, user_id, attrs, oauth)
        await self._db.commit()
        await self._db.refresh(row)
        row.__dict__["_attrs"] = attrs
        row.__dict__["_oauth"] = oauth
        return row

    async def update(self, row: ConnectorRow) -> ConnectorRow:
        # ``row`` came from an owner-scoped read; merge preserves its user_id and
        # writes the mapped columns. The side data is replaced wholesale (the
        # holders are the desired state) — not an ORM relationship, so the
        # datastore owns its persistence.
        attrs = row._attr_store()
        oauth = row._oauth_store()
        merged = await self._db.merge(row)
        await self._persist_children(row.id, row.user_id, attrs, oauth)
        await self._db.commit()
        await self._db.refresh(merged)
        merged.__dict__["_attrs"] = attrs
        merged.__dict__["_oauth"] = oauth
        return merged

    async def _hydrate(self, rows: list[ConnectorRow]) -> None:
        """Load each connector's side-table data (attrs + oauth) into its plain
        in-memory holders. Column-only selects, so the side rows are NOT added to
        the session identity map — the datastore replaces them wholesale on
        write rather than mutating tracked ORM objects."""
        if not rows:
            return
        ids = [r.id for r in rows]

        attr_pairs = (
            await self._db.execute(
                select(
                    ConnectorAttrRow.connector_id,
                    ConnectorAttrRow.key,
                    ConnectorAttrRow.value,
                ).where(ConnectorAttrRow.connector_id.in_(ids))
            )
        ).all()
        attrs_by_conn: dict[str, dict[str, str]] = {}
        for connector_id, key, value in attr_pairs:
            attrs_by_conn.setdefault(connector_id, {})[key] = value

        oauth_pairs = (
            await self._db.execute(
                select(
                    ConnectorOAuthRow.connector_id,
                    ConnectorOAuthRow.client_info,
                    ConnectorOAuthRow.token,
                    ConnectorOAuthRow.expires_at,
                ).where(ConnectorOAuthRow.connector_id.in_(ids))
            )
        ).all()
        oauth_by_conn: dict[str, dict[str, object]] = {}
        for connector_id, client_info, token, expires_at in oauth_pairs:
            oauth_by_conn[connector_id] = {
                k: v
                for k, v in (
                    ("client_info", client_info),
                    ("token", token),
                    ("expires_at", expires_at),
                )
                if v is not None
            }

        for r in rows:
            r.__dict__["_attrs"] = attrs_by_conn.get(r.id, {})
            r.__dict__["_oauth"] = oauth_by_conn.get(r.id, {})

    async def _persist_children(
        self,
        connector_id: str,
        user_id: str,
        attrs: dict[str, str],
        oauth: dict[str, object],
    ) -> None:
        """Desired-state replace of a connector's side rows. The Core deletes run
        immediately (before the pending inserts flush), so re-inserting the same
        keys never collides on the primary key."""
        await self._db.execute(
            delete(ConnectorAttrRow).where(ConnectorAttrRow.connector_id == connector_id)
        )
        await self._db.execute(
            delete(ConnectorOAuthRow).where(ConnectorOAuthRow.connector_id == connector_id)
        )
        for key, value in attrs.items():
            self._db.add(
                ConnectorAttrRow(connector_id=connector_id, key=key, value=value, user_id=user_id)
            )
        if oauth:
            self._db.add(
                ConnectorOAuthRow(
                    connector_id=connector_id,
                    client_info=cast("str | None", oauth.get("client_info")),
                    token=cast("str | None", oauth.get("token")),
                    expires_at=cast("int | None", oauth.get("expires_at")),
                    user_id=user_id,
                )
            )

    async def delete(self, user_id: str, connector_id: str) -> bool:
        row = await self.get_by_id(user_id, connector_id)
        if row is None:
            return False
        # Drop the connector's side rows (attrs + oauth) explicitly — they are
        # not an ORM relationship and there is no DB-level FK cascade, so the
        # datastore owns the cleanup.
        await self._db.execute(
            delete(ConnectorAttrRow).where(
                ConnectorAttrRow.connector_id == connector_id,
                ConnectorAttrRow.user_id == user_id,
            )
        )
        await self._db.execute(
            delete(ConnectorOAuthRow).where(
                ConnectorOAuthRow.connector_id == connector_id,
                ConnectorOAuthRow.user_id == user_id,
            )
        )
        await self._db.execute(
            delete(ConnectorRow).where(
                ConnectorRow.id == connector_id, ConnectorRow.user_id == user_id
            )
        )
        await self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Per-project connector selection (persisted in valuz_project_connector)
    # ------------------------------------------------------------------

    async def get_project_connectors(self, user_id: str, project_id: str) -> list[str]:
        rows = (
            (
                await self._db.execute(
                    select(ProjectConnectorRow)
                    .where(
                        ProjectConnectorRow.project_id == project_id,
                        ProjectConnectorRow.user_id == user_id,
                    )
                    # Selection is a membership set (resolved per-slug); order by
                    # slug for a stable, deterministic return — rows inserted in
                    # one ``set`` call share an ``added_at`` so it can't order them.
                    .order_by(ProjectConnectorRow.slug)
                )
            )
            .scalars()
            .all()
        )
        return [r.slug for r in rows]

    async def set_project_connectors(self, user_id: str, project_id: str, slugs: list[str]) -> None:
        # Desired-state replace: drop this project's rows, re-insert the new set.
        await self._db.execute(
            delete(ProjectConnectorRow).where(
                ProjectConnectorRow.project_id == project_id,
                ProjectConnectorRow.user_id == user_id,
            )
        )
        added = now_ms()
        self._db.add_all(
            [
                ProjectConnectorRow(
                    project_id=project_id, slug=slug, user_id=user_id, added_at=added
                )
                for slug in slugs
            ]
        )
        await async_commit_with_retry(self._db, where="ConnectorDatastore.set_project_connectors")
