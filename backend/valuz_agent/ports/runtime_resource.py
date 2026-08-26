"""Authority and apply seams for managed runtime resources.

The OSS runtime owns its local rows and files.  Commercial editions can bind
the mutation ports to a Control Plane, but the OSS default remains a local
pass-through.  ``sync_apply`` is deliberately an explicit origin: a remote
snapshot may update the executable projection, while the same update must not
be interpreted as a user mutation and uploaded again.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

MutationOrigin = Literal["local", "sync_apply", "legacy_import"]
ManagedDomain = Literal["agent", "skill", "connector"]


class RuntimeResourceContractError(ValueError):
    """Raised when a managed-resource boundary receives an unsafe value."""


class ManagedMutationRejectedError(RuntimeError):
    """A Control Plane mutation was rejected before the local row was changed."""


ManagedMutationRejected = ManagedMutationRejectedError


@dataclass(frozen=True)
class ManagedMutationResult:
    """Result returned by an authority port after a successful mutation."""

    resource_id: str | None = None
    revision: int | None = None
    definition_etag: str | None = None
    credential_etag: str | None = None
    domain_versions: Mapping[str, int] = field(default_factory=dict)
    normalized: Mapping[str, Any] = field(default_factory=dict)
    cloud_committed: bool = False
    local_status: Literal["ready", "applying"] = "ready"


class ManagedAgentMutationPort(Protocol):
    async def mutate(
        self,
        user_id: str,
        command: Mapping[str, Any],
        *,
        expected_etag: str | None,
        idempotency_key: str | None,
        origin: MutationOrigin = "local",
    ) -> ManagedMutationResult:
        """Commit an Agent mutation before a local executable row is changed."""


class ManagedConnectorMutationPort(Protocol):
    async def mutate(
        self,
        user_id: str,
        definition: Mapping[str, Any],
        credential_patch: Mapping[str, Any] | None = None,
        *,
        expected_definition_etag: str | None,
        expected_credential_etag: str | None,
        idempotency_key: str | None,
        origin: MutationOrigin = "local",
    ) -> ManagedMutationResult:
        """Commit definition and credential changes as one authority operation."""


class RuntimeResourceApplyPort(Protocol):
    async def apply(
        self,
        user_id: str,
        domain: ManagedDomain,
        resource: Mapping[str, Any],
        *,
        origin: MutationOrigin,
    ) -> ManagedMutationResult:
        """Apply a typed snapshot to the local executable projection."""


class LocalManagedAgentMutationPort:
    """OSS default: local services remain the authority."""

    async def mutate(
        self,
        user_id: str,
        command: Mapping[str, Any],
        *,
        expected_etag: str | None = None,
        idempotency_key: str | None = None,
        origin: MutationOrigin = "local",
    ) -> ManagedMutationResult:
        if not user_id:
            raise RuntimeResourceContractError("user_id is required")
        if origin == "sync_apply":
            raise RuntimeResourceContractError(
                "sync_apply cannot enter the managed Agent mutation path"
            )
        return ManagedMutationResult(
            resource_id=str(command.get("resource_id") or command.get("id") or "") or None,
            normalized=dict(command),
            cloud_committed=False,
        )


class LocalManagedConnectorMutationPort:
    """OSS default connector authority; no cloud call is made."""

    async def mutate(
        self,
        user_id: str,
        definition: Mapping[str, Any],
        credential_patch: Mapping[str, Any] | None = None,
        *,
        expected_definition_etag: str | None = None,
        expected_credential_etag: str | None = None,
        idempotency_key: str | None = None,
        origin: MutationOrigin = "local",
    ) -> ManagedMutationResult:
        if not user_id:
            raise RuntimeResourceContractError("user_id is required")
        if origin == "sync_apply":
            raise RuntimeResourceContractError(
                "sync_apply cannot enter the managed Connector mutation path"
            )
        normalized = dict(definition)
        if credential_patch is not None:
            normalized["credential_patch"] = dict(credential_patch)
        return ManagedMutationResult(normalized=normalized, cloud_committed=False)


class LocalRuntimeResourceApplyPort:
    """OSS default apply seam; callers still perform the existing local write."""

    async def apply(
        self,
        user_id: str,
        domain: ManagedDomain,
        resource: Mapping[str, Any],
        *,
        origin: MutationOrigin,
    ) -> ManagedMutationResult:
        require_sync_apply_origin(origin)
        if not user_id:
            raise RuntimeResourceContractError("user_id is required")
        if domain not in {"agent", "skill", "connector"}:
            raise RuntimeResourceContractError(f"unsupported domain: {domain}")
        return ManagedMutationResult(
            resource_id=str(resource.get("resource_id") or resource.get("id") or "") or None,
            normalized=dict(resource),
        )


def require_sync_apply_origin(origin: MutationOrigin) -> None:
    """Guard against a projection update entering the mutation path."""

    if origin != "sync_apply":
        raise RuntimeResourceContractError(
            "executable projection updates must use origin='sync_apply'"
        )


_SAFE_REF = re.compile(r"^[^/\\\x00]+$")


def validate_skill_reference(value: str) -> str:
    """Validate the canonical slug/ref form accepted by the authority boundary."""

    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise RuntimeResourceContractError("skill reference must be a non-empty slug")
    if os.path.isabs(value) or not _SAFE_REF.fullmatch(value) or ".." in value.split("/"):
        raise RuntimeResourceContractError("absolute or traversal Skill references are forbidden")
    if "/" in value or "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise RuntimeResourceContractError("Skill reference must be a canonical slug")
    return value


def ensure_managed_root_containment(root: str | Path, candidate: str | Path) -> Path:
    """Resolve a candidate and reject symlink/path escapes from a managed root."""

    root_path = Path(root).expanduser().resolve(strict=False)
    candidate_path = Path(candidate).expanduser()
    try:
        resolved = candidate_path.resolve(strict=False)
        if os.path.commonpath((str(root_path), str(resolved))) != str(root_path):
            raise RuntimeResourceContractError("resolved path escapes the managed root")
    except (OSError, ValueError) as exc:
        raise RuntimeResourceContractError("managed path cannot be resolved") from exc
    return resolved


__all__ = [
    "LocalManagedAgentMutationPort",
    "LocalManagedConnectorMutationPort",
    "LocalRuntimeResourceApplyPort",
    "ManagedAgentMutationPort",
    "ManagedConnectorMutationPort",
    "ManagedDomain",
    "ManagedMutationRejected",
    "ManagedMutationResult",
    "MutationOrigin",
    "RuntimeResourceApplyPort",
    "RuntimeResourceContractError",
    "ensure_managed_root_containment",
    "require_sync_apply_origin",
    "validate_skill_reference",
]
