"""Playbook Definition, version and Run HTTP/domain contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class PlaybookContent(BaseModel):
    goal: str = Field(min_length=1, max_length=20_000)
    applicability: dict[str, Any] = Field(default_factory=dict)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    context_reads: list[str] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    context_writes: list[dict[str, Any]] = Field(default_factory=list)
    failure_policy: Literal["retry", "continue_partial", "stop"] = "stop"


class PlaybookCreateRequest(PlaybookContent):
    name: str = Field(min_length=1, max_length=200)
    project_id: str | None = Field(default=None, max_length=36)
    current_project_id: str | None = Field(default=None, max_length=36)
    origin: Literal["user", "system_example_copy", "fork"] = "user"
    source_definition_id: str | None = Field(default=None, max_length=36)
    produced_by_run: str | None = Field(default=None, max_length=128)


class PlaybookVersionCreateRequest(PlaybookContent):
    base_version: int = Field(ge=1)
    status: Literal["draft", "active", "retired"] | None = None
    produced_by_run: str | None = Field(default=None, max_length=128)


class PlaybookDefinitionView(BaseModel):
    id: str
    project_id: str
    name: str
    status: str
    origin: str
    source_definition_id: str | None
    current_version: int
    revision: int
    created_at: int
    updated_at: int


class PlaybookVersionView(PlaybookContent):
    id: str
    definition_id: str
    version: int
    created_by: str | None
    produced_by_run: str | None
    base_version: int | None
    created_at: int


class PlaybookRunCreateRequest(BaseModel):
    definition_id: str = Field(min_length=1, max_length=36)
    definition_version: int | None = Field(default=None, ge=1)
    project_id: str | None = Field(default=None, max_length=36)
    current_project_id: str | None = Field(default=None, max_length=36)
    research_scope_id: str | None = Field(default=None, max_length=36)
    trigger_kind: Literal["user", "agent", "automation", "playbook", "api"] = "user"
    trigger_ref: str | None = Field(default=None, max_length=128)
    subject_refs: list[dict[str, Any]] = Field(default_factory=list)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)


class PlaybookRunUpdateRequest(BaseModel):
    status: Literal[
        "planning",
        "running",
        "waiting_approval",
        "completed",
        "failed",
        "stopped",
    ]
    plan: list[dict[str, Any]] | None = None
    tasks: list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    approvals: list[dict[str, Any]] | None = None
    artifact_refs: list[str] | None = None
    change_set_refs: list[str] | None = None
    output_refs: list[dict[str, Any]] | None = None
    checkpoint: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def require_failure_reason(self) -> PlaybookRunUpdateRequest:
        if self.status == "failed" and not (self.error_code or self.error_message):
            raise ValueError("failed PlaybookRun requires an error")
        return self


class PlaybookRunView(BaseModel):
    id: str
    definition_id: str
    definition_version: int
    project_id: str
    research_scope_id: str | None
    status: str
    trigger_kind: str
    trigger_ref: str | None
    subject_refs: list[dict[str, Any]]
    input_snapshot: dict[str, Any]
    context_snapshot: dict[str, Any]
    plan: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    artifact_refs: list[str]
    change_set_refs: list[str]
    output_refs: list[dict[str, Any]]
    checkpoint: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: int | None
    completed_at: int | None
    created_at: int
    updated_at: int


__all__ = [
    "PlaybookContent",
    "PlaybookCreateRequest",
    "PlaybookDefinitionView",
    "PlaybookRunCreateRequest",
    "PlaybookRunUpdateRequest",
    "PlaybookRunView",
    "PlaybookVersionCreateRequest",
    "PlaybookVersionView",
]
