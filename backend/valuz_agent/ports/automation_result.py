"""Observe structured automation results.

A run that declares ``result.kind = "artifact"`` ends with one JSON object on
its run row. Deployments that project such artifacts elsewhere — a site data
channel, a workbench binding, a notification — register a hook here. OSS
registers none; hooks are best-effort and never fail the run that fired them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AutomationArtifactFile:
    """One file the run declared. ``artifact_id`` is the artifact row it became;
    ``None`` with ``error`` set when the host could not record it — the run is
    still a success, the artifact is what the contract promised, and the
    failed registration is reported rather than hidden behind a failed run."""

    artifact_id: str | None
    name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AutomationArtifactEvent:
    user_id: str
    automation_id: str
    run_id: str
    project_id: str
    artifact: Mapping[str, Any]
    files: Sequence[AutomationArtifactFile] = field(default_factory=tuple)
    produced_at: int = 0
    #: ``"code"`` or ``"agent"`` — who produced it.
    producer: str = "code"


@runtime_checkable
class AutomationResultHook(Protocol):
    async def on_artifact(self, event: AutomationArtifactEvent) -> None: ...


__all__ = ["AutomationArtifactEvent", "AutomationArtifactFile", "AutomationResultHook"]
