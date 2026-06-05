"""CompositeEventSink — fans out events to multiple EventSink implementations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from src.core.events import Event

if TYPE_CHECKING:
    from src.core.events import EventSink

logger = logging.getLogger(__name__)


class CompositeEventSink:
    """Fans out events to multiple EventSink implementations.

    Failures in one sink (e.g. a closed WebSocket) must not block the
    others (e.g. the DB sink) — events still need to be persisted even
    if the live client is gone. We gather with ``return_exceptions=True``
    and log any sink failures at debug level.
    """

    def __init__(self, sinks: Sequence[EventSink]) -> None:
        self._sinks = list(sinks)

    async def emit(self, event: Event) -> None:
        results = await asyncio.gather(
            *(sink.emit(event) for sink in self._sinks),
            return_exceptions=True,
        )
        for sink, result in zip(self._sinks, results, strict=True):
            if isinstance(result, BaseException):
                logger.debug(
                    "Sink %s failed on event %s: %s",
                    type(sink).__name__,
                    event.type,
                    result,
                )
