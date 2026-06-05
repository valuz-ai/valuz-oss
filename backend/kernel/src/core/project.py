"""Project — top-level concept for a host workspace.

A Project owns:
- a host-side absolute path (cwd) where its sessions operate
- zero or more Sessions, each picking a model + agent at creation time

Agents are global capability presets (see ``agent_config.py``). They are not
owned by a project — sessions reference them directly via ``session.agent_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.time_utils import now_ms

ProjectStatus = Literal["active", "deleted"]


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    cwd: str
    status: ProjectStatus = "active"
    created_at: int = field(default_factory=now_ms)  # Unix epoch ms (UTC)
    metadata: dict[str, Any] = field(default_factory=dict)
