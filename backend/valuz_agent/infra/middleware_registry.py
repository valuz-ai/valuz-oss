"""Registry: ordered middleware registration for overlays.

Overlays call ``middleware_registry.register(cls, order)`` at startup.
``middleware_registry.apply(app)`` installs them in ascending order
after OSS middleware is already mounted.

``MiddlewareOrder`` provides named constants so overlays reference
semantic positions rather than magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


class MiddlewareOrder(IntEnum):
    CORS = 10
    COMPRESSION = 20
    REQUEST_ID = 30
    LOGGING = 40
    AUTH = 50
    RBAC = 60
    AUDIT = 70
    ROUTER = 100


@dataclass
class _MiddlewareEntry:
    cls: type
    order: int
    kwargs: dict[str, Any] = field(default_factory=dict)


class MiddlewareRegistry:
    """Collect overlay middleware and apply them in order."""

    def __init__(self) -> None:
        self._entries: list[_MiddlewareEntry] = []

    def register(
        self,
        cls: type,
        order: int | MiddlewareOrder,
        **kwargs: Any,
    ) -> None:
        self._entries.append(_MiddlewareEntry(cls=cls, order=int(order), kwargs=kwargs))

    def apply(self, app: FastAPI) -> None:
        for entry in sorted(self._entries, key=lambda e: e.order, reverse=True):
            app.add_middleware(entry.cls, **entry.kwargs)

    @property
    def registered_count(self) -> int:
        return len(self._entries)


middleware_registry = MiddlewareRegistry()

__all__ = ["MiddlewareOrder", "MiddlewareRegistry", "middleware_registry"]
