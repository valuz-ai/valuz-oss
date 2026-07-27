"""User-level preference helpers — currently surfaces ``default_timezone``
and ``default_locale``, both required by the schedule module (see ADR-010).

The wider ``SettingsService`` is intentionally stubby because the host has
no shared settings UX yet. Rather than implement that whole surface now,
this file exposes the two keys schedules actually depends on as
free-standing helpers backed by the existing
``valuz_app_setting`` key-value table. A future refactor that grows
``SettingsService`` can absorb these helpers without touching call sites
(``get_default_timezone`` etc. become thin wrappers around the service).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.settings.datastore import SettingsDatastore
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.ports.model_defaults import ModelDefaults

logger = logging.getLogger(__name__)

# Single canonical key per preference. Stored as JSON to leave room for
# structured values later (e.g. ``{"value": "Asia/Shanghai", "auto_detected": true}``)
# without another schema bump.
KEY_DEFAULT_TIMEZONE = "schedule.default_timezone"
KEY_DEFAULT_LOCALE = "ui.default_locale"
# Kernel V5+bba3014 ``ModelSettings.effort``. Storage key is the new
# ``model.default_effort`` (5-value enum + null = SDK default). The
# legacy ``model.default_thinking`` (4-value: off / low / medium / high)
# is still readable for back-compat — see ``get_default_effort``.
KEY_DEFAULT_EFFORT = "model.default_effort"
KEY_DEFAULT_THINKING_LEGACY = "model.default_thinking"
KEY_DEFAULT_RUNTIME = "model.default_runtime"
KEY_DEFAULT_PROVIDER_ID = "model.default_provider_id"
KEY_DEFAULT_MODEL = "model.default_model"
KEY_THEME = "ui.theme"
KEY_FONT_SIZE = "ui.font_size"
# Memory system toggles (memory-system-design §11). ``memory.enabled`` is the
# product master switch (gates injection, the foreground tool, and the
# background extractor); ``memory.auto_extract`` gates ONLY the background
# extractor, so a user can keep manual/agent memory while turning off the
# automatic (LLM-spending) review. Both default ON.
KEY_MEMORY_ENABLED = "memory.enabled"
KEY_MEMORY_AUTO_EXTRACT = "memory.auto_extract"
# Global, user-authored guidance appended to the background reviewer prompt
# (memory-system-design §7.4). It refines "what to save/skip" and OVERRIDES the
# default soft heuristics (so e.g. "remember key conclusions" can beat the
# default "skip transcript-derivable facts" rule); the hard rules (secret
# redaction, KB dedup, JSON contract) are not overridable. Empty = disabled.
# Stored as a preference like the toggles; only the background extractor reads
# it (never injected into normal turns). Capped to keep the review prompt bounded.
KEY_MEMORY_CUSTOM_INSTRUCTIONS = "memory.custom_instructions"
MEMORY_CUSTOM_INSTRUCTIONS_MAX_CHARS = 1500
# Local backup (docs/design/client-local-backup.md §6). Config keys are
# user-tunable; the ``last_run`` / ``next_run_at`` pair is runtime state the
# scheduler + service maintain. Structured values (scope / retention /
# last_run) are stored as a JSON string inside the usual ``{"value": ...}``
# envelope — the backup module owns their shape (``modules/backup/schemas``).
KEY_BACKUP_ENABLED = "backup.enabled"
KEY_BACKUP_FREQUENCY = "backup.frequency"
KEY_BACKUP_DESTINATION = "backup.destination"
KEY_BACKUP_SCOPE = "backup.scope"
KEY_BACKUP_RETENTION = "backup.retention"
KEY_BACKUP_LAST_RUN = "backup.last_run"
KEY_BACKUP_NEXT_RUN_AT = "backup.next_run_at"

BACKUP_FREQUENCY_VALUES = ("manual", "every_6h", "daily", "weekly")
FALLBACK_BACKUP_FREQUENCY = "daily"

FALLBACK_TIMEZONE = "UTC"
FALLBACK_LOCALE = "zh-CN"
# Default reasoning-effort budget when no ``model.default_effort`` is
# persisted (fresh install, legacy ``"off"`` clear, or unknown stored
# value). New sessions created without an explicit effort land at
# "high". Mirrors the frontend ``EFFORT_FALLBACK`` so UI ↔ DB ↔ runtime
# all agree on what "no preference set" means — the Composer's old
# "Default" option is gone, so this is the single source of truth for
# the implicit default.
FALLBACK_EFFORT: str = "high"
FALLBACK_RUNTIME = "claude_agent"
FALLBACK_THEME = "light"
FALLBACK_FONT_SIZE = "default"

# Kernel V5+bba3014 5-value enum (mirrors ``src.core.types.EffortLevel``).
# ``None`` is allowed at the API surface and means "no override" — the
# runtime falls through to its SDK default. ``"off"`` from the legacy
# 4-value enum is normalized to ``None`` on read for back-compat.
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
RUNTIME_VALUES = ("claude_agent", "codex", "deepagents")


# ── factory defaults (ext.model_defaults) ────────────────────────────
#
# When the user never explicitly chose, the fallback comes from the
# ``ext.model_defaults`` port instead of the module constants above: OSS
# binds a Settings-backed implementation (env-overridable per build), the
# commercial overlay layers cloud-delivered per-distribution values on top.
# The constants remain as the defensive last line when a port returns a
# value outside the known enums.


async def _factory_defaults(user_id: str | None) -> "ModelDefaults":
    from valuz_agent.ports.extensions import ext

    return await ext.model_defaults.get(user_id)


async def _factory_runtime(user_id: str | None) -> str:
    value = (await _factory_defaults(user_id)).default_runtime
    if value in RUNTIME_VALUES:
        return value
    logger.warning("ignoring unknown factory default_runtime: %r", value)
    return FALLBACK_RUNTIME


async def _factory_effort(user_id: str | None) -> str:
    value = (await _factory_defaults(user_id)).default_effort
    if value in EFFORT_VALUES:
        return value
    logger.warning("ignoring unknown factory default_effort: %r", value)
    return FALLBACK_EFFORT
ALLOWED_THEMES = {"light", "dark", "auto"}
ALLOWED_FONT_SIZES = {"compact", "default", "comfortable"}


# These helpers are ASYNC: every caller runs on the asyncio event loop (route
# handlers, on-loop scheduler tasks, MCP tool handlers) and passes an
# ``AsyncSession`` from ``async_unit_of_work``. ``i18n.t()`` no longer reads
# them directly — the host resolves the locale once (async) and pushes it into
# the i18n cache via ``i18n.set_locale`` (see ``set_default_locale`` below), so
# the sync ``t()`` path is decoupled from the DB.
#
# DB access goes through ``SettingsDatastore`` (the datastore layer owns all
# ``valuz_app_setting`` reads/writes) — these helpers hold only the JSON
# ``{"value": ...}`` (de)serialization + validation, never a raw ``Session``.


async def _read(db: AsyncSession, key: str, user_id: str | None = None) -> str | None:
    if user_id is None:
        raise ValueError("user_id is required")

    row = await SettingsDatastore(db).get_setting(user_id, key)
    if row is None:
        return None
    try:
        data = json.loads(row.value_json or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("value")
    return value if isinstance(value, str) and value else None


async def _write(db: AsyncSession, key: str, value: str, user_id: str | None = None) -> None:
    if user_id is None:
        raise ValueError("user_id is required")
    await SettingsDatastore(db).upsert_setting(
        user_id,
        AppSettingRow(
            key=key,
            value_json=json.dumps({"value": value}),
            updated_at=now_ms(),
        ),
    )


async def get_default_timezone(db: AsyncSession, user_id: str | None = None) -> str:
    """Return the user's configured default timezone, or ``UTC`` if unset.

    Resolution order (first match wins):

    1. ``valuz_app_setting`` row keyed by ``schedule.default_timezone``.
    2. ``FALLBACK_TIMEZONE`` (UTC).

    We deliberately don't attempt OS-level auto-detection here — the
    install/first-run wizard should call ``detect_system_timezone()`` and
    persist the value explicitly, so the runtime path stays a pure DB read.
    """
    return await _read(db, KEY_DEFAULT_TIMEZONE, user_id=user_id) or FALLBACK_TIMEZONE


async def get_effective_default_timezone(db: AsyncSession, user_id: str | None = None) -> str:
    """Create-time default timezone for schedules: configured value, else the
    *detected* OS timezone, else UTC.

    Distinct from ``get_default_timezone`` (a pure DB read used for settings
    display, which falls straight back to UTC). When a user has never set a
    default, scheduling in UTC silently fires automations at the wrong local
    wall-clock time; resolving to the detected system tz here means a chat- or
    MCP-created automation lands on the user's local clock by default. The tz
    is always *persisted* on the row (see ``AutomationService._apply_trigger``)
    so it stays visible/editable rather than an invisible UTC fallback.
    """
    return await _read(db, KEY_DEFAULT_TIMEZONE, user_id=user_id) or detect_system_timezone()


async def set_default_timezone(db: AsyncSession, value: str, user_id: str | None = None) -> None:
    """Persist the user's default timezone preference.

    The IANA name is validated by ``zoneinfo.ZoneInfo`` before write —
    invalid values raise so we never end up with a typo silently breaking
    every future ``next_run_at`` calculation.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value!r}") from exc
    await _write(db, KEY_DEFAULT_TIMEZONE, value, user_id=user_id)


async def get_default_locale(db: AsyncSession, user_id: str | None = None) -> str:
    return await _read(db, KEY_DEFAULT_LOCALE, user_id=user_id) or FALLBACK_LOCALE


async def set_default_locale(db: AsyncSession, value: str, user_id: str | None = None) -> None:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("locale cannot be empty")
    await _write(db, KEY_DEFAULT_LOCALE, cleaned, user_id=user_id)
    # Push the new locale into the i18n in-memory cache so the sync ``t()``
    # path picks it up immediately without any DB read.
    from valuz_agent.i18n import set_locale

    set_locale(cleaned)


async def get_default_effort(db: AsyncSession, user_id: str | None = None) -> str:
    """Return the user's configured default reasoning-effort budget.

    Always one of ``low`` / ``medium`` / ``high`` / ``xhigh`` / ``max``.
    Used by ``create_session`` to fill ``effort`` when the caller
    didn't pass one explicitly. The Composer's old "Default" sentinel
    is gone: unset / cleared / unknown stored values all collapse to
    ``FALLBACK_EFFORT`` ("high") so UI ↔ DB ↔ runtime stay in sync.

    Back-compat: reads the legacy ``model.default_thinking`` key when
    the new ``model.default_effort`` is unset. The legacy 4-value enum
    (``off`` / ``low`` / ``medium`` / ``high``) maps to the new enum:
    ``off`` → fallback; the rest pass through unchanged.
    """
    raw = await _read(db, KEY_DEFAULT_EFFORT, user_id=user_id)
    if raw is None:
        # Legacy key fallback for one-time graceful upgrade. ``"off"``
        # was the old "no override" sentinel and now resolves to the
        # factory default (matches what every other unset / corrupt
        # path returns below).
        legacy = await _read(db, KEY_DEFAULT_THINKING_LEGACY, user_id=user_id)
        if legacy in (None, "", "off"):
            return await _factory_effort(user_id)
        raw = legacy
    if raw in EFFORT_VALUES:
        return raw
    # Unknown stored value (e.g. legacy ``xmax`` typo) — defensive
    # fallback so a single corrupt row doesn't 500 the settings page.
    logger.warning("ignoring unknown default_effort value: %r", raw)
    return await _factory_effort(user_id)


async def set_default_effort(
    db: AsyncSession, value: str | None, user_id: str | None = None
) -> None:
    """Persist the user's default effort budget.

    ``None`` (or empty string) was the legacy "clear override" path —
    we now treat it as "reset to FALLBACK_EFFORT" and persist the
    fallback verbatim. There is no clear-back-to-null path anymore:
    the Composer's old Default sentinel is gone and every dropdown
    pick is a concrete level. Unknown values raise ``ValueError`` so
    the route layer surfaces a 400 to the UI.
    """
    if value is None or value.strip() == "":
        await _write(db, KEY_DEFAULT_EFFORT, FALLBACK_EFFORT, user_id=user_id)
        return
    cleaned = value.strip().lower()
    if cleaned not in EFFORT_VALUES:
        raise ValueError(f"default effort must be one of {EFFORT_VALUES}, got {value!r}")
    await _write(db, KEY_DEFAULT_EFFORT, cleaned, user_id=user_id)


async def get_default_runtime(db: AsyncSession, user_id: str | None = None) -> str:
    """Return the user's configured default runtime id.

    Unset → the factory default from ``ext.model_defaults`` (Settings env /
    distribution override / cloud-delivered, depending on the bound port)."""
    stored = await _read(db, KEY_DEFAULT_RUNTIME, user_id=user_id)
    if stored:
        return stored
    return await _factory_runtime(user_id)


async def set_default_runtime(db: AsyncSession, value: str, user_id: str | None = None) -> None:
    cleaned = value.strip()
    if cleaned not in RUNTIME_VALUES:
        raise ValueError(f"runtime must be one of {RUNTIME_VALUES}, got {value!r}")
    await _write(db, KEY_DEFAULT_RUNTIME, cleaned, user_id=user_id)


# ── default model selection ──────────────────────────────────────────
#
# ``default_provider_id`` + ``default_model`` together pin the global
# default model the user picked in Settings → Default. They're stored
# as their own keys (not in ``valuz_provider.is_default`` /
# ``valuz_provider.default_model``) because:
#  - the user can pick the same model id under different providers
#    (e.g. both reportify-pro and openai expose gpt-4-style ids); we
#    can't disambiguate from model id alone, so the provider id has to
#    persist next to it.
#  - ``provider.is_default`` is a per-row flag that doesn't compose with
#    runtime — "default for claude_agent" vs "default for deepagents"
#    would need two flags. App-setting keys keep the surface flat.
# Both keys can be cleared (empty string) — that's the post-switch
# state when the user changes runtime and the previous default isn't
# compatible with the new one.


async def get_default_provider_id(db: AsyncSession, user_id: str | None = None) -> str | None:
    stored = await _read(db, KEY_DEFAULT_PROVIDER_ID, user_id=user_id)
    if stored:
        return stored
    return (await _factory_defaults(user_id)).default_provider_id


async def set_default_provider_id(
    db: AsyncSession, value: str | None, user_id: str | None = None
) -> None:
    await _write(db, KEY_DEFAULT_PROVIDER_ID, value or "", user_id=user_id)


async def get_default_model(db: AsyncSession, user_id: str | None = None) -> str | None:
    stored = await _read(db, KEY_DEFAULT_MODEL, user_id=user_id)
    if stored:
        return stored
    return (await _factory_defaults(user_id)).default_model or None


async def set_default_model(
    db: AsyncSession, value: str | None, user_id: str | None = None
) -> None:
    await _write(db, KEY_DEFAULT_MODEL, value or "", user_id=user_id)


async def get_theme(db: AsyncSession, user_id: str | None = None) -> str:
    return await _read(db, KEY_THEME, user_id=user_id) or FALLBACK_THEME


async def set_theme(db: AsyncSession, value: str, user_id: str | None = None) -> None:
    if value not in ALLOWED_THEMES:
        raise ValueError(f"Invalid theme: {value!r}. Allowed: {sorted(ALLOWED_THEMES)}")
    await _write(db, KEY_THEME, value, user_id=user_id)


async def get_font_size(db: AsyncSession, user_id: str | None = None) -> str:
    return await _read(db, KEY_FONT_SIZE, user_id=user_id) or FALLBACK_FONT_SIZE


async def set_font_size(db: AsyncSession, value: str, user_id: str | None = None) -> None:
    if value not in ALLOWED_FONT_SIZES:
        raise ValueError(f"Invalid font_size: {value!r}. Allowed: {sorted(ALLOWED_FONT_SIZES)}")
    await _write(db, KEY_FONT_SIZE, value, user_id=user_id)


async def _read_bool(
    db: AsyncSession, key: str, default: bool, user_id: str | None = None
) -> bool:
    raw = await _read(db, key, user_id=user_id)
    if raw is None:
        return default
    return raw == "true"


async def get_memory_enabled(db: AsyncSession, user_id: str | None = None) -> bool:
    """Memory master switch (default ON). Gates injection + tool + extractor."""
    return await _read_bool(db, KEY_MEMORY_ENABLED, True, user_id=user_id)


async def set_memory_enabled(
    db: AsyncSession, value: bool, user_id: str | None = None
) -> None:
    await _write(db, KEY_MEMORY_ENABLED, "true" if value else "false", user_id=user_id)


async def get_memory_auto_extract(db: AsyncSession, user_id: str | None = None) -> bool:
    """Background-extractor switch (default ON). Independent of the foreground
    tool: turning this off keeps manual/agent memory but stops the automatic
    (LLM-spending) review."""
    return await _read_bool(db, KEY_MEMORY_AUTO_EXTRACT, True, user_id=user_id)


async def set_memory_auto_extract(
    db: AsyncSession, value: bool, user_id: str | None = None
) -> None:
    await _write(db, KEY_MEMORY_AUTO_EXTRACT, "true" if value else "false", user_id=user_id)


async def get_memory_custom_instructions(db: AsyncSession, user_id: str | None = None) -> str:
    """Global reviewer guidance (default empty = off). See
    ``KEY_MEMORY_CUSTOM_INSTRUCTIONS``."""
    return (await _read(db, KEY_MEMORY_CUSTOM_INSTRUCTIONS, user_id=user_id)) or ""


async def set_memory_custom_instructions(
    db: AsyncSession, value: str, user_id: str | None = None
) -> None:
    """Persist global reviewer guidance, trimmed and hard-capped to
    ``MEMORY_CUSTOM_INSTRUCTIONS_MAX_CHARS`` so the review prompt stays bounded."""
    await _write(
        db,
        KEY_MEMORY_CUSTOM_INSTRUCTIONS,
        value.strip()[:MEMORY_CUSTOM_INSTRUCTIONS_MAX_CHARS],
        user_id=user_id,
    )




# ── local backup preferences ─────────────────────────────────────────


async def get_backup_enabled(db: AsyncSession, user_id: str | None = None) -> bool:
    """Backup master switch (default OFF — the user opts in from Settings)."""
    return await _read_bool(db, KEY_BACKUP_ENABLED, False, user_id=user_id)


async def set_backup_enabled(db: AsyncSession, value: bool, user_id: str | None = None) -> None:
    await _write(db, KEY_BACKUP_ENABLED, "true" if value else "false", user_id=user_id)


async def get_backup_frequency(db: AsyncSession, user_id: str | None = None) -> str:
    raw = await _read(db, KEY_BACKUP_FREQUENCY, user_id=user_id)
    return raw if raw in BACKUP_FREQUENCY_VALUES else FALLBACK_BACKUP_FREQUENCY


async def set_backup_frequency(db: AsyncSession, value: str, user_id: str | None = None) -> None:
    if value not in BACKUP_FREQUENCY_VALUES:
        raise ValueError(
            f"backup frequency must be one of {BACKUP_FREQUENCY_VALUES}, got {value!r}"
        )
    await _write(db, KEY_BACKUP_FREQUENCY, value, user_id=user_id)


async def get_backup_destination(db: AsyncSession, user_id: str | None = None) -> str | None:
    """User-chosen destination root, or None → the FsRegistry default."""
    return await _read(db, KEY_BACKUP_DESTINATION, user_id=user_id) or None


async def set_backup_destination(
    db: AsyncSession, value: str, user_id: str | None = None
) -> None:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("backup destination cannot be empty")
    await _write(db, KEY_BACKUP_DESTINATION, cleaned, user_id=user_id)


async def _read_json(db: AsyncSession, key: str, user_id: str | None = None) -> dict | None:
    raw = await _read(db, key, user_id=user_id)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def get_backup_scope(db: AsyncSession, user_id: str | None = None) -> dict | None:
    return await _read_json(db, KEY_BACKUP_SCOPE, user_id=user_id)


async def set_backup_scope(db: AsyncSession, value: dict, user_id: str | None = None) -> None:
    await _write(db, KEY_BACKUP_SCOPE, json.dumps(value), user_id=user_id)


async def get_backup_retention(db: AsyncSession, user_id: str | None = None) -> dict | None:
    return await _read_json(db, KEY_BACKUP_RETENTION, user_id=user_id)


async def set_backup_retention(db: AsyncSession, value: dict, user_id: str | None = None) -> None:
    await _write(db, KEY_BACKUP_RETENTION, json.dumps(value), user_id=user_id)


async def get_backup_last_run(db: AsyncSession, user_id: str | None = None) -> dict | None:
    return await _read_json(db, KEY_BACKUP_LAST_RUN, user_id=user_id)


async def set_backup_last_run(db: AsyncSession, value: dict, user_id: str | None = None) -> None:
    await _write(db, KEY_BACKUP_LAST_RUN, json.dumps(value), user_id=user_id)


async def get_backup_next_run_at(db: AsyncSession, user_id: str | None = None) -> int | None:
    raw = await _read(db, KEY_BACKUP_NEXT_RUN_AT, user_id=user_id)
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def set_backup_next_run_at(
    db: AsyncSession, value: int | None, user_id: str | None = None
) -> None:
    await _write(db, KEY_BACKUP_NEXT_RUN_AT, str(value) if value else "", user_id=user_id)


def detect_system_timezone() -> str:
    """Best-effort detection of the user's local timezone.

    Tries, in order:

    - ``/etc/localtime`` symlink target on POSIX (resolves to e.g.
      ``/usr/share/zoneinfo/Asia/Shanghai``).
    - ``TZ`` env var.
    - ``datetime.now().astimezone().tzname()`` as a last resort
      (not always IANA, but at least non-empty).

    Returns ``"UTC"`` if nothing usable is found. The caller is expected
    to surface the detected value to the user for confirmation, not blindly
    persist it — auto-detection is a UX nicety, not a contract.
    """
    import os
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        # /etc/localtime does not exist on Windows. Try TZ env var first,
        # then fall back to datetime-based detection.
        tz_env = os.environ.get("TZ", "").strip()
        if tz_env:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(tz_env)
                return tz_env
            except Exception:
                pass
        try:
            name = datetime.now().astimezone().tzname()
            if name:
                return name
        except Exception:
            pass
        return FALLBACK_TIMEZONE

    local = Path("/etc/localtime")
    if local.is_symlink():
        target = os.readlink(local)
        # Typical: /var/db/timezone/zoneinfo/Asia/Shanghai or
        #          /usr/share/zoneinfo/Asia/Shanghai
        marker = "zoneinfo/"
        idx = target.rfind(marker)
        if idx >= 0:
            candidate = target[idx + len(marker) :]
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(candidate)
                return candidate
            except Exception:
                pass

    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz_env)
            return tz_env
        except Exception:
            pass

    try:
        name = datetime.now().astimezone().tzname()
        if name:
            return name
    except Exception:
        pass

    return FALLBACK_TIMEZONE
