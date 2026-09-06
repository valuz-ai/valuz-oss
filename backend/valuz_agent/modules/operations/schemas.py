"""Wire contracts for persistent product operations and decisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OperationProposal(BaseModel):
    operation_type: str = Field(min_length=1, max_length=96)
    operation_version: int = Field(default=1, ge=1)
    project_id: str | None = Field(default=None, max_length=36)
    actor_kind: Literal["user", "agent", "playbook", "automation", "system"]
    actor_id: str | None = Field(default=None, max_length=128)
    origin_session_id: str | None = Field(default=None, max_length=36)
    origin_tool_call_id: str | None = Field(default=None, max_length=128)
    origin_playbook_run_id: str | None = Field(default=None, max_length=36)
    origin_automation_run_id: str | None = Field(default=None, max_length=36)
    target_refs: list[dict[str, Any]] = Field(default_factory=list)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    expected_revisions: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "material", "destructive", "external"] = "material"
    confirmation_policy: Literal[
        "direct", "explicit_submit", "confirm", "approval", "preauthorized"
    ] = "confirm"
    idempotency_key: str = Field(min_length=1, max_length=128)
    #: Epoch ms after which the pending proposal can no longer be confirmed.
    #: Omitted → the registration's ``default_ttl_ms`` (or never).
    expires_at: int | None = Field(default=None, ge=0)


class OperationDecisionRequest(BaseModel):
    proposal_hash: str = Field(min_length=64, max_length=64)
    comment: str | None = Field(default=None, max_length=4_000)
    #: Parameters of the confirmation itself, for proposals that leave a
    #: choice to the user (which of two ways to resolve a conflict). Handed
    #: to the handler as ``OperationContext.decision``; ignored on cancel.
    decision: dict[str, Any] | None = None


class OperationRequestChangesRequest(BaseModel):
    proposal_hash: str = Field(min_length=64, max_length=64)
    #: What the proposer must change. Required: a request for changes
    #: without a reason is not actionable.
    comment: str = Field(min_length=1, max_length=4_000)


class OperationDecisionView(BaseModel):
    decision: Literal["approve", "reject", "request_changes"]
    decided_by: str
    decided_at: int
    proposal_hash: str
    comment: str | None


class OperationView(BaseModel):
    id: str
    project_id: str | None
    operation_type: str
    operation_version: int
    actor_kind: str
    actor_id: str | None
    origin_session_id: str | None
    origin_tool_call_id: str | None
    origin_playbook_run_id: str | None
    origin_automation_run_id: str | None
    target_refs: list[dict[str, Any]]
    input_payload: dict[str, Any]
    preview: dict[str, Any]
    expected_revisions: dict[str, Any]
    risk_level: str
    confirmation_policy: str
    state: str
    proposal_hash: str
    canonical_result_refs: list[dict[str, Any]]
    result_payload: dict[str, Any]
    error_code: str | None
    error_message: str | None
    expires_at: int | None
    superseded_by_id: str | None
    #: Most recent confirmation decision on this record, so the proposer can
    #: read a ``request_changes`` comment back.
    latest_decision: OperationDecisionView | None
    created_at: int
    updated_at: int


class OperationStatusRequest(BaseModel):
    operation_ids: list[str] = Field(default_factory=list, max_length=100)


class OperationStatusResponse(BaseModel):
    operations: dict[str, OperationView]


__all__ = [
    "OperationDecisionRequest",
    "OperationDecisionView",
    "OperationProposal",
    "OperationRequestChangesRequest",
    "OperationStatusRequest",
    "OperationStatusResponse",
    "OperationView",
]
