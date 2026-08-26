"""Task MCP tool DECLARATIONS — the static, import-safe surface.

Holds the tool-name constants, JSON-schema parameter dicts, and the
``ToolDef(handler=None)`` declarations, grouped into the TWO audience tuples
that are this module's real output:

  * :data:`DISPATCH_TOOL_DECLARATIONS`      — the **lead** toolset (a running
    task lead: plan, dispatch, await, review, finish).
  * :data:`ORCHESTRATION_TOOL_DECLARATIONS` — the **chat** toolset (a plain
    project conversation acting as a control surface: create/draft/commit a
    task, inspect it, inject into it, resume it).

``boot/steps.py`` partitions the host toolkit MCP server by exactly these two
name lists (``/_internal/mcp/toolkit/{lead,base}``), so an audience change here
IS the wire change — nothing else needs updating. Both tuples deliberately
share ``list_members`` and the plan tools; the per-call authorization that the
overlap needs lives in ``tools/gate.py`` (e.g. "an active task's plan is
lead-only, chat must inject").

This module is handler-free and orchestrator-free on purpose: it is imported
during AgentConfig construction, so it must never reach the orchestrator (which
would re-introduce the startup circular-import the handler closures avoid).

The executable handlers live in ``handlers.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from src.core import ToolDef

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
# Lead-only: after the task is completed, the lead may refresh the
# deliverable card (summary + artifacts) during follow-up chat. Appends a
# ``deliverable_updated`` event; does not change task status.
UPDATE_DELIVERABLE_TOOL_NAME = "update_deliverable"

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
                "dispatch. Dispatchable when its deps are done and its status "
                "is 'planned', 'rework' or 'paused'. NOTE get_plan's `ready` "
                "list omits 'rework' nodes — dispatch those by key directly."
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
        # Per-member ``project_mode`` (shared | repo-worktree) is retired
        # (design §5): isolation is now a TASK-level property — a worktree
        # task runs lead + every member in one shared worktree cwd, and a
        # plain task runs everyone in the shared project cwd. The dispatch
        # handler still tolerates a legacy ``project_mode`` argument from
        # old prompts, but the knob is no longer offered to leads.
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
            "maximum": 600,
            "description": (
                "Optional max seconds to wait, capped at 600 (larger values are "
                "clamped — a bigger number does NOT let you wait longer). await "
                "is meant to be LOOPED: one call need not wait long; to keep "
                "waiting, just call await_members again. On timeout, returns "
                "whatever finished plus a 'pending' list AND 'pending_status' — "
                "each pending member's live state: 'running' means it is ALIVE "
                "and still working (long builds/tests routinely exceed this wait; "
                "await again rather than treating it as dead), "
                "'awaiting_user' means it is paused on a question only the "
                "USER can answer (do not busy-wait; do other work or end your "
                "turn — member_done will wake you)."
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
                "The member session id returned by dispatch (or seen in a "
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
        "lead_agent_slug": {
            "type": "string",
            "description": (
                "Project-local agent slug to lead the task. Omit to use the "
                "project's default lead, falling back to the agent of the "
                "current conversation."
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
            "enum": [
                "draft",
                "active",
                "paused",
                "stopped",
                "completed",
                "blocked",
                "abandoned",
            ],
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
        "force": {
            "type": "boolean",
            "default": False,
            "description": (
                "Only meaningful with status='stopped'. A stopped finish is "
                "rejected while members are still running (a silent member is "
                "usually mid-build, not dead — check await_members' "
                "pending_status first, or stop_subtask the ones you no longer "
                "need). Pass true ONLY after deliberately deciding to "
                "terminate despite running members."
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
                "Version token from get_plan's current_version. "
                "When passed, the call is rejected with PLAN_VERSION_CONFLICT if "
                "it no longer matches. A lead editing its OWN task is the single "
                "writer and may omit it; a human/REST editor of a RUNNING task "
                "must pass it (the request is refused otherwise — the lead is "
                "writing the same document concurrently). Note every plan write "
                "bumps the version, including a subtask moving to in_review or "
                "done, so re-read before retrying."
            ),
        },
    },
}

# VALUZ-CHATPLAN S2: state-transition parameters

_DRAFT_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["goal"],
    "properties": {
        "goal": {
            "type": "string",
            "description": "The task goal — what the lead should achieve.",
        },
        "lead_agent_slug": {
            "type": "string",
            "description": (
                "Which project agent becomes the lead at commit time. Omit to "
                "use the project's default lead, falling back to the agent of "
                "the current conversation."
            ),
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
                "The task to revive: 'paused', 'blocked', 'stopped', or "
                "'completed' (reopening a completed task lets you supplement "
                "or adjust its subtasks). The lead session is respawned and "
                "the task flips back to 'active'. Rejected for 'abandoned' "
                "(a discarded draft — nothing to revive) and 'draft' (launch "
                "it with commit_task)."
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
            "description": (
                "approve → mark done (unlocks dependents; only for a subtask "
                "that ran); rework → send back for another attempt."
            ),
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
                "when you got the id from an await_members result entry."
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

_UPDATE_DELIVERABLE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "The refreshed deliverable summary shown on the task's deliverable "
                "card. Reflect whatever you just changed."
            ),
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "The COMPLETE list of deliverable file paths (relative to the "
                "project working dir). This REPLACES the previous list — always "
                "pass every current deliverable file, not just the ones you "
                "changed. Omitting it clears the displayed artifacts."
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
        "(one entry per member: subtask_key, session_id, agent, status, summary, "
        "artifacts) — call this ONLY after dispatch(). Use mode='any' in "
        "a loop to review members the moment each one completes; mode='all' to "
        "wait for the whole batch. Each returned member then awaits your "
        "review_subtask. Omit 'keys' to wait for all outstanding subtasks. "
        "Precondition: at least one dispatched member must be in flight — if "
        "nothing is dispatched (or none of 'keys' were dispatched) it returns "
        "immediately with an error and the ready-to-dispatch keys instead of "
        "blocking, so dispatch first."
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
    read_only=True,
)

MODIFY_PLAN_TOOL_DECLARATION = ToolDef(
    name=MODIFY_PLAN_TOOL_NAME,
    description=(
        "The subtask-level add/update primitive. Revise the plan after it "
        "exists: add new subtasks, or update existing ones by key (goal, "
        "agent, deps, title). Subtasks are a durable record — there is no "
        "removal; to retire one, re-scope its goal. Validates the DAG (no "
        "cycles / dangling deps). In CHAT on a "
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
        "member's session_id. Call this after a member reports a result — a "
        "subtask that was never dispatched cannot be approved (dispatch it "
        "first), and the task itself must still be active. Re-approving an "
        "already-approved subtask is a no-op that returns already_done=true."
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
        "can re-dispatch it with a corrected goal via "
        "dispatch(subtask_key=...), or re-scope it first with "
        "modify_plan(update=[...])), and injects a synthetic "
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

UPDATE_DELIVERABLE_TOOL_DECLARATION = ToolDef(
    name=UPDATE_DELIVERABLE_TOOL_NAME,
    description=(
        "Refresh the task's deliverable card after the task is COMPLETED. "
        "Call this when, during post-completion follow-up chat, you edited "
        "a deliverable file and want the card's summary/artifacts to reflect "
        "the latest state. Only valid on a completed task; it does NOT "
        "reopen the task, re-plan, or dispatch members."
    ),
    parameters=_UPDATE_DELIVERABLE_PARAMETERS,
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
    UPDATE_DELIVERABLE_TOOL_DECLARATION,
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
        "in-flight members are reconciled the same way as they are after an "
        "app restart "
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
