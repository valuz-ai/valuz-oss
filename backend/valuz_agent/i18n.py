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
from contextvars import ContextVar, Token
from pathlib import Path

from valuz_agent.generated.i18n_keys import I18nKey  # noqa: TCH004

# The locales the product ships, most-preferred first. Anything a client asks
# for outside this set resolves to ``SUPPORTED_LOCALES[0]``.
SUPPORTED_LOCALES: tuple[str, ...] = ("zh-CN", "en-US")

# Locale for a code path that knows nothing: no request, no pushed locale, no
# provider. NOTE this deliberately differs from
# ``modules.settings.preferences.FALLBACK_LOCALE`` ("zh-CN", the locale a user
# with no stored preference is reported as having). Reconciling the two would
# change the default language of everything rendered outside a request —
# scheduler runs, notifications, and the agent-pack instructions that are
# rendered once and persisted onto the agent row — so it is a separate product
# decision, not part of making requests honour their own language.
_FALLBACK_LOCALE = "en-US"

# Locale of the request being served, bound by the HTTP layer from
# ``Accept-Language`` (see ``api.middleware.locale``). It takes precedence over
# the process-wide pushed locale: one backend process serves many users (cloud
# webui) and, on the desktop, the pushed locale is only refreshed at startup —
# which the commercial build skips entirely
# (``VALUZ_INITIALIZE_USER_CONTENT_ON_STARTUP=false``), so the client's own
# header is the only reliable statement of what language to answer in.
_request_locale: ContextVar[str | None] = ContextVar("valuz_request_locale", default=None)

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


def _locales_dir() -> Path:
    """Locate the shared i18n locale catalogs (``i18n/locales/*.json``).

    ``i18n.py`` lives at ``<root>/backend/valuz_agent/i18n.py`` and the catalogs
    at ``<root>/i18n/locales`` — a FIXED relative position, three levels up
    (file → ``valuz_agent`` → ``backend`` → ``<root>``). Resolving straight off
    ``__file__`` works for a source checkout AND for the backend run as plain
    Python (e.g. in a container), with no repo-marker or cwd assumptions.

    A PyInstaller-frozen ``valuz-server`` has no such source tree — the catalogs
    are bundled under ``_internal/i18n/locales`` (see
    ``backend/scripts/valuz_agent.spec`` ``datas``) and ``sys._MEIPASS`` points
    at that ``_internal`` dir.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "i18n" / "locales"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2] / "i18n" / "locales"


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


def parse_accept_language(header: str | None) -> str:
    """Pick the best supported locale from an ``Accept-Language`` header.

    Returns one of :data:`SUPPORTED_LOCALES`, defaulting to the first entry
    for a missing or unsupported header — the same default the clients
    themselves use. q-values are ignored: clients send a single token, so
    first match wins.
    """
    default = SUPPORTED_LOCALES[0]
    if not header:
        return default
    for raw in header.split(","):
        tag = raw.split(";")[0].strip()
        if tag in SUPPORTED_LOCALES:
            return tag
        prefix = tag.split("-")[0].lower()  # "en" → "en-US"
        for supported in SUPPORTED_LOCALES:
            if supported.split("-")[0].lower() == prefix:
                return supported
    return default


def set_request_locale(value: str | None) -> Token[str | None]:
    """Bind the locale of the request being served. Returns the token to hand
    back to :func:`reset_request_locale` when the request finishes."""
    return _request_locale.set(value or None)


def reset_request_locale(token: Token[str | None]) -> None:
    _request_locale.reset(token)


def get_request_locale() -> str | None:
    """The locale bound for this request, if any. ``None`` outside a request."""
    return _request_locale.get()


def _current_locale() -> str:
    request_locale = _request_locale.get()
    if request_locale is not None:
        return request_locale
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
