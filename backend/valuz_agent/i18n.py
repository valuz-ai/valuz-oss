"""Minimal i18n translation loader for the Python backend.

Usage:
    from valuz_agent.i18n import t

    # Resolves against the user's default_locale (read from DB on first use).
    t("common.save")

    # Resolve against a specific locale (preferred call site for code
    # paths that already know the user's locale — request handlers,
    # schedule runs, etc).
    t("common.save", locale="zh-CN")

    # With interpolation params.
    t("common.greeting", params={"name": "Alice"})

Each locale's flattened translation table is loaded lazily on first
use and cached process-wide. ``set_default_locale_provider`` lets the
host wire in a DB-backed resolver (e.g. ``preferences.get_default_locale``)
so callers that don't pass an explicit ``locale=`` still see the user's
configured language.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from valuz_agent.generated.i18n_keys import I18nKey  # noqa: TCH004

_FALLBACK_LOCALE = "en-US"

# Lazily-loaded, process-wide cache. Key = locale code, value = flat key→string.
_loaded: dict[str, dict[str, str]] = {}

# Effective default locale, pushed into memory by the host. ``t()`` reads this
# directly so the sync translation path NEVER touches the DB — the host resolves
# the locale asynchronously (at startup and whenever the user changes it via
# ``preferences.set_default_locale``) and pushes it here through ``set_locale``.
# This is what lets the host run fully async: no sync DB session behind ``t()``.
_pushed_locale: str | None = None

# Back-compat lazy resolver (used by ``init_i18n`` constant providers + tests).
# New host code should push via ``set_locale`` instead of wiring a DB provider.
_default_locale_provider: Callable[[], str] | None = None


def _repo_root() -> Path:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "Makefile").is_file() and (p / "frontend").is_dir():
            return p
        p = p.parent
    raise RuntimeError("Cannot locate repo root")


def _locales_dir() -> Path:
    """Locate the shared i18n locale catalogs (``i18n/locales/*.json``).

    Under a PyInstaller-frozen ``valuz-server`` there is no source repo to walk
    up to: the catalogs are bundled under ``_internal/i18n/locales`` (see
    ``backend/scripts/valuz_agent.spec`` ``datas``), and ``sys._MEIPASS`` points
    at that ``_internal`` dir. In a dev checkout, resolve relative to the repo
    root. Without this, any backend ``t()`` in the packaged app raised
    ``Cannot locate repo root`` and 500'd (e.g. onboarding's example project).
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "i18n" / "locales"  # type: ignore[attr-defined]
    return _repo_root() / "i18n" / "locales"


def _flatten(obj: object, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(obj, dict):
        return result
    for key, value in obj.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, full))
        elif isinstance(value, str):
            result[full] = value
    return result


def _load(locale: str) -> dict[str, str]:
    if locale in _loaded:
        return _loaded[locale]
    path = _locales_dir() / f"{locale}.json"
    if not path.is_file():
        # Unknown locale → empty table; resolution will fall back to
        # ``_FALLBACK_LOCALE`` and then to the key itself.
        _loaded[locale] = {}
        return _loaded[locale]
    _loaded[locale] = _flatten(json.loads(path.read_text(encoding="utf-8")))
    return _loaded[locale]


def set_locale(value: str | None) -> None:
    """Push the effective default locale into the in-memory cache.

    The host calls this after an **async** DB read at startup, and again
    whenever the user changes locale (``preferences.set_default_locale``).
    ``t()`` then reads this value with zero DB access — the sync translation
    path is decoupled from the database, which is what allows the host to drop
    the synchronous engine entirely.

    ``None`` / empty resets to the fallback.
    """
    global _pushed_locale  # noqa: PLW0603
    _pushed_locale = value or None


def set_default_locale_provider(provider: Callable[[], str] | None) -> None:
    """Back-compat: register a lazy locale resolver.

    Retained for ``init_i18n`` constant providers and tests. New host code
    should push the resolved locale via ``set_locale`` instead of wiring a
    DB-backed provider (which would reintroduce a sync DB read behind ``t()``).
    A pushed locale takes precedence over this provider.
    """
    global _default_locale_provider  # noqa: PLW0603
    _default_locale_provider = provider


def clear_locale_cache() -> None:
    """Back-compat no-op-ish: drop the pushed locale so the next ``t()`` falls
    back to the lazy provider / fallback. Prefer ``set_locale(new_value)`` to
    push the new locale directly after a change."""
    global _pushed_locale  # noqa: PLW0603
    _pushed_locale = None


def _current_locale() -> str:
    if _pushed_locale is not None:
        return _pushed_locale
    if _default_locale_provider is not None:
        try:
            return _default_locale_provider() or _FALLBACK_LOCALE
        except Exception:
            # Provider failures must never break ``t()``.
            return _FALLBACK_LOCALE
    return _FALLBACK_LOCALE


def _interpolate(template: str, params: dict[str, str | int | float] | None) -> str:
    if not params:
        return template
    for name, value in params.items():
        template = template.replace(f"{{{name}}}", str(value))
    return template


def _resolve(key: str, locale: str) -> str:
    table = _load(locale)
    if key in table:
        return table[key]
    if locale != _FALLBACK_LOCALE:
        fallback_table = _load(_FALLBACK_LOCALE)
        if key in fallback_table:
            return fallback_table[key]
    return key


def t(
    key: I18nKey,
    fallback: str | dict[str, str | int | float] | None = None,
    params: dict[str, str | int | float] | None = None,
    *,
    locale: str | None = None,
) -> str:
    """Resolve an i18n key to a string in the given locale.

    Args:
        key: The dotted i18n key (validated by ``I18nKey`` Literal).
        fallback: Either a plain-string fallback used when the key
            is missing in both the target and fallback locale, OR a
            dict of interpolation params (back-compat shape — kept
            because legacy call sites pass params here positionally).
        params: Interpolation params. Preferred over the dict form of
            ``fallback`` for new code.
        locale: Resolve against this locale instead of the user's
            default. Use this when the caller already knows the locale
            (request middleware, schedule runs, etc).
    """
    resolved_locale = locale or _current_locale()

    # Back-compat: ``t(key, {"name": "Alice"})`` treats the dict as params.
    if isinstance(fallback, dict):
        return _interpolate(_resolve(key, resolved_locale), fallback)

    raw = _resolve(key, resolved_locale)
    resolved = raw if raw != key else (fallback if fallback is not None else key)
    return _interpolate(resolved, params)


def register_locale_namespace(
    locale: str,
    namespace: str,
    data: dict[str, object],
) -> None:
    """Merge external translations into the i18n cache.

    Called by overlays at startup to inject their own keys. ``namespace``
    is a dot-prefix (e.g. ``"commercial"``) used only for documentation —
    the actual keys come from flattening ``data``.

    Example::

        register_locale_namespace("zh-CN", "commercial", {
            "commercial": {"license": {"title": "许可证"}}
        })
    """
    flat = _flatten(data)
    existing = _load(locale)
    existing.update(flat)


def get_locale() -> str:
    """Return the current effective default locale (provider result or fallback)."""
    return _current_locale()


# ── Back-compat shim ─────────────────────────────────────────────────
# Some early call sites used ``init_i18n({"locale": "zh-CN"})`` to set
# a global locale. Keep that surface working by registering a constant
# provider — but new code should prefer ``set_default_locale_provider``
# (DB-backed) or pass ``locale=`` per call.


def init_i18n(config: dict[str, str]) -> None:
    locale = config.get("locale") or _FALLBACK_LOCALE
    set_default_locale_provider(lambda: locale)
