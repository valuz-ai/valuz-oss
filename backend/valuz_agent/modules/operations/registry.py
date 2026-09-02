"""Versioned operation handler registry shared by host and Editions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.projects import ProjectLibrary


@dataclass(frozen=True, slots=True)
class OperationResult:
    canonical_result_refs: list[dict[str, Any]]
    result_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OperationContext:
    db: AsyncSession
    projects: ProjectLibrary
    user_id: str
    #: What the confirming user chose, when the proposal left a choice open
    #: (``OperationDecisionRequest.decision``). Not part of the proposal
    #: hash: the proposal is what was shown, the decision is the answer to
    #: it. Empty for a plain confirm.
    decision: dict[str, Any] = field(default_factory=dict)


OperationHandler = Callable[[OperationContext, dict[str, Any]], Awaitable[OperationResult]]


@dataclass(frozen=True, slots=True)
class OperationRegistration:
    operation_type: str
    version: int
    handler: OperationHandler
    #: Optional cleanup when the user cancels: best-effort, outside the
    #: record's transaction, never able to fail the cancel (a proposal that
    #: parked files somewhere gets to remove them).
    cancel_handler: OperationHandler | None = None


class OperationRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, int], OperationRegistration] = {}

    def register(self, registration: OperationRegistration) -> None:
        key = (registration.operation_type, registration.version)
        current = self._items.get(key)
        if current is not None and current.handler is not registration.handler:
            raise ValueError(f"operation already registered: {key}")
        self._items[key] = registration

    def get(self, operation_type: str, version: int) -> OperationRegistration:
        try:
            return self._items[(operation_type, version)]
        except KeyError as exc:
            raise LookupError("operation_type_unavailable") from exc


operation_registry = OperationRegistry()


__all__ = [
    "OperationContext",
    "OperationHandler",
    "OperationRegistration",
    "OperationRegistry",
    "OperationResult",
    "operation_registry",
]
