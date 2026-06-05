from collections import defaultdict
from collections.abc import Callable
from typing import Any


class EventBus:
    """In-process synchronous event bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event_type: str, **payload: Any) -> None:
        for handler in self._handlers.get(event_type, []):
            handler(**payload)


event_bus = EventBus()
