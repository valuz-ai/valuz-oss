"""Owner-scoped memory catalog with compatible text-entry operations.

The owner memory.json is the sole authority. Markdown files are generated views;
records carry stable identities, revisions and source lineage, while deletion
tombstones and operation receipts contain no retained text. The service uses
cross-process file transactions and has no database/kernel coupling. Entrypoints
resolve the verified owner and scope and offload blocking IO from async turns.
"""

from valuz_agent.modules.memory.models import (
    CHAR_LIMITS,
    ENTRY_DELIMITER,
    TARGETS,
    Source,
    Target,
)
from valuz_agent.modules.memory.service import MemoryError, MemoryStore, memory_store

__all__ = [
    "CHAR_LIMITS",
    "ENTRY_DELIMITER",
    "TARGETS",
    "Source",
    "Target",
    "MemoryError",
    "MemoryStore",
    "memory_store",
]
