"""Memory types (memory-system-design §2.2 / §3.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scope = Literal["global", "project", "task"]
# Four-way convergence: Claude Code / Hermes / Multica #838 / valuz auto-memory.
MemType = Literal["user", "feedback", "project", "reference"]
Source = Literal["auto", "agent", "user"]

MEM_TYPES: tuple[MemType, ...] = ("user", "feedback", "project", "reference")

# Index lines must stay short (Claude Code Dreaming rule: index, not dump).
MAX_DESCRIPTION_CHARS = 150


@dataclass(frozen=True)
class MemoryScope:
    """Addresses one scope's storage location.

    - global: ``MemoryScope("global")``
    - project: ``MemoryScope("project", project_cwd=<abs path>)``
    - task: ``MemoryScope("task", project_cwd=<abs path>, task_id=<id>)``
    """

    scope: Scope
    project_cwd: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class MemoryIndexEntry:
    """One line of an injected index (name + one-line description)."""

    scope: Scope
    name: str
    type: MemType
    description: str


@dataclass(frozen=True)
class MemoryEntry:
    """A full memory: frontmatter + body."""

    scope: Scope
    name: str
    type: MemType
    description: str
    content: str
    source: Source
    status: Literal["active", "superseded"] = "active"
    superseded_by: str | None = None
    created_at: str = ""
    updated_at: str = ""
