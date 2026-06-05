"""CallbackEventSink — invokes a callback for programmatic/web usage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.core.events import Event

EventCallback = Callable[[Event], Awaitable[None]]


class CallbackEventSink:
    """Emits events by invoking a user-provided async callback."""

    def __init__(self, callback: EventCallback) -> None:
        self._callback = callback

    async def emit(self, event: Event) -> None:
        await self._callback(event)
