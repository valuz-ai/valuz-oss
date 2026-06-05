"""Automation Pydantic schemas.

Public shape mirrors the old schedule schemas (so route handlers can be
ported with minimal reshape) with two key differences:

1. ``trigger`` is a discriminated union — cron / interval / manual rather
   than a flat ``cron_expr`` field. This makes the wire shape unambiguous
   and gives the frontend one place to switch UI on.

2. ``agent_slug`` replaces the ``(model_id, provider_id, runtime_id)``
   triple. Execution identity comes from the bound agent at fire time —
   see [ADR-021](../../../../docs/decisions/ADR-021-automation-trigger-agent.md).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from valuz_agent.modules.automations.triggers import MIN_INTERVAL_SECONDS

# ── Trigger discriminated union ───────────────────────────────────────


class CronTrigger(BaseModel):
    kind: Literal["cron"] = "cron"
    cron_expr: str = Field(min_length=1)
    # ``None`` / empty string → inherit user default. Mirrors ADR-010
    # semantics, scoped down to cron triggers only.
    timezone: str | None = None


class IntervalTrigger(BaseModel):
    kind: Literal["interval"] = "interval"
    # Floor at 30s (tick interval). The DB CheckConstraint enforces the
    # same; the Pydantic guard fails fast with a friendly 422 before the
    # row gets anywhere near the engine.
    seconds: int = Field(ge=MIN_INTERVAL_SECONDS)


class ManualTrigger(BaseModel):
    """No automated firing — only ``run_now`` (and future webhook). Reserved
    for the case where the user wants the automation row to exist as a
    template even though it's not on a schedule."""

    kind: Literal["manual"] = "manual"


Trigger = Annotated[
    Union[CronTrigger, IntervalTrigger, ManualTrigger],  # noqa: UP007
    Field(discriminator="kind"),
]


# ── Agent reference ───────────────────────────────────────────────────


# ``project_member`` — already a member of the bound workspace. Picker shows
# the project's instantiated agents.
# ``library_agent``  — picked from the global ``LIBRARY/Agents``. Service
# instantiates it into the bound chat workspace at create time, so what
# lands in storage always normalises to ``project_member``. The frontend
# still sends the kind so the picker UI can show the right list.
AgentKind = Literal["project_member", "library_agent"]


# ── Action mode ──────────────────────────────────────────────────────


# ``chat`` — single agent run; the rendered prompt drives one session +
# send_message_sync. Original ScheduleService semantic.
# ``task`` — kick off a project task with the bound agent as Lead; the
# rendered prompt becomes the task goal. Only valid for project
# workspaces (validated at create / update time).
ActionKind = Literal["chat", "task"]


# ── Create / Update payloads ──────────────────────────────────────────


class AutomationCreatePayload(BaseModel):
    """Two-level routing on ``(workspace_kind, workspace_id)`` — see ADR-021 §4.

    - ``workspace_kind="chat"`` + ``workspace_id=None``  → service lazy-creates
      a fresh chat workspace named after the automation. Automation page
      "Chat" picker path.
    - ``workspace_kind="chat"`` + ``workspace_id=<id>``  → bind to that
      existing chat workspace. ``automation create`` MCP-from-chat path.
    - ``workspace_kind="project"`` + ``workspace_id=<id>`` → bind to the
      project workspace. Required for project-kind automations.

    ``agent_kind`` + ``agent_slug`` together identify the executing agent:

    - chat automations pick from ``LIBRARY/Agents`` (``library_agent``).
    - project automations pick from the project's instantiated members
      (``project_member``). The service rejects mismatches (e.g. a project
      payload pointing at a library agent slug that isn't a member).
    """

    name: str = Field(min_length=1, max_length=50)

    workspace_kind: Literal["chat", "project"]
    workspace_id: str | None = None

    agent_kind: AgentKind
    agent_slug: str = Field(min_length=1)

    prompt_template: str = Field(min_length=1)

    trigger: Trigger

    # Execution mode (see ``ActionKind`` docstring). Default ``chat`` keeps
    # callers that omit the field on the simple-task path; the service
    # rejects ``task`` on chat workspaces.
    action_kind: ActionKind = "chat"


class AutomationUpdatePayload(BaseModel):
    """Partial update. ``None`` everywhere = "leave field untouched".

    ``trigger`` is all-or-nothing: passing it replaces the whole trigger
    (kind + config); omitting it keeps the existing one. We deliberately
    don't allow patching cron_expr alone — switching kinds with a half-
    populated row would race the CheckConstraint.

    Cross-kind agent swap (``library_agent`` → ``project_member`` or
    vice-versa) is intentionally not supported on update — the workspace
    binding makes those rows fundamentally different. Delete + recreate.
    """

    name: str | None = Field(default=None, min_length=1, max_length=50)
    prompt_template: str | None = None
    trigger: Trigger | None = None
    agent_slug: str | None = None
    # Switching action_kind on update is allowed (within the workspace's
    # constraint — task only on project workspaces). The service validates
    # the resulting (workspace_kind, action_kind) pair.
    action_kind: ActionKind | None = None


# ── Response models ──────────────────────────────────────────────────


class AutomationItemResponse(BaseModel):
    automation_id: str
    workspace_id: str
    workspace_name: str
    workspace_kind: str

    name: str
    agent_kind: str
    agent_slug: str
    # Resolved name of the bound agent so the list can render it without an
    # extra round-trip. ``None`` when the agent has been deleted upstream —
    # the row stays so the user can see the broken reference and fix it.
    agent_name: str | None
    # Execution mode (see ``ActionKind``). UI uses this to render the
    # appropriate badge and pre-select the right Tab when editing.
    action_kind: str

    # Trigger payload re-projected as a discriminated union so the frontend
    # doesn't have to reconstitute it from flat columns.
    trigger: Trigger
    # Localised "每天 9 点" / "Every 5 minutes" string; convenient for table
    # rendering. Cron rows go through ``cron-descriptor``; interval rows
    # format inline.
    trigger_human_readable: str

    status: str
    next_run_at: int | None
    last_run_at: int | None
    last_run_status: str | None


class AutomationGroupResponse(BaseModel):
    workspace_id: str
    workspace_name: str
    workspace_kind: str
    automations: list[AutomationItemResponse]


class AutomationDetailResponse(AutomationItemResponse):
    prompt_template: str
    total_runs: int
    recent_failures: int
    created_at: int
    updated_at: int


class AutomationRunItemResponse(BaseModel):
    run_id: str
    automation_id: str
    workspace_id: str
    trigger_type: str
    status: str
    triggered_at: int
    started_at: int | None
    completed_at: int | None
    duration_ms: int | None
    result_summary: str | None
    error_code: str | None
    session_id: str | None
    created_files: list[str]


class CronValidateRequest(BaseModel):
    expr: str
    timezone: str = "UTC"


class CronValidationResultResponse(BaseModel):
    valid: bool
    human_readable: str | None = None
    next_runs: list[int] = []
    error_message: str | None = None


class IntervalValidateRequest(BaseModel):
    seconds: int


class IntervalValidationResultResponse(BaseModel):
    valid: bool
    human_readable: str | None = None
    error_message: str | None = None


class AutomationRunAcceptedResponse(BaseModel):
    run_id: str
    automation_id: str
    status: str


class AutomationWorkspaceTarget(BaseModel):
    """One selectable target in the workspace picker on the create form.

    Composition rule mirrors the old schedule one:
    - A fixed "Chat" sentinel at the top (``workspace_id=None``).
    - Every project workspace by stable order.

    Chat workspaces aren't listed individually — each chat-bound automation
    allocates its own row at create time (or reuses the calling chat's
    workspace via the ``automation`` MCP tool), so a global list of them
    would be UI noise.
    """

    id: str
    name: str
    kind: Literal["chat", "project"]
    workspace_id: str | None


class AutomationWorkspaceTargetsResponse(BaseModel):
    targets: list[AutomationWorkspaceTarget]


# ── MCP tool surface (replaces the legacy ``cronjob`` schema) ────────


class AutomationToolPayload(BaseModel):
    """Multi-action ``automation`` tool input.

    Replaces the old ``cronjob`` payload:

    - ``model_id / provider_id`` removed (execution identity follows the agent)
    - ``cron_expr`` replaced by polymorphic ``trigger``
    - ``agent_slug`` added (required on create; the agent picker)
    """

    action: str = Field(
        description="One of: create, list, update, pause, resume, run, remove.",
    )
    automation_id: str | None = None
    name: str | None = Field(default=None, max_length=50)
    prompt_template: str | None = None
    trigger: Trigger | None = None
    agent_slug: str | None = None
    scope: str | None = Field(
        default=None,
        description=(
            "`this` = current workspace only; `all` = entire user library "
            "(only honoured in chat sessions). Omit to use the natural "
            "default: chat sessions see all automations; project sessions "
            "see only the current project regardless of value."
        ),
    )


class AutomationToolResult(BaseModel):
    """Structured tool result. Frontend ``AutomationToolCard`` parses this
    JSON into a card; the LLM also reads the same JSON via the tool result
    content channel."""

    action: str
    ok: bool
    message: str
    automation: AutomationItemResponse | None = None
    automations: list[AutomationItemResponse] = []
    next_runs: list[int] = []
    error_code: str | None = None
