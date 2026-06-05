"""Memory module — global / project / task scoped agent memory.

See docs/exec-plans/active/memory-system-design.md for the architecture.

MVP scope: file-based storage (topic ``<name>.md`` + per-scope ``MEMORY.md``
index), runtime-agnostic, single-writer. The service layer is pure (it takes
``project_cwd`` / ``task_id`` explicitly and does not couple to the DB/kernel),
so it is fully unit-testable; callers (tools, injection, extraction) resolve
the project cwd from the kernel project.
"""

from valuz_agent.modules.memory.models import (
    MemoryEntry,
    MemoryIndexEntry,
    MemoryScope,
    MemType,
    Scope,
    Source,
)
from valuz_agent.modules.memory.service import MemoryService, memory_service

__all__ = [
    "MemoryEntry",
    "MemoryIndexEntry",
    "MemoryScope",
    "MemType",
    "Scope",
    "Source",
    "MemoryService",
    "memory_service",
]
