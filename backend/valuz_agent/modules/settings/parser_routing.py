"""Parser routing + plugin-config helpers backed by ``valuz_app_setting``.

Three keys live here:

- ``parser.primary_plugin_id`` — the user's chosen main engine. Defaults
  to ``light_local`` when unset.
- ``parser.by_kind`` — optional per-kind overrides (advanced users). When
  the map is missing a kind, the router falls back to the primary plugin
  + capability gate.
- ``parser.fallback_to_local_on_error`` — whether the router demotes to
  ``light_local`` when the chosen plugin throws at runtime. Defaults to
  ``True``.
- ``parser.plugin_configs`` — per-plugin user-supplied config:
  ``{plugin_id: {enabled, secret_ref, options}}``. API keys are stored
  via ``secret_store`` and only the ``secret_ref`` lands here.

Storage shape: the row's ``value_json`` always wraps the value in
``{"value": ...}`` to match ``preferences.py`` convention — this lets us
add metadata later (e.g. ``updated_by``) without another schema bump.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.settings.datastore import SettingsDatastore
from valuz_agent.modules.settings.models import AppSettingRow

logger = logging.getLogger(__name__)

KEY_PRIMARY_PLUGIN_ID = "parser.primary_plugin_id"
KEY_BY_KIND = "parser.by_kind"
KEY_FALLBACK_ON_ERROR = "parser.fallback_to_local_on_error"
KEY_PLUGIN_CONFIGS = "parser.plugin_configs"

DEFAULT_PRIMARY_PLUGIN_ID = "light_local"
DEFAULT_FALLBACK_ON_ERROR = True

# Kinds the router never lets the user route away from local. ``text``
# (md/txt/csv/json/xml) has no OCR signal worth shipping to the cloud;
# cloud APIs reject these or return garbage. Keep this explicit so the
# settings page can render them as locked rows.
LOCKED_LOCAL_KINDS: frozenset[str] = frozenset({"text"})


# These helpers are ASYNC and go through ``SettingsDatastore`` (the datastore
# layer owns all ``valuz_app_setting`` I/O). The async parser routes ``await``
# them directly; ``ParserRouter`` reads a config snapshot resolved once per
# request (see ``ParserRoutingConfig``), so it never touches the DB per parse.


async def _read_json(db: AsyncSession, key: str) -> Any | None:
    row = await SettingsDatastore(db).get_setting(require_current_user_id(), key)
    if row is None:
        return None
    try:
        data = json.loads(row.value_json or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("value")


async def _write_json(db: AsyncSession, key: str, value: Any) -> None:
    await SettingsDatastore(db).upsert_setting(
        require_current_user_id(),
        AppSettingRow(
            key=key,
            value_json=json.dumps({"value": value}),
            updated_at=now_ms(),
        )
    )


# ----- primary plugin id ------------------------------------------------


async def get_primary_plugin_id(db: AsyncSession) -> str:
    raw = await _read_json(db, KEY_PRIMARY_PLUGIN_ID)
    if isinstance(raw, str) and raw:
        return raw
    return DEFAULT_PRIMARY_PLUGIN_ID


async def set_primary_plugin_id(db: AsyncSession, plugin_id: str) -> None:
    cleaned = plugin_id.strip()
    if not cleaned:
        raise ValueError("plugin_id cannot be empty")
    await _write_json(db, KEY_PRIMARY_PLUGIN_ID, cleaned)


# ----- by-kind overrides ------------------------------------------------


async def get_by_kind(db: AsyncSession) -> dict[str, str]:
    raw = await _read_json(db, KEY_BY_KIND)
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and v:
            out[k] = v
    return out


async def set_by_kind(db: AsyncSession, mapping: Mapping[str, str]) -> None:
    # Defensive validation: drop locked kinds the caller may have
    # accidentally included; the router enforces lock anyway but
    # round-tripping a write→read should match.
    cleaned = {
        k: v
        for k, v in mapping.items()
        if isinstance(k, str) and isinstance(v, str) and v and k not in LOCKED_LOCAL_KINDS
    }
    await _write_json(db, KEY_BY_KIND, cleaned)


# ----- runtime fallback flag --------------------------------------------


async def get_fallback_to_local_on_error(db: AsyncSession) -> bool:
    raw = await _read_json(db, KEY_FALLBACK_ON_ERROR)
    if isinstance(raw, bool):
        return raw
    return DEFAULT_FALLBACK_ON_ERROR


async def set_fallback_to_local_on_error(db: AsyncSession, value: bool) -> None:
    await _write_json(db, KEY_FALLBACK_ON_ERROR, bool(value))


# ----- per-plugin configs -----------------------------------------------


async def get_plugin_configs(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Read the full ``{plugin_id: config}`` map.

    Each config has shape ``{enabled: bool, secret_ref: str | None,
    options: dict[str, Any]}``. Returns ``{}`` if unset; callers should
    not rely on every plugin_id having an entry.
    """
    raw = await _read_json(db, KEY_PLUGIN_CONFIGS)
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for plugin_id, cfg in raw.items():
        if not isinstance(plugin_id, str) or not isinstance(cfg, Mapping):
            continue
        out[plugin_id] = {
            "enabled": bool(cfg.get("enabled", False)),
            "secret_ref": (
                cfg.get("secret_ref") if isinstance(cfg.get("secret_ref"), str) else None
            ),
            "options": (dict(cfg["options"]) if isinstance(cfg.get("options"), Mapping) else {}),
        }
    return out


async def get_plugin_config(db: AsyncSession, plugin_id: str) -> dict[str, Any]:
    cfg = (await get_plugin_configs(db)).get(plugin_id)
    if cfg is None:
        return {"enabled": False, "secret_ref": None, "options": {}}
    return cfg


async def update_plugin_config(
    db: AsyncSession,
    plugin_id: str,
    *,
    enabled: bool | None = None,
    secret_ref_change: tuple[str | None] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Patch one plugin's config in place.

    Field-update conventions (chosen to give "leave unchanged" a distinct
    signal from "explicitly clear"):

    - ``enabled=None`` leaves unchanged; ``enabled=True/False`` writes.
    - ``secret_ref_change=None`` leaves unchanged.
      ``secret_ref_change=("ref-abc",)`` sets to ``"ref-abc"``.
      ``secret_ref_change=(None,)`` clears the ref.
    - ``options=None`` leaves unchanged; passing a dict replaces the
      whole options blob (callers merge themselves if they want partial
      patching).

    Returns the post-update view of this plugin's config.
    """
    all_configs = await get_plugin_configs(db)
    cfg = all_configs.get(plugin_id, {"enabled": False, "secret_ref": None, "options": {}})

    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    if secret_ref_change is not None:
        cfg["secret_ref"] = secret_ref_change[0]
    if options is not None:
        cfg["options"] = dict(options)

    all_configs[plugin_id] = cfg
    await _write_json(db, KEY_PLUGIN_CONFIGS, all_configs)
    return cfg


# ----- read-only snapshot for the router --------------------------------


@dataclass(frozen=True)
class ParserRoutingConfig:
    """Immutable snapshot of the routing settings, resolved once per request.

    ``ParserRouter`` holds one of these instead of a DB session so its hot
    per-parse path (``_resolve_plugin`` / ``_config_for``) is pure in-memory —
    no DB access, callable from both the async ``parse`` and the sync
    ``parse_sync`` paths.
    """

    primary_plugin_id: str = DEFAULT_PRIMARY_PLUGIN_ID
    by_kind: dict[str, str] = field(default_factory=dict)
    fallback_to_local_on_error: bool = DEFAULT_FALLBACK_ON_ERROR
    plugin_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def plugin_config(self, plugin_id: str) -> dict[str, Any]:
        cfg = self.plugin_configs.get(plugin_id)
        if cfg is None:
            return {"enabled": False, "secret_ref": None, "options": {}}
        return cfg


DEFAULT_ROUTING_CONFIG = ParserRoutingConfig()


async def load_routing_config(db: AsyncSession) -> ParserRoutingConfig:
    """Resolve the full routing snapshot from settings (one async read each)."""
    return ParserRoutingConfig(
        primary_plugin_id=await get_primary_plugin_id(db),
        by_kind=await get_by_kind(db),
        fallback_to_local_on_error=await get_fallback_to_local_on_error(db),
        plugin_configs=await get_plugin_configs(db),
    )
