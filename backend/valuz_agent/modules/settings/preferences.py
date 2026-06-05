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


async def _read(db: AsyncSession, key: str) -> str | None:
    row = await SettingsDatastore(db).get_setting(key)
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


async def _write(db: AsyncSession, key: str, value: str) -> None:
    await SettingsDatastore(db).upsert_setting(
        AppSettingRow(
            key=key,
            value_json=json.dumps({"value": value}),
            updated_at=now_ms(),
        )
    )


async def get_default_timezone(db: AsyncSession) -> str:
    """Return the user's configured default timezone, or ``UTC`` if unset.

    Resolution order (first match wins):

    1. ``valuz_app_setting`` row keyed by ``schedule.default_timezone``.
    2. ``FALLBACK_TIMEZONE`` (UTC).

    We deliberately don't attempt OS-level auto-detection here — the
    install/first-run wizard should call ``detect_system_timezone()`` and
    persist the value explicitly, so the runtime path stays a pure DB read.
    """
    return await _read(db, KEY_DEFAULT_TIMEZONE) or FALLBACK_TIMEZONE


async def get_effective_default_timezone(db: AsyncSession) -> str:
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
    return await _read(db, KEY_DEFAULT_TIMEZONE) or detect_system_timezone()


async def set_default_timezone(db: AsyncSession, value: str) -> None:
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
    await _write(db, KEY_DEFAULT_TIMEZONE, value)


async def get_default_locale(db: AsyncSession) -> str:
    return await _read(db, KEY_DEFAULT_LOCALE) or FALLBACK_LOCALE


async def set_default_locale(db: AsyncSession, value: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("locale cannot be empty")
    await _write(db, KEY_DEFAULT_LOCALE, cleaned)
    # Push the new locale into the i18n in-memory cache so the sync ``t()``
    # path picks it up immediately without any DB read.
    from valuz_agent.i18n import set_locale

    set_locale(cleaned)


async def get_default_effort(db: AsyncSession) -> str:
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
    raw = await _read(db, KEY_DEFAULT_EFFORT)
    if raw is None:
        # Legacy key fallback for one-time graceful upgrade. ``"off"``
        # was the old "no override" sentinel and now resolves to the
        # explicit fallback (matches what every other unset / corrupt
        # path returns below).
        legacy = await _read(db, KEY_DEFAULT_THINKING_LEGACY)
        if legacy in (None, "", "off"):
            return FALLBACK_EFFORT
        raw = legacy
    if raw in EFFORT_VALUES:
        return raw
    # Unknown stored value (e.g. legacy ``xmax`` typo) — defensive
    # fallback so a single corrupt row doesn't 500 the settings page.
    logger.warning("ignoring unknown default_effort value: %r", raw)
    return FALLBACK_EFFORT


async def set_default_effort(db: AsyncSession, value: str | None) -> None:
    """Persist the user's default effort budget.

    ``None`` (or empty string) was the legacy "clear override" path —
    we now treat it as "reset to FALLBACK_EFFORT" and persist the
    fallback verbatim. There is no clear-back-to-null path anymore:
    the Composer's old Default sentinel is gone and every dropdown
    pick is a concrete level. Unknown values raise ``ValueError`` so
    the route layer surfaces a 400 to the UI.
    """
    if value is None or value.strip() == "":
        await _write(db, KEY_DEFAULT_EFFORT, FALLBACK_EFFORT)
        return
    cleaned = value.strip().lower()
    if cleaned not in EFFORT_VALUES:
        raise ValueError(f"default effort must be one of {EFFORT_VALUES}, got {value!r}")
    await _write(db, KEY_DEFAULT_EFFORT, cleaned)


async def get_default_runtime(db: AsyncSession) -> str:
    """Return the user's configured default runtime id."""
    return await _read(db, KEY_DEFAULT_RUNTIME) or FALLBACK_RUNTIME


async def set_default_runtime(db: AsyncSession, value: str) -> None:
    cleaned = value.strip()
    if cleaned not in RUNTIME_VALUES:
        raise ValueError(f"runtime must be one of {RUNTIME_VALUES}, got {value!r}")
    await _write(db, KEY_DEFAULT_RUNTIME, cleaned)


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


async def get_default_provider_id(db: AsyncSession) -> str | None:
    return await _read(db, KEY_DEFAULT_PROVIDER_ID) or None


async def set_default_provider_id(db: AsyncSession, value: str | None) -> None:
    await _write(db, KEY_DEFAULT_PROVIDER_ID, value or "")


async def get_default_model(db: AsyncSession) -> str | None:
    return await _read(db, KEY_DEFAULT_MODEL) or None


async def set_default_model(db: AsyncSession, value: str | None) -> None:
    await _write(db, KEY_DEFAULT_MODEL, value or "")


async def get_theme(db: AsyncSession) -> str:
    return await _read(db, KEY_THEME) or FALLBACK_THEME


async def set_theme(db: AsyncSession, value: str) -> None:
    if value not in ALLOWED_THEMES:
        raise ValueError(f"Invalid theme: {value!r}. Allowed: {sorted(ALLOWED_THEMES)}")
    await _write(db, KEY_THEME, value)


async def get_font_size(db: AsyncSession) -> str:
    return await _read(db, KEY_FONT_SIZE) or FALLBACK_FONT_SIZE


async def set_font_size(db: AsyncSession, value: str) -> None:
    if value not in ALLOWED_FONT_SIZES:
        raise ValueError(f"Invalid font_size: {value!r}. Allowed: {sorted(ALLOWED_FONT_SIZES)}")
    await _write(db, KEY_FONT_SIZE, value)


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
    from pathlib import Path

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
