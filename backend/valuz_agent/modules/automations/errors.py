"""Automation module exceptions.

Error code segment ``404_71x / 422_71x / 409_71x / 403_71x`` — distinct from
the legacy schedules range (70x) so a transitional log entry still tells you
which module it came from.
"""

from valuz_agent.infra.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)

# ── 404 ────────────────────────────────────────────────────────────────


class AutomationNotFound(NotFoundError):
    error_code = 404_711
    message = "Automation not found"


class AutomationRunNotFound(NotFoundError):
    error_code = 404_712
    message = "Automation run not found"


class AutomationWorkspaceNotFound(NotFoundError):
    error_code = 404_713
    message = "Automation workspace not found"


class AgentNotInWorkspace(NotFoundError):
    """The (workspace_id, agent_slug) pair doesn't resolve to a project member.

    Distinct from ``AgentNotFound`` (library agent missing): this fires at
    bind/fire time when the workspace was found but the chosen agent isn't
    a member of it.
    """

    error_code = 404_714
    message = "Agent is not a member of this workspace"


class AgentNotFound(NotFoundError):
    """The library agent slug doesn't resolve. Surfaced when ``library_agent``
    automations reference an ``AgentRow.slug`` that no longer exists."""

    error_code = 404_715
    message = "Agent not found in the library"


# ── 422 ────────────────────────────────────────────────────────────────


class InvalidCronExpression(UnprocessableEntityError):
    error_code = 422_711
    message = "Invalid cron expression"


class IntervalTooShort(UnprocessableEntityError):
    """Interval triggers floor at 30s — the runner tick is 30s, anything
    smaller would alias against the tick and fire unpredictably."""

    error_code = 422_712
    message = "Interval must be at least 30 seconds"


class AutomationNameEmpty(UnprocessableEntityError):
    error_code = 422_713
    message = "Automation name is empty"


class AutomationPromptEmpty(UnprocessableEntityError):
    error_code = 422_714
    message = "Automation prompt template is empty"


class AutomationPromptRenderFailed(UnprocessableEntityError):
    error_code = 422_715
    message = "Automation prompt template render failed"


class AutomationAgentRequired(UnprocessableEntityError):
    """``agent_slug`` is mandatory on create; the agent picker can't be empty."""

    error_code = 422_716
    message = "Agent must be selected for the automation"


class AutomationTaskOnlyOnProject(UnprocessableEntityError):
    """``action_kind="task"`` requires a project workspace.

    Task mode kicks off a project task with the bound agent as Lead — the
    task orchestrator needs a project context (multiple members, the
    project's task table, project plan). Chat workspaces don't have that
    structure, so we reject the combination at the API edge with a clear
    message rather than failing mid-fire inside ``task_orchestrator.kickoff``.
    """

    error_code = 422_717
    message = "Task mode is only available for project workspaces"


# ── 409 ────────────────────────────────────────────────────────────────


class AutomationPaused(ConflictError):
    error_code = 409_711
    message = "Automation is paused"


class AutomationAlreadyRunning(ConflictError):
    error_code = 409_712
    message = "Automation already has an active run"


class AutomationAlreadyQueued(ConflictError):
    error_code = 409_713
    message = "Automation is already queued"


# ── 403 ────────────────────────────────────────────────────────────────


class AutomationCrossWorkspaceDenied(ForbiddenError):
    """A session in workspace A is trying to mutate an automation in
    workspace B. ``automation`` MCP tool enforces this — project sessions
    must stay in their project; chat sessions can reach across only when
    ``scope=all``."""

    error_code = 403_711
    message = "Automation belongs to a different workspace"
