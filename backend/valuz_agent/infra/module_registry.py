"""Registry: overlay module (router) registration.

Commercial overlays call ``module_registry.register(...)`` at startup to
inject their routers into the FastAPI app. The registry collects entries
and applies them in ``module_registry.apply(app)`` — called once by
``create_app()`` after the OSS routers are mounted.

This gives overlays a stable, named API instead of reaching into
``app.include_router()`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI


@dataclass
class _ModuleEntry:
    name: str
    router: APIRouter
    prefix: str
    tags: list[str] = field(default_factory=list)


class ModuleRegistry:
    """Collect overlay routers and apply them to a FastAPI app."""

    def __init__(self) -> None:
        self._modules: list[_ModuleEntry] = []

    def register(
        self,
        name: str,
        router: APIRouter,
        prefix: str,
        *,
        tags: list[str] | None = None,
    ) -> _ModuleEntry:
        """Register a router; returns the entry so a caller can ``unregister`` it."""
        entry = _ModuleEntry(name=name, router=router, prefix=prefix, tags=tags or [name])
        self._modules.append(entry)
        return entry

    def unregister(self, entry: _ModuleEntry) -> None:
        """Remove an entry returned by :meth:`register` (no-op when absent).

        Only meaningful before :meth:`apply`: a router already mounted on an app
        stays mounted. Used by the backend plugin host to roll back a plugin
        that failed during startup.
        """
        try:
            self._modules.remove(entry)
        except ValueError:
            pass

    def apply(self, target: FastAPI | APIRouter) -> None:
        """Mount every registered router onto ``target``.

        ``target`` is normally the ``FastAPI`` app, but may also be an
        ``APIRouter`` — ``create_app`` aggregates the whole public surface into
        one router so a global ``api_prefix`` can be applied uniformly, and
        passes that router here. Both types expose the same
        ``include_router(router, prefix=, tags=)`` signature.
        """
        for entry in self._modules:
            target.include_router(entry.router, prefix=entry.prefix, tags=[*entry.tags])

    @property
    def registered_names(self) -> list[str]:
        return [m.name for m in self._modules]


module_registry = ModuleRegistry()

__all__ = ["ModuleRegistry", "module_registry"]
