"""In-process MCP server exposing the ``automation`` tool.

Replaces the legacy ``cronjob`` tool per ADR-021. Same in-process MCP
pattern (FastMCP + ContextVar-scoped session_id + ASGI wrapper with the
per-process shared secret), but the tool surface is rebuilt:

Wire shape
==========

POST /_internal/mcp/automations/mcp
  (also served at the legacy ``/internal/mcp/automations/mcp`` — ADR-013
  dual-mount, see ``api/app.py::_mount_internal``)
  headers:
    X-Valuz-Internal:    <per-process token>
    X-Valuz-Session-Id:  <kernel session id>

Permission model
================

The tool resolves the calling kernel session into ``(project_id,
project_kind)`` and lets:

- **Chat session** — manage every user-level automation (list / pause /
  resume / run / remove across the whole library when ``scope=all``,
  which is the default for chat). ``create`` defaults to materialising
  a fresh chat project named after the automation; if the chat session
  has its own project_id (the common case) the automation binds there
  instead.
- **Project session** — ``scope`` is forced to ``this``, ``create`` binds
  to the project, ``agent_slug`` must resolve to a project
  member of the current project.

This keeps a project-side LLM from accidentally listing or mutating
unrelated projects' automations.

Key differences from the legacy ``cronjob`` tool
================================================

1. **Tool name** ``cronjob`` → ``automation``.
2. **Execution identity comes from the bound agent**, so the old
   ``model_id`` / ``provider_id`` / ``natural_language`` parameters are
   gone. The caller picks an ``agent_slug`` instead — the project's
   instantiated members (for project sessions) or a library agent (for
   chat sessions).
3. **Trigger is polymorphic.** Cron / interval / manual are three
   discriminated branches; the tool surface uses ``trigger_kind`` +
   ``cron_expr`` / ``interval_seconds`` / ``timezone`` rather than a
   single ``cron_expr`` field.

Result shape
============

Every action returns a JSON string parsable as ``AutomationToolResult``.
The frontend ``AutomationToolCard`` parses that into a structured card;
the LLM reads the same JSON via the tool result text channel.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from valuz_agent.integrations._mcp_asgi import (
    build_internal_mcp_asgi,
    get_current_mcp_session_id,
    get_current_mcp_user_id,
    internal_mcp_transport_security,
)
from valuz_agent.modules.automations.schemas import (
    AutomationToolPayload,
    AutomationToolResult,
    CronTrigger,
    IntervalTrigger,
    ManualTrigger,
    Trigger,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def _current_session_id() -> str:
    sid = get_current_mcp_session_id()
    if not sid:
        raise RuntimeError("automation tool called outside of a session-scoped request")
    return sid


async def _resolve_session_context(
    session_id: str, user_id: str | None = None
) -> tuple[str | None, str, str | None]:
    """Resolve ``(project_id, project_kind, bound_agent_slug)`` for the call.

    ``bound_agent_slug`` is the agent the calling conversation is bound to —
    recorded on the kernel session as ``metadata["valuz"]["agent_slug"]``. For a
    quick/temp chat that's a *library* agent slug (e.g. the seeded
    ``default-assistant``); ``_handle_create`` uses it to default a chat
    automation's ``agent_slug`` so the user/LLM need not pick one — and need not
    consult ``list_members``, which lists project members only and is empty for
    a project-less chat.

    Returns ``(None, "chat", <slug|None>)`` when the kernel session has been
    GC'd or the host can't find its project — the agent should still be able
    to operate on user-level automations even when its origin chat project is
    gone. The caller then forwards ``None`` to ``AutomationService.create``
    which lazy-creates a fresh chat project named after the automation.
    """
    from valuz_agent.adapters.data_reader import data_reader
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    if user_id is None:
        raise ValueError("user_id is required")

    # ``agent_slug`` lives only on the kernel session (``metadata.valuz``), so we
    # fetch the session — but via the DataService reader (durable, sandbox-agnostic;
    # DataService design §5), not a kernel round-trip to a possibly-gone sandbox.
    kernel_session = await data_reader().get_session(user_id, session_id)
    if kernel_session is None:
        return None, "chat", None

    # ``SessionData`` has no ``project_id`` column — the host records it (and the
    # bound agent slug) under ``metadata["valuz"]``.
    meta = getattr(kernel_session, "metadata", None) or {}
    valuz_meta = meta.get("valuz") if isinstance(meta, dict) else None
    project_id: str | None = None
    bound_agent_slug: str | None = None
    if isinstance(valuz_meta, dict):
        pid = valuz_meta.get("project_id")
        if isinstance(pid, str) and pid:
            project_id = pid
        slug = valuz_meta.get("agent_slug")
        if isinstance(slug, str) and slug:
            bound_agent_slug = slug

    if project_id is None:
        return None, "chat", bound_agent_slug

    async with async_unit_of_work(commit=False) as db:
        ws = await ProjectDatastore(db).get_by_id(kernel_session.user_id, project_id)
    if ws is None:
        return None, "chat", bound_agent_slug
    return ws.id, ws.kind, bound_agent_slug


# ---------------------------------------------------------------------------
# Service helper
# ---------------------------------------------------------------------------


async def _build_automation_service(db: Any, user_id: str) -> Any:
    """Build an ``AutomationService`` bound to the given async ``db`` session.

    Mirrors ``api/deps.get_automation_service`` minus the FastAPI generator
    plumbing. The settings-preferences helpers are async and awaited directly
    on the given async ``db`` session.
    """
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    locale = await get_default_locale(db, user_id=user_id)
    # Effective default = configured tz, else the detected OS tz — so an
    # automation the LLM creates without an explicit timezone is scheduled on
    # the user's local clock (and that resolved tz is persisted on the row).
    default_tz = await get_effective_default_timezone(db, user_id=user_id)
    project_svc = ProjectService(
        datastore=ProjectDatastore(db),
        event_bus=event_bus,
    )
    connector_svc = ConnectorService(datastore=ConnectorDatastore(db))
    agent_svc = AgentService(db=db, connector_service=connector_svc)
    return AutomationService(
        db=db,
        event_bus=event_bus,
        project_service=project_svc,
        agent_service=agent_svc,
        locale=locale,
        default_timezone=default_tz,
    )


# ---------------------------------------------------------------------------
# Action handlers — each returns an ``AutomationToolResult``.
# ---------------------------------------------------------------------------


_VALID_ACTIONS = {"create", "get", "list", "update", "pause", "resume", "run", "remove"}


def _err(action: str, message: str, code: str | None = None) -> AutomationToolResult:
    return AutomationToolResult(action=action, ok=False, message=message, error_code=code)


def _coerce_scope(payload: AutomationToolPayload, project_kind: str) -> str:
    """Project sessions are always ``this``; chat sessions default to ``all``."""
    if project_kind == "project":
        return "this"
    requested = payload.scope
    if requested == "this":
        return "this"
    return "all"


def _trigger_from_payload(trigger: Trigger | None) -> Trigger | None:
    """Pass-through helper — the payload already carries a typed Trigger.

    Kept as a seam in case we later need to coerce a flat-shape payload
    into the discriminated union (e.g. when supporting legacy clients).
    """
    return trigger


async def _handle_create(
    *,
    svc: Any,
    payload: AutomationToolPayload,
    project_kind: str,
    project_id: str | None,
    user_id: str,
    session_agent_slug: str | None = None,
) -> AutomationToolResult:
    """Validate + PREVIEW the proposed automation — never persist.

    Mirrors ``propose_agent``: the tool returns a resolved-but-unsaved spec the
    frontend renders as a confirmation card; the user's confirm click hits
    ``POST /v1/automations/proposals/{session_id}/confirm`` to do the actual
    create. So the LLM should call ``create`` ONCE, state the resolved schedule
    in prose, then stop and wait for the user.
    """
    from valuz_agent.modules.automations.errors import (
        AgentNotFound,
        AgentNotInProject,
        AutomationAgentRequired,
        AutomationNameEmpty,
        AutomationPlaybookNotFound,
        AutomationPlaybookTaskUnsupported,
        AutomationPlaybookVersionNotFound,
        AutomationProjectNotFound,
        AutomationPromptEmpty,
        AutomationTaskOnlyOnProject,
        IntervalTooShort,
        InvalidCronExpression,
    )
    from valuz_agent.modules.automations.service import AutomationService

    trigger = _trigger_from_payload(payload.trigger)
    if trigger is None:
        return _err(
            "create",
            (
                "trigger is required for create. Pass a discriminated trigger "
                "object: {kind: 'cron', cron_expr: '0 9 * * *'} or "
                "{kind: 'interval', seconds: 300} or {kind: 'manual'}."
            ),
            code="MISSING_TRIGGER",
        )

    action_kind = (payload.action_kind or "chat").strip() or "chat"
    if action_kind not in ("chat", "task"):
        return _err(
            "create",
            "action_kind must be 'chat' or 'task'.",
            code="INVALID_ACTION_KIND",
        )
    # Task mode kicks off a project task with the bound agent as Lead — it needs
    # a project context, so it can only be created from inside a project session.
    if action_kind == "task" and project_kind != "project":
        return _err(
            "create",
            (
                "task automations can only be created from inside a PROJECT "
                "session (the agent runs as the task Lead). Open the project and "
                "ask there, or use action_kind='chat'."
            ),
            code="TASK_REQUIRES_PROJECT",
        )

    # Shared input-assembly + defaulting (single source of truth with the
    # confirm route). The effective agent defaults to the session's bound agent
    # in a chat, so the LLM/user need not pick one (and need not call
    # list_members, which is empty in a project-less chat).
    try:
        create_payload = AutomationService.build_create_payload(
            name=payload.name,
            prompt_template=payload.prompt_template,
            trigger=trigger,
            agent_slug=payload.agent_slug,
            action_kind=action_kind,
            project_kind=project_kind,
            project_id=project_id,
            session_agent_slug=session_agent_slug,
            worktree=bool(payload.worktree),
            playbook_definition_id=payload.playbook_definition_id,
            playbook_version=payload.playbook_version,
        )
    except AutomationNameEmpty:
        return _err("create", "name is required for create.", code="MISSING_NAME")
    except AutomationPromptEmpty:
        return _err("create", "prompt_template is required for create.", code="MISSING_PROMPT")
    except AutomationAgentRequired:
        return _err(
            "create",
            (
                "agent_slug is required. In a PROJECT session pick a team member "
                "(call list_members to see them). In a chat it defaults to your "
                "current agent; pass a library agent slug only to override."
            ),
            code="MISSING_AGENT",
        )
    except AutomationTaskOnlyOnProject as exc:
        return _err("create", str(exc.message), code=exc.__class__.__name__)

    # MCP-from-chat: forward the calling session's project_id so the preview
    # resolves the agent against the user's current chat project.
    calling_ws = project_id if project_kind == "chat" else None

    try:
        spec = await svc.preview(
            create_payload,
            calling_session_project_id=calling_ws,
            user_id=user_id,
        )
    except (
        InvalidCronExpression,
        IntervalTooShort,
        AutomationProjectNotFound,
        AgentNotInProject,
        AgentNotFound,
        AutomationTaskOnlyOnProject,
        AutomationPlaybookNotFound,
        AutomationPlaybookVersionNotFound,
        AutomationPlaybookTaskUnsupported,
    ) as exc:
        return _err("create", str(exc.message), code=exc.__class__.__name__)

    return AutomationToolResult(
        action="create",
        ok=True,
        message=(
            f"Proposed automation '{spec.name}' — {spec.trigger_human_readable}. "
            "Awaiting the user's confirmation in the card; do not call create again."
        ),
        proposal=spec,
        next_runs=[spec.next_run_at] if spec.next_run_at else [],
    )


async def _handle_list(
    *, svc: Any, project_id: str | None, scope: str, user_id: str
) -> AutomationToolResult:
    if scope == "all":
        items = await svc.list_all_automations(user_id=user_id)
    else:
        # Chat sessions narrowed to ``this`` use the singleton chat-default
        # sentinel; project sessions pass their project_id directly.
        items = await svc.list_automations_in_project(project_id or "chat-default", user_id=user_id)
    if not items:
        return AutomationToolResult(
            action="list",
            ok=True,
            message="No automations yet.",
            automations=[],
        )
    summary = ", ".join(f"{i.name} ({i.trigger_human_readable})" for i in items[:3])
    if len(items) > 3:
        summary += f", and {len(items) - 3} more"
    return AutomationToolResult(
        action="list",
        ok=True,
        message=f"Found {len(items)} automation(s): {summary}.",
        automations=items,
    )


async def _handle_get(
    *,
    svc: Any,
    payload: AutomationToolPayload,
    project_id: str | None,
    scope: str,
    user_id: str,
) -> AutomationToolResult:
    """Return the full detail of a single automation by id (read-only)."""
    if not payload.automation_id:
        return _err("get", "automation_id is required for get.", code="MISSING_AUTOMATION_ID")
    row = await svc._ds.get_automation(user_id, payload.automation_id)  # noqa: SLF001
    if row is None:
        return _err("get", "No such automation.", code="AutomationNotFound")
    if scope == "this" and project_id is not None and row.project_id != project_id:
        return _err(
            "get",
            "Automation belongs to a different project.",
            code="CROSS_PROJECT_DENIED",
        )
    item = await svc._row_to_item(row, user_id)  # noqa: SLF001
    return AutomationToolResult(
        action="get",
        ok=True,
        message=f"'{item.name}' — {item.trigger_human_readable}.",
        automation=item,
    )


async def _handle_update(
    *,
    svc: Any,
    payload: AutomationToolPayload,
    project_id: str | None,
    scope: str,
    user_id: str,
) -> AutomationToolResult:
    from valuz_agent.modules.automations.errors import (
        AgentNotInProject,
        AutomationAgentRequired,
        AutomationNameEmpty,
        AutomationNotFound,
        AutomationPlaybookNotFound,
        AutomationPlaybookTaskUnsupported,
        AutomationPlaybookVersionNotFound,
        AutomationPromptEmpty,
        IntervalTooShort,
        InvalidCronExpression,
    )
    from valuz_agent.modules.automations.schemas import AutomationUpdatePayload

    if not payload.automation_id:
        return _err("update", "automation_id is required for update.", code="MISSING_AUTOMATION_ID")
    row = await svc._ds.get_automation(user_id, payload.automation_id)  # noqa: SLF001
    if row is None:
        return _err("update", "No such automation.", code="AutomationNotFound")
    if scope == "this" and project_id is not None and row.project_id != project_id:
        return _err(
            "update",
            "This automation belongs to a different project; switch to "
            "that project's chat to modify it.",
            code="CROSS_PROJECT_DENIED",
        )

    update_payload = AutomationUpdatePayload(
        name=payload.name,
        prompt_template=payload.prompt_template,
        trigger=_trigger_from_payload(payload.trigger),
        agent_slug=payload.agent_slug,
        action_kind=payload.action_kind,  # type: ignore[arg-type]
        worktree=payload.worktree,
        playbook_definition_id=payload.playbook_definition_id,
        playbook_version=payload.playbook_version,
    )
    try:
        detail = await svc.update(payload.automation_id, update_payload, user_id=user_id)
    except (
        InvalidCronExpression,
        IntervalTooShort,
        AutomationNameEmpty,
        AutomationPromptEmpty,
        AutomationAgentRequired,
        AutomationNotFound,
        AgentNotInProject,
        AutomationPlaybookNotFound,
        AutomationPlaybookVersionNotFound,
        AutomationPlaybookTaskUnsupported,
    ) as exc:
        return _err("update", str(exc.message), code=exc.__class__.__name__)
    fresh = await svc._row_to_item(  # noqa: SLF001
        await svc._ds.get_automation(user_id, detail.automation_id),  # noqa: SLF001
        user_id,
    )
    return AutomationToolResult(
        action="update",
        ok=True,
        message=f"Updated '{detail.name}'.",
        automation=fresh,
        next_runs=[detail.next_run_at] if detail.next_run_at else [],
    )


async def _handle_status_change(
    *,
    svc: Any,
    action: str,
    payload: AutomationToolPayload,
    project_id: str | None,
    scope: str,
    user_id: str,
) -> AutomationToolResult:
    """Shared handler for pause / resume / remove / run — all single-verb
    actions with the same scope check."""
    from valuz_agent.modules.automations.errors import (
        AutomationAlreadyQueued,
        AutomationAlreadyRunning,
        AutomationNotFound,
        AutomationPaused,
    )

    if not payload.automation_id:
        return _err(
            action, f"automation_id is required for {action}.", code="MISSING_AUTOMATION_ID"
        )
    row = await svc._ds.get_automation(user_id, payload.automation_id)  # noqa: SLF001
    if row is None:
        return _err(action, "No such automation.", code="AutomationNotFound")
    if scope == "this" and project_id is not None and row.project_id != project_id:
        return _err(
            action,
            "Automation belongs to a different project.",
            code="CROSS_PROJECT_DENIED",
        )

    try:
        if action == "pause":
            detail = await svc.pause(payload.automation_id, user_id=user_id)
            msg = f"Paused '{detail.name}'."
        elif action == "resume":
            detail = await svc.resume(payload.automation_id, user_id=user_id)
            msg = f"Resumed '{detail.name}'."
        elif action == "remove":
            name = row.name
            await svc.delete(payload.automation_id, user_id=user_id)
            return AutomationToolResult(
                action="remove",
                ok=True,
                message=f"Removed '{name}'.",
            )
        elif action == "run":
            # Agent-initiated off-schedule fire — tag it ``agent`` so the
            # execution log distinguishes it from a human's "Run now" click
            # (``manual``) and the scheduled cron/interval runs. Carry the
            # invoking session so a task this run spawns can chain its
            # provenance back to the originating task (task→automation→task).
            run = await svc.run_now(
                payload.automation_id,
                trigger_type="agent",
                invoked_by_session_id=_current_session_id(),
                extra_input=payload.input,
                user_id=user_id,
            )
            return AutomationToolResult(
                action="run",
                ok=True,
                message=(
                    f"Queued automation for immediate execution (run_id={run.run_id}). "
                    "The session it spawns will appear in the project shortly."
                ),
                automation=await svc._row_to_item(row, user_id),  # noqa: SLF001
            )
        else:  # pragma: no cover — guarded above
            return _err(action, f"Unknown action {action!r}.")
    except (
        AutomationNotFound,
        AutomationPaused,
        AutomationAlreadyQueued,
        AutomationAlreadyRunning,
    ) as exc:
        return _err(action, str(exc.message), code=exc.__class__.__name__)
    fresh = await svc._row_to_item(  # noqa: SLF001
        await svc._ds.get_automation(user_id, detail.automation_id),  # noqa: SLF001
        user_id,
    )
    return AutomationToolResult(
        action=action,
        ok=True,
        message=msg,
        automation=fresh,
    )


async def _dispatch(payload: AutomationToolPayload) -> AutomationToolResult:
    if payload.action not in _VALID_ACTIONS:
        return _err(
            payload.action,
            f"Unknown action {payload.action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.",
            code="UNKNOWN_ACTION",
        )

    from valuz_agent.infra.db import async_unit_of_work

    session_id = _current_session_id()
    try:
        user_id = get_current_mcp_user_id()
    except RuntimeError:
        return _err(payload.action, "Unknown session owner.", code="UNKNOWN_SESSION_OWNER")
    project_id, project_kind, session_agent_slug = await _resolve_session_context(
        session_id, user_id
    )
    scope = _coerce_scope(payload, project_kind)

    async with async_unit_of_work() as db:
        svc = await _build_automation_service(db, user_id)
        if payload.action == "list":
            return await _handle_list(svc=svc, project_id=project_id, scope=scope, user_id=user_id)
        if payload.action == "create":
            return await _handle_create(
                svc=svc,
                payload=payload,
                project_kind=project_kind,
                project_id=project_id,
                session_agent_slug=session_agent_slug,
                user_id=user_id,
            )
        if payload.action == "get":
            return await _handle_get(
                svc=svc,
                payload=payload,
                project_id=project_id,
                scope=scope,
                user_id=user_id,
            )
        if payload.action == "update":
            return await _handle_update(
                svc=svc,
                payload=payload,
                project_id=project_id,
                scope=scope,
                user_id=user_id,
            )
        return await _handle_status_change(
            svc=svc,
            action=payload.action,
            payload=payload,
            project_id=project_id,
            scope=scope,
            user_id=user_id,
        )


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


_mcp = FastMCP(
    "valuz-automations",
    transport_security=internal_mcp_transport_security(),
    # Stateless like the toolkit server: session state in process memory 404s
    # any follow-up request that lands on another replica/worker behind a
    # load balancer (client surfaces it as "McpError: Session terminated").
    stateless_http=True,
)


_AUTOMATION_DESCRIPTION = """Manage the user's automations (recurring or
interval-driven agent runs).

Use this tool ONLY when the user explicitly asks to create, list, pause,
resume, run, modify, or remove an automation / scheduled task / recurring
job — phrases like "every day at 9am", "every 5 minutes", "remind me
weekly", "automation", "schedule". Do not call it for one-off reminders
or to manage non-recurring follow-ups.

Actions
=======
- create: PROPOSES a new automation — it does NOT save. The user is shown a
  confirmation card and nothing is written until they approve (same "tool
  proposes, user disposes" model as propose_agent). So call create ONCE, state
  the resolved schedule in plain prose, then STOP — do not call create again
  for the same automation, and do not assume it exists yet.
  Requires name, prompt_template, trigger. agent_slug is CONTEXT-DEPENDENT —
  do NOT treat it as universally required:
    • Chat / quick conversation (no project): agent_slug is OPTIONAL. Omit it
      and the automation runs as the agent you are CURRENTLY talking to. Do
      NOT call list_members here — it lists *project members*, so it is empty
      in a project-less chat, and an empty roster does NOT mean "no agent
      available". Pass an explicit LIBRARY agent slug only to override.
    • Project session: agent_slug is REQUIRED and must be a project team
      member — call list_members first to see candidates. Do NOT invent slugs.
  action_kind — "chat" (default) runs the bound agent once per fire; "task"
    kicks off a full project task with the bound agent as the Lead. "task" is
    ONLY valid in a PROJECT session (it needs the project's task context); in a
    chat it is rejected — omit it / use "chat" there.
  worktree — OPTIONAL (both "chat" and "task"): true runs each fire in an
    isolated git worktree of the project repo. For "chat" the single session
    runs in its own worktree; for "task" the lead and every member share ONE
    worktree branch. The main workspace stays untouched, and a worktree left
    with no changes is removed automatically when the run / task finishes. Only
    meaningful when the project is a git repository (silently ignored for
    chat-only projects). Default false (the fire works in the project directory).
  playbook_definition_id / playbook_version — OPTIONAL immutable Playbook pin.
    When a Definition is provided without a version, create resolves its current
    version once and stores that exact version. Pinned Playbooks currently use
    action_kind="chat"; task mode is rejected until task lifecycle completion can
    close the corresponding PlaybookRun truthfully.
  trigger — discriminated object. Use interval for "every N minutes/seconds"
    schedules, cron for clock-time schedules:
    {"kind": "cron", "cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"}
    {"kind": "interval", "seconds": 300}  — every 300s (min 30); NOT a cron
    {"kind": "manual"}  — never tick-fires; only run via run action
  Cron format: standard 5-field POSIX (minute hour dom month dow). If the
  user gave a natural-language schedule, translate it yourself, confirm
  in plain prose, then call with cron_expr filled.
  TIMEZONE — a cron schedule is meaningless without one, so ALWAYS set the
  cron trigger's "timezone" to a concrete IANA name. Use the USER'S timezone,
  given to you in the per-turn context (the "Current time" line) — the user
  almost always means their LOCAL time; NEVER default to UTC. If you omit it
  the server falls back to the user's configured/detected timezone, but pass
  it explicitly and state the resolved zone back to the user to confirm.
- list: returns existing automations. In a chat session, lists across all
  projects by default (set scope="this" to narrow to the current chat).
  In a project session, always scoped to the current project.
- get: returns the full detail of ONE automation by automation_id (read-only;
  prompt_template, trigger, agent, next run, status). Use it to inspect a
  single automation before update / pause / resume.
- update / pause / resume / run / remove: require automation_id (get one
  from a prior list). For ``run`` you may also pass ``input`` — extra text
  appended to the automation's instruction for THAT single run (e.g. a task id
  you just discovered). It does NOT modify the saved automation.

Execution identity follows the bound agent — there is NO model_id or
provider_id input. The agent's configured model / provider / runtime is
what each fire uses."""


async def automation_invoke(payload: AutomationToolPayload) -> str:
    """Pure-Python entrypoint — separated from the FastMCP decorator so
    tests can exercise the full dispatch + JSON-encode path without
    standing up an MCP transport. The decorated ``automation`` thin-wraps
    this with the schema's parameter list."""
    try:
        result = await _dispatch(payload)
    except Exception as exc:  # defensive — never let the tool 500 the runtime
        logger.exception("automation dispatch failed")
        result = _err(payload.action, f"internal error: {exc!r}", code="INTERNAL")
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


@_mcp.tool(description=_AUTOMATION_DESCRIPTION)
async def automation(
    action: str,
    automation_id: str | None = None,
    name: str | None = None,
    prompt_template: str | None = None,
    agent_slug: str | None = None,
    trigger: dict[str, Any] | None = None,
    action_kind: str | None = None,
    worktree: bool | None = None,
    playbook_definition_id: str | None = None,
    playbook_version: int | None = None,
    scope: str | None = None,
    input: str | None = None,  # noqa: A002 — MCP wire arg name; intentional
) -> str:
    """Unified entrypoint — see ``_AUTOMATION_DESCRIPTION`` for usage.

    ``trigger`` is accepted as a plain dict and coerced into the
    discriminated union — the MCP wire format doesn't carry the Pydantic
    metadata that drives FastMCP's auto-schema-generation, so we adapt
    here rather than letting the SDK infer a type it can't render.
    """
    coerced_trigger: Trigger | None = None
    if trigger is not None:
        kind = trigger.get("kind")
        if kind == "cron":
            coerced_trigger = CronTrigger(
                cron_expr=trigger.get("cron_expr", ""),
                timezone=trigger.get("timezone"),
            )
        elif kind == "interval":
            coerced_trigger = IntervalTrigger(seconds=int(trigger.get("seconds", 0)))
        elif kind == "manual":
            coerced_trigger = ManualTrigger()

    return await automation_invoke(
        AutomationToolPayload(
            action=action,
            automation_id=automation_id,
            name=name,
            prompt_template=prompt_template,
            agent_slug=agent_slug,
            trigger=coerced_trigger,
            action_kind=action_kind,
            worktree=worktree,
            playbook_definition_id=playbook_definition_id,
            playbook_version=playbook_version,
            scope=scope,
            input=input,
        )
    )


# ---------------------------------------------------------------------------
# ASGI wrapper (mirrors docs_mcp_server)
# ---------------------------------------------------------------------------


def automations_mcp_session_manager_run() -> Any:
    """Mirror of ``docs_mcp_session_manager_run`` — see that docstring."""
    _mcp.streamable_http_app()
    return _mcp.session_manager.run()


def build_automations_mcp_asgi() -> Any:
    """Return an ASGI app to mount at ``/_internal/mcp/automations`` (and,
    dual-mounted for pre-ADR-013 session compatibility,
    ``/internal/mcp/automations`` — see ``api/app.py::_mount_internal``)."""
    return build_internal_mcp_asgi(_mcp.streamable_http_app())


def automations_mcp_url(*, base_url: str) -> str:
    """ADR-013: newly created sessions get the ``/_internal/...`` path;
    ``/internal/...`` stays mounted so session snapshots that persisted the
    pre-rename URL keep working on restore (see ``api/app.py::_mount_internal``,
    removed the next OSS major version)."""
    return f"{base_url.rstrip('/')}/_internal/mcp/automations/mcp"


__all__ = [
    "_dispatch",
    "automation",
    "automation_invoke",
    "automations_mcp_session_manager_run",
    "automations_mcp_url",
    "build_automations_mcp_asgi",
]
