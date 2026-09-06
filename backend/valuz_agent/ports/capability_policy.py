"""Trusted per-task policy for OPTIONAL post-run checks.

Only server callers supply TaskCheckConfig. Host references provide location,
never permission or a reason to bypass checks. These policies cannot change
authorization, confirmation, or tool/schema validation. Queue items carry their
own config; continuations reuse an owner/session/run matched snapshot. Fresh
conversation inputs never inherit a previous input's resolved decision.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from valuz_agent.ports.message_context import HostRef


class OptionalCheckOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_enabled: StrictBool | None = None
    verification_enabled: StrictBool | None = None
    task_coverage_enabled: StrictBool | None = None


class TaskCheckConfig(BaseModel):
    """Non-secret, durable input authored by an owned server operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation: str = Field(default="conversation", min_length=1, max_length=128)
    origin: Literal["chat", "task", "automation"] = "chat"
    run_id: str | None = None
    revision: str | None = None
    automation_id: str | None = None
    playbook_definition_id: str | None = None
    playbook_run_id: str | None = None
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    overrides: OptionalCheckOverrides = Field(default_factory=OptionalCheckOverrides)


class TaskCheckContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    session_id: str
    project_id: str | None = None
    task_id: str | None = None
    host_ref: HostRef | None = None
    config: TaskCheckConfig
    policy_sources: tuple[str, ...] = ()


class TaskCheckPolicyPort(Protocol):
    async def resolve(self, context: TaskCheckContext) -> OptionalCheckOverrides | None:
        """First non-None value per check wins; errors defer to preferences.

        Resolve references under context.user_id before using their config.
        HostRef alone is not authority. Never inspect prompt text for policy.
        """
        ...


class HostCapabilityPolicyPort(Protocol):
    """Deprecated live-host compatibility; no sticky session stamp."""

    def task_coverage_override(self, host_ref: HostRef) -> bool | None:
        """Whether task-coverage continuations run for this host.

        ``True``/``False`` forces the value for sessions conversing on
        ``host_ref``; ``None`` defers to other policies and ultimately the
        user's global preference.
        """
        ...
