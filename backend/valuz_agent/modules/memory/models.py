"""Memory implementation constants and compatibility type re-exports.

Shared DTOs live in ports.memory, allowing ports and facades to depend on them
without importing persistence. Existing module imports remain the same classes.
"""

from __future__ import annotations

from valuz_agent.ports.memory import (
    TARGETS as TARGETS,
)
from valuz_agent.ports.memory import (
    MemoryConflict as MemoryConflict,
)
from valuz_agent.ports.memory import (
    MemoryError as MemoryError,
)
from valuz_agent.ports.memory import (
    MemoryInvalidation as MemoryInvalidation,
)
from valuz_agent.ports.memory import (
    MemoryKind as MemoryKind,
)
from valuz_agent.ports.memory import (
    MemoryMutationResult as MemoryMutationResult,
)
from valuz_agent.ports.memory import (
    MemoryProtected as MemoryProtected,
)
from valuz_agent.ports.memory import (
    MemoryRecord as MemoryRecord,
)
from valuz_agent.ports.memory import (
    MemorySnapshot as MemorySnapshot,
)
from valuz_agent.ports.memory import (
    MemoryUnavailable as MemoryUnavailable,
)
from valuz_agent.ports.memory import (
    MutationAction as MutationAction,
)
from valuz_agent.ports.memory import (
    Source as Source,
)
from valuz_agent.ports.memory import (
    SourceKind as SourceKind,
)
from valuz_agent.ports.memory import (
    SourceOrigin as SourceOrigin,
)
from valuz_agent.ports.memory import (
    SourceRef as SourceRef,
)
from valuz_agent.ports.memory import (
    Target as Target,
)

# Delimiter and capacity policy belong to the local implementation, not the port.
ENTRY_DELIMITER = "\n§\n"
CHAR_LIMITS: dict[Target, int] = {"user": 1500, "global": 2500, "project": 4000}

__all__ = [
    "Target",
    "TARGETS",
    "Source",
    "SourceKind",
    "SourceOrigin",
    "SourceRef",
    "MemoryKind",
    "MutationAction",
    "MemoryRecord",
    "MemorySnapshot",
    "MemoryInvalidation",
    "MemoryMutationResult",
    "MemoryError",
    "MemoryConflict",
    "MemoryUnavailable",
    "MemoryProtected",
    "ENTRY_DELIMITER",
    "CHAR_LIMITS",
]
