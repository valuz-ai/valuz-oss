"""Cross-process transport for UI bus pushes (``ctx.ui.push``).

A push is delivered to the SSE streams of the process that made it
(:mod:`valuz_agent.modules.plugin_ui.push`). A deployment with several backend
processes behind one load balancer needs the push to reach the process that
holds the user's stream, so the hub also hands every push to this transport,
and the transport hands pushes from other processes back to the hub.

OSS binds :class:`LocalUiPushTransport` (single process: nothing to do). An
overlay binds a pub/sub implementation through ``ctx.ports.bind
("ui_push_transport", …)``. Pushes are live-only; a lost one costs a missed
toast or a stale surface until the next ``invalidate``, never correctness.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

#: ``deliver(message)`` fans a push from another process out to this process's
#: streams. ``message`` is the wire form of a ``UiPush`` (``UiPush.to_wire``).
Deliver = Callable[[Mapping[str, Any]], None]


class UiPushTransport(Protocol):
    async def start(self, deliver: Deliver) -> None:
        """Begin receiving pushes made by other processes."""
        ...

    async def publish(self, message: Mapping[str, Any]) -> None:
        """Send a push made here to every other process."""
        ...

    async def stop(self) -> None: ...


class LocalUiPushTransport:
    """Single process: every stream already lives here."""

    async def start(self, deliver: Deliver) -> None:
        del deliver

    async def publish(self, message: Mapping[str, Any]) -> None:
        del message

    async def stop(self) -> None:
        return None


def is_local(transport: object) -> bool:
    return isinstance(transport, LocalUiPushTransport)


__all__ = ["Deliver", "LocalUiPushTransport", "UiPushTransport", "is_local"]
