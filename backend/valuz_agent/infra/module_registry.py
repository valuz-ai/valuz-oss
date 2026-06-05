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
    ) -> None:
        self._modules.append(
            _ModuleEntry(name=name, router=router, prefix=prefix, tags=tags or [name])
        )

    def apply(self, app: FastAPI) -> None:
        for entry in self._modules:
            app.include_router(entry.router, prefix=entry.prefix, tags=entry.tags)

    @property
    def registered_names(self) -> list[str]:
        return [m.name for m in self._modules]


module_registry = ModuleRegistry()

__all__ = ["ModuleRegistry", "module_registry"]
