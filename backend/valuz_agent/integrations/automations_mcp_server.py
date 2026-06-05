"""In-process MCP server exposing the ``automation`` tool.

Replaces the legacy ``cronjob`` tool per ADR-021. Same in-process MCP
pattern (FastMCP + ContextVar-scoped session_id + ASGI wrapper with the
per-process shared secret), but the tool surface is rebuilt:

Wire shape
==========

POST /internal/mcp/automations/mcp
  headers:
    X-Valuz-Internal:    <per-process token>
    X-Valuz-Session-Id:  <kernel session id>

Permission model
================

The tool resolves the calling kernel session into ``(workspace_id,
workspace_kind)`` and lets:

- **Chat session** — manage every user-level automation (list / pause /
  resume / run / remove across the whole library when ``scope=all``,
  which is the default for chat). ``create`` defaults to materialising
  a fresh chat workspace named after the automation; if the chat session
  has its own workspace_id (the common case) the automation binds there
  instead.
- **Project session** — ``scope`` is forced to ``this``, ``create`` binds
  to the project workspace, ``agent_slug`` must resolve to a project
  member of the current workspace.

This keeps a project-side LLM from accidentally listing or mutating
unrelated workspaces' automations.

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
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP

from valuz_agent.modules.automations.schemas import (
    AutomationToolPayload,
    AutomationToolResult,
    CronTrigger,
    IntervalTrigger,
    ManualTrigger,
    Trigger,
)

logger = logging.getLogger(__name__)


_session_var: ContextVar[str | None] = ContextVar("valuz_automations_mcp_session_id", default=None)


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def _current_session_id() -> str:
    sid = _session_var.get()
    if not sid:
        raise RuntimeError("automation tool called outside of a session-scoped request")
    return sid


async def _resolve_session_context(session_id: str) -> tuple[str | None, str, str | None]:
    """Resolve ``(workspace_id, workspace_kind, bound_agent_slug)`` for the call.

    ``bound_agent_slug`` is the agent the calling conversation is bound to —
    recorded on the kernel session as ``metadata["valuz"]["agent_slug"]``. For a
    quick/temp chat that's a *library* agent slug (e.g. the seeded
    ``default-assistant``); ``_handle_create`` uses it to default a chat
    automation's ``agent_slug`` so the user/LLM need not pick one — and need not
    consult ``list_members``, which lists project members only and is empty for
    a project-less chat.

    Returns ``(None, "chat", <slug|None>)`` when the kernel session has been
    GC'd or the host can't find its workspace — the agent should still be able
    to operate on user-level automations even when its origin chat workspace is
    gone. The caller then forwards ``None`` to ``AutomationService.create``
    which lazy-creates a fresh chat workspace named after the automation.
    """
    from valuz_agent.adapters import kernel_store
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import WorkspaceDatastore

    kernel_session = await kernel_store.load_session(session_id)
    if kernel_session is None:
        return None, "chat", None
    project_id = str(kernel_session.project_id)

    meta = getattr(kernel_session, "metadata", None) or {}
    valuz_meta = meta.get("valuz") if isinstance(meta, dict) else None
    bound_agent_slug: str | None = None
    if isinstance(valuz_meta, dict):
        slug = valuz_meta.get("agent_slug")
        if isinstance(slug, str) and slug:
            bound_agent_slug = slug

    async with async_unit_of_work(commit=False) as db:
        ws = await WorkspaceDatastore(db).get_by_id(project_id)
    if ws is None:
        return None, "chat", bound_agent_slug
    return ws.id, ws.kind, bound_agent_slug


# ---------------------------------------------------------------------------
# Service helper
# ---------------------------------------------------------------------------


async def _build_automation_service(db: Any) -> Any:
    """Build an ``AutomationService`` bound to the given async ``db`` session.

    Mirrors ``api/deps.get_automation_service`` minus the FastAPI generator
    plumbing. The settings-preferences helpers are async and awaited directly
    on the given async ``db`` session.
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.infra.secret_store import FileSecretStore
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.projects.datastore import WorkspaceDatastore
    from valuz_agent.modules.projects.service import WorkspaceService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    locale = await get_default_locale(db)
    # Effective default = configured tz, else the detected OS tz — so an
    # automation the LLM creates without an explicit timezone is scheduled on
    # the user's local clock (and that resolved tz is persisted on the row).
    default_tz = await get_effective_default_timezone(db)
    workspace_svc = WorkspaceService(
        datastore=WorkspaceDatastore(db),
        event_bus=event_bus,
    )
    connector_svc = ConnectorService(
        datastore=ConnectorDatastore(db),
        secrets=FileSecretStore(settings.secrets_dir),
    )
    agent_svc = AgentService(db=db, connector_service=connector_svc)
    return AutomationService(
        db=db,
        event_bus=event_bus,
        workspace_service=workspace_svc,
        agent_service=agent_svc,
        locale=locale,
        default_timezone=default_tz,
    )


# ---------------------------------------------------------------------------
# Action handlers — each returns an ``AutomationToolResult``.
# ---------------------------------------------------------------------------


_VALID_ACTIONS = {"create", "list", "update", "pause", "resume", "run", "remove"}


def _err(action: str, message: str, code: str | None = None) -> AutomationToolResult:
    return AutomationToolResult(action=action, ok=False, message=message, error_code=code)


def _coerce_scope(payload: AutomationToolPayload, workspace_kind: str) -> str:
    """Project sessions are always ``this``; chat sessions default to ``all``."""
    if workspace_kind == "project":
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
    workspace_kind: str,
    workspace_id: str | None,
    session_agent_slug: str | None = None,
) -> AutomationToolResult:
    from valuz_agent.modules.automations.errors import (
        AgentNotFound,
        AgentNotInWorkspace,
        AutomationAgentRequired,
        AutomationNameEmpty,
        AutomationPromptEmpty,
        AutomationWorkspaceNotFound,
        IntervalTooShort,
        InvalidCronExpression,
    )
    from valuz_agent.modules.automations.schemas import AutomationCreatePayload

    if not payload.name:
        return _err("create", "name is required for create.", code="MISSING_NAME")
    if not payload.prompt_template:
        return _err(
            "create",
            "prompt_template is required for create.",
            code="MISSING_PROMPT",
        )
    # Resolve the effective agent. In a chat / quick conversation the automation
    # runs as the agent the user is already talking to: default ``agent_slug``
    # to the session's bound agent (a library agent such as ``default-assistant``)
    # when omitted. This removes the false dependency on ``list_members`` —
    # project-member-scoped, hence empty for a project-less chat — that
    # otherwise made the LLM give up before ever calling create.
    effective_agent_slug = payload.agent_slug
    if not effective_agent_slug and workspace_kind == "chat":
        effective_agent_slug = session_agent_slug
    if not effective_agent_slug:
        return _err(
            "create",
            (
                "agent_slug is required. In a PROJECT session pick a team member "
                "(call list_members to see them). In a chat it defaults to your "
                "current agent; pass a library agent slug only to override."
            ),
            code="MISSING_AGENT",
        )

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

    # Project sessions store project_member; chat sessions store library_agent
    # (the service then instantiates the library agent into the chat workspace
    # and normalises the slug — see ADR-021 §4).
    agent_kind = "project_member" if workspace_kind == "project" else "library_agent"

    create_payload = AutomationCreatePayload(
        name=payload.name,
        workspace_kind=workspace_kind,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        agent_kind=agent_kind,  # type: ignore[arg-type]
        agent_slug=effective_agent_slug,
        prompt_template=payload.prompt_template,
        trigger=trigger,
    )

    # MCP-from-chat: forward the calling session's workspace_id so library
    # agents land in the user's current chat rather than a fresh ws.
    calling_ws = workspace_id if workspace_kind == "chat" else None

    try:
        detail = await svc.create(create_payload, calling_session_workspace_id=calling_ws)
    except (
        InvalidCronExpression,
        IntervalTooShort,
        AutomationNameEmpty,
        AutomationPromptEmpty,
        AutomationAgentRequired,
        AutomationWorkspaceNotFound,
        AgentNotInWorkspace,
        AgentNotFound,
    ) as exc:
        return _err("create", str(exc.message), code=exc.__class__.__name__)

    fresh = await svc._row_to_item(  # noqa: SLF001 — sanctioned local projection
        await svc._ds.get_automation(detail.automation_id)  # noqa: SLF001
    )
    return AutomationToolResult(
        action="create",
        ok=True,
        message=f"Created automation '{detail.name}' — {detail.trigger_human_readable}.",
        automation=fresh,
        next_runs=[detail.next_run_at] if detail.next_run_at else [],
    )


async def _handle_list(*, svc: Any, workspace_id: str | None, scope: str) -> AutomationToolResult:
    if scope == "all":
        items = await svc.list_all_automations()
    else:
        # Chat sessions narrowed to ``this`` use the singleton chat-default
        # sentinel; project sessions pass their workspace_id directly.
        items = await svc.list_automations_in_workspace(workspace_id or "chat-default")
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


async def _handle_update(
    *,
    svc: Any,
    payload: AutomationToolPayload,
    workspace_id: str | None,
    scope: str,
) -> AutomationToolResult:
    from valuz_agent.modules.automations.errors import (
        AgentNotInWorkspace,
        AutomationAgentRequired,
        AutomationNameEmpty,
        AutomationNotFound,
        AutomationPromptEmpty,
        IntervalTooShort,
        InvalidCronExpression,
    )
    from valuz_agent.modules.automations.schemas import AutomationUpdatePayload

    if not payload.automation_id:
        return _err("update", "automation_id is required for update.", code="MISSING_AUTOMATION_ID")
    row = await svc._ds.get_automation(payload.automation_id)  # noqa: SLF001
    if row is None:
        return _err("update", "No such automation.", code="AutomationNotFound")
    if scope == "this" and workspace_id is not None and row.workspace_id != workspace_id:
        return _err(
            "update",
            "This automation belongs to a different workspace; switch to "
            "that workspace's chat to modify it.",
            code="CROSS_WORKSPACE_DENIED",
        )

    update_payload = AutomationUpdatePayload(
        name=payload.name,
        prompt_template=payload.prompt_template,
        trigger=_trigger_from_payload(payload.trigger),
        agent_slug=payload.agent_slug,
    )
    try:
        detail = await svc.update(payload.automation_id, update_payload)
    except (
        InvalidCronExpression,
        IntervalTooShort,
        AutomationNameEmpty,
        AutomationPromptEmpty,
        AutomationAgentRequired,
        AutomationNotFound,
        AgentNotInWorkspace,
    ) as exc:
        return _err("update", str(exc.message), code=exc.__class__.__name__)
    fresh = await svc._row_to_item(  # noqa: SLF001
        await svc._ds.get_automation(detail.automation_id)  # noqa: SLF001
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
    workspace_id: str | None,
    scope: str,
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
    row = await svc._ds.get_automation(payload.automation_id)  # noqa: SLF001
    if row is None:
        return _err(action, "No such automation.", code="AutomationNotFound")
    if scope == "this" and workspace_id is not None and row.workspace_id != workspace_id:
        return _err(
            action,
            "Automation belongs to a different workspace.",
            code="CROSS_WORKSPACE_DENIED",
        )

    try:
        if action == "pause":
            detail = await svc.pause(payload.automation_id)
            msg = f"Paused '{detail.name}'."
        elif action == "resume":
            detail = await svc.resume(payload.automation_id)
            msg = f"Resumed '{detail.name}'."
        elif action == "remove":
            name = row.name
            await svc.delete(payload.automation_id)
            return AutomationToolResult(
                action="remove",
                ok=True,
                message=f"Removed '{name}'.",
            )
        elif action == "run":
            run = await svc.run_now(payload.automation_id)
            return AutomationToolResult(
                action="run",
                ok=True,
                message=(
                    f"Queued automation for immediate execution (run_id={run.run_id}). "
                    "The session it spawns will appear in the workspace shortly."
                ),
                automation=await svc._row_to_item(row),  # noqa: SLF001
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
        await svc._ds.get_automation(detail.automation_id)  # noqa: SLF001
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
    workspace_id, workspace_kind, session_agent_slug = await _resolve_session_context(session_id)
    scope = _coerce_scope(payload, workspace_kind)

    async with async_unit_of_work() as db:
        svc = await _build_automation_service(db)
        if payload.action == "list":
            return await _handle_list(svc=svc, workspace_id=workspace_id, scope=scope)
        if payload.action == "create":
            return await _handle_create(
                svc=svc,
                payload=payload,
                workspace_kind=workspace_kind,
                workspace_id=workspace_id,
                session_agent_slug=session_agent_slug,
            )
        if payload.action == "update":
            return await _handle_update(
                svc=svc, payload=payload, workspace_id=workspace_id, scope=scope
            )
        return await _handle_status_change(
            svc=svc,
            action=payload.action,
            payload=payload,
            workspace_id=workspace_id,
            scope=scope,
        )


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


_mcp = FastMCP("valuz-automations")


_AUTOMATION_DESCRIPTION = """Manage the user's automations (recurring or
interval-driven agent runs).

Use this tool ONLY when the user explicitly asks to create, list, pause,
resume, run, modify, or remove an automation / scheduled task / recurring
job — phrases like "every day at 9am", "every 5 minutes", "remind me
weekly", "automation", "schedule". Do not call it for one-off reminders
or to manage non-recurring follow-ups.

Actions
=======
- create: requires name, prompt_template, trigger. agent_slug is
  CONTEXT-DEPENDENT — do NOT treat it as universally required:
    • Chat / quick conversation (no project): agent_slug is OPTIONAL. Omit it
      and the automation runs as the agent you are CURRENTLY talking to. Do
      NOT call list_members here — it lists *project members*, so it is empty
      in a project-less chat, and an empty roster does NOT mean "no agent
      available". Pass an explicit LIBRARY agent slug only to override.
    • Project session: agent_slug is REQUIRED and must be a project team
      member — call list_members first to see candidates. Do NOT invent slugs.
  trigger — discriminated object. Either:
    {"kind": "cron", "cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"}
    {"kind": "interval", "seconds": 300}
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
  workspaces by default (set scope="this" to narrow to the current chat).
  In a project session, always scoped to the current project.
- update / pause / resume / run / remove: require automation_id (get one
  from a prior list).

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
    scope: str | None = None,
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
            scope=scope,
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
    """Return an ASGI app to mount at ``/internal/mcp/automations``.

    Same gating as docs MCP: per-process ``X-Valuz-Internal`` token plus
    ``X-Valuz-Session-Id`` recorded into the ContextVar so the tool sees
    the calling session.
    """
    from starlette.responses import PlainTextResponse

    inner = _mcp.streamable_http_app()

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        from valuz_agent.infra.config import settings as _settings

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        if headers.get("x-valuz-internal") != _settings.internal_mcp_token:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        if not session_id:
            response = PlainTextResponse("Missing X-Valuz-Session-Id header", status_code=400)
            await response(scope, receive, send)
            return

        ctx_token = _session_var.set(session_id)
        try:
            await inner(scope, receive, send)
        finally:
            _session_var.reset(ctx_token)

    return _app


def automations_mcp_url(*, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/internal/mcp/automations/mcp"


__all__ = [
    "_dispatch",
    "_session_var",
    "automation",
    "automation_invoke",
    "automations_mcp_session_manager_run",
    "automations_mcp_url",
    "build_automations_mcp_asgi",
]
