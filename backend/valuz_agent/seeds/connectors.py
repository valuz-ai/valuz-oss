"""First-boot installation of bundled first-party connectors.

Installation deliberately stops at ``pending_auth``.  OSS has no Valuz
account federation contract, so it must never start an account login merely
because the process booted.  Commercial editions may subsequently materialize
an authorized snapshot through their control plane.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.connectors.catalog import load_catalog
from valuz_agent.modules.connectors.models import ConnectorRow


def _localized(value: object, fallback: str) -> str:
    if isinstance(value, dict):
        for key in ("zh-CN", "en-US"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for candidate in value.values():
            if isinstance(candidate, str) and candidate:
                return candidate
    return value if isinstance(value, str) and value else fallback


def _builtin_entries() -> list[dict]:
    entries: list[dict] = []
    for item in load_catalog():
        members = item.get("connectors")
        candidates = members if isinstance(members, list) else [item]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("builtin") is True:
                entries.append(candidate)
    return entries


async def seed_builtin_connectors(db: AsyncSession, *, user_id: str) -> None:
    """Insert missing built-ins without initiating or fabricating OAuth."""

    existing = set(
        (
            await db.execute(
                select(ConnectorRow.slug).where(ConnectorRow.user_id == user_id)
            )
        ).scalars()
    )
    for entry in _builtin_entries():
        slug = str(entry["slug"])
        if slug in existing:
            continue
        auth_type = str(entry.get("auth_type") or "none")
        db.add(
            ConnectorRow(
                user_id=user_id,
                slug=slug,
                display_name=_localized(entry.get("display_name"), slug),
                description=_localized(entry.get("description"), "") or None,
                connector_type="builtin",
                transport=str(entry.get("transport") or "http"),
                url=entry.get("url"),
                auth_type=auth_type,
                args="[]",
                enabled=auth_type != "oauth",
                status="pending_auth" if auth_type == "oauth" else "unknown",
            )
        )
    await db.flush()


__all__ = ["seed_builtin_connectors"]
