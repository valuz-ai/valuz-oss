"""Port: contribute opaque, non-persisted context to one runtime turn."""

from __future__ import annotations

from typing import Any, Protocol


class RuntimeTurnContextContributor(Protocol):
    """Build an overlay-owned context map for one runtime invocation.

    The keys and values are deliberately opaque to OSS.  The kernel only
    applies values to matching generic runtime-context markers in its in-memory
    Session copy; it never persists or interprets them.
    """

    async def build(
        self,
        *,
        user_id: str,
        session_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, str] | None: ...


class NoopRuntimeTurnContextContributor:
    """OSS default: no overlay-owned context accompanies a turn."""

    async def build(
        self,
        *,
        user_id: str,
        session_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, str] | None:
        del user_id, session_id, metadata
        return None


def get_runtime_turn_context_contributor() -> RuntimeTurnContextContributor:
    from valuz_agent.ports.extensions import ext

    return ext.runtime_turn_context


def set_runtime_turn_context_contributor(contributor: RuntimeTurnContextContributor) -> None:
    from valuz_agent.ports.extensions import ext

    ext.runtime_turn_context = contributor


__all__ = [
    "NoopRuntimeTurnContextContributor",
    "RuntimeTurnContextContributor",
    "get_runtime_turn_context_contributor",
    "set_runtime_turn_context_contributor",
]
