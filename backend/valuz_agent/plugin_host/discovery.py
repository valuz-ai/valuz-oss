"""Plugin discovery: the ``valuz.bundles`` entry-point group plus explicit lists.

A bundle entry point resolves to one of: a ``BackendPlugin`` instance, a plugin
class (instantiated without arguments), a callable returning plugin(s), or an
iterable of those. First-party bundles are static (build-time) per red line 4;
this only reads installed distribution metadata and never downloads anything.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

from valuz_agent.plugin_host.errors import InvalidPluginError
from valuz_agent.plugin_host.host import _validate_plugin
from valuz_agent.plugin_host.plugin import BackendPlugin

logger = logging.getLogger("valuz_agent.plugin_host")

BUNDLE_ENTRY_POINT_GROUP = "valuz.bundles"


@dataclass
class DiscoveryResult:
    plugins: list[BackendPlugin] = field(default_factory=list)
    #: ``entry point name -> reason`` for bundles that failed to load.
    failures: dict[str, str] = field(default_factory=dict)


def coerce_plugins(obj: Any) -> list[BackendPlugin]:
    """Normalise an entry-point payload / explicit item into plugin instances."""
    if inspect.isclass(obj):
        return [_validate_plugin(obj())]
    if hasattr(obj, "apply") and hasattr(obj, "id"):
        return [_validate_plugin(obj)]
    if callable(obj):
        return coerce_plugins(obj())
    if isinstance(obj, Iterable) and not isinstance(obj, str | bytes):
        out: list[BackendPlugin] = []
        for item in obj:
            out.extend(coerce_plugins(item))
        return out
    raise InvalidPluginError(f"cannot turn {obj!r} into backend plugins")


def discover_bundles(
    group: str = BUNDLE_ENTRY_POINT_GROUP,
    *,
    entry_points: Callable[..., Iterable[Any]] | None = None,
) -> DiscoveryResult:
    """Load every ``group`` entry point. One broken bundle never hides the rest."""
    finder = entry_points or metadata.entry_points
    result = DiscoveryResult()
    for ep in sorted(finder(group=group), key=lambda e: e.name):
        try:
            result.plugins.extend(coerce_plugins(ep.load()))
        except Exception as exc:  # noqa: BLE001 - isolate a broken distribution
            result.failures[ep.name] = f"{type(exc).__name__}: {exc}"
            logger.warning("bundle entry point %s failed to load: %s", ep.name, exc)
    return result


def collect_plugins(
    explicit: Iterable[Any] = (),
    *,
    discover: bool = False,
    group: str = BUNDLE_ENTRY_POINT_GROUP,
    entry_points: Callable[..., Iterable[Any]] | None = None,
) -> DiscoveryResult:
    """Explicit plugins first, then (optionally) discovered ones.

    An id that is already present wins over a later duplicate, so an explicit
    list can pin a plugin against a same-named bundle.
    """
    result = DiscoveryResult()
    seen: set[str] = set()
    for item in explicit:
        for plugin in coerce_plugins(item):
            seen.add(plugin.id)
            result.plugins.append(plugin)
    if discover:
        found = discover_bundles(group, entry_points=entry_points)
        result.failures.update(found.failures)
        for plugin in found.plugins:
            if plugin.id in seen:
                logger.info("discovered plugin %s shadowed by an explicit one", plugin.id)
                continue
            seen.add(plugin.id)
            result.plugins.append(plugin)
    return result
