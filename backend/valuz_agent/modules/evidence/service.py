"""Owner-scoped evidence operations participating in the caller transaction.

No implicit commits, promotion on reads, source fetches or industry bindings.
The application command is responsible for authorization and proof validation.
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Literal

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.evidence import SealedMessageEvidence
from valuz_agent.modules.evidence.identity import bounded_payload, snapshot_values
from valuz_agent.modules.evidence.models import (
    EvidenceSnapshotRow,
    PendingEvidenceSealRow,
    ProvenanceRecordRow,
)
from valuz_agent.modules.evidence.schemas import (
    EvidenceSnapshot,
    EvidenceStatus,
    PendingEvidenceSeal,
    PendingEvidenceSealInput,
    ProvenanceInput,
    ProvenancePage,
    ProvenanceRecord,
)
from valuz_agent.modules.evidence.transaction import ensure_outer_transaction


def _owner(owner: str) -> None:
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 64:
        raise ValueError("evidence_owner_required")


def _limit(limit: int, maximum: int = 100) -> None:
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError("evidence_invalid_limit")


class DurableEvidenceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def capture(
        self, owner: str, sealed: SealedMessageEvidence
    ) -> tuple[EvidenceSnapshot, bool]:
        """Capture a revalidated canonical seal; return (snapshot, reused)."""
        _owner(owner)
        values = deepcopy(snapshot_values(sealed))
        statement = select(EvidenceSnapshotRow).where(
            EvidenceSnapshotRow.user_id == owner,
            EvidenceSnapshotRow.fingerprint == values["fingerprint"],
        )
        row = await self._db.scalar(statement)
        reused = row is not None
        if row is None:
            await self._db.run_sync(lambda session: ensure_outer_transaction(session.connection()))
            try:
                async with self._db.begin_nested():
                    row = EvidenceSnapshotRow(user_id=owner, **values)
                    self._db.add(row)
                    await self._db.flush()
            except IntegrityError:
                row = await self._db.scalar(statement)
                if row is None:
                    raise
                reused = True
        if row.status in ("forbidden", "missing"):
            raise LookupError("evidence_snapshot_unavailable")
        return EvidenceSnapshot.model_validate(row).model_copy(deep=True), reused

    async def get_snapshot(self, owner: str, snapshot_id: str) -> EvidenceSnapshot:
        _owner(owner)
        row = await self._db.scalar(
            select(EvidenceSnapshotRow).where(
                EvidenceSnapshotRow.user_id == owner,
                EvidenceSnapshotRow.id == snapshot_id,
                EvidenceSnapshotRow.status.not_in(("forbidden", "missing")),
            )
        )
        if row is None:
            raise LookupError("evidence_snapshot_unavailable")
        return EvidenceSnapshot.model_validate(row).model_copy(deep=True)

    async def set_snapshot_status(
        self,
        owner: str,
        snapshot_id: str,
        *,
        expected_status: EvidenceStatus,
        status: EvidenceStatus,
    ) -> None:
        _owner(owner)
        allowed = {"ready", "stale", "missing", "forbidden", "retracted", "superseded"}
        if expected_status not in allowed or status not in allowed:
            raise ValueError("evidence_invalid_status")
        result = await self._db.execute(
            update(EvidenceSnapshotRow)
            .where(
                EvidenceSnapshotRow.user_id == owner,
                EvidenceSnapshotRow.id == snapshot_id,
                EvidenceSnapshotRow.status == expected_status,
            )
            .values(status=status)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise ValueError("evidence_status_conflict_or_unavailable")

    async def append_provenance(self, owner: str, value: ProvenanceInput) -> ProvenanceRecord:
        _owner(owner)
        payload = value.model_dump(mode="json")
        bounded_payload(payload)
        # Exact owner-scoped references, not an existence probe across accounts.
        for model, refs in (
            (EvidenceSnapshotRow, value.evidence_snapshot_refs),
            (ProvenanceRecordRow, value.parent_provenance_refs),
        ):
            if not refs:
                continue
            found = set(
                (
                    await self._db.scalars(
                        select(model.id).where(
                            model.user_id == owner,
                            model.id.in_(set(refs)),
                        )
                    )
                ).all()
            )
            if found != set(refs):
                raise LookupError("evidence_lineage_reference_unavailable")
        row = ProvenanceRecordRow(user_id=owner, **deepcopy(payload))
        self._db.add(row)
        await self._db.flush()
        return ProvenanceRecord.model_validate(row).model_copy(deep=True)

    async def list_provenance(
        self,
        owner: str,
        *,
        subject_type: str,
        subject_id: str,
        subject_version: str | None = None,
        as_of_ms: int | None = None,
        limit: int = 50,
        before: tuple[int, str] | None = None,
    ) -> ProvenancePage:
        _owner(owner)
        _limit(limit)
        row = ProvenanceRecordRow
        statement = select(row).where(
            row.user_id == owner, row.subject_type == subject_type, row.subject_id == subject_id
        )
        if subject_version is not None:
            statement = statement.where(row.subject_version == subject_version)
        if as_of_ms is not None:
            statement = statement.where(row.created_at <= as_of_ms)
        if before is not None:
            statement = statement.where(
                or_(
                    row.created_at < before[0],
                    and_(row.created_at == before[0], row.id < before[1]),
                )
            )
        rows = list(
            (
                await self._db.scalars(
                    statement.order_by(row.created_at.desc(), row.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        items = [
            ProvenanceRecord.model_validate(item).model_copy(deep=True) for item in rows[:limit]
        ]
        cursor = (items[-1].created_at, items[-1].id) if len(rows) > limit else None
        return ProvenancePage(items=items, next_before=cursor)

    async def seal_pending(
        self, owner: str, value: PendingEvidenceSealInput
    ) -> PendingEvidenceSeal:
        """Store validated source proof; exact repeats never reopen old seals."""
        _owner(owner)
        payload = value.model_dump(mode="json")
        bounded_payload(payload["payload"])
        statement = select(PendingEvidenceSealRow).where(
            PendingEvidenceSealRow.user_id == owner,
            PendingEvidenceSealRow.operation_id == value.operation_id,
            PendingEvidenceSealRow.message_id == value.message_id,
            PendingEvidenceSealRow.citation_id == value.citation_id,
        )
        row = await self._db.scalar(statement)
        if row is None:
            await self._db.run_sync(lambda session: ensure_outer_transaction(session.connection()))
            try:
                async with self._db.begin_nested():
                    row = PendingEvidenceSealRow(
                        user_id=owner, **deepcopy(payload), historical_only=False
                    )
                    self._db.add(row)
                    await self._db.flush()
            except IntegrityError:
                row = await self._db.scalar(statement)
                if row is None:
                    raise
        if any(getattr(row, key) != item for key, item in payload.items()):
            raise ValueError("evidence_seal_conflict")
        return PendingEvidenceSeal.model_validate(row).model_copy(deep=True)

    async def pending_for_operation(
        self, owner: str, operation_id: str, *, limit: int = 100
    ) -> list[PendingEvidenceSeal]:
        _owner(owner)
        _limit(limit, 200)
        row = PendingEvidenceSealRow
        rows = list(
            (
                await self._db.scalars(
                    select(row)
                    .where(
                        row.user_id == owner,
                        row.operation_id == operation_id,
                        row.status == "pending",
                        row.historical_only.is_(False),
                    )
                    .order_by(row.created_at, row.id)
                    .limit(limit + 1)
                )
            ).all()
        )
        if len(rows) > limit:
            raise ValueError("evidence_seal_limit_exceeded")
        now = int(time.time() * 1000)
        if any(item.expires_at is not None and item.expires_at <= now for item in rows):
            raise ValueError("evidence_seal_expired")
        return [PendingEvidenceSeal.model_validate(item).model_copy(deep=True) for item in rows]

    async def finish_seal(
        self,
        owner: str,
        seal_id: str,
        *,
        operation_id: str,
        citation_hash: str,
        status: Literal["consumed", "invalid"] = "consumed",
    ) -> None:
        _owner(owner)
        if status not in ("consumed", "invalid"):
            raise ValueError("evidence_invalid_seal_status")
        row = PendingEvidenceSealRow
        result = await self._db.execute(
            update(row)
            .where(
                row.user_id == owner,
                row.id == seal_id,
                row.operation_id == operation_id,
                row.citation_hash == citation_hash,
                row.status == "pending",
                row.historical_only.is_(False),
                or_(row.expires_at.is_(None), row.expires_at > int(time.time() * 1000)),
            )
            .values(status=status)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise ValueError("evidence_seal_conflict_or_unavailable")
