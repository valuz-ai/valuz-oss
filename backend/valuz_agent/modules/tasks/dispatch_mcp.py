"""Dispatch MCP tools — the lead-agent's orchestration surface.

Registers in-process MCP tools via the kernel's ``register_tool``
mechanism (same pattern as ``providers/tools_skill_creator.py``):

  dispatch          — NON-BLOCKING spawn of a planned subtask (returns a handle)
  await_members     — block (in-turn) to collect finished members' results
  list_members      — list project members (slug/runtime/description)
  finish_task       — close the task with a summary and optional artifacts
  (+ send / plan_task / get_plan / modify_plan / review_subtask)

The lead-only tools enforce the **lead gate** (§S0①): handlers inspect
``session.metadata["valuz"]["run_kind"]`` via ``kernel_sync`` and return
an error ToolResult when the caller is not the lead session. This is the
only enforcement point — kernel Session has no ``tools`` field, so we
cannot restrict tool availability at the session level.

Tools are registered globally at startup and declared (handler=None) on
lead-capable AgentConfig.tools so the runtime surfaces them to the model
as ``mcp__harness__dispatch`` etc. Member sessions whose agents also
carry the declarations are still blocked by the handler gate.

Usage (startup):
    from valuz_agent.modules.tasks.dispatch_mcp import register_dispatch_tools
    register_dispatch_tools(orchestrator)

ADR-023 Step 4: this module is now a thin re-export shim. The static,
import-safe surface (tool names, JSON-schema parameters,
``ToolDef(handler=None)`` declarations, ``strip_dispatch_tools`` /
``ensure_orchestration_tools_on_agent``) lives in ``tools/declarations.py``;
the thin args → service-call → ToolResult handlers and
``register_dispatch_tools`` live in ``tools/handlers.py``. Keeping this module
name + its public surface stable means existing import sites (app.py,
projects/service.py, agents/service.py, lifecycle._materialize_lead_agent) keep
working unchanged.
"""

from __future__ import annotations

# Re-export the adapter the gate helpers use, so test monkeypatching of
# ``dispatch_mcp.kernel_store.load_session`` keeps targeting the same object
# the handlers reach (it is the shared adapter module object).
from valuz_agent.adapters import kernel_store, kernel_sync
from valuz_agent.modules.tasks.tools.declarations import (
    ABANDON_TASK_TOOL_DECLARATION,
    ABANDON_TASK_TOOL_NAME,
    AWAIT_MEMBERS_TOOL_DECLARATION,
    AWAIT_MEMBERS_TOOL_NAME,
    COMMIT_TASK_TOOL_DECLARATION,
    COMMIT_TASK_TOOL_NAME,
    CREATE_TASK_TOOL_DECLARATION,
    CREATE_TASK_TOOL_NAME,
    DISPATCH_TOOL_DECLARATION,
    DISPATCH_TOOL_DECLARATIONS,
    DISPATCH_TOOL_NAME,
    DISPATCH_TOOL_NAMES,
    DRAFT_TASK_TOOL_DECLARATION,
    FINISH_TASK_TOOL_DECLARATION,
    FINISH_TASK_TOOL_NAME,
    GET_PLAN_TOOL_DECLARATION,
    GET_PLAN_TOOL_NAME,
    GET_TASK_TOOL_DECLARATION,
    GET_TASK_TOOL_NAME,
    INJECT_INTO_TASK_TOOL_DECLARATION,
    INJECT_INTO_TASK_TOOL_NAME,
    LEAD_ONLY_TOOL_NAMES,
    LIST_MEMBERS_TOOL_DECLARATION,
    LIST_MEMBERS_TOOL_NAME,
    LIST_TASKS_TOOL_DECLARATION,
    LIST_TASKS_TOOL_NAME,
    MODIFY_PLAN_TOOL_DECLARATION,
    MODIFY_PLAN_TOOL_NAME,
    ORCHESTRATION_TOOL_DECLARATIONS,
    ORCHESTRATION_TOOL_NAMES,
    PLAN_REVIEW_TOOL_DECLARATIONS,
    PLAN_TASK_TOOL_DECLARATION,
    PLAN_TASK_TOOL_NAME,
    RESUME_TASK_TOOL_DECLARATION,
    RESUME_TASK_TOOL_NAME,
    REVIEW_SUBTASK_TOOL_DECLARATION,
    REVIEW_SUBTASK_TOOL_NAME,
    SEND_TOOL_DECLARATION,
    SEND_TOOL_NAME,
    STOP_SUBTASK_TOOL_DECLARATION,
    STOP_SUBTASK_TOOL_NAME,
    ensure_orchestration_tools_on_agent,
    strip_dispatch_tools,
)
from valuz_agent.modules.tasks.tools.handlers import (
    _check_lead_gate,
    _check_orchestration_gate,
    _check_plan_writer_gate,
    _resolve_plan_reader_task,
    _resolve_plan_writer_task,
    register_dispatch_tools,
)

__all__ = [
    "DISPATCH_TOOL_NAME",
    "AWAIT_MEMBERS_TOOL_NAME",
    "LIST_MEMBERS_TOOL_NAME",
    "FINISH_TASK_TOOL_NAME",
    "SEND_TOOL_NAME",
    "CREATE_TASK_TOOL_NAME",
    "LIST_TASKS_TOOL_NAME",
    "GET_TASK_TOOL_NAME",
    "PLAN_TASK_TOOL_NAME",
    "GET_PLAN_TOOL_NAME",
    "MODIFY_PLAN_TOOL_NAME",
    "REVIEW_SUBTASK_TOOL_NAME",
    "INJECT_INTO_TASK_TOOL_NAME",
    "INJECT_INTO_TASK_TOOL_DECLARATION",
    "RESUME_TASK_TOOL_NAME",
    "RESUME_TASK_TOOL_DECLARATION",
    "DISPATCH_TOOL_NAMES",
    "DISPATCH_TOOL_DECLARATIONS",
    "PLAN_REVIEW_TOOL_DECLARATIONS",
    "ORCHESTRATION_TOOL_DECLARATIONS",
    "ORCHESTRATION_TOOL_NAMES",
    "LEAD_ONLY_TOOL_NAMES",
    "strip_dispatch_tools",
    "DISPATCH_TOOL_DECLARATION",
    "AWAIT_MEMBERS_TOOL_DECLARATION",
    "LIST_MEMBERS_TOOL_DECLARATION",
    "FINISH_TASK_TOOL_DECLARATION",
    "SEND_TOOL_DECLARATION",
    "CREATE_TASK_TOOL_DECLARATION",
    "LIST_TASKS_TOOL_DECLARATION",
    "GET_TASK_TOOL_DECLARATION",
    "ensure_orchestration_tools_on_agent",
    "register_dispatch_tools",
    # Additional re-exports kept for completeness (used by tests / internal
    # callers that reference the per-tool declarations or names).
    "ABANDON_TASK_TOOL_NAME",
    "ABANDON_TASK_TOOL_DECLARATION",
    "COMMIT_TASK_TOOL_NAME",
    "COMMIT_TASK_TOOL_DECLARATION",
    "DRAFT_TASK_TOOL_DECLARATION",
    "GET_PLAN_TOOL_DECLARATION",
    "MODIFY_PLAN_TOOL_DECLARATION",
    "PLAN_TASK_TOOL_DECLARATION",
    "REVIEW_SUBTASK_TOOL_DECLARATION",
    "STOP_SUBTASK_TOOL_NAME",
    "STOP_SUBTASK_TOOL_DECLARATION",
    # Gate helpers + adapters re-exported for tests / internal callers that
    # reference them through the ``dispatch_mcp`` module path.
    "kernel_store",
    "kernel_sync",
    "_check_lead_gate",
    "_check_orchestration_gate",
    "_check_plan_writer_gate",
    "_resolve_plan_reader_task",
    "_resolve_plan_writer_task",
]
