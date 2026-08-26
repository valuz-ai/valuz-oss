"""Skill execution, discovery, and tree-mutation seams.

The default policy preserves OSS discovery/execution behavior.  A commercial
overlay can switch external roots to catalog-only and provide a durable claim
reservation without changing the OSS Skill index schema or importing cloud
policy into the open-source package.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from valuz_agent.ports.runtime_resource import ensure_managed_root_containment


@dataclass(frozen=True)
class DiscoveryDecision:
    execution_eligible: bool
    owner_user_id: str | None = None
    reason: str | None = None


class ExternalSkillDiscoveryPolicy(Protocol):
    def decide(self, *, user_id: str, source_path: str, slug: str) -> DiscoveryDecision:
        """Classify an external Skill without treating path existence as ownership."""


class DiscoverAndExecuteExternalSkills:
    def decide(self, *, user_id: str, source_path: str, slug: str) -> DiscoveryDecision:
        return DiscoveryDecision(execution_eligible=True, owner_user_id=user_id)


class CatalogOnlyUntilClaimed:
    def __init__(self, on_discovered=None) -> None:  # type: ignore[no-untyped-def]
        self.on_discovered = on_discovered

    def decide(self, *, user_id: str, source_path: str, slug: str) -> DiscoveryDecision:
        if self.on_discovered is not None:
            self.on_discovered(user_id=user_id, source_path=source_path, slug=slug)
        return DiscoveryDecision(
            execution_eligible=False,
            owner_user_id=None,
            reason="external Skill requires an explicit owner claim",
        )


class ExternalSkillClaimReservationGate(Protocol):
    def is_reserved(self, *, user_id: str, slug: str, runtime_id: str | None = None) -> bool: ...

    async def reserve(
        self, *, user_id: str, slug: str, claim_id: str, runtime_id: str | None = None
    ) -> None: ...

    async def release(
        self, *, user_id: str, slug: str, claim_id: str, runtime_id: str | None = None
    ) -> None: ...


class InMemoryExternalSkillClaimReservationGate:
    def __init__(self) -> None:
        self._claims: dict[tuple[str, str, str], str] = {}

    def is_reserved(self, *, user_id: str, slug: str, runtime_id: str | None = None) -> bool:
        return (runtime_id or "", user_id, slug) in self._claims

    async def reserve(
        self, *, user_id: str, slug: str, claim_id: str, runtime_id: str | None = None
    ) -> None:
        key = (runtime_id or "", user_id, slug)
        current = self._claims.get(key)
        if current is not None and current != claim_id:
            raise RuntimeError("external Skill target is already reserved")
        self._claims[key] = claim_id

    async def release(
        self, *, user_id: str, slug: str, claim_id: str, runtime_id: str | None = None
    ) -> None:
        key = (runtime_id or "", user_id, slug)
        if self._claims.get(key) == claim_id:
            self._claims.pop(key, None)


class ExecutionResourceResolver(Protocol):
    async def resolve_skill_paths(
        self,
        user_id: str,
        skill_refs: Sequence[str],
        *,
        managed_root: str | Path,
        project_root: str | Path | None = None,
    ) -> list[str]: ...


class ExecutionResourceGate(Protocol):
    async def check(
        self,
        user_id: str,
        *,
        agent_id: str | None = None,
        skill_ids: Sequence[str] = (),
        connector_ids: Sequence[str] = (),
    ) -> None: ...


class SkillTreeMutationPort(Protocol):
    async def scan_and_index(self, user_id: str, *, slug: str | None = None) -> str: ...

    async def apply_snapshot(
        self,
        user_id: str,
        desired: dict[str, object],
        *,
        origin: Literal["sync_apply"] = "sync_apply",
    ) -> str: ...

    async def acquire_lease(
        self, user_id: str, slug: str, *, owner: str, ttl_seconds: int
    ) -> tuple[str, int]: ...

    async def publish(
        self,
        user_id: str,
        slug: str,
        *,
        owner: str,
        fencing_token: int,
        desired_hash: str,
        observed_hash: str,
        active_ref: str,
        previous_ref: str | None = None,
    ) -> str: ...

    async def rollback_pre_publish(
        self, user_id: str, slug: str, *, owner: str, fencing_token: int
    ) -> str: ...


class TurnBoundaryObservedHashHook(Protocol):
    async def observed_hash(self, user_id: str, slug: str, path: str | Path) -> str: ...


def validate_managed_skill_path(root: str | Path, path: str | Path) -> Path:
    """Public seam for resolver callers that need a second containment check."""

    return ensure_managed_root_containment(root, path)


__all__ = [
    "CatalogOnlyUntilClaimed",
    "DiscoverAndExecuteExternalSkills",
    "DiscoveryDecision",
    "ExecutionResourceGate",
    "ExecutionResourceResolver",
    "ExternalSkillClaimReservationGate",
    "InMemoryExternalSkillClaimReservationGate",
    "SkillTreeMutationPort",
    "TurnBoundaryObservedHashHook",
    "validate_managed_skill_path",
]
