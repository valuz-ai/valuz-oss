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
    cron_expr: str = Field(
        min_length=1,
        description=(
            "Standard 5-field POSIX cron expression (minute hour day-of-month "
            "month day-of-week), e.g. '0 9 * * *' for 09:00 daily. For a "
            "natural-language schedule, translate it to cron yourself."
        ),
    )
    # ``None`` / empty string → inherit user default. Mirrors ADR-010
    # semantics, scoped down to cron triggers only.
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA timezone name (e.g. 'Asia/Shanghai', 'America/New_York'). "
            "REQUIRED for cron: take the user's timezone from the per-turn "
            "'Current time' context line and pass it verbatim; never invent "
            "one and never default to UTC on your own."
        ),
    )


class IntervalTrigger(BaseModel):
    kind: Literal["interval"] = "interval"
    # Floor at 30s (tick interval). The DB CheckConstraint enforces the
    # same; the Pydantic guard fails fast with a friendly 422 before the
    # row gets anywhere near the engine.
    seconds: int = Field(
        ge=MIN_INTERVAL_SECONDS,
        description=(
            "Interval in seconds between fires (minimum 30). Use for "
            "'every N minutes/seconds' schedules, NOT for clock-time ones."
        ),
    )


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


# ``project_member`` — already a member of the bound project. Picker shows
# the project's instantiated agents.
# ``library_agent``  — picked from the global ``LIBRARY/Agents``. Service
# instantiates it into the bound chat project at create time, so what
# lands in storage always normalises to ``project_member``. The frontend
# still sends the kind so the picker UI can show the right list.
AgentKind = Literal["project_member", "library_agent"]


# ── Action mode ──────────────────────────────────────────────────────


# ``chat`` — single agent run; the rendered prompt drives one session +
# send_message_sync. Original ScheduleService semantic.
# ``task`` — kick off a project task with the bound agent as Lead; the
# rendered prompt becomes the task goal. Only valid for project
# projects (validated at create / update time).
ActionKind = Literal["chat", "task"]


# ── Create / Update payloads ──────────────────────────────────────────


class AutomationCreatePayload(BaseModel):
    """Two-level routing on ``(project_kind, project_id)`` — see ADR-021 §4.

    - ``project_kind="chat"`` + ``project_id=None``  → service lazy-creates
      a fresh chat project named after the automation. Automation page
      "Chat" picker path.
    - ``project_kind="chat"`` + ``project_id=<id>``  → bind to that
      existing chat project. ``automation create`` MCP-from-chat path.
    - ``project_kind="project"`` + ``project_id=<id>`` → bind to the
      project. Required for project-kind automations.

    ``agent_kind`` + ``agent_slug`` together identify the executing agent:

    - chat automations pick from ``LIBRARY/Agents`` (``library_agent``).
    - project automations pick from the project's instantiated members
      (``project_member``). The service rejects mismatches (e.g. a project
      payload pointing at a library agent slug that isn't a member).
    """

    name: str = Field(min_length=1, max_length=50)

    project_kind: Literal["chat", "project"]
    project_id: str | None = None

    agent_kind: AgentKind
    agent_slug: str = Field(min_length=1)

    # Optional when a Playbook is pinned. At least one of prompt_template or
    # playbook_definition_id is enforced by AutomationService so API, Agent
    # proposal, and update paths share the same composition rule.
    prompt_template: str = ""

    # Optional immutable Playbook pin. ``playbook_version`` may be omitted on
    # create; the service resolves and stores the Definition's current version
    # exactly once. A version without a Definition is rejected.
    playbook_definition_id: str | None = Field(default=None, max_length=36)
    playbook_version: int | None = Field(default=None, ge=1)

    trigger: Trigger

    # Execution mode (see ``ActionKind`` docstring). Default ``chat`` keeps
    # callers that omit the field on the simple-task path; the service
    # rejects ``task`` on chat projects.
    action_kind: ActionKind = "chat"
    # Worktree isolation (design §5) — valid for BOTH action kinds, gated on the
    # bound project being a git repo. ``chat`` runs each fire in its own git
    # worktree of the project repo; ``task`` runs lead + every member in one
    # worktree (clean worktrees auto-remove at finish). Silently dropped for
    # non-git (chat-sentinel) projects.
    worktree: bool = False


class AutomationUpdatePayload(BaseModel):
    """Partial update. ``None`` everywhere = "leave field untouched".

    ``trigger`` is all-or-nothing: passing it replaces the whole trigger
    (kind + config); omitting it keeps the existing one. We deliberately
    don't allow patching cron_expr alone — switching kinds with a half-
    populated row would race the CheckConstraint.

    Cross-kind agent swap (``library_agent`` → ``project_member`` or
    vice-versa) is intentionally not supported on update — the project
    binding makes those rows fundamentally different. Delete + recreate.
    """

    name: str | None = Field(default=None, min_length=1, max_length=50)
    prompt_template: str | None = None
    trigger: Trigger | None = None
    agent_slug: str | None = None
    # Switching action_kind on update is allowed (within the project's
    # constraint — task only on projects). The service validates
    # the resulting (project_kind, action_kind) pair.
    action_kind: ActionKind | None = None
    worktree: bool | None = None
    # Both fields are patchable. Explicit ``playbook_definition_id=null``
    # unpins the Playbook; changing a Definition without naming a version pins
    # that Definition's current version at update time.
    playbook_definition_id: str | None = Field(default=None, max_length=36)
    playbook_version: int | None = Field(default=None, ge=1)


# ── Response models ──────────────────────────────────────────────────


class AutomationItemResponse(BaseModel):
    automation_id: str
    project_id: str
    project_name: str
    project_kind: str

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
    # Worktree isolation flag (both action kinds; git-repo projects only).
    worktree: bool = False
    playbook_definition_id: str | None = None
    playbook_version: int | None = None

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
    project_id: str
    project_name: str
    project_kind: str
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
    project_id: str
    trigger_type: str
    status: str
    triggered_at: int
    started_at: int | None
    completed_at: int | None
    duration_ms: int | None
    result_summary: str | None
    error_code: str | None
    error_message_key: str | None
    session_id: str | None
    created_files: list[str]
    playbook_run_id: str | None = None
    # The task this run kicked off (task automations only) — id + title let the
    # execution log deep-link to it ("→ 任务《title》"). ``None`` for non-task runs.
    task_id: str | None = None
    task_title: str | None = None
    # Live status of that task. ``run.status`` freezes to ``success`` the moment
    # kickoff returns, so the client prefers this — resolved from the lead
    # session's task at read time (``active`` / ``completed`` / ``failed`` /
    # ``paused``). ``None`` for non-task runs or when the task is gone.
    task_status: str | None = None
    # When the run kicked off a task, the owning ``task_id`` so the client can
    # deep-link to the task detail page instead of the raw lead conversation.
    # ``None`` for non-task (chat) runs.
    task_id: str | None = None


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


class AutomationProjectTarget(BaseModel):
    """One selectable target in the project picker on the create form.

    Composition rule mirrors the old schedule one:
    - A fixed "Chat" sentinel at the top (``project_id=None``).
    - Every project by stable order.

    Chat projects aren't listed individually — each chat-bound automation
    allocates its own row at create time (or reuses the calling chat's
    project via the ``automation`` MCP tool), so a global list of them
    would be UI noise.
    """

    id: str
    name: str
    kind: Literal["chat", "project"]
    project_id: str | None


class AutomationProjectTargetsResponse(BaseModel):
    targets: list[AutomationProjectTarget]


# ── MCP tool surface (replaces the legacy ``cronjob`` schema) ────────


class AutomationToolPayload(BaseModel):
    """Multi-action ``automation`` tool input.

    Replaces the old ``cronjob`` payload:

    - ``model_id / provider_id`` removed (execution identity follows the agent)
    - ``cron_expr`` replaced by polymorphic ``trigger``
    - ``agent_slug`` added (required on create; the agent picker)
    """

    action: str = Field(
        description="One of: create, get, list, update, pause, resume, run, remove.",
    )
    automation_id: str | None = Field(
        default=None,
        description=(
            "Automation id — required for get/update/pause/resume/run/remove; "
            "obtain one from a prior list."
        ),
    )
    name: str | None = Field(
        default=None,
        max_length=50,
        description="Display name (required on create).",
    )
    prompt_template: str | None = Field(
        default=None,
        description=(
            "The instruction the agent receives on every fire (required on "
            "create unless playbook_definition_id is set)."
        ),
    )
    # ``run`` only: extra text appended to the automation's instruction for THIS
    # run (e.g. a discovered task id). Ignored by the other actions.
    input: str | None = Field(
        default=None,
        description=(
            "run only: extra text appended to the automation's instruction for "
            "this single run (e.g. a task id you discovered). Does NOT modify the "
            "saved automation."
        ),
    )
    trigger: Trigger | None = Field(
        default=None,
        description=(
            "Discriminated trigger — REQUIRED on create. One of "
            "{kind: 'cron', cron_expr, timezone} / {kind: 'interval', seconds} / "
            "{kind: 'manual'}."
        ),
    )
    agent_slug: str | None = Field(
        default=None,
        description=(
            "CONTEXT-DEPENDENT — do not treat as universally required. Chat / "
            "quick conversation (no project): OPTIONAL, omit and the server "
            "resolves the execution agent (the one you are talking to, or the "
            "system agent Valurion when none is bound). Project session: "
            "REQUIRED and must be a project team member — call list_members "
            "first to see candidates. Do NOT invent slugs."
        ),
    )
    # Execution mode on create. ``chat`` (default) runs the bound agent once;
    # ``task`` kicks off a project task with the bound agent as Lead — only
    # valid from a PROJECT session (the tool rejects ``task`` in a chat).
    action_kind: Literal["chat", "task"] | None = Field(
        default=None,
        description=(
            "'chat' (default) runs the bound agent once per fire; 'task' kicks "
            "off a full project task with the bound agent as the Lead — ONLY "
            "valid in a PROJECT session; omit it / use 'chat' in a chat."
        ),
    )
    # create/update, both action kinds: run each fire in an isolated git
    # worktree of the project repo. ``chat`` isolates the single session;
    # ``task`` isolates the whole task (lead + every member). Requires the
    # project to be a git repository (silently dropped otherwise).
    worktree: bool | None = Field(
        default=None,
        description=(
            "Optional (create/update): true runs each fire in an isolated git "
            "worktree of the project repo. Only meaningful for git-repo "
            "projects (silently ignored for chat-only projects). Default false."
        ),
    )
    playbook_definition_id: str | None = Field(
        default=None,
        max_length=36,
        description=(
            "Optional immutable Playbook pin (action_kind must be 'chat'). "
            "When set, playbook_version may be omitted — the current version "
            "is pinned at create time."
        ),
    )
    playbook_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Exact immutable Playbook version to pin. Only meaningful together "
            "with playbook_definition_id; omit to pin the Definition's current "
            "version."
        ),
    )
    scope: Literal["all", "this"] | None = Field(
        default=None,
        description=(
            "`this` = current project only; `all` = entire user library "
            "(only honoured in chat sessions). Omit to use the natural "
            "default: chat sessions see all automations; project sessions "
            "see only the current project regardless of value."
        ),
    )


class AutomationProposalSpec(BaseModel):
    """Resolved (but NOT persisted) automation proposed by the ``create``
    action. Echoed in ``AutomationToolResult.proposal`` so the frontend can
    render a confirmation card and replay the exact spec to the confirm
    endpoint; the LLM reads the same JSON. ``create`` validates + previews
    and writes nothing — the user's confirm click does the actual create
    (mirrors ``propose_agent`` → ``confirm``)."""

    name: str
    prompt_template: str
    trigger: Trigger
    # Resolved executing agent. In a chat the slug defaults to the session's
    # bound agent; in a project session it's the chosen project member (the
    # Lead in ``task`` mode).
    agent_slug: str
    agent_kind: str
    agent_name: str | None = None
    action_kind: str
    # Worktree isolation (both action kinds; git-repo projects only) — echoed
    # so the confirm card can display and replay it.
    worktree: bool = False
    playbook_definition_id: str | None = None
    playbook_version: int | None = None
    # Localised "每天 9 点" / "every 5 minutes" — the card's primary schedule line.
    trigger_human_readable: str
    # First fire instant (epoch ms) the schedule would produce — preview only.
    next_run_at: int | None = None


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
    # Set only by the ``create`` action — the proposed-but-unsaved spec the
    # confirmation card renders. ``automation`` stays ``None`` on create
    # (nothing is persisted until the user confirms).
    proposal: AutomationProposalSpec | None = None
    error_code: str | None = None


# ── Proposal confirm + status (mirrors propose_agent → confirm) ──────


class AutomationProposalConfirmRequest(BaseModel):
    """Body for ``POST /v1/automations/proposals/{session_id}/confirm`` — the
    user accepting an automation the ``create`` action proposed. ``tool_call_id``
    is the kernel ``tool_use`` id of the proposing call; it's persisted on the
    created row (``origin_tool_call_id``) so a session reload can detect the
    automation already exists. Project / agent_kind are re-resolved from the
    session, mirroring how the tool resolved them when proposing."""

    tool_call_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=50)
    prompt_template: str = ""
    trigger: Trigger
    agent_slug: str | None = None
    action_kind: ActionKind = "chat"
    worktree: bool = False
    playbook_definition_id: str | None = Field(default=None, max_length=36)
    playbook_version: int | None = Field(default=None, ge=1)


class AutomationProposalStatusRequest(BaseModel):
    tool_call_ids: list[str]


class AutomationProposalStatusEntry(BaseModel):
    automation_id: str


class AutomationProposalStatusResponse(BaseModel):
    """Maps each already-confirmed proposing ``tool_call_id`` to its created
    automation, so the frontend can seed those cards to a confirmed state on
    re-entry instead of showing a fresh Confirm button."""

    confirmed: dict[str, AutomationProposalStatusEntry]
