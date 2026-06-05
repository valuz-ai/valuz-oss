from collections.abc import Iterator
from typing import Any


class ServiceContainer:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, key: str, value: Any) -> None:
        self._services[key] = value

    def replace(self, key: str, value: Any) -> None:
        self._services[key] = value

    def get(self, key: str) -> Any:
        return self._services[key]

    def has(self, key: str) -> bool:
        return key in self._services

    def items(self) -> Iterator[tuple[str, Any]]:
        return iter(self._services.items())
