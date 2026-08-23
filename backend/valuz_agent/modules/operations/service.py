"""State machine, idempotency and canonical execution for operations."""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.projects import ProjectLibrary
from valuz_agent.modules.operations.models import (
    ConfirmationDecisionRow,
    OperationRecordRow,
)
from valuz_agent.modules.operations.registry import OperationContext, operation_registry
from valuz_agent.modules.operations.schemas import OperationProposal

logger = logging.getLogger(__name__)


def proposal_hash(proposal: OperationProposal) -> str:
    canonical = {
        "operation_type": proposal.operation_type,
        "operation_version": proposal.operation_version,
        "project_id": proposal.project_id,
        "actor_kind": proposal.actor_kind,
        "actor_id": proposal.actor_id,
        "origin_session_id": proposal.origin_session_id,
        "origin_tool_call_id": proposal.origin_tool_call_id,
        "origin_playbook_run_id": proposal.origin_playbook_run_id,
        "origin_automation_run_id": proposal.origin_automation_run_id,
        "target_refs": proposal.target_refs,
        "input_payload": proposal.input_payload,
        "preview": proposal.preview,
        "expected_revisions": proposal.expected_revisions,
        "risk_level": proposal.risk_level,
        "confirmation_policy": proposal.confirmation_policy,
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class OperationService:
    def __init__(self, db: AsyncSession, projects: ProjectLibrary) -> None:
        self._db = db
        self._projects = projects

    async def get(self, user_id: str, operation_id: str) -> OperationRecordRow:
        row = await self._db.scalar(
            select(OperationRecordRow).where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.id == operation_id,
            )
        )
        if row is None:
            raise LookupError("operation_not_found")
        return row

    async def _get_for_update(self, user_id: str, operation_id: str) -> OperationRecordRow:
        row = await self._db.scalar(
            select(OperationRecordRow)
            .where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.id == operation_id,
            )
            .with_for_update()
        )
        if row is None:
            raise LookupError("operation_not_found")
        return row

    async def propose(self, user_id: str, proposal: OperationProposal) -> OperationRecordRow:
        operation_registry.get(proposal.operation_type, proposal.operation_version)
        existing = await self._db.scalar(
            select(OperationRecordRow).where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.idempotency_key == proposal.idempotency_key,
            )
        )
        digest = proposal_hash(proposal)
        if existing is not None:
            if existing.proposal_hash != digest:
                raise ValueError("operation_idempotency_conflict")
            return existing
        row = OperationRecordRow(
            user_id=user_id,
            project_id=proposal.project_id,
            operation_type=proposal.operation_type,
            operation_version=proposal.operation_version,
            actor_kind=proposal.actor_kind,
            actor_id=proposal.actor_id,
            origin_session_id=proposal.origin_session_id,
            origin_tool_call_id=proposal.origin_tool_call_id,
            origin_playbook_run_id=proposal.origin_playbook_run_id,
            origin_automation_run_id=proposal.origin_automation_run_id,
            target_refs=proposal.target_refs,
            input_payload=proposal.input_payload,
            preview=proposal.preview,
            expected_revisions=proposal.expected_revisions,
            risk_level=proposal.risk_level,
            confirmation_policy=proposal.confirmation_policy,
            state=(
                "awaiting_confirmation"
                if proposal.confirmation_policy in {"confirm", "approval"}
                else "proposed"
            ),
            proposal_hash=digest,
            idempotency_key=proposal.idempotency_key,
            canonical_result_refs=[],
            result_payload={},
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def confirm(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str | None = None,
    ) -> OperationRecordRow:
        row = await self._get_for_update(user_id, operation_id)
        if row.proposal_hash != expected_proposal_hash:
            raise ValueError("operation_proposal_hash_mismatch")
        if row.state == "succeeded":
            return row
        if row.state not in {"proposed", "awaiting_confirmation", "failed"}:
            raise ValueError(f"operation_not_confirmable:{row.state}")
        self._db.add(
            ConfirmationDecisionRow(
                user_id=user_id,
                operation_id=row.id,
                decision="approve",
                decided_by=user_id,
                proposal_hash=row.proposal_hash,
                comment=comment,
            )
        )
        row.state = "executing"
        row.error_code = None
        row.error_message = None
        await self._db.flush()
        registration = operation_registry.get(row.operation_type, row.operation_version)
        try:
            # Roll back partial domain writes while preserving the outer
            # OperationRecord so a failed attempt remains durable and retryable.
            async with self._db.begin_nested():
                result = await registration.handler(
                    OperationContext(
                        db=self._db,
                        projects=self._projects,
                        user_id=user_id,
                    ),
                    row.input_payload,
                )
        except ValueError as exc:
            message = str(exc)
            row.state = "stale" if "stale" in message.lower() else "failed"
            row.error_code = "OPERATION_STALE" if row.state == "stale" else "OPERATION_FAILED"
            row.error_message = message
        except LookupError as exc:
            row.state = "stale"
            row.error_code = "OPERATION_TARGET_MISSING"
            row.error_message = str(exc)
        except Exception as exc:  # noqa: BLE001 - persist a safe terminal snapshot
            logger.exception(
                "operation handler failed",
                extra={
                    "operation_id": row.id,
                    "operation_type": row.operation_type,
                },
            )
            row.state = "failed"
            row.error_code = "OPERATION_INTERNAL_ERROR"
            row.error_message = str(exc)
        else:
            row.state = "succeeded"
            row.canonical_result_refs = result.canonical_result_refs
            row.result_payload = result.result_payload
        await self._db.flush()
        return row

    async def cancel(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str | None = None,
    ) -> OperationRecordRow:
        row = await self._get_for_update(user_id, operation_id)
        if row.proposal_hash != expected_proposal_hash:
            raise ValueError("operation_proposal_hash_mismatch")
        if row.state == "cancelled":
            return row
        if row.state not in {"proposed", "awaiting_confirmation"}:
            raise ValueError(f"operation_not_cancellable:{row.state}")
        self._db.add(
            ConfirmationDecisionRow(
                user_id=user_id,
                operation_id=row.id,
                decision="reject",
                decided_by=user_id,
                proposal_hash=row.proposal_hash,
                comment=comment,
            )
        )
        row.state = "cancelled"
        await self._db.flush()
        return row

    async def status(self, user_id: str, operation_ids: list[str]) -> list[OperationRecordRow]:
        if not operation_ids:
            return []
        rows = await self._db.scalars(
            select(OperationRecordRow).where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.id.in_(operation_ids),
            )
        )
        return list(rows.all())


__all__ = ["OperationService", "proposal_hash"]
