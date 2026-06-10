"""Dispatch MCP tool DECLARATIONS — the static, import-safe surface.

Holds the tool-name constants, JSON-schema parameter dicts,
``ToolDef(handler=None)`` declarations, the declaration tuples, and the pure
agent-config transforms (``strip_dispatch_tools`` /
``ensure_orchestration_tools_on_agent``).

This module is handler-free and orchestrator-free on purpose: it is imported by
``projects/service.py``, ``agents/service.py``, and ``lifecycle._materialize_lead_agent``
during AgentConfig construction, so it must never reach the orchestrator (which
would re-introduce the startup circular-import the handler closures avoid).

The executable handlers live in ``handlers.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from src.core import ToolDef  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Tool names (surfaced by the kernel as mcp__harness__<name>)
# ---------------------------------------------------------------------------

# v0.14: single non-blocking dispatch verb + await_members collection.
# (dispatch_batch / dispatch_async removed — see decision doc §14.)
DISPATCH_TOOL_NAME = "dispatch"
AWAIT_MEMBERS_TOOL_NAME = "await_members"
LIST_MEMBERS_TOOL_NAME = "list_members"
FINISH_TASK_TOOL_NAME = "finish_task"
SEND_TOOL_NAME = "send"
# v3 session-driven task launcher + observability (M10 附录 E)
CREATE_TASK_TOOL_NAME = "create_task"
LIST_TASKS_TOOL_NAME = "list_tasks"
GET_TASK_TOOL_NAME = "get_task"
# VALUZ-TASK: lead plan / review surface
PLAN_TASK_TOOL_NAME = "plan_task"
GET_PLAN_TOOL_NAME = "get_plan"
MODIFY_PLAN_TOOL_NAME = "modify_plan"
REVIEW_SUBTASK_TOOL_NAME = "review_subtask"
# VALUZ-CHATPLAN: chat-as-control-surface state transitions (S2)
DRAFT_TASK_TOOL_NAME = "draft_task"
COMMIT_TASK_TOOL_NAME = "commit_task"
ABANDON_TASK_TOOL_NAME = "abandon_task"
# VALUZ-CHATPLAN S4: chat → running-lead intervention
INJECT_INTO_TASK_TOOL_NAME = "inject_into_task"
# Chat-side resume — wraps orchestrator.resume_task; accepts paused /
# blocked / stopped / completed (completed = reopen to tweak subtasks).
# Lives on chat agents (not the lead clone, because the lead is dead/idle
# when a task is paused/blocked/stopped/completed — no one's running to call it).
RESUME_TASK_TOOL_NAME = "resume_task"
# Lead-only: stop a specific in-flight subtask. Wraps the existing
# user-initiated ``stop_member`` orchestrator path so the lead can cut a
# misdirected / stuck member without resorting to ``finish_task`` on the
# whole task.
STOP_SUBTASK_TOOL_NAME = "stop_subtask"

DISPATCH_TOOL_NAMES = (
    DISPATCH_TOOL_NAME,
    AWAIT_MEMBERS_TOOL_NAME,
    LIST_MEMBERS_TOOL_NAME,
    FINISH_TASK_TOOL_NAME,
    SEND_TOOL_NAME,
    CREATE_TASK_TOOL_NAME,
    LIST_TASKS_TOOL_NAME,
    GET_TASK_TOOL_NAME,
    PLAN_TASK_TOOL_NAME,
    GET_PLAN_TOOL_NAME,
    MODIFY_PLAN_TOOL_NAME,
    REVIEW_SUBTASK_TOOL_NAME,
    DRAFT_TASK_TOOL_NAME,
    COMMIT_TASK_TOOL_NAME,
    ABANDON_TASK_TOOL_NAME,
    INJECT_INTO_TASK_TOOL_NAME,
    RESUME_TASK_TOOL_NAME,
    STOP_SUBTASK_TOOL_NAME,
)

# Strict-lead-only dispatch tools — must NOT be on a plain conversation agent
# (stripped via ``strip_dispatch_tools``).
#
# VALUZ-CHATPLAN S2 (D4): plan_task / modify_plan / get_plan are NO LONGER
# strict-lead-only — they are also advertised on chat agents (via
# ORCHESTRATION_TOOL_DECLARATIONS) so the chat-as-control-surface flow can
# write plans on draft tasks. Handler-level ``_check_plan_writer_gate``
# enforces "active-task plan is lead-only" semantics.
#
# VALUZ-CHATPLAN bug fix: ``list_members`` was previously in this set with
# a comment claiming ``ensure_orchestration_tools_on_agent`` would re-add it
# via ORCHESTRATION_TOOL_DECLARATIONS. But ``_prepare_conversation_tools``
# runs ``strip_dispatch_tools(ensure_orchestration_tools_on_agent(agent))``
# (add THEN strip), so list_members was added and immediately stripped —
# leaving chat agents without it. The chat-as-control-surface flow needs
# list_members on chat agents (and the description explicitly tells the
# LLM to call it before draft_task), so it's removed from the strip set.
# Lead clones still get list_members via DISPATCH_TOOL_DECLARATIONS in
# ``_materialize_lead_agent``, so removing it from the strip set is safe.
LEAD_ONLY_TOOL_NAMES = frozenset(
    {
        DISPATCH_TOOL_NAME,
        AWAIT_MEMBERS_TOOL_NAME,
        SEND_TOOL_NAME,
        FINISH_TASK_TOOL_NAME,
        REVIEW_SUBTASK_TOOL_NAME,
        STOP_SUBTASK_TOOL_NAME,
    }
)


def strip_dispatch_tools(agent_cfg: Any) -> Any:
    """Return *agent_cfg* with all lead-only dispatch tools removed (idempotent).

    A conversation/member agent must not advertise dispatch/finish_task — those
    belong on the per-task lead clone. Pure — caller persists if changed.
    """
    from dataclasses import replace

    tools = agent_cfg.tools or ()
    kept = tuple(t for t in tools if getattr(t, "name", None) not in LEAD_ONLY_TOOL_NAMES)
    if len(kept) == len(tools):
        return agent_cfg
    return replace(agent_cfg, tools=kept)


# ---------------------------------------------------------------------------
# JSON Schema parameters
# ---------------------------------------------------------------------------

_DISPATCH_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["subtask_key"],
    "properties": {
        "subtask_key": {
            "type": "string",
            "description": (
                "The key of a planned subtask (from plan_task/get_plan) to "
                "dispatch. It must be ready: deps done, status planned/rework."
            ),
        },
        "agent": {
            "type": "string",
            "description": "Optional override of the subtask's planned agent slug.",
        },
        "goal": {
            "type": "string",
            "description": "Optional override of the subtask's planned goal/brief.",
        },
        "refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of file paths or references relevant to the subtask.",
            "default": [],
        },
        "project_mode": {
            "type": "string",
            "enum": ["shared", "repo-worktree"],
            "description": (
                "Working directory mode. "
                "'shared' (default) = the project directory itself, so the "
                "member reads/writes project files natively. "
                "'repo-worktree' = an isolated git worktree (only when the "
                "project is a git repo; for parallel code changes)."
            ),
        },
    },
}

_AWAIT_MEMBERS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Subtask keys to wait for. Omit to wait for ALL currently "
                "outstanding subtasks (plan status in_progress/in_review)."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["all", "any"],
            "description": (
                "'any' (DEFAULT) returns as soon as the first member finishes "
                "so you review it immediately — loop await_members to collect "
                "the rest. 'all' blocks until every target key finishes (only "
                "use when you truly need the whole batch before acting)."
            ),
        },
        "timeout_s": {
            "type": "number",
            "description": (
                "Optional max seconds to wait. On timeout, returns whatever "
                "finished plus a 'pending' list (so a stuck member can't hang "
                "you forever)."
            ),
        },
    },
}

_SEND_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "text"],
    "properties": {
        "session_id": {
            "type": "string",
            "description": (
                "The member session id returned by dispatch_async (or seen in a "
                "<member-result> block) to send this follow-up to."
            ),
        },
        "text": {
            "type": "string",
            "description": "The follow-up instruction/message for the running member.",
        },
    },
}

_LIST_MEMBERS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

# v3: create_task launches a brand-new task (own lead session + runner) via the
# unchanged TaskOrchestrator.kickoff. Surfaced on project-conversation agents.
_CREATE_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["goal"],
    "properties": {
        "goal": {
            "type": "string",
            "description": "The task goal/brief handed to the spawned lead agent.",
        },
        "lead_agent": {
            "type": "string",
            "description": (
                "Project-local agent slug to lead the task. Defaults to the "
                "agent of the current conversation."
            ),
        },
        "dispatch_mode": {
            "type": "string",
            "enum": ["sync", "async"],
            "description": (
                "Dispatch architecture for the spawned lead. 'async' (default) "
                "= persistent actor that dispatches members in parallel; 'sync' "
                "= lead blocks on each dispatch in a single turn."
            ),
        },
        "refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional file paths / references relevant to the task.",
            "default": [],
        },
        "title": {
            "type": "string",
            "description": "Optional short task title (defaults to the goal prefix).",
        },
    },
}

# v3 observability: list/check the tasks in this project (for the conversation
# launcher to report progress on tasks it created).
_LIST_TASKS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["active", "completed", "failed"],
            "description": "Optional filter by task status.",
        },
        "mine_only": {
            "type": "boolean",
            "description": (
                "When true, only tasks launched from THIS conversation are "
                "returned (default false = all tasks in the project)."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max tasks to return (default 20, newest first).",
        },
    },
}

_GET_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["task_id"],
    "properties": {
        "task_id": {
            "type": "string",
            "description": "The task id (returned by create_task or list_tasks).",
        },
    },
}

_FINISH_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "Final summary of the task result.",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of artifact file paths to promote to the task level.",
            "default": [],
        },
        "status": {
            "type": "string",
            "enum": ["completed", "stopped"],
            "description": (
                "Terminal task status. 'completed' (default) = goal achieved; "
                "'stopped' = user-requested stop or goal unreachable — picks "
                "this when the user injects a stop instruction or you judge "
                "the goal cannot be reached. Records a task_stopped event. "
                "Task-level 'failed' is intentionally not in this enum (see "
                "task_state.py): unrecoverable user-driven termination is "
                "'stopped'; a mid-turn lead crash is surfaced as 'blocked' "
                "by auto-finalize, not by you."
            ),
        },
    },
}

# VALUZ-TASK: plan / review schemas
_SUBTASK_NODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["key", "title"],
    "properties": {
        "key": {"type": "string", "description": "Stable, task-unique node key."},
        "title": {"type": "string", "description": "Short label for the subtask."},
        "goal": {"type": "string", "description": "The scoped goal/brief for the member."},
        "agent": {"type": "string", "description": "Project-local agent slug to run it."},
        "review_criteria": {
            "type": "string",
            "description": (
                "Acceptance bar for THIS subtask — the concrete, checkable items "
                "you (the lead) will review it against (e.g. 'covers price/%chg/"
                "volume + a 1-line takeaway; figures cited with source'). It is "
                "given to the member so it knows the bar, and shown back to you "
                "at review_subtask time."
            ),
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Keys that must be 'done' before this one is dispatchable.",
            "default": [],
        },
        "parallel_group": {"type": "string", "description": "Optional parallel-batch label."},
    },
}

_PLAN_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["subtasks"],
    "properties": {
        "task_id": {
            "type": "string",
            "description": (
                "Required when called from a chat session writing a draft plan. "
                "When omitted, the caller must be a lead session (the task is "
                "inferred from the lead's session metadata)."
            ),
        },
        "subtasks": {
            "type": "array",
            "items": _SUBTASK_NODE_SCHEMA,
            "description": "The full subtask DAG to lay down before dispatching.",
        },
    },
}

_GET_PLAN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": (
                "Required when called from a chat session. Lead sessions may omit it; "
                "the task is inferred from the lead's session metadata."
            ),
        },
    },
}

_MODIFY_PLAN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": (
                "Required when called from a chat session. Lead sessions may omit it; "
                "the task is inferred from the lead's session metadata."
            ),
        },
        "add": {
            "type": "array",
            "items": _SUBTASK_NODE_SCHEMA,
            "description": "New subtask nodes to add.",
        },
        "update": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key"],
                "properties": {
                    "key": {"type": "string"},
                    "title": {"type": "string"},
                    "goal": {"type": "string"},
                    "agent": {"type": "string"},
                    "review_criteria": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "parallel_group": {"type": "string"},
                },
            },
            "description": "Patches to existing nodes (each carries 'key' + changed fields).",
        },
        "expected_version": {
            "type": "integer",
            "description": (
                "CAS optimistic-lock token (from get_plan response). When passed, "
                "the call is rejected with PLAN_VERSION_CONFLICT if it does not "
                "equal the current plan_version. Chat callers (multi-session "
                "concurrency possible) should always pass it; lead callers "
                "(single-actor serial) may omit it."
            ),
        },
    },
}

# VALUZ-CHATPLAN S2: state-transition parameters

_DRAFT_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["goal", "lead_agent_slug"],
    "properties": {
        "goal": {
            "type": "string",
            "description": "The task goal — what the lead should achieve.",
        },
        "lead_agent_slug": {
            "type": "string",
            "description": "Which project agent will become the lead at commit time.",
        },
        "title": {
            "type": "string",
            "description": "Optional human title (defaults to first 100 chars of goal).",
        },
        "refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional file paths or references for the lead's brief.",
        },
    },
}

_COMMIT_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["task_id"],
    "properties": {
        "task_id": {"type": "string", "description": "The draft task to commit."},
        "lead_agent_slug": {
            "type": "string",
            "description": (
                "Optional override of the lead agent set at draft time. "
                "Most callers omit this and let the draft's lead_agent_slug stand."
            ),
        },
    },
}

_ABANDON_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["task_id"],
    "properties": {
        "task_id": {"type": "string", "description": "The draft task to abandon."},
        "reason": {
            "type": "string",
            "description": "Optional human-readable reason recorded in the timeline.",
        },
    },
}

# VALUZ-CHATPLAN S4: chat → running-lead intervention
_INJECT_INTO_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["task_id", "text"],
    "properties": {
        "task_id": {
            "type": "string",
            "description": "The active (or paused) task whose lead should receive the message.",
        },
        "text": {
            "type": "string",
            "description": (
                "The instruction / clarification to deliver to the running lead. "
                'Wrapped server-side in <user-instruction source="chat"> and '
                "delivered at the lead's next turn boundary."
            ),
        },
    },
}

_RESUME_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["task_id"],
    "properties": {
        "task_id": {
            "type": "string",
            "description": (
                "The paused, blocked, or stopped task to revive. The lead "
                "session is respawned (Layer-2 recovery path) and the task "
                "flips back to 'active'. Rejected for hard-terminal tasks "
                "('completed' = goal already met → run a follow-up task; "
                "'abandoned' = draft was discarded, nothing to revive)."
            ),
        },
    },
}

_REVIEW_SUBTASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["decision"],
    "properties": {
        "subtask_key": {"type": "string", "description": "Plan node key to review."},
        "session_id": {
            "type": "string",
            "description": "Member run session id (alternative to subtask_key).",
        },
        "decision": {
            "type": "string",
            "enum": ["approve", "rework"],
            "description": "approve → mark done (unlocks dependents); rework → send back.",
        },
        "feedback": {
            "type": "string",
            "description": (
                "For rework: instructions delivered to the member (required). "
                "For approve: a short reason why it passed — shown in the task "
                "timeline so the user can see the review rationale (optional)."
            ),
        },
    },
}

_STOP_SUBTASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subtask_key": {
            "type": "string",
            "description": (
                "Plan node key whose in-flight run should be cancelled. "
                "Either subtask_key OR session_id must be given."
            ),
        },
        "session_id": {
            "type": "string",
            "description": (
                "Member run session id (alternative to subtask_key). Use this "
                "when you got the id from a SubtaskResult / await_members reply."
            ),
        },
        "reason": {
            "type": "string",
            "description": (
                "Short reason recorded on the plan node's review_feedback and in "
                "the task timeline (subtask_stopped event)."
            ),
        },
    },
}

# ---------------------------------------------------------------------------
# ToolDef declarations (handler=None) — placed on lead agent AgentConfig.tools
# so the runtime advertises these tools to the model. The actual handlers
# are registered globally and resolved by build_toolkit_for_config.
# ---------------------------------------------------------------------------

DISPATCH_TOOL_DECLARATION = ToolDef(
    name=DISPATCH_TOOL_NAME,
    description=(
        "Dispatch a PLANNED subtask (by subtask_key) to its member agent. "
        "NON-BLOCKING: returns immediately with the member's session_id; the "
        "member runs concurrently. To run independent subtasks in parallel, "
        "call dispatch once per subtask, THEN call await_members to collect "
        "their results. The subtask must exist in the plan (call plan_task "
        "first) and be ready (deps done)."
    ),
    parameters=_DISPATCH_PARAMETERS,
    handler=None,
)

AWAIT_MEMBERS_TOOL_DECLARATION = ToolDef(
    name=AWAIT_MEMBERS_TOOL_NAME,
    description=(
        "Wait for dispatched members to finish and collect their results "
        "(SubtaskResult[]) — call this after dispatch(). Use mode='any' in a "
        "loop to review members the moment each one completes; mode='all' to "
        "wait for the whole batch. Each returned member then awaits your "
        "review_subtask. Omit 'keys' to wait for all outstanding subtasks."
    ),
    parameters=_AWAIT_MEMBERS_PARAMETERS,
    handler=None,
)

PLAN_TASK_TOOL_DECLARATION = ToolDef(
    name=PLAN_TASK_TOOL_NAME,
    description=(
        "Lay down the whole task as a structured subtask plan (DAG) BEFORE "
        "dispatching anything. Each subtask has a key, goal, optional agent, and "
        "depends_on (keys that must finish first). You MUST plan before you can "
        "dispatch. Returns the plan + which keys are ready now."
    ),
    parameters=_PLAN_TASK_PARAMETERS,
    handler=None,
)

GET_PLAN_TOOL_DECLARATION = ToolDef(
    name=GET_PLAN_TOOL_NAME,
    description=(
        "Read the current plan: every subtask's status, which keys are ready to "
        "dispatch now (deps satisfied), and overall counts. Use to decide the "
        "next dispatch and to check if all subtasks are done."
    ),
    parameters=_GET_PLAN_PARAMETERS,
    handler=None,
)

MODIFY_PLAN_TOOL_DECLARATION = ToolDef(
    name=MODIFY_PLAN_TOOL_NAME,
    description=(
        "The subtask-level add/update/remove primitive. Revise the plan after "
        "it exists: add new subtasks, update existing ones (by key), or remove "
        "them. Validates the DAG (no cycles / dangling deps). In CHAT on a "
        "DRAFT task, call this directly to amend subtasks; on a RUNNING task "
        "the lead owns the plan, so from chat send the change via "
        "inject_into_task and let the lead call modify_plan."
    ),
    parameters=_MODIFY_PLAN_PARAMETERS,
    handler=None,
)

REVIEW_SUBTASK_TOOL_DECLARATION = ToolDef(
    name=REVIEW_SUBTASK_TOOL_NAME,
    description=(
        "Review a finished subtask: approve (mark done, unlocking dependents) or "
        "rework (send it back with feedback). Identify it by subtask_key or the "
        "member's session_id. Call this after a member reports a result."
    ),
    parameters=_REVIEW_SUBTASK_PARAMETERS,
    handler=None,
)

STOP_SUBTASK_TOOL_DECLARATION = ToolDef(
    name=STOP_SUBTASK_TOOL_NAME,
    description=(
        "HARD-stop a specific in-flight subtask. Use when a member is misdirected, "
        "stuck, or no longer needed (e.g. plan was revised). Interrupts the "
        "kernel session immediately, flips the plan node to ``rework`` (so you "
        "can re-dispatch with a corrected goal via dispatch()) or you can "
        "modify_plan to retire the work, and injects a synthetic "
        "``member_done(status=cancelled)`` into your mailbox so await_members "
        "doesn't hang. Identify the target by ``subtask_key`` OR ``session_id``. "
        "This is different from ``send`` (which just nudges a member that keeps "
        "running) and from ``review_subtask rework`` (which sends a member back "
        "to redo with feedback — that member is still considered useful). Use "
        "``stop_subtask`` only when you want to abandon a member's work outright."
    ),
    parameters=_STOP_SUBTASK_PARAMETERS,
    handler=None,
)

LIST_MEMBERS_TOOL_DECLARATION = ToolDef(
    name=LIST_MEMBERS_TOOL_NAME,
    description=(
        "List the project members available for dispatch. Each item has "
        "slug, name, runtime, source_agent_slug, and role_summary (the member's "
        "role/capabilities) — use role_summary to route each subtask to the "
        "best-fit member."
    ),
    parameters=_LIST_MEMBERS_PARAMETERS,
    handler=None,
)

FINISH_TASK_TOOL_DECLARATION = ToolDef(
    name=FINISH_TASK_TOOL_NAME,
    description=(
        "Close the task with a summary and optional artifact list. Call this "
        "exactly once. Pass status='completed' when the goal is fully achieved "
        "(default) or status='stopped' when the user explicitly asked to stop "
        "via inject or the goal has become unreachable."
    ),
    parameters=_FINISH_TASK_PARAMETERS,
    handler=None,
)

SEND_TOOL_DECLARATION = ToolDef(
    name=SEND_TOOL_NAME,
    description=(
        "Send a free-text follow-up to a running member (by its session_id). "
        "Delivered at the member's next turn boundary. Use to refine a member's "
        "goal, answer its question, or give more context while it is still alive."
    ),
    parameters=_SEND_PARAMETERS,
    handler=None,
)

CREATE_TASK_TOOL_DECLARATION = ToolDef(
    name=CREATE_TASK_TOOL_NAME,
    description=(
        "ONLY for a NEW task/goal. To add or change SUBTASKS of an EXISTING "
        "task, do NOT create a new one — use inject_into_task / modify_plan "
        "(active/paused) or resume_task then inject (blocked/stopped/"
        "completed). "
        "Launch a new multi-agent task in this project. Hands the goal to a "
        "lead agent that dispatches work to member agents and runs to "
        "completion in the background. Returns the task id immediately; track "
        "progress in the project's task panel. Use when the user's request is "
        "better served by orchestrating multiple agents than answering inline. "
        "IMPORTANT: ALWAYS call list_members FIRST to see which member agents "
        "exist (their slugs, runtimes, and role summaries), then pick the lead "
        "and frame the goal around delegating to those members — do not create "
        "the task before knowing the available team."
    ),
    parameters=_CREATE_TASK_PARAMETERS,
    handler=None,
)

# Plan + review tools are surfaced on the lead in BOTH dispatch modes.
PLAN_REVIEW_TOOL_DECLARATIONS: tuple[ToolDef, ...] = (
    PLAN_TASK_TOOL_DECLARATION,
    GET_PLAN_TOOL_DECLARATION,
    MODIFY_PLAN_TOOL_DECLARATION,
    REVIEW_SUBTASK_TOOL_DECLARATION,
)

DISPATCH_TOOL_DECLARATIONS: tuple[ToolDef, ...] = (
    DISPATCH_TOOL_DECLARATION,
    AWAIT_MEMBERS_TOOL_DECLARATION,
    SEND_TOOL_DECLARATION,
    LIST_MEMBERS_TOOL_DECLARATION,
    FINISH_TASK_TOOL_DECLARATION,
    STOP_SUBTASK_TOOL_DECLARATION,
) + PLAN_REVIEW_TOOL_DECLARATIONS

LIST_TASKS_TOOL_DECLARATION = ToolDef(
    name=LIST_TASKS_TOOL_NAME,
    description=(
        "List the tasks in this project with their status and progress (run "
        "counts). Use to report on tasks you launched with create_task. "
        "Optional filters: status, mine_only (tasks from this conversation)."
    ),
    parameters=_LIST_TASKS_PARAMETERS,
    handler=None,
)

GET_TASK_TOOL_DECLARATION = ToolDef(
    name=GET_TASK_TOOL_NAME,
    description=(
        "Get one task's current status, its lead/member run states, and the "
        "latest result summary. Use to check on or report a specific task."
    ),
    parameters=_GET_TASK_PARAMETERS,
    handler=None,
)

# VALUZ-CHATPLAN S2: chat-as-control-surface state transitions
DRAFT_TASK_TOOL_DECLARATION = ToolDef(
    name=DRAFT_TASK_TOOL_NAME,
    description=(
        "Open a draft task for a NEW goal. FIRST check it isn't an amendment "
        "to an EXISTING task: if the user references a task that already "
        "exists or names a subtask to add/change/remove, do NOT draft a new "
        "task — locate it via list_tasks and use inject_into_task / "
        "modify_plan (active/paused) or resume_task then inject "
        "(blocked/stopped/completed). Only use draft_task for a genuinely new "
        "goal (optionally pass refs to a finished task for a follow-up). "
        "Like create_task but does NOT start the lead session "
        "yet. The draft holds a plan (set/edited via plan_task / modify_plan) "
        "that you iterate with the user before commit_task starts execution. "
        "Returns task_id; status=draft. Use this when the user wants to review "
        "the task breakdown before committing to spending tokens on execution. "
        "IMPORTANT: ALWAYS call list_members FIRST to see which member agents "
        "exist in this project (their slugs, runtimes, role_summary). Pick "
        "lead_agent_slug from that list — do NOT make up an agent name (it will "
        "be rejected with 'agent <slug> is not a member of project'). Frame "
        "the goal around delegating to the members that exist."
    ),
    parameters=_DRAFT_TASK_PARAMETERS,
    handler=None,
)

COMMIT_TASK_TOOL_DECLARATION = ToolDef(
    name=COMMIT_TASK_TOOL_NAME,
    description=(
        "Commit a draft task to execution: spawns the lead session against the "
        "already-written plan and transitions status draft → active. The lead "
        "is briefed that the plan is pre-committed and goes straight to dispatch "
        "(it will NOT call plan_task again). REQUIRES that the user has "
        "explicitly approved execution — do NOT call this without an unambiguous "
        '"go" / "execute" / "OK 执行" / equivalent confirmation from the user.'
    ),
    parameters=_COMMIT_TASK_PARAMETERS,
    handler=None,
)

ABANDON_TASK_TOOL_DECLARATION = ToolDef(
    name=ABANDON_TASK_TOOL_NAME,
    description=(
        "Discard a draft task. Terminal — abandoned tasks cannot be resurrected. "
        "Use when the user explicitly says they don't want to do this task. "
        "Only callable on status=draft; for active tasks use the user-facing "
        "stop intervention instead."
    ),
    parameters=_ABANDON_TASK_PARAMETERS,
    handler=None,
)

# VALUZ-CHATPLAN S4 — chat → running-lead intervention
INJECT_INTO_TASK_TOOL_DECLARATION = ToolDef(
    name=INJECT_INTO_TASK_TOOL_NAME,
    description=(
        "PRIMARY way to add / change / remove SUBTASKS of a running task from "
        "chat — prefer it over creating a new task whenever the user is "
        "amending an existing task. "
        "Send a clarification or new instruction from THIS chat session to the "
        "lead of an already-running task (status=active or paused). The lead "
        "receives it at its next turn boundary, wrapped as a "
        '<user-instruction source="chat"> envelope it treats as authoritative '
        "user intent — typically it will translate the message into modify_plan "
        "+ dispatch (or rework an in-flight subtask). Use this when the user "
        "wants to redirect or refine a task that is already executing; do NOT "
        "use it to edit a draft (modify_plan does that directly) or to talk to "
        "individual member sessions (the lead is the only entry point)."
    ),
    parameters=_INJECT_INTO_TASK_PARAMETERS,
    handler=None,
)

# Resume a paused or blocked task from this chat session.
RESUME_TASK_TOOL_DECLARATION = ToolDef(
    name=RESUME_TASK_TOOL_NAME,
    description=(
        "Revive a task that is paused, blocked, stopped, OR completed. The "
        "lead session is respawned and the task flips back to 'active'; "
        "in-flight members are reconciled the same way as Layer-1 startup "
        "recovery. Use when the user asks to 'continue the task we "
        "stopped/paused', 'restart the blocked task', or — the key new case "
        "— wants to SUPPLEMENT OR ADJUST the subtasks of an already-COMPLETED "
        "task ('给那个已完成的任务再加/改一个子任务'): resume_task reopens it, "
        "then you modify_plan / inject the new subtasks. REJECTED only for "
        "'abandoned' (draft discarded, nothing to revive) and 'draft' (use "
        "commit_task to launch it the first time). When the user wants a "
        "genuinely NEW goal (not a tweak to the finished one), prefer a fresh "
        "follow-up draft_task over reopening. Inject_into_task is the wrong "
        "tool for a dead lead: it only delivers to a STILL-RUNNING lead; "
        "resume_task is what brings a dead/finished lead back."
    ),
    parameters=_RESUME_TASK_PARAMETERS,
    handler=None,
)

# v3 + VALUZ-CHATPLAN: tools surfaced on a project-conversation agent so it can
# spawn + observe tasks (launcher + read-only progress queries) AND draft/edit
# plans before committing to execution. ``list_members`` is included so the
# launcher can inspect the available team BEFORE create_task / draft_task.
#
# plan_task / modify_plan / get_plan are advertised here (VALUZ-CHATPLAN D4
# tool reuse): chat agents call them on draft tasks; handler-level
# ``_check_plan_writer_gate`` enforces "draft writer = originator/project;
# active writer = lead-only" semantics.
ORCHESTRATION_TOOL_DECLARATIONS: tuple[ToolDef, ...] = (
    LIST_MEMBERS_TOOL_DECLARATION,
    CREATE_TASK_TOOL_DECLARATION,
    LIST_TASKS_TOOL_DECLARATION,
    GET_TASK_TOOL_DECLARATION,
    # VALUZ-CHATPLAN S2:
    DRAFT_TASK_TOOL_DECLARATION,
    COMMIT_TASK_TOOL_DECLARATION,
    ABANDON_TASK_TOOL_DECLARATION,
    PLAN_TASK_TOOL_DECLARATION,
    MODIFY_PLAN_TOOL_DECLARATION,
    GET_PLAN_TOOL_DECLARATION,
    # VALUZ-CHATPLAN S4:
    INJECT_INTO_TASK_TOOL_DECLARATION,
    RESUME_TASK_TOOL_DECLARATION,
)

# Names of conversation-launcher tools — dropped from the per-task lead clone
# (the lead dispatches; it doesn't launch new tasks). ``list_members`` is NOT
# in this set: it stays on the lead too (it's part of the dispatch toolset),
# so it must not be stripped from the lead clone.
#
# VALUZ-CHATPLAN S2: draft/commit/abandon are chat-only — a running lead has
# no need (and no business) creating new drafts inside its own execution.
# plan_task / modify_plan / get_plan are deliberately NOT here: the lead
# clone re-adds them via DISPATCH_TOOL_DECLARATIONS. Dedup of duplicate
# advertisements happens in ``_materialize_lead_agent``.
ORCHESTRATION_TOOL_NAMES = frozenset(
    {
        CREATE_TASK_TOOL_NAME,
        LIST_TASKS_TOOL_NAME,
        GET_TASK_TOOL_NAME,
        DRAFT_TASK_TOOL_NAME,
        COMMIT_TASK_TOOL_NAME,
        ABANDON_TASK_TOOL_NAME,
        INJECT_INTO_TASK_TOOL_NAME,
        RESUME_TASK_TOOL_NAME,
    }
)


def ensure_orchestration_tools_on_agent(agent_cfg: Any) -> Any:
    """Return *agent_cfg* with the ``create_task`` ToolDef declared (idempotent).

    Project-conversation agents need ``create_task`` advertised to the model;
    tools live on ``AgentConfig.tools`` (kernel Session has no tools field).
    The launcher counterpart to ``TaskOrchestrator._materialize_lead_agent``
    (which builds the per-task lead clone). Pure — caller persists.
    """
    from dataclasses import replace

    existing = {getattr(t, "name", None) for t in (agent_cfg.tools or ())}
    missing = [td for td in ORCHESTRATION_TOOL_DECLARATIONS if td.name not in existing]
    if not missing:
        return agent_cfg
    return replace(agent_cfg, tools=tuple(agent_cfg.tools or ()) + tuple(missing))
