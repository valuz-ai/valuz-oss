"""Plugin registry — the inventory of parser plugins shipped with this build.

Built-in plugins live under ``backend/plugins/parser/<id>/`` (one
sub-package per plugin id). ``build_default_registry`` discovers them
by convention: ``pkgutil.iter_modules(plugins.parser.__path__)``
enumerates every sub-package, imports it, and calls its
``make_plugin(scheduler=)`` factory. Sub-packages missing the factory
or raising during construction are logged and skipped — the registry
never aborts on a single broken plugin.

After in-tree discovery, the registry layers in any out-of-tree plugins
installed via the ``valuz.parser_plugins`` entry-point group (Phase 2 —
separate pypi packages). Built-in ids win on collision; conflicting
third-party plugins are skipped with a warning.

Import-order note: the cloud plugins need ``modules.parser.polling`` at
import time; ``modules.parser.__init__`` in turn imports this registry.
To break the cycle, plugin sub-packages are imported lazily inside
``_load_builtin`` rather than at module load.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterator
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING

from valuz_agent.ports.parser_plugin import ParserPlugin, ParserPluginDescriptor

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "valuz.parser_plugins"
_BUILTIN_PACKAGE = "plugins.parser"

# Stable plugin id constants — exported for type-checking and string-key
# usage. The values are duplicated from the plugins' own descriptors so
# external callers can import them without triggering the plugin's
# heavy ``polling``/SDK imports.
LIGHT_LOCAL_PLUGIN_ID = "light_local"
MINERU_PLUGIN_ID = "mineru"
PADDLEOCR_PLUGIN_ID = "paddleocr"
VALUZ_OCR_PLUGIN_ID = "valuz_ocr"

if TYPE_CHECKING:  # pragma: no cover
    from valuz_agent.modules.parser.polling import PollingScheduler


class UnknownPluginError(KeyError):
    """Raised when a settings row references a plugin id this build does
    not ship. The router treats this as "fall back to light_local"."""


class ParserPluginRegistry:
    def __init__(self, plugins: list[ParserPlugin]) -> None:
        self._plugins: dict[str, ParserPlugin] = {}
        for plugin in plugins:
            pid = plugin.descriptor.id
            if pid in self._plugins:
                raise ValueError(f"duplicate parser plugin id: {pid}")
            self._plugins[pid] = plugin

    def get(self, plugin_id: str) -> ParserPlugin:
        try:
            return self._plugins[plugin_id]
        except KeyError as exc:
            raise UnknownPluginError(plugin_id) from exc

    def try_get(self, plugin_id: str) -> ParserPlugin | None:
        return self._plugins.get(plugin_id)

    def descriptors(self) -> list[ParserPluginDescriptor]:
        return [p.descriptor for p in self._plugins.values()]

    def __iter__(self) -> Iterator[ParserPlugin]:
        return iter(self._plugins.values())

    def __contains__(self, plugin_id: object) -> bool:
        return isinstance(plugin_id, str) and plugin_id in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)


def build_default_registry(
    scheduler: PollingScheduler | None = None,
) -> ParserPluginRegistry:
    """Default plugin set shipped with this build.

    Step 1 — convention scan: walk every sub-package of
    ``plugins.parser`` and call its ``make_plugin(scheduler=)`` factory.

    Step 2 — entry-point fan-in: discover out-of-tree plugins via
    ``importlib.metadata.entry_points(group="valuz.parser_plugins")``.
    Built-ins always win on id collisions.

    ``scheduler`` is required for async-poll plugins (PaddleOCR +
    MinerU). When omitted (test code that doesn't drive cloud plugins),
    those plugins' ``make_plugin`` raises and we skip them — the
    resulting registry still ships ``light_local`` + ``valuz_ocr``.
    """
    plugins: dict[str, ParserPlugin] = {}

    for sub_name in _discover_builtin_subpackages():
        plugin = _load_builtin(sub_name, scheduler)
        if plugin is None:
            continue
        pid = plugin.descriptor.id
        if pid in plugins:
            logger.warning(
                "parser plugin %r is declared by two built-in sub-packages — "
                "first wins, dropping the second",
                pid,
            )
            continue
        plugins[pid] = plugin

    for ep in _discover_entry_point_plugins():
        try:
            factory = ep.load()
        except Exception:
            logger.warning(
                "parser plugin entry-point %r failed to load — skipping",
                ep.name,
                exc_info=True,
            )
            continue
        try:
            plugin = factory(scheduler=scheduler)
        except Exception:
            logger.warning(
                "parser plugin entry-point %r factory raised — skipping",
                ep.name,
                exc_info=True,
            )
            continue
        # ``ParserPlugin`` is a non-runtime Protocol; duck-type instead
        # via the required ``.descriptor`` attribute and surface the
        # AttributeError clearly.
        if not hasattr(plugin, "descriptor"):
            logger.warning(
                "parser plugin entry-point %r returned non-ParserPlugin %r — skipping",
                ep.name,
                type(plugin).__name__,
            )
            continue
        pid = plugin.descriptor.id
        if pid in plugins:
            logger.warning(
                "parser plugin entry-point %r duplicates built-in id %r — "
                "built-ins win; skipping the third-party registration",
                ep.name,
                pid,
            )
            continue
        plugins[pid] = plugin

    return ParserPluginRegistry(plugins=list(plugins.values()))


def _discover_builtin_subpackages() -> list[str]:
    """Return the names of every sub-package under
    ``plugins.parser``. Importing the parent package is cheap (its
    ``__init__.py`` does nothing); the per-plugin modules are imported
    lazily by ``_load_builtin``.

    Returns an empty list — with a warning — when the package isn't
    importable (e.g. uninstalled in some odd test environment)."""
    try:
        pkg = importlib.import_module(_BUILTIN_PACKAGE)
    except Exception:
        logger.warning(
            "built-in parser plugin package %r failed to import",
            _BUILTIN_PACKAGE,
            exc_info=True,
        )
        return []
    paths = getattr(pkg, "__path__", None)
    if paths is None:
        return []
    return [
        info.name
        for info in pkgutil.iter_modules(paths)
        if info.ispkg
    ]


def _load_builtin(
    sub_name: str,
    scheduler: PollingScheduler | None,
) -> ParserPlugin | None:
    """Import ``plugins.parser.<sub_name>`` and call its
    ``make_plugin(scheduler=)`` factory. Logs and returns None on any
    failure so a single broken plugin can't take down the registry."""
    mod_name = f"{_BUILTIN_PACKAGE}.{sub_name}"
    try:
        module = importlib.import_module(mod_name)
    except Exception:
        logger.warning(
            "parser plugin %r failed to import — skipping", mod_name, exc_info=True
        )
        return None
    factory = getattr(module, "make_plugin", None)
    if not callable(factory):
        logger.warning(
            "parser plugin %r has no callable ``make_plugin`` — skipping",
            mod_name,
        )
        return None
    try:
        plugin = factory(scheduler=scheduler)
    except Exception:
        # ``make_plugin`` raising is the normal path for cloud plugins
        # when ``scheduler`` is None (test contexts); log at INFO to
        # keep the production log quiet.
        logger.info(
            "parser plugin %r make_plugin returned no plugin: %s",
            mod_name,
            _exc_summary(),
        )
        return None
    if not hasattr(plugin, "descriptor"):
        logger.warning(
            "parser plugin %r make_plugin returned non-ParserPlugin %r — skipping",
            mod_name,
            type(plugin).__name__,
        )
        return None
    return plugin


def _exc_summary() -> str:
    """Render the active exception as ``<Type>: <msg>``. Inline helper
    so the discovery loop's INFO log stays a single line."""
    import sys as _sys

    exc = _sys.exc_info()[1]
    if exc is None:
        return ""
    return f"{type(exc).__name__}: {exc}"


def _discover_entry_point_plugins() -> list[EntryPoint]:
    """Return every ``valuz.parser_plugins`` entry-point installed in
    the current process. Wrapped in a helper so tests can monkey-patch
    a fixed list without touching ``importlib.metadata``."""
    try:
        eps = entry_points()
    except Exception:
        logger.warning("entry-points discovery failed", exc_info=True)
        return []
    # ``entry_points()`` returns either a ``SelectableGroups``
    # (Python >= 3.10 with ``.select(...)``) or a dict-like; the
    # ``.select`` form is what we ship for, but defensive against
    # forks / dev environments.
    try:
        return list(eps.select(group=_ENTRY_POINT_GROUP))
    except AttributeError:  # pragma: no cover — Python < 3.10 path
        return [
            ep
            for ep in eps.get(_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
            if isinstance(ep, EntryPoint)
        ]


__all__ = [
    "LIGHT_LOCAL_PLUGIN_ID",
    "MINERU_PLUGIN_ID",
    "PADDLEOCR_PLUGIN_ID",
    "VALUZ_OCR_PLUGIN_ID",
    "ParserPluginRegistry",
    "UnknownPluginError",
    "build_default_registry",
]
