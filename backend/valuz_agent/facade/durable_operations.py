"""Trusted, transaction-scoped archival port, not an alternate mutation API.

Storage mappings are public only for bounded read joins and identity-preserving
migration validation. Normal commands use OperationLibrary and registered handlers.
"""

from valuz_agent.modules.operations.migration import (
    OperationRecordKind,
    import_operation_records,
    validate_operation_record,
)
from valuz_agent.modules.operations.models import (
    ConfirmationDecisionRow as ConfirmationDecisionStorage,
)
from valuz_agent.modules.operations.models import (
    OperationRecordRow as OperationRecordStorage,
)

__all__ = [
    "OperationRecordKind",
    "OperationRecordStorage",
    "ConfirmationDecisionStorage",
    "import_operation_records",
    "validate_operation_record",
]
