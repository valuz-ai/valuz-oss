"""Task MCP tool HANDLERS — thin args → service call → ToolResult shims.

Module-level functions taking the orchestrator first; ``build_task_tool_defs``
zips them with the ``ToolDef`` declarations (identity lives THERE) and wraps
each in ``_guarded`` (+ ``_with_inbox_notice`` for gap-callable lead tools).
Business logic lives on the composed services (``orchestrator.lifecycle`` etc.)
and in ``plan_commands`` for plan reads/writes.
"""

# ruff: noqa: I001
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

import valuz_agent.boot.kernel  # noqa: F401

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext, ToolHandler

from valuz_agent.adapters.agent_resolver import summarize_role
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import messaging, plan_commands, planning
from valuz_agent.modules.tasks import service as task_service
from valuz_agent.modules.tasks.datastore import TaskDatastore
from valuz_agent.modules.tasks import mailbox_store
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.resolution import task_session_resolver

from valuz_agent.modules.tasks.tools.declarations import (
    ABANDON_TASK_TOOL_NAME,
    AWAIT_MEMBERS_TOOL_NAME,
    COMMIT_TASK_TOOL_NAME,
    CREATE_TASK_TOOL_NAME,
    DISPATCH_TOOL_DECLARATIONS,
    DISPATCH_TOOL_NAME,
    DRAFT_TASK_TOOL_NAME,
    FINISH_TASK_TOOL_NAME,
    GET_PLAN_TOOL_NAME,
    GET_TASK_TOOL_NAME,
    INJECT_INTO_TASK_TOOL_NAME,
    LIST_MEMBERS_TOOL_NAME,
    LIST_TASKS_TOOL_NAME,
    MODIFY_PLAN_TOOL_NAME,
    ORCHESTRATION_TOOL_DECLARATIONS,
    PLAN_TASK_TOOL_NAME,
    RESUME_TASK_TOOL_NAME,
    REVIEW_SUBTASK_TOOL_NAME,
    SEND_TOOL_NAME,
    STOP_SUBTASK_TOOL_NAME,
    UPDATE_DELIVERABLE_TOOL_NAME,
)

if TYPE_CHECKING:
    from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

from valuz_agent.modules.tasks import gate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lead gate helper
# ---------------------------------------------------------------------------


async def _check_lead_gate(
    ctx: ExecContext, *, tool: str = "dispatch"
) -> tuple[str, str] | ToolResult:
    """Verify the caller is a lead session and return (task_id, project_id).

    I/O wrapper: loads the caller session, applies the pure policy in
    ``tools/gate.py`` and wraps a rejection into ToolResult(is_error=True).
    ``tool`` labels rejections with the actual tool the model called.
    """
    # Source the caller from the per-request built-in MCP context when not
    # passed explicitly (the toolkit MCP server publishes the session owner).
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content=f"{tool}: caller session not found", is_error=True)
    verdict = gate.check_lead_gate(sess, tool=tool)
    if isinstance(verdict, Failure):
        return ToolResult(content=verdict.reason, is_error=True)
    return verdict


async def _resolve_plan_writer_task(
    ctx: ExecContext, args: dict[str, Any]
) -> tuple[Any, str, str] | ToolResult:
    """Resolve the target task + run the writer gate for the state-transition
    tools (commit / abandon). task_id comes from args (chat) or the caller
    session's metadata (lead). Plan reads/writes do NOT use this — they go
    through ``plan_commands``, the single authorized door.
    """
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="plan tool: caller session not found", is_error=True)

    v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
    task_id = args.get("task_id") or v.get("task_id") or ""
    if not task_id:
        return ToolResult(
            content=(
                "plan tool: task_id is required (chat callers must pass it explicitly; "
                "lead callers must have it in session metadata)"
            ),
            is_error=True,
        )


    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        task = await task_ds.get_task(user_id, task_id)
    if task is None:
        return ToolResult(content=f"plan tool: task {task_id!r} not found", is_error=True)

    gate_err = _check_plan_writer_gate(sess, task)
    if gate_err is not None:
        return gate_err
    return task, task.project_id, task_id


def _check_plan_writer_gate(sess: Any, task: Any) -> ToolResult | None:
    """Verify ``sess`` is allowed to write plan / state on ``task``.

    Pure policy lives in ``tools/gate.py`` (VALUZ-CHATPLAN D6 strict) — this
    wrapper only shapes the rejection for the tool wire.
    """
    failure = gate.check_plan_writer_gate(sess, task)
    if failure is not None:
        return ToolResult(content=failure.reason, is_error=True)
    return None


async def _check_orchestration_gate(ctx: ExecContext) -> tuple[str, str] | ToolResult:
    """Gate for ``create_task`` (M10 附录 E). Returns (project_id, agent_slug).

    Allowed only from a **plain project conversation** session: it must carry a
    ``project_id`` and must NOT already be a task session (``run_kind`` in
    {lead, subtask}) — that prevents a task lead/member from recursively
    spawning nested tasks (附录 E E-3). The project must be a project (chat
    projects are ephemeral). Returns a ToolResult(is_error=True) on failure.
    """
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="create_task: caller session not found", is_error=True)

    verdict = gate.check_orchestration_caller(sess)
    if isinstance(verdict, Failure):
        return ToolResult(content=verdict.reason, is_error=True)
    project_id, agent_slug = verdict

    # Restrict to projects — chat projects are per-session ephemeral.
    # (Needs the DB, so it stays outside the pure policy; project knowledge
    # is read through the resolver seam.)

    async with async_unit_of_work(commit=False) as db:
        env = await task_session_resolver.resolve_project_env(
            db, user_id=sess.user_id, project_id=project_id
        )
    if env is None or env.project_row.kind != "project":
        return ToolResult(
            content="create_task is only available inside a project",
            is_error=True,
        )

    return project_id, agent_slug


async def _resolve_task_lead(
    *,
    user_id: str,
    project_id: str,
    explicit_slug: str | None,
    conversation_agent_slug: str,
) -> str:
    """Who leads a task: explicit > project default > the conversation agent.

    Shared by ``create_task`` and ``draft_task`` on purpose — two launchers
    disagreeing about the lead is the kind of inconsistency nobody can diagnose
    from a chat transcript
    (docs/design/channel-project-binding-and-default-lead.md §4.3).

    The project default is only honoured while it is still a member; a dangling
    slug (member removed) falls through instead of failing the launch. The
    conversation agent remains the floor, so a project with no default lead
    still answers "pull the bot in and ask something".
    """
    explicit = (explicit_slug or "").strip()
    if explicit:
        return explicit

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.resolution import task_session_resolver

    try:
        async with async_unit_of_work(commit=False) as db:
            env = await task_session_resolver.resolve_project_env(
                db, user_id=user_id, project_id=project_id
            )
            default_lead = (
                (getattr(env.project_row, "default_lead_agent_slug", None) or "").strip()
                if env is not None
                else ""
            )
            if default_lead and await task_session_resolver.member_exists(
                db,
                user_id=user_id,
                project_id=project_id,
                agent_slug=default_lead,
            ):
                return default_lead
    except Exception:  # noqa: BLE001 — a lookup failure must not block the launch
        logger.warning(
            "default lead lookup failed for project %s; using the conversation agent",
            project_id,
            exc_info=True,
        )
    return (conversation_agent_slug or "").strip()


async def _bound_agent_member(sess: Any) -> dict[str, Any] | None:
    """The conversation's own bound agent, shaped like a ``list_members`` row.

    A project-less *chat* project has no deployed project members, but the
    conversation is still driven by a real agent — its bound library agent
    (e.g. the seeded ``default-assistant``), recorded on the session as
    ``metadata["valuz"]["agent_slug"]`` with the kernel agent at
    ``session.agent_id``. ``_list_members_handler`` surfaces it as a fallback
    so the roster isn't an empty dead-end the caller gives up on — the slug is
    directly usable as an automation's ``agent_slug``. Returns ``None`` when
    the session carries no bound agent slug.
    """

    valuz = (getattr(sess, "metadata", None) or {}).get("valuz", {})
    slug = valuz.get("agent_slug") if isinstance(valuz, dict) else None
    if not slug:
        return None
    agent_cfg = getattr(sess, "agent_config", None)
    return {
        "slug": slug,
        "name": agent_cfg.name if agent_cfg else slug,
        "runtime": agent_cfg.runtime_provider if agent_cfg else "unknown",
        "source_agent_slug": slug,
        "role_summary": summarize_role(agent_cfg.instructions) if agent_cfg else "",
    }


# ---------------------------------------------------------------------------
# Pull-gap: surface a member_done that landed between two await_members calls
# ---------------------------------------------------------------------------


def _with_inbox_notice(handler: ToolHandler) -> ToolHandler:
    """Surface a member_done that arrived between two ``await_members`` calls.

    PEEKS the caller's mailbox (non-consuming) after a gap-callable lead tool
    and appends an ``inbox_pending`` notice to the JSON envelope, so a queued
    result is collected now instead of sitting unread for minutes. Self-gating:
    no-op for chat callers, empty inboxes, errors, non-JSON envelopes.
    """
    import json as _json

    async def _wrapped(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        result = await handler(args, ctx)
        try:
            if result.is_error:
                return result

            # Asked of the table. This used to consult a per-process queue,
            # which could not see anything another process had written — so the
            # hint appeared only when the sender happened to share a host
            # process with the agent being hinted.
            if not await mailbox_store.has_pending(ctx.session_id):
                return result
            try:
                payload = _json.loads(result.content)
            except (ValueError, TypeError):
                return result  # plain-text envelope (e.g. finish_task) — leave as-is
            if not isinstance(payload, dict):
                return result
            payload["inbox_pending"] = True
            payload["inbox_hint"] = (
                "A member finished (or a user message arrived) and is waiting in "
                "your inbox. Call await_members now to collect and review it "
                "before continuing — a completed result is already queued and "
                "returns to you instantly."
            )
            return ToolResult(content=_json.dumps(payload, ensure_ascii=False), is_error=False)
        except Exception:  # noqa: BLE001 — a notice must never break the tool call
            return result

    return _wrapped


def _json_result(result: dict[str, Any]) -> ToolResult:
    """The ONE spelling of the JSON tool envelope: a service dict goes to the
    model as JSON, and ``{"error": ...}`` is the error marker. Handlers whose
    service returns a different failure key (``delivered``/``ok``/``status``)
    map it explicitly at the call site — don't invent a second envelope."""
    return ToolResult(content=json.dumps(result, ensure_ascii=False), is_error="error" in result)


def _guarded(tool_name: str, handler: ToolHandler) -> ToolHandler:
    """Turn an unexpected exception into a tool error instead of a transport fault.

    Every handler used to end with its own copy of this — nineteen identical
    ``except Exception: log; return ToolResult(is_error=True)`` tails. Uniform
    behaviour belongs in one place: a handler body should read as the happy
    path plus its OWN validation errors, and a crash it did not anticipate is
    not something nineteen call sites should each decide how to report.

    Expected failures still return their own ``ToolResult`` — this only catches
    what nobody planned for. Sits beside ``_with_inbox_notice`` in the same
    decorator layer applied by ``build_task_tool_defs``.
    """

    async def _wrapped(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        try:
            return await handler(args, ctx)
        except Exception as exc:  # noqa: BLE001 — the point is to catch everything
            logger.exception("%s handler failed (session %s)", tool_name, ctx.session_id)
            # Never echo str(exc) to the model: a SQLAlchemy OperationalError
            # stringifies with the SQL + parameters, filesystem errors carry
            # absolute host paths. The full traceback is already in the log.
            return ToolResult(
                content=(
                    f"{tool_name} failed unexpectedly ({type(exc).__name__}) — "
                    "details are in the server logs"
                ),
                is_error=True,
            )

    return _wrapped


# Lead tools worth augmenting: those a lead can call *during* a wait gap. Excludes
# await_members (the consumer itself), terminal/plain-text tools (finish_task,
# update_deliverable), and chat-side orchestration tools (create/list/get_task,
# draft/commit/abandon/inject/resume) a running lead never calls.
_INBOX_NOTICE_TOOLS: frozenset[str] = frozenset(
    {
        DISPATCH_TOOL_NAME,
        GET_PLAN_TOOL_NAME,
        MODIFY_PLAN_TOOL_NAME,
        REVIEW_SUBTASK_TOOL_NAME,
        SEND_TOOL_NAME,
        STOP_SUBTASK_TOOL_NAME,
        PLAN_TASK_TOOL_NAME,
        LIST_MEMBERS_TOOL_NAME,
    }
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def _dispatch_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_lead_gate(ctx, tool="dispatch")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate

    subtask_key: str = (args.get("subtask_key") or "").strip()
    if not subtask_key:
        return ToolResult(content="dispatch: 'subtask_key' is required", is_error=True)
    result = await orch.dispatcher.dispatch_async(
        task_id=task_id,
        project_id=project_id,
        lead_session_id=ctx.session_id,
        subtask_key=subtask_key,
        agent=args.get("agent"),
        goal=args.get("goal"),
        refs=args.get("refs") or [],
        project_mode=args.get("project_mode"),
        user_id=ctx.user_id,
    )
    return _json_result(result)


async def _await_members_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_lead_gate(ctx, tool="await_members")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate
    result = await orch.coordination.await_member_results(
        lead_session_id=ctx.session_id,
        project_id=project_id,
        task_id=task_id,
        keys=args.get("keys"),
        # Default to "any" for immediate per-member review: return as
        # soon as one member finishes so the lead reviews it without
        # waiting for the slowest sibling. Loop to collect the rest.
        mode=args.get("mode") or "any",
        timeout_s=args.get("timeout_s"),
        user_id=ctx.user_id,
    )
    return _json_result(result)


async def _send_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_lead_gate(ctx, tool="send")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate

    to_session_id: str = args.get("session_id", "")
    text: str = args.get("text", "")
    if not to_session_id or not text:
        return ToolResult(content="send: session_id and text are required", is_error=True)

    result = await messaging.send_to_member(
        from_session_id=ctx.session_id,
        to_session_id=to_session_id,
        text=text,
        project_id=project_id,
        task_id=task_id,
        user_id=ctx.user_id,
    )
    return ToolResult(
        content=json.dumps(result, ensure_ascii=False),
        is_error=not result.get("delivered"),
    )


async def _draft_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_orchestration_gate(ctx)
    if isinstance(gate, ToolResult):
        return gate
    project_id, conversation_agent_slug = gate

    goal: str = (args.get("goal") or "").strip()
    if not goal:
        return ToolResult(content="draft_task: goal is required", is_error=True)
    lead_agent = await _resolve_task_lead(
        user_id=ctx.user_id,
        project_id=project_id,
        explicit_slug=args.get("lead_agent_slug"),
        conversation_agent_slug=conversation_agent_slug,
    )
    if not lead_agent:
        return ToolResult(
            content="draft_task: no lead_agent_slug given and conversation has no agent",
            is_error=True,
        )

    try:
        task_row = await orch.lifecycle.draft_task(
            project_id=project_id,
            goal=goal,
            lead_agent_slug=lead_agent,
            originating_session_id=ctx.session_id,
            refs=args.get("refs") or [],
            title=args.get("title"),
            user_id=ctx.user_id,
        )
        return ToolResult(
            content=json.dumps(
                {
                    "task_id": task_row.id,
                    "title": task_row.title,
                    "lead_agent_slug": lead_agent,
                    "status": "draft",
                    "plan_version": task_row.plan_version,
                },
                ensure_ascii=False,
            )
        )
    except ValueError as exc:
        # An EXPECTED failure (unknown project / agent not a member) — keep it
        # here with its own message. Anything unexpected is _guarded's job.
        return ToolResult(content=f"draft_task: {exc}", is_error=True)


async def _commit_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    # commit_task is the writer-gate-protected state transition: only the
    # draft's originator (or a same-project chat) can flip it active.
    user_id = ctx.user_id
    resolved = await _resolve_plan_writer_task(ctx, args)
    if isinstance(resolved, ToolResult):
        return resolved
    task, project_id, task_id = resolved
    result = await orch.lifecycle.commit_task(
        task_id=task_id,
        project_id=project_id,
        caller_session_id=ctx.session_id,
        lead_agent_slug_override=args.get("lead_agent_slug"),
        user_id=user_id,
    )
    return _json_result(result)


async def _abandon_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    user_id = ctx.user_id
    resolved = await _resolve_plan_writer_task(ctx, args)
    if isinstance(resolved, ToolResult):
        return resolved
    task, project_id, task_id = resolved
    result = await orch.lifecycle.abandon_task(
        task_id=task_id,
        project_id=project_id,
        caller_session_id=ctx.session_id,
        reason=(args.get("reason") or ""),
        user_id=user_id,
    )
    return _json_result(result)


async def _authorize_task_conversation_caller(
    ctx: ExecContext, task_id: str, tool: str
) -> tuple[Any, Any] | ToolResult:
    """Load caller session + task, allow the task's originator or a session in
    its project. The auth model inject_into_task and resume_task share: looser
    than the writer gate because the LEAD keeps full authority over what to do
    with the request."""
    sess = await data_reader().get_session(ctx.user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content=f"{tool}: caller session not found", is_error=True)
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(ctx.user_id, task_id)
    if task is None:
        return ToolResult(content=f"{tool}: task {task_id!r} not found", is_error=True)
    v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
    caller_ws = v.get("project_id", "")  # SessionData has no project_id field
    origin = (task.metadata_ or {}).get("originating_session_id")
    if not ((origin and sess.id == origin) or (caller_ws and caller_ws == task.project_id)):
        return ToolResult(
            content=(
                f"{tool}: FORBIDDEN — caller is neither the task's originator "
                f"nor a session in the task's project (task project {task.project_id!r})"
            ),
            is_error=True,
        )
    return sess, task


async def _inject_into_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    # VALUZ-CHATPLAN S4: chat → running-lead intervention. Auth is looser
    # than the writer gate (a chat session may not be the originator AND
    # the task is past draft) — project-member is enough because the
    # lead retains full authority over what to do with the message.
    task_id = (args.get("task_id") or "").strip()
    text = args.get("text") or ""
    if not task_id:
        return ToolResult(content="inject_into_task: task_id is required", is_error=True)
    if not text.strip():
        return ToolResult(content="inject_into_task: text is required", is_error=True)

    authorized = await _authorize_task_conversation_caller(ctx, task_id, "inject_into_task")
    if isinstance(authorized, ToolResult):
        return authorized
    _sess, task = authorized

    # Halted-task revive policy lives in ONE place (recovery.inject_or_revive)
    # — both transports call it.
    result = await orch.recovery.inject_or_revive(
        task_id=task_id,
        project_id=task.project_id,
        text=text,
        from_session_id=ctx.session_id,
        user_id=ctx.user_id,
    )
    return ToolResult(
        content=json.dumps(result, ensure_ascii=False),
        is_error=not result.get("delivered"),
    )


async def _resume_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    # orch.resume_task itself validates the task is in a resumable status.
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return ToolResult(content="resume_task: task_id is required", is_error=True)
    authorized = await _authorize_task_conversation_caller(ctx, task_id, "resume_task")
    if isinstance(authorized, ToolResult):
        return authorized
    _sess, task = authorized

    result = await orch.recovery.resume_task(
        task_id=task_id,
        project_id=task.project_id,
        actor=ctx.session_id,
        user_id=ctx.user_id,
    )
    return ToolResult(
        content=json.dumps(result, ensure_ascii=False),
        is_error=not result.get("ok"),
    )


async def _create_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_orchestration_gate(ctx)
    if isinstance(gate, ToolResult):
        return gate
    project_id, conversation_agent_slug = gate

    goal: str = (args.get("goal") or "").strip()
    if not goal:
        return ToolResult(content="create_task: goal is required", is_error=True)
    lead_agent = await _resolve_task_lead(
        user_id=ctx.user_id,
        project_id=project_id,
        # ``lead_agent_slug`` is the declared name (matching draft_task);
        # ``lead_agent`` was the old spelling — keep reading it so a model
        # working from a cached tool schema still lands.
        explicit_slug=args.get("lead_agent_slug") or args.get("lead_agent"),
        conversation_agent_slug=conversation_agent_slug,
    )
    if not lead_agent:
        return ToolResult(
            content="create_task: no lead_agent given and conversation has no agent",
            is_error=True,
        )
    # ``created_by`` is a SOURCE KIND (user | automation | …), not an id — a
    # chat-created task is user-initiated, and the "via chat" channel is
    # already captured by trigger provenance (originating_session_id →
    # trigger_type="chat"). This used to pass the raw chat session UUID, which
    # leaked into TaskRow.created_by AND the kickoff event's actor, so the
    # timeline's first row rendered a bare hex id.
    task_row = await orch.lifecycle.kickoff(
        project_id=project_id,
        goal=goal,
        lead_agent_slug=lead_agent,
        refs=args.get("refs") or [],
        created_by="user",
        title=args.get("title"),
        originating_session_id=ctx.session_id,
        user_id=ctx.user_id,
    )
    return ToolResult(
        content=json.dumps(
            {
                "task_id": task_row.id,
                "title": task_row.title,
                "lead_agent_slug": lead_agent,
                "status": "active",
            },
            ensure_ascii=False,
        )
    )


async def _list_tasks_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_orchestration_gate(ctx)
    if isinstance(gate, ToolResult):
        return gate
    project_id, _agent_slug = gate
    tasks = await task_service.list_tasks(
        project_id,
        status=args.get("status"),
        mine_session_id=ctx.session_id if args.get("mine_only") else None,
        limit=int(args.get("limit") or 20),
        user_id=ctx.user_id,
    )
    return ToolResult(content=json.dumps({"tasks": tasks}, ensure_ascii=False))


async def _get_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_orchestration_gate(ctx)
    if isinstance(gate, ToolResult):
        return gate
    project_id, _agent_slug = gate
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return ToolResult(content="get_task: task_id is required", is_error=True)
    detail = await task_service.get_task(task_id, project_id, user_id=ctx.user_id)
    if detail is None:
        return ToolResult(
            content=f"task {task_id!r} not found in this project", is_error=True
        )
    return ToolResult(content=json.dumps(detail, ensure_ascii=False))


async def _list_members_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    # Read-only roster query — allowed for BOTH a task lead AND a plain
    # project-conversation launcher (so it can inspect the team before
    # create_task). NOT lead-gated; just needs a project. Resolve from
    # valuz metadata (task runs) or session.project_id (launcher).
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="list_members: caller session not found", is_error=True)
    v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
    project_id = v.get("project_id", "")  # SessionData has no project_id field
    if not project_id:
        return ToolResult(content="list_members: caller session has no project", is_error=True)

    members = await task_service.list_members(project_id, user_id=user_id)
    if not members:
        # Project-less chat fallback (see ``_bound_agent_member``):
        # a chat project has no deployed project members, but the
        # conversation IS driven by its bound agent. Surface it so the
        # roster isn't an empty dead-end that makes the caller give up
        # (e.g. abort an automation create) — the slug is usable
        # directly as the automation's agent_slug.
        bound = await _bound_agent_member(sess)
        if bound is not None:
            members = [bound]
    return ToolResult(content=json.dumps({"members": members}, ensure_ascii=False))


async def _finish_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_lead_gate(ctx, tool="finish_task")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate

    summary: str = args.get("summary", "")
    artifacts: list[str] = args.get("artifacts") or []
    status: str = args.get("status") or "completed"
    force: bool = bool(args.get("force") or False)

    result = await orch.finalization.finish_task(
        task_id=task_id,
        project_id=project_id,
        lead_session_id=ctx.session_id,
        summary=summary,
        artifacts=artifacts,
        status=status,
        force=force,
        user_id=ctx.user_id,
    )
    # Plan-completeness guard rejected the close — surface it so the
    # lead dispatches the remaining subtasks instead of stopping.
    if isinstance(result, dict) and result.get("status") == "rejected":
        return ToolResult(
            content=result.get("error", "finish_task rejected"), is_error=True
        )
    return ToolResult(content="Task closed. Events appended. Do not continue working.")


async def _resolve_plan_task_id(ctx: ExecContext, args: dict[str, Any]) -> str | None:
    """The task a plan tool is aimed at: explicit for chat callers, from the
    session's own metadata for a lead. Authorization is NOT decided here — that
    belongs to ``plan_commands``, which is the single door both transports use."""
    if task_id := (args.get("task_id") or ""):
        return str(task_id)
    sess = await data_reader().get_session(ctx.user_id, ctx.session_id)
    if sess is None:
        return None
    return (sess.metadata or {}).get("valuz", {}).get("task_id") or None


async def _plan_task_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    task_id = await _resolve_plan_task_id(ctx, args)
    if not task_id:
        return ToolResult(
            content=(
                "plan tool: task_id is required (chat callers must pass it "
                "explicitly; lead callers must have it in session metadata)"
            ),
            is_error=True,
        )
    return _json_result(
        await plan_commands.plan_task(
            plan_commands.AgentCaller(ctx.session_id, ctx.user_id),
            task_id=task_id,
            subtasks=args.get("subtasks") or [],
        )
    )


async def _get_plan_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    task_id = await _resolve_plan_task_id(ctx, args)
    if not task_id:
        return ToolResult(content="plan tool: task_id is required", is_error=True)
    return _json_result(
        await plan_commands.get_plan(
            plan_commands.AgentCaller(ctx.session_id, ctx.user_id), task_id=task_id
        )
    )


async def _modify_plan_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    task_id = await _resolve_plan_task_id(ctx, args)
    if not task_id:
        return ToolResult(content="plan tool: task_id is required", is_error=True)
    expected = args.get("expected_version")
    return _json_result(
        await plan_commands.modify_plan(
            plan_commands.AgentCaller(ctx.session_id, ctx.user_id),
            task_id=task_id,
            add=args.get("add"),
            update=args.get("update"),
            expected_version=int(expected) if expected is not None else None,
        )
    )


async def _review_subtask_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    gate = await _check_lead_gate(ctx, tool="review_subtask")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate
    decision = (args.get("decision") or "").strip()
    if decision not in ("approve", "rework"):
        return ToolResult(
            content="review_subtask: 'decision' must be 'approve' or 'rework'", is_error=True
        )
    if decision == "rework" and not (args.get("feedback") or "").strip():
        return ToolResult(
            content="review_subtask: 'feedback' is required when decision='rework'",
            is_error=True,
        )
    result = await planning.review_subtask(
        task_id=task_id,
        project_id=project_id,
        lead_session_id=ctx.session_id,
        decision=decision,
        subtask_key=args.get("subtask_key"),
        session_id=args.get("session_id"),
        feedback=args.get("feedback"),
        user_id=ctx.user_id,
    )
    return _json_result(result)


async def _stop_subtask_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    """Lead-only HARD stop of an in-flight subtask. Wraps the existing
    ``orch.stop_member`` (which was reachable only from the user
    ``:intervene`` HTTP route) so the lead can cancel a member from inside
    its own turn."""
    user_id = ctx.user_id
    gate = await _check_lead_gate(ctx, tool="stop_subtask")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate

    # Resolve target session id from explicit arg or via subtask_key →
    # latest_run_session_id on the plan node.
    target_session_id = (args.get("session_id") or "").strip()
    subtask_key = (args.get("subtask_key") or "").strip()
    if not target_session_id and not subtask_key:
        return ToolResult(
            content="stop_subtask: either 'session_id' or 'subtask_key' is required",
            is_error=True,
        )

    if not target_session_id:
        # Look up by subtask_key

        async with async_unit_of_work(commit=False) as db:
            task = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
        if task is None:
            return ToolResult(
                content=f"stop_subtask: task {task_id!r} not found", is_error=True
            )
        node = TaskPlan.from_dict(task.plan).get(subtask_key)
        if node is None:
            return ToolResult(
                content=f"stop_subtask: no subtask with key {subtask_key!r}",
                is_error=True,
            )
        target_session_id = node.latest_run_session_id or ""
        if not target_session_id:
            return ToolResult(
                content=(
                    f"stop_subtask: subtask {subtask_key!r} has no in-flight run "
                    "to stop (latest_run_session_id is null)"
                ),
                is_error=True,
            )

    reason = (args.get("reason") or "").strip()
    ok = await orch.recovery.stop_member(target_session_id, user_id=user_id)
    if not ok:
        return ToolResult(
            content=(
                f"stop_subtask: member session {target_session_id!r} not found "
                "or is not a subtask (already finished?)"
            ),
            is_error=True,
        )
    return ToolResult(
        content=json.dumps(
            {
                "stopped": True,
                "session_id": target_session_id,
                "subtask_key": subtask_key or None,
                "reason": reason,
                "next": (
                    "plan node is now `rework`; call dispatch(subtask_key=...) to retry "
                    "with a corrected goal, or re-scope it with modify_plan(update=[...]) first"
                ),
            },
            ensure_ascii=False,
        )
    )


async def _update_deliverable_handler(
    orch: TaskOrchestrator, args: dict[str, Any], ctx: ExecContext
) -> ToolResult:
    user_id = ctx.user_id
    gate = await _check_lead_gate(ctx, tool="update_deliverable")
    if isinstance(gate, ToolResult):
        return gate
    task_id, project_id = gate

    summary: str = args.get("summary", "")
    artifacts: list[str] = args.get("artifacts") or []

    result = await orch.finalization.update_deliverable(
        task_id=task_id,
        project_id=project_id,
        lead_session_id=ctx.session_id,
        summary=summary,
        artifacts=artifacts,
        user_id=user_id,
    )
    if isinstance(result, dict) and result.get("status") == "rejected":
        return ToolResult(
            content=result.get("error", "update_deliverable rejected"),
            is_error=True,
        )
    return ToolResult(content="Deliverable card refreshed.")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


# Wire identity (name / description / parameters) lives on the ToolDef
# declarations; this maps each declared tool to the handler that serves it.
# ``build_task_tool_defs`` zips the two — repeating the identity here would be
# a second copy to keep in sync (and was, until 2026-07).
_HANDLERS: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    DISPATCH_TOOL_NAME: _dispatch_handler,
    AWAIT_MEMBERS_TOOL_NAME: _await_members_handler,
    LIST_MEMBERS_TOOL_NAME: _list_members_handler,
    FINISH_TASK_TOOL_NAME: _finish_task_handler,
    SEND_TOOL_NAME: _send_handler,
    CREATE_TASK_TOOL_NAME: _create_task_handler,
    LIST_TASKS_TOOL_NAME: _list_tasks_handler,
    GET_TASK_TOOL_NAME: _get_task_handler,
    PLAN_TASK_TOOL_NAME: _plan_task_handler,
    GET_PLAN_TOOL_NAME: _get_plan_handler,
    MODIFY_PLAN_TOOL_NAME: _modify_plan_handler,
    REVIEW_SUBTASK_TOOL_NAME: _review_subtask_handler,
    DRAFT_TASK_TOOL_NAME: _draft_task_handler,
    COMMIT_TASK_TOOL_NAME: _commit_task_handler,
    ABANDON_TASK_TOOL_NAME: _abandon_task_handler,
    INJECT_INTO_TASK_TOOL_NAME: _inject_into_task_handler,
    RESUME_TASK_TOOL_NAME: _resume_task_handler,
    STOP_SUBTASK_TOOL_NAME: _stop_subtask_handler,
    UPDATE_DELIVERABLE_TOOL_NAME: _update_deliverable_handler,
}


def build_task_tool_defs(orchestrator: TaskOrchestrator) -> tuple[ToolDef, ...]:
    """Bind *orchestrator* into every declared task tool and return the set.

    Identity comes from the declaration tuples (deduped by name — the lead and
    chat toolsets deliberately overlap); behaviour comes from ``_HANDLERS``.
    The toolkit MCP server partitions the result into base/lead toolsets by
    name (boot/steps.py). Call after ``init_kernel_dependencies()``.
    """
    defs: list[ToolDef] = []
    seen: set[str] = set()
    for decl in DISPATCH_TOOL_DECLARATIONS + ORCHESTRATION_TOOL_DECLARATIONS:
        if decl.name in seen:
            continue
        seen.add(decl.name)
        handler: ToolHandler = _guarded(decl.name, partial(_HANDLERS[decl.name], orchestrator))
        # Pull-gap: surface a queued member_done on gap-callable lead tools.
        if decl.name in _INBOX_NOTICE_TOOLS:
            handler = _with_inbox_notice(handler)
        defs.append(replace(decl, handler=handler))
    logger.info("Built task tool defs: %s", ", ".join(sorted(t.name for t in defs)))
    return tuple(defs)
