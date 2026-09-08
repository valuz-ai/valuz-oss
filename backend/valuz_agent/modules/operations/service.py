"""State machine, idempotency and canonical execution for operations."""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.projects import ProjectLibrary
from valuz_agent.infra.db import defer_commits
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.operations.models import (
    ConfirmationDecisionRow,
    OperationRecordRow,
)
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationExecution,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import OperationProposal

logger = logging.getLogger(__name__)

#: States in which a proposal still waits for the user's decision. Only
#: these can expire (``awaiting_confirmation → expired: timeout``) or take a
#: ``request_changes`` decision.
PENDING_STATES = frozenset({"proposed", "awaiting_confirmation"})
#: States a newer proposal for the same owner/type/target replaces
#: (``awaiting_confirmation → superseded`` and ``stale → superseded``).
SUPERSEDABLE_STATES = PENDING_STATES | {"stale"}


def target_scope(target_refs: list[dict[str, Any]]) -> frozenset[tuple[str, str]] | None:
    """Identity of what a proposal targets, for supersede matching.

    Each ``EntityRef`` contributes ``(type, id)`` — or ``(type, slug)`` for a
    ref that has no id yet (a skill not saved before). Refs that carry
    neither are ignored; a proposal with no identifiable target (a create)
    returns ``None`` and never supersedes anything: two creates are two
    different intents, not one replacing the other.
    """
    scope: set[tuple[str, str]] = set()
    for ref in target_refs:
        if not isinstance(ref, dict):
            continue
        ref_type = ref.get("type")
        ident = ref.get("id") or ref.get("slug")
        if not ref_type or not ident:
            continue
        scope.add((str(ref_type), str(ident)))
    return frozenset(scope) or None


def apply_expiry(row: OperationRecordRow, *, now: int | None = None) -> bool:
    """Settle a pending row past its ``expires_at`` into ``expired``.

    Pure state change on the instance — persisted whenever the surrounding
    unit of work flushes/commits, so a read reports the row as expired
    before any writer touched it. Returns whether the row is expired.
    """
    if row.state == "expired":
        return True
    if row.state not in PENDING_STATES or row.expires_at is None:
        return False
    if row.expires_at > (now_ms() if now is None else now):
        return False
    row.state = "expired"
    row.error_code = "OPERATION_EXPIRED"
    row.error_message = None
    return True


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

    def _context(
        self, row: OperationRecordRow, decision: dict[str, Any] | None = None
    ) -> OperationContext:
        return OperationContext(
            db=self._db,
            projects=self._projects,
            user_id=row.user_id,
            decision=deepcopy(decision or {}),
            operation=OperationExecution(
                id=row.id,
                operation_type=row.operation_type,
                operation_version=row.operation_version,
                proposal_hash=row.proposal_hash,
                project_id=row.project_id,
                actor_kind=row.actor_kind,
                actor_id=row.actor_id,
                origin_session_id=row.origin_session_id,
                origin_tool_call_id=row.origin_tool_call_id,
                origin_playbook_run_id=row.origin_playbook_run_id,
                origin_automation_run_id=row.origin_automation_run_id,
                expected_revisions=deepcopy(row.expected_revisions),
                target_refs=deepcopy(row.target_refs),
            ),
        )

    async def get(self, user_id: str, operation_id: str) -> OperationRecordRow:
        row = await self._db.scalar(
            select(OperationRecordRow).where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.id == operation_id,
            )
        )
        if row is None:
            raise LookupError("operation_not_found")
        apply_expiry(row)
        return row

    async def find_by_idempotency(
        self, user_id: str, idempotency_key: str
    ) -> OperationRecordRow | None:
        row = await self._db.scalar(
            select(OperationRecordRow).where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.idempotency_key == idempotency_key,
            )
        )
        if row is not None:
            apply_expiry(row)
        return row

    async def list_page(
        self,
        user_id: str,
        *,
        operation_types: tuple[str, ...] = (),
        project_id: str | None = None,
        origin_session_id: str | None = None,
        limit: int = 100,
        before: tuple[int, str] | None = None,
    ) -> tuple[list[OperationRecordRow], tuple[int, str] | None]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("operation_page_limit_invalid")
        if len(operation_types) > 32 or any(
            not isinstance(item, str) or not 1 <= len(item) <= 96 for item in operation_types
        ):
            raise ValueError("operation_page_types_invalid")
        if before is not None and (
            not isinstance(before, tuple)
            or len(before) != 2
            or type(before[0]) is not int
            or not 0 <= before[0] < 2**63
            or not isinstance(before[1], str)
            or not 1 <= len(before[1]) <= 36
        ):
            raise ValueError("operation_page_position_invalid")
        statement = select(OperationRecordRow).where(OperationRecordRow.user_id == user_id)
        if operation_types:
            statement = statement.where(OperationRecordRow.operation_type.in_(operation_types))
        if project_id is not None:
            statement = statement.where(OperationRecordRow.project_id == project_id)
        if origin_session_id is not None:
            statement = statement.where(OperationRecordRow.origin_session_id == origin_session_id)
        if before is not None:
            statement = statement.where(
                tuple_(OperationRecordRow.created_at, OperationRecordRow.id) < before
            )
        result = await self._db.scalars(
            statement.order_by(
                OperationRecordRow.created_at.desc(),
                OperationRecordRow.id.desc(),
            ).limit(limit + 1)
        )
        candidates = list(result.all())
        rows = candidates[:limit]
        for row in rows:
            apply_expiry(row)
        next_before = (rows[-1].created_at, rows[-1].id) if len(candidates) > limit else None
        return rows, next_before

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

    async def latest_decisions(
        self, user_id: str, operation_ids: list[str]
    ) -> dict[str, ConfirmationDecisionRow]:
        """Most recent append-only decision per operation (by decided time)."""
        if not operation_ids:
            return {}
        ranked = (
            select(
                ConfirmationDecisionRow.id,
                func.row_number()
                .over(
                    partition_by=ConfirmationDecisionRow.operation_id,
                    order_by=(
                        ConfirmationDecisionRow.created_at.desc(),
                        ConfirmationDecisionRow.id.desc(),
                    ),
                )
                .label("position"),
            )
            .where(
                ConfirmationDecisionRow.user_id == user_id,
                ConfirmationDecisionRow.operation_id.in_(operation_ids),
            )
            .subquery()
        )
        rows = await self._db.scalars(
            select(ConfirmationDecisionRow)
            .join(ranked, ranked.c.id == ConfirmationDecisionRow.id)
            .where(ranked.c.position == 1, ConfirmationDecisionRow.user_id == user_id)
        )
        latest: dict[str, ConfirmationDecisionRow] = {}
        for decision in rows.all():
            latest[decision.operation_id] = decision
        return latest

    async def propose(self, user_id: str, proposal: OperationProposal) -> OperationRecordRow:
        registration = operation_registry.get(proposal.operation_type, proposal.operation_version)
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
            apply_expiry(existing)
            return existing
        if registration.input_schema is not None:
            # Validate new input without rewriting the reviewed payload.
            # An exact replay above remains a read of its original receipt.
            registration.input_schema.model_validate(deepcopy(proposal.input_payload))
        created = now_ms()
        expires_at = proposal.expires_at
        if expires_at is None and registration.default_ttl_ms is not None:
            expires_at = created + registration.default_ttl_ms
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
            target_refs=deepcopy(proposal.target_refs),
            input_payload=deepcopy(proposal.input_payload),
            preview=deepcopy(proposal.preview),
            expected_revisions=deepcopy(proposal.expected_revisions),
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
            expires_at=expires_at,
        )
        self._db.add(row)
        await self._db.flush()
        await self._supersede_replaced(user_id, row)
        return row

    async def _supersede_replaced(self, user_id: str, successor: OperationRecordRow) -> None:
        """Mark older undecided proposals for the same target as replaced.

        Scope = owner + ``operation_type`` + :func:`target_scope`. Terminal
        rows are left alone; ``failed`` keeps its retry path. No cancel hook
        runs: the successor typically shares the resources a cancel would
        clean up (a skill's staging directory).
        """
        scope = target_scope(successor.target_refs)
        if scope is None:
            return
        candidates = await self._db.scalars(
            select(OperationRecordRow)
            .where(
                OperationRecordRow.user_id == user_id,
                OperationRecordRow.operation_type == successor.operation_type,
                OperationRecordRow.state.in_(sorted(SUPERSEDABLE_STATES)),
                OperationRecordRow.id != successor.id,
            )
            .with_for_update()
        )
        replaced = False
        for candidate in candidates.all():
            if target_scope(candidate.target_refs) != scope:
                continue
            if apply_expiry(candidate):
                continue
            candidate.state = "superseded"
            candidate.superseded_by_id = successor.id
            candidate.error_code = None
            candidate.error_message = None
            replaced = True
        if replaced:
            await self._db.flush()

    async def request_changes(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str,
    ) -> OperationRecordRow:
        """Append a ``request_changes`` decision; the proposal stays pending.

        The contract names no dedicated state for "please revise", so the
        record keeps waiting: the proposer reads the comment back through
        ``latest_decision`` and either proposes a replacement (which
        supersedes this one) or the user confirms/cancels it as is.
        """
        row = await self._get_for_update(user_id, operation_id)
        if row.proposal_hash != expected_proposal_hash:
            raise ValueError("operation_proposal_hash_mismatch")
        if apply_expiry(row):
            await self._db.flush()
        if row.state not in PENDING_STATES:
            raise ValueError(f"operation_not_revisable:{row.state}")
        self._db.add(
            ConfirmationDecisionRow(
                user_id=user_id,
                operation_id=row.id,
                decision="request_changes",
                decided_by=user_id,
                proposal_hash=row.proposal_hash,
                comment=comment,
            )
        )
        await self._db.flush()
        return row

    async def confirm(
        self,
        user_id: str,
        operation_id: str,
        *,
        expected_proposal_hash: str,
        comment: str | None = None,
        decision: dict[str, Any] | None = None,
    ) -> OperationRecordRow:
        row = await self._get_for_update(user_id, operation_id)
        if row.proposal_hash != expected_proposal_hash:
            raise ValueError("operation_proposal_hash_mismatch")
        if row.state == "succeeded":
            return row
        if apply_expiry(row):
            # Persist the timeout so the refusal is durable and no handler runs.
            await self._db.flush()
        if row.state not in PENDING_STATES | {"failed"}:
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
            # ``defer_commits``: the handler may reuse domain code written for
            # the request path, which commits as it goes. Inside this
            # savepoint a commit would close the context; deferring turns each
            # one into a flush, and the record's own commit lands all of it.
            async with self._db.begin_nested(), defer_commits():
                if registration.input_schema is not None:
                    registration.input_schema.model_validate(deepcopy(row.input_payload))
                result = await registration.handler(
                    self._context(row, decision),
                    deepcopy(row.input_payload),
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
        if apply_expiry(row):
            await self._db.flush()
        if row.state not in PENDING_STATES:
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
        registration = operation_registry.get(row.operation_type, row.operation_version)
        if registration.cancel_handler is not None:
            try:
                await registration.cancel_handler(
                    self._context(row),
                    deepcopy(row.input_payload),
                )
            except Exception:  # noqa: BLE001 — cleanup must not undo the cancel
                logger.warning(
                    "operation cancel handler failed",
                    extra={"operation_id": row.id, "operation_type": row.operation_type},
                    exc_info=True,
                )
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
        result = list(rows.all())
        now = now_ms()
        for row in result:
            apply_expiry(row, now=now)
        return result


__all__ = [
    "PENDING_STATES",
    "SUPERSEDABLE_STATES",
    "OperationService",
    "apply_expiry",
    "proposal_hash",
    "target_scope",
]
