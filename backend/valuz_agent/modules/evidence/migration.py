"""Identity-preserving import for trusted, preflighted schema migrations.

Only the owning extension knows its old table names and domain references.
This port neither discovers other tables nor decides which data to authorize.
The caller owns the outer transaction; each bounded batch is also atomic.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import Connection, select
from sqlalchemy.exc import IntegrityError

from valuz_agent.modules.evidence.identity import bounded_payload
from valuz_agent.modules.evidence.models import (
    EvidenceSnapshotRow,
    PendingEvidenceSealRow,
    ProvenanceRecordRow,
)
from valuz_agent.modules.evidence.schemas import (
    EvidenceSnapshot,
    PendingEvidenceSeal,
    ProvenanceRecord,
)
from valuz_agent.modules.evidence.transaction import ensure_outer_transaction

EvidenceRecordKind = Literal["snapshot", "provenance", "seal"]
_CONTRACTS = {
    "snapshot": (EvidenceSnapshotRow.__table__, EvidenceSnapshot),
    "provenance": (ProvenanceRecordRow.__table__, ProvenanceRecord),
    "seal": (PendingEvidenceSealRow.__table__, PendingEvidenceSeal),
}


def import_evidence_records(
    connection: Connection,
    *,
    owner_user_id: str,
    kind: EvidenceRecordKind,
    records: Sequence[dict[str, Any]],
) -> int:
    """Copy once or verify exact replay. No rekey, overwrite or seal authority.

    Source DTOs must include original identity, timestamps and hashes. Imported
    seals retain their original status and acquire an independent history fence.
    The authorized importer validates the complete reference closure separately.
    """
    if not owner_user_id.strip() or len(owner_user_id) > 64:
        raise ValueError("evidence_owner_required")
    if len(records) > 100 or kind not in _CONTRACTS:
        raise ValueError("evidence_import_invalid_batch")
    table, schema = _CONTRACTS[kind]
    prepared = []
    for record in records:
        bounded_payload(record)
        value = schema.model_validate(record).model_dump(mode="json")
        if value["user_id"] != owner_user_id or not value["id"]:
            raise ValueError("evidence_import_owner_mismatch")
        if kind == "seal":
            value["historical_only"] = True
        prepared.append(value)
    inserted = 0
    ensure_outer_transaction(connection)
    try:
        with connection.begin_nested():
            for value in prepared:
                existing = (
                    connection.execute(select(table).where(table.c.id == value["id"]))
                    .mappings()
                    .first()
                )
                if existing is not None:
                    if dict(existing) != value:
                        raise ValueError("evidence_import_identity_conflict")
                    continue
                connection.execute(table.insert().values(**value))
                inserted += 1
    except IntegrityError as exc:
        raise ValueError("evidence_import_identity_conflict") from exc
    return inserted
