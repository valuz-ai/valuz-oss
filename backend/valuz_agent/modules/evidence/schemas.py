"""Stable durable reference contracts; CitationBundle wire remains unchanged."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceStatus = Literal["ready", "stale", "missing", "forbidden", "retracted", "superseded"]


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=256)
    version: str | None = Field(default=None, max_length=256)


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_type: str = Field(min_length=1, max_length=32)
    provider_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=256)
    source_version: str | None = Field(default=None, max_length=256)


class Locator(BaseModel):
    """Normalized reference envelope, not a replacement for Citation locator JSON."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: str = Field(min_length=1, max_length=64)
    value: dict[str, Any]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=36)
    role: str | None = Field(default=None, max_length=64)


class EvidenceSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid", strict=True)
    id: str
    user_id: str
    created_at: int
    updated_at: int
    fingerprint: str
    source_type: str
    provider_id: str
    source_id: str
    source_version: str | None
    source_title: str
    canonical_url: str | None
    locator: dict[str, Any] | None
    evidence_kind: str
    snapshot: dict[str, Any]
    content_hash: str
    as_of: str | None
    period: str | None
    unit: str | None
    currency: str | None
    scale: str | None
    basis: str | None
    status: EvidenceStatus

    @property
    def source_ref(self) -> SourceRef:
        return SourceRef(
            source_type=self.source_type,
            provider_id=self.provider_id,
            source_id=self.source_id,
            source_version=self.source_version,
        )


class ProvenanceInput(BaseModel):
    """Lineage supplied by an authorized command; no industry taxonomy in core."""

    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str | None = Field(default=None, max_length=36)
    context_refs: list[EntityRef] = Field(default_factory=list, max_length=100)
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_version: str | None = Field(default=None, max_length=128)
    origin_kind: str = Field(min_length=1, max_length=32)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    evidence_snapshot_refs: list[str] = Field(default_factory=list, max_length=200)
    parent_provenance_refs: list[str] = Field(default_factory=list, max_length=200)
    session_id: str | None = Field(default=None, max_length=64)
    turn_id: str | None = Field(default=None, max_length=64)
    message_id: str | None = Field(default=None, max_length=64)
    run_ref: str | None = Field(default=None, max_length=128)
    actor_user_id: str | None = Field(default=None, max_length=64)
    agent_ref: str | None = Field(default=None, max_length=128)
    tool_name: str | None = Field(default=None, max_length=128)
    tool_call_id: str | None = Field(default=None, max_length=128)
    method_ref: str | None = Field(default=None, max_length=128)
    model_ref: str | None = Field(default=None, max_length=256)
    policy_revision: str | None = Field(default=None, max_length=128)
    transformation_class: str | None = Field(default=None, max_length=32)
    input_version_refs: list[str] = Field(default_factory=list, max_length=200)
    base_version_ref: str | None = Field(default=None, max_length=128)
    output_version_ref: str | None = Field(default=None, max_length=128)
    data_as_of: str | None = Field(default=None, max_length=64)
    observed_at: str | None = Field(default=None, max_length=64)
    approval_ref: str | None = Field(default=None, max_length=128)
    domain_operation_ref: str | None = Field(default=None, max_length=36)
    notes: str | None = Field(default=None, max_length=16_000)


class ProvenanceRecord(ProvenanceInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    created_at: int
    updated_at: int


class PendingEvidenceSeal(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid", strict=True)
    id: str
    user_id: str
    created_at: int
    updated_at: int
    operation_id: str
    message_id: str
    citation_id: str
    claim_id: str | None
    payload: dict[str, Any]
    citation_hash: str
    validation_revision: str
    expires_at: int | None
    status: Literal["pending", "consumed", "expired", "invalid"]
    historical_only: bool


class PendingEvidenceSealInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=36)
    message_id: str = Field(min_length=1, max_length=64)
    citation_id: str = Field(min_length=1, max_length=128)
    claim_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any]
    citation_hash: str = Field(min_length=1, max_length=80)
    validation_revision: str = Field(min_length=1, max_length=32)
    expires_at: int | None = Field(default=None, gt=0)


class ProvenancePage(BaseModel):
    items: list[ProvenanceRecord]
    next_before: tuple[int, str] | None
