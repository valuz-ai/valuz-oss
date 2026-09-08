"""Stable shared evidence API for trusted application commands.

Caller supplies an owner and transaction, authorizes the domain write, and
revalidates canonical message evidence / runtime proof before capture. Reading
a citation alone never calls capture. No HTTP surface accepts raw seal payloads.

Storage mappings are exported solely for bounded joined projections and
identity-preserving migrations. Normal writes go through DurableEvidenceLibrary.
"""

from valuz_agent.modules.evidence.migration import EvidenceRecordKind, import_evidence_records
from valuz_agent.modules.evidence.models import (
    EvidenceSnapshotRow as EvidenceSnapshotStorage,
)
from valuz_agent.modules.evidence.models import (
    PendingEvidenceSealRow as PendingEvidenceSealStorage,
)
from valuz_agent.modules.evidence.models import (
    ProvenanceRecordRow as ProvenanceRecordStorage,
)
from valuz_agent.modules.evidence.schemas import (
    EntityRef,
    EvidenceRef,
    EvidenceSnapshot,
    EvidenceStatus,
    Locator,
    PendingEvidenceSeal,
    PendingEvidenceSealInput,
    ProvenanceInput,
    ProvenancePage,
    ProvenanceRecord,
    SourceRef,
)
from valuz_agent.modules.evidence.service import DurableEvidenceService as DurableEvidenceLibrary

__all__ = [
    "EvidenceRecordKind",
    "import_evidence_records",
    "DurableEvidenceLibrary",
    "EntityRef",
    "EvidenceRef",
    "EvidenceSnapshot",
    "EvidenceStatus",
    "Locator",
    "PendingEvidenceSeal",
    "PendingEvidenceSealInput",
    "ProvenanceInput",
    "ProvenancePage",
    "ProvenanceRecord",
    "SourceRef",
    "EvidenceSnapshotStorage",
    "PendingEvidenceSealStorage",
    "ProvenanceRecordStorage",
]
