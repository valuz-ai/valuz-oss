"""First-boot installation of bundled first-party connectors.

Installation deliberately stops at ``pending_auth``.  OSS has no Valuz
account federation contract, so it must never start an account login merely
because the process booted.  Commercial editions may subsequently materialize
an authorized snapshot through their control plane.
"""

from __future__ import annotations

from typing import Any

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


def _builtin_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in load_catalog():
        members = item.get("connectors")
        candidates = members if isinstance(members, list) else [item]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("builtin") is True:
                entries.append(candidate)
    return entries


def _catalog_entry(slug: str) -> dict[str, Any] | None:
    for item in load_catalog():
        members = item.get("connectors")
        candidates = members if isinstance(members, list) else [item]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("slug") == slug:
                return candidate
    return None


async def _declared_entries() -> list[dict[str, Any]]:
    """The connector set from the builtin declaration port.

    A declaration's ``connector_config`` wins; a packaged-manifest entry that
    only points at the catalog (``asset: connector_catalog.json#<slug>``)
    resolves through the catalog. An empty declared set falls back to the
    catalog's ``builtin: true`` rows so a build without a manifest keeps
    today's behavior.
    """
    from valuz_agent.ports.builtin_declaration import get_builtin_declarations_port

    try:
        declarations = await get_builtin_declarations_port().declarations()
    except Exception:  # noqa: BLE001 — seeding must never block boot
        return _builtin_entries()
    entries: list[dict[str, Any]] = []
    for decl in declarations.by_kind("connector"):
        if decl.provisioning != "provisioned":
            continue
        entry = dict(decl.connector_config or _catalog_entry(decl.slug) or {})
        if not entry.get("slug"):
            entry["slug"] = decl.slug
        if entry.get("url") or entry.get("command"):
            entries.append(entry)
    return entries or _builtin_entries()


async def seed_builtin_connectors(
    db: AsyncSession, *, user_id: str, entries: list[dict[str, Any]] | None = None
) -> None:
    """Insert missing built-ins without initiating or fabricating OAuth.

    The slug set comes from the builtin declaration port (packaged manifest
    on OSS, cloud-backed in commercial editions); ``entries`` overrides it for
    tests / callers that already resolved a declaration set.
    """

    existing = set(
        (
            await db.execute(
                select(ConnectorRow.slug).where(ConnectorRow.user_id == user_id)
            )
        ).scalars()
    )
    for entry in entries if entries is not None else await _declared_entries():
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
