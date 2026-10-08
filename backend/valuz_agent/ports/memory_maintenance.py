"""Awaitable cleanup of memory-derived pending work after an owner mutation.

Catalog writes remain authoritative. Cleanup describes additional coverage and
must report partial failure rather than claim that every derived copy vanished.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from valuz_agent.ports.memory import SourceRef, Target


@dataclass(frozen=True)
class MemoryMaintenanceScope:
    target: Target
    project_id: str | None = None
    namespace: str = "core"


@dataclass(frozen=True)
class MemoryMaintenanceResult:
    complete: bool
    cancelled_jobs: int = 0
    purged_plans: int = 0
    reason_code: str | None = None


class MemoryMaintenancePort(Protocol):
    async def purge_after_mutation(
        self,
        *,
        owner_user_id: str,
        revision: int,
        affected_ids: tuple[str, ...] = (),
        source_refs: tuple[SourceRef, ...] = (),
        scope: MemoryMaintenanceScope | None = None,
    ) -> MemoryMaintenanceResult: ...


class NoopMemoryMaintenance:
    async def purge_after_mutation(
        self,
        *,
        owner_user_id: str,
        revision: int,
        affected_ids: tuple[str, ...] = (),
        source_refs: tuple[SourceRef, ...] = (),
        scope: MemoryMaintenanceScope | None = None,
    ) -> MemoryMaintenanceResult:
        return MemoryMaintenanceResult(complete=True)
