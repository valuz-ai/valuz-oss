"""Boot-time sync of packaged builtin plugins.

The builtin declaration port names the plugins this install treats as builtin
(``kind: plugin``, ``provisioning: provisioned``); a packaged declaration's
``asset`` points into ``resources/`` (e.g. ``bundled_plugins/office``). Each
one installs through the ordinary plugin pipeline with ``builtin=True`` —
``source="builtin"``, ``deletable=False``, member skills landing in the
read-only official root with ``.bundled-version`` convergence (design D3 /
§6.5.4 in the commercial repo's builtin-resources design).

Semantics honored here:

- **D6** — a builtin plugin the user disabled stays disabled: the install
  path preserves ``enabled`` on refresh, and this sync never flips it.
- **create-only conservatism** — a user plugin that already claimed the name
  is left alone (logged), never clobbered.
- **idempotence** — an unchanged pass re-runs the content-hash comparison and
  touches nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _resources_root() -> Path:
    return Path(__file__).resolve().parent.parent / "resources"


async def sync_bundled_builtin_plugins(user_id: str) -> list[str]:
    """Install/refresh every packaged builtin plugin; returns synced names."""
    from valuz_agent.api.deps import get_skill_service_for_user
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.plugins.datastore import PluginDatastore
    from valuz_agent.modules.plugins.errors import PluginConflict
    from valuz_agent.modules.plugins.service import PluginService
    from valuz_agent.ports.builtin_declaration import get_builtin_declarations_port

    try:
        declarations = await get_builtin_declarations_port().declarations()
    except Exception:  # noqa: BLE001 — a declaration failure must not block boot
        logger.exception("builtin declaration read failed; skipping plugin sync")
        return []

    targets: list[tuple[str, Path]] = []
    for decl in declarations.by_kind("plugin"):
        if decl.provisioning != "provisioned" or not decl.asset:
            continue
        src = _resources_root() / decl.asset
        if not src.is_dir():
            logger.warning("bundled builtin plugin asset missing: %s", src)
            continue
        targets.append((decl.slug, src))
    if not targets:
        return []

    synced: list[str] = []
    async for skills in get_skill_service_for_user(user_id):
        async with async_unit_of_work() as db:
            connector_svc = ConnectorService(ConnectorDatastore(db))
            service = PluginService(
                datastore=PluginDatastore(db),
                skill_service=skills,
                connector_service=connector_svc,
            )
            for slug, src in targets:
                try:
                    result = await service.install(user_id, path=str(src), builtin=True)
                    if result.status != "already_installed":
                        synced.append(slug)
                        logger.info("synced builtin plugin %s (%s)", slug, result.status)
                except PluginConflict:
                    logger.warning(
                        "builtin plugin %s: name taken by a user install; left alone", slug
                    )
                except Exception:  # noqa: BLE001 — one bad plugin must not sink boot
                    logger.exception("failed to sync builtin plugin %s", slug)
    if synced:
        try:
            from valuz_agent.modules.skills.service import reindex_official_skills

            await reindex_official_skills(user_id)
        except Exception:  # noqa: BLE001 — the boot scan backstops it
            logger.exception("reindex after builtin plugin sync failed")
    return synced


__all__ = ["sync_bundled_builtin_plugins"]
