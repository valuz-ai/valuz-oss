"""Public memory data contracts shared by hosts and maintenance providers.

These types describe owner-scoped evidence and outcomes, not storage, scheduling
or action permission. No module implementation is imported by this port.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Target = Literal["user", "global", "project"]
TARGETS: tuple[Target, ...] = ("user", "global", "project")
Source = Literal["agent", "auto", "user"]

SourceKind = Literal["message", "session", "task", "input", "document", "tool", "manual", "import"]
SourceOrigin = Literal["owner", "agent", "untrusted", "system"]
MemoryKind = Literal["preference", "fact", "decision", "lesson", "work_context"]
MutationAction = Literal["add", "replace", "remove", "clear"]


class SourceRef(BaseModel):
    """Host-resolved evidence identity, never a model's claim of authority.

    The owner comes from the catalog, not an input field. Entrypoints must build
    references from verified messages/resources; an unclassified source stays
    untrusted. A revision identifies an observation, not a separate deletion scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    kind: SourceKind
    source_id: str = Field(min_length=1, max_length=256)
    revision: str | None = Field(default=None, max_length=128)
    origin: SourceOrigin = "untrusted"

    @field_validator("source_id")
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_id must not be blank")
        return value


class MemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    target: Target
    project_id: str | None = None
    content: str
    revision: int = Field(ge=1, strict=True)
    source: Source
    source_refs: tuple[SourceRef, ...] = ()
    kind: MemoryKind = "fact"
    namespace: str = "core"
    object_refs: tuple[str, ...] = ()
    observed_at: float = Field(allow_inf_nan=False, description="Observation time, Unix seconds")
    supersedes: tuple[str, ...] = ()
    confirmed: bool = False

    @model_validator(mode="after")
    def valid_scope(self) -> MemoryRecord:
        if self.target == "project":
            if not self.project_id or any(part in self.project_id for part in ("/", "\\", "..")):
                raise ValueError("project memory requires a valid project_id")
        elif self.project_id is not None:
            raise ValueError("Only project memory has project_id")
        return self


class MemoryInvalidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    record_id: str
    record_revision: int
    catalog_revision: int
    target: Target
    project_id: str | None = None
    namespace: str = "core"


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    owner_user_id: str
    authority_id: str | None = None
    authority_epoch: int | None = Field(default=None, ge=0, strict=True)
    revision: int = Field(ge=0, strict=True)
    records: tuple[MemoryRecord, ...] = ()

    @model_validator(mode="after")
    def paired_authority(self) -> MemorySnapshot:
        if (self.authority_id is None) != (self.authority_epoch is None):
            raise ValueError("Authority id and epoch must be provided together")
        if self.authority_id is not None and not self.authority_id.strip():
            raise ValueError("Authority id must not be blank")
        return self


class MemoryMutationResult(BaseModel):
    """A receipt contains identities, never a second retained copy of the text."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["applied", "noop"]
    revision: int
    record_id: str | None = None
    record_revision: int | None = None
    affected_ids: tuple[str, ...] = ()
    affected_project_ids: tuple[str, ...] = ()
    replayed: bool = False
    authority_id: str | None = None
    authority_epoch: int | None = Field(default=None, ge=0, strict=True)
    maintenance_complete: bool = True
    maintenance_reason_code: str | None = None

    @model_validator(mode="after")
    def paired_authority(self) -> MemoryMutationResult:
        if (self.authority_id is None) != (self.authority_epoch is None):
            raise ValueError("Authority id and epoch must be provided together")
        if self.authority_id is not None and not self.authority_id.strip():
            raise ValueError("Authority id must not be blank")
        return self


class MemoryError(ValueError):
    error_code = "memory.invalid"


class MemoryConflictError(MemoryError):
    error_code = "memory.revision_conflict"


class MemoryUnavailableError(MemoryError):
    error_code = "memory.unavailable"


class MemoryAdmissionUncertainError(MemoryUnavailableError):
    error_code = "memory.admission_recovery_required"


class MemoryProtectedError(MemoryError):
    error_code = "memory.protected"


MemoryAdmissionUncertain = MemoryAdmissionUncertainError
MemoryConflict = MemoryConflictError
MemoryUnavailable = MemoryUnavailableError
MemoryProtected = MemoryProtectedError


class MemoryMutationCommand(BaseModel):
    """Host-classified operation. This DTO does not itself authenticate evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    authority_id: str | None = None
    authority_epoch: int | None = Field(default=None, ge=0, strict=True)
    action: MutationAction
    target: Target
    operation_id: str = Field(min_length=1, max_length=128)
    base_revision: int = Field(ge=0, strict=True)
    record_id: str | None = None
    content: str | None = None
    project_id: str | None = None
    source: Source = "agent"
    source_refs: tuple[SourceRef, ...] = ()
    kind: MemoryKind | None = None
    namespace: str = "core"
    object_refs: tuple[str, ...] | None = None
    observed_at: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def paired_authority(self) -> MemoryMutationCommand:
        if (self.authority_id is None) != (self.authority_epoch is None):
            raise ValueError("Authority id and epoch must be provided together")
        if self.authority_id is not None and not self.authority_id.strip():
            raise ValueError("Authority id must not be blank")
        return self


class MemoryNamespaceDelegation(BaseModel):
    """Host-signed exact scope, not a client confirmation flag."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    owner_user_id: str
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    purpose: Literal["read", "accept_learning"]
    authority_id: str
    authority_epoch: int = Field(ge=0, strict=True)
    command_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str | None = None
    proposal_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_version_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def approved_write(self) -> MemoryNamespaceDelegation:
        fields = (
            self.operation_id,
            self.proposal_hash,
            self.candidate_hash,
            self.source_version_hash,
        )
        if self.purpose == "accept_learning" and not all(fields):
            raise ValueError("A learning write requires its approved operation and exact candidate")
        if self.purpose == "read" and any(value is not None for value in fields):
            raise ValueError("Read scope cannot assert a write approval")
        return self


class MemoryBackendPort(Protocol):
    """Async owner/namespace authority seam; failures must never trigger a fallback."""

    async def snapshot(self, user_id: str, *, namespace: str = "core") -> MemorySnapshot: ...

    async def recall(
        self,
        user_id: str,
        query: str = "",
        *,
        project_id: str | None = None,
        object_refs: tuple[str, ...] = (),
        limit: int = 8,
        max_chars: int = 4000,
        namespace: str = "core",
    ) -> MemorySnapshot: ...

    async def mutate(
        self,
        user_id: str,
        command: MemoryMutationCommand,
        *,
        namespace_access: object | None = None,
    ) -> MemoryMutationResult: ...

    async def forget_source(
        self,
        user_id: str,
        source_ref: SourceRef,
        *,
        operation_id: str,
        base_revision: int,
        namespace: str = "core",
        authority_id: str | None = None,
        authority_epoch: int | None = None,
    ) -> MemoryMutationResult: ...

    async def invalidated_ids(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        namespace: str = "core",
    ) -> tuple[MemoryInvalidation, ...]: ...

    async def sources_forgotten(
        self,
        user_id: str,
        source_refs: tuple[SourceRef, ...],
        *,
        namespace: str = "core",
    ) -> tuple[SourceRef, ...]: ...

    async def operation_receipt(
        self,
        user_id: str,
        operation_id: str,
        *,
        namespace: str = "core",
        expected_fingerprint: str | None = None,
    ) -> MemoryMutationResult | None: ...


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
    "MemoryAdmissionUncertain",
    "MemoryProtected",
    "MemoryMutationCommand",
    "MemoryBackendPort",
    "MemoryNamespaceDelegation",
]
