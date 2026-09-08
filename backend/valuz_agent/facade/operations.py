"""Stable Edition API for the shared Operation/Decision engine.

The caller supplies its request/job owner and transaction. This library never
commits, grants authority, or maintains a second operation table. Handlers must
validate domain permissions/revisions on that same transaction; registration
and a confirmation decision are not substitutes for authorization.

Returned Pydantic views contain detached data, not writable ORM records. Import
these contracts here instead of depending on ``modules.operations`` internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.projects import ProjectLibrary
from valuz_agent.modules.operations.models import ConfirmationDecisionRow, OperationRecordRow
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationExecution,
    OperationHandler,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import (
    OperationDecisionRequest,
    OperationDecisionView,
    OperationProposal,
    OperationRequestChangesRequest,
)
from valuz_agent.modules.operations.schemas import (
    OperationView as OperationWireView,
)
from valuz_agent.modules.operations.service import OperationService


class OperationView(OperationWireView):
    """Detached record including attempts derived from approved decisions."""

    attempt_count: int = 0
    historical_only: bool = False


def register_operation(registration: OperationRegistration) -> None:
    """Register an installed capability at composition time, never per request."""
    operation_registry.register(registration)


@dataclass(frozen=True, slots=True)
class OperationPage:
    items: tuple[OperationView, ...]
    next_before: tuple[int, str] | None


class OperationLibrary:
    def __init__(
        self,
        db: AsyncSession,
        projects: ProjectLibrary,
        *,
        services: Mapping[str, object] | None = None,
    ) -> None:
        # Local dependencies, not persisted approval data. Construct a fresh
        # library per request/UOW; explicit context.user_id stays authoritative.
        self._service = OperationService(db, projects, services=services)
        self._db = db

    @staticmethod
    def _owner(user_id: str) -> str:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("operation_owner_required")
        return user_id

    async def _views(self, user_id: str, rows: list[OperationRecordRow]) -> list[OperationView]:
        decisions = await self._service.latest_decisions(user_id, [row.id for row in rows])
        attempts = (
            dict(
                (
                    await self._db.execute(
                        select(ConfirmationDecisionRow.operation_id, func.count())
                        .where(
                            ConfirmationDecisionRow.user_id == user_id,
                            ConfirmationDecisionRow.operation_id.in_([row.id for row in rows]),
                            ConfirmationDecisionRow.decision == "approve",
                        )
                        .group_by(ConfirmationDecisionRow.operation_id)
                    )
                ).all()
            )
            if rows
            else {}
        )
        views = []
        for row in rows:
            decision = decisions.get(row.id)
            data = {
                name: deepcopy(getattr(row, name))
                for name in OperationView.model_fields
                if name not in {"latest_decision", "attempt_count"}
            }
            data["attempt_count"] = attempts.get(row.id, 0)
            data["latest_decision"] = (
                OperationDecisionView(
                    decision=decision.decision,  # type: ignore[arg-type]
                    decided_by=decision.decided_by,
                    decided_at=decision.created_at,
                    proposal_hash=decision.proposal_hash,
                    comment=decision.comment,
                )
                if decision is not None
                else None
            )
            views.append(OperationView.model_validate(data))
        return views

    async def get(self, user_id: str, operation_id: str) -> OperationView:
        row = await self._service.get(self._owner(user_id), operation_id)
        return (await self._views(user_id, [row]))[0]

    async def propose(self, user_id: str, proposal: OperationProposal) -> OperationView:
        row = await self._service.propose(self._owner(user_id), proposal)
        return (await self._views(user_id, [row]))[0]

    async def find_by_idempotency(self, user_id: str, idempotency_key: str) -> OperationView | None:
        self._owner(user_id)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("operation_idempotency_key_invalid")
        row = await self._service.find_by_idempotency(user_id, idempotency_key)
        return (await self._views(user_id, [row]))[0] if row is not None else None

    async def list_page(
        self,
        user_id: str,
        *,
        operation_types: tuple[str, ...] = (),
        project_id: str | None = None,
        origin_session_id: str | None = None,
        limit: int = 100,
        before: tuple[int, str] | None = None,
    ) -> OperationPage:
        rows, next_before = await self._service.list_page(
            self._owner(user_id),
            operation_types=operation_types,
            project_id=project_id,
            origin_session_id=origin_session_id,
            limit=limit,
            before=before,
        )
        return OperationPage(tuple(await self._views(user_id, rows)), next_before)

    async def confirm(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str | None = None,
        decision: dict[str, Any] | None = None,
    ) -> OperationView:
        row = await self._service.confirm(
            self._owner(user_id),
            operation_id,
            expected_proposal_hash=expected_proposal_hash,
            comment=comment,
            decision=decision,
        )
        return (await self._views(user_id, [row]))[0]

    async def cancel(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str | None = None,
    ) -> OperationView:
        row = await self._service.cancel(
            self._owner(user_id),
            operation_id,
            expected_proposal_hash=expected_proposal_hash,
            comment=comment,
        )
        return (await self._views(user_id, [row]))[0]

    async def request_changes(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str,
    ) -> OperationView:
        row = await self._service.request_changes(
            self._owner(user_id),
            operation_id,
            expected_proposal_hash=expected_proposal_hash,
            comment=comment,
        )
        return (await self._views(user_id, [row]))[0]

    async def status(self, user_id: str, operation_ids: list[str]) -> list[OperationView]:
        self._owner(user_id)
        if len(operation_ids) > 100:
            raise ValueError("operation_status_limit_exceeded")
        return await self._views(user_id, await self._service.status(user_id, operation_ids))


__all__ = [
    "OperationContext",
    "OperationDecisionRequest",
    "OperationDecisionView",
    "OperationExecution",
    "OperationHandler",
    "OperationLibrary",
    "OperationPage",
    "OperationProposal",
    "OperationRegistration",
    "OperationRequestChangesRequest",
    "OperationResult",
    "OperationView",
    "register_operation",
]
