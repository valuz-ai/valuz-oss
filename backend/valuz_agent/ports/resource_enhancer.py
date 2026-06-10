"""Port: resource list enhancement for commercial overlay.

OSS mode uses ``NoopResourceEnhancer`` — list endpoints return data unchanged.
The commercial overlay binds a real enhancer via ``set_resource_enhancer()``
at app startup to inject cloud sync status and org-level resources.
"""

from __future__ import annotations

from typing import Any, Protocol


class ResourceListEnhancer(Protocol):
    """Enhance resource list responses with external data (e.g. cloud sync status)."""

    async def enhance(
        self, resource_type: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Receive local resource list, return enhanced list."""
        ...


class NoopResourceEnhancer:
    """Default enhancer — returns items unchanged."""

    async def enhance(
        self, resource_type: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return items


_enhancer: ResourceListEnhancer = NoopResourceEnhancer()


def get_resource_enhancer() -> ResourceListEnhancer:
    return _enhancer


def set_resource_enhancer(enhancer: ResourceListEnhancer) -> None:
    """Replace the enhancer (called by commercial app at startup)."""
    global _enhancer
    _enhancer = enhancer


__all__ = [
    "NoopResourceEnhancer",
    "ResourceListEnhancer",
    "get_resource_enhancer",
    "set_resource_enhancer",
]
