"""Pure helpers for the Claude approval bridge.

Split out of ``runtime.py`` for the same reason as the codex sibling
module: the runtime file is long, and these helpers are stateless
classifiers / payload-builders that don't need the runtime instance.

Responsibilities (parallels the codex bridge layout):

* ``_classify_subject``: Claude SDK tool name → approval-card subject
  (``shell_command`` / ``file_change`` / ``mcp_tool_call`` /
  ``tool_input``). Heuristic — falls back to ``tool_input`` for
  anything not recognised so the front-end's generic card still
  renders the raw input.
* ``_build_pending_payload``: subject-specific payload extracted from
  ``can_use_tool``'s ``input_data`` dict. Per-tool field names track
  the Claude SDK's tool-input contract (``command`` for Bash,
  ``file_path`` for file-change tools, ``mcp__<server>__<tool>``
  naming for MCP).
* ``_summarize_file_change``: best-effort one-line summary for the
  approval card's ``diff_summary`` field. Returns ``None`` when we
  can't make a useful summary — the front-end falls back to showing
  just the path + change kind.

The codex bridge also exports ``_build_approval_response`` because
codex needs to translate (approve, reject) into one of two wire shapes
(``decision`` vs ``action``). Claude's response is a typed SDK object
(``PermissionResultAllow`` / ``PermissionResultDeny``) constructed in
one line at the handler, so there's no equivalent helper here.

Names stay underscore-prefixed because they're not part of the
runtime's public surface — call sites import them explicitly from this
module (or from ``runtime.py`` for backward-compat re-export).
"""

from __future__ import annotations

from typing import Any, Literal

_SHELL_TOOLS: frozenset[str] = frozenset({"Bash", "KillBash"})
_FILE_CHANGE_TOOLS: frozenset[str] = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
# ``AskUserQuestion`` is the Claude SDK tool name for clarifying
# questions (per
# https://code.claude.com/docs/en/agent-sdk/user-input.md). It flows
# through the same ``can_use_tool`` callback as any other tool but the
# input shape is structured questions + options rather than tool args,
# and the response carries an ``answers`` dict back via
# ``PermissionResultAllow(updated_input={"questions": [...], "answers":
# {...}})``.
_CLARIFYING_QUESTION_TOOL: str = "AskUserQuestion"
# ``ExitPlanMode`` is the Claude SDK's plan-mode-exit signal: the model
# emits it (with ``{plan: <markdown>}``) when it believes the plan is
# ready for human review. Like ``AskUserQuestion`` it ALWAYS parks on
# host regardless of permission_mode — see ``_permission_handler``'s
# decision chain. The card renders the plan markdown + Approve / Reject;
# on approve the runtime also flips ``session.mode = "default"`` since
# the user has acknowledged plan completion.
_EXIT_PLAN_MODE_TOOL: str = "ExitPlanMode"


def _classify_subject(
    tool_name: str,
) -> Literal[
    "shell_command",
    "file_change",
    "mcp_tool_call",
    "clarifying_questions",
    "exit_plan_mode",
    "tool_input",
]:
    """Map a Claude SDK tool name to the approval-card subject (per
    design doc §2.4). Heuristic — falls back to ``tool_input`` for
    anything not recognised, which is fine: the front-end's generic
    card still renders raw tool_name + input json.
    """
    if tool_name in _SHELL_TOOLS:
        return "shell_command"
    if tool_name in _FILE_CHANGE_TOOLS:
        return "file_change"
    if tool_name == _CLARIFYING_QUESTION_TOOL:
        return "clarifying_questions"
    if tool_name == _EXIT_PLAN_MODE_TOOL:
        return "exit_plan_mode"
    if tool_name.startswith("mcp__"):
        return "mcp_tool_call"
    return "tool_input"


def _build_pending_payload(
    subject: str,
    tool_name: str,
    input_data: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Subject-specific payload for the approval card.

    Tool-approval subjects ship the full ``original_input`` alongside the
    display summary so the front-end can seed an "Edit & Approve" JSON
    editor without a round trip — the editor's value is what gets sent
    back as ``modified_input`` (Claude SDK contract:
    ``PermissionResultAllow.updated_input`` must match the original tool
    input shape). ``clarifying_questions`` uses the ``answer`` verb
    instead and doesn't need the seed.
    """
    if subject == "shell_command":
        return {
            "command": str(input_data.get("command", "")),
            "cwd": workspace_root or "",
            "reason": input_data.get("description"),
            "original_input": dict(input_data),
        }
    if subject == "file_change":
        change_kind = "create" if tool_name == "Write" else "edit"
        return {
            "path": str(input_data.get("file_path", "")),
            "change_kind": change_kind,
            "diff_summary": _summarize_file_change(tool_name, input_data),
            "original_input": dict(input_data),
        }
    if subject == "mcp_tool_call":
        # ``mcp__github__create_issue`` → server="github", tool_name="create_issue"
        parts = tool_name.split("__", 2)
        return {
            "server": parts[1] if len(parts) >= 2 else "",
            "tool_name": parts[2] if len(parts) >= 3 else parts[-1],
            "args": dict(input_data),
            "original_input": dict(input_data),
        }
    if subject == "exit_plan_mode":
        # ExitPlanMode's input is ``{"plan": "<markdown>"}`` (Claude
        # SDK contract). Pass plan through verbatim so the front-end
        # can render it as markdown. No ``original_input`` here — the
        # user can't edit the plan via "Edit & Approve" (the model
        # owns plan authorship; rejection is the refine signal).
        plan = input_data.get("plan")
        return {"plan": plan if isinstance(plan, str) else ""}
    if subject == "clarifying_questions":
        # AskUserQuestion's input shape (Claude SDK docs §"Question
        # format"): ``{"questions": [{question, header, options:
        # [{label, description, preview?}], multiSelect}]}``. Pass
        # ``questions`` through verbatim so the front-end can render
        # the radio/checkbox form without backend transformation.
        # Anything not matching the documented shape becomes an empty
        # list — the front-end then degrades to "no questions to
        # answer" rather than blowing up. No ``original_input`` here:
        # clarifying uses ``answer``, not ``approve_with_changes``.
        questions = input_data.get("questions")
        return {"questions": list(questions) if isinstance(questions, list) else []}
    return {"tool_name": tool_name, "input": dict(input_data), "original_input": dict(input_data)}


def _summarize_file_change(tool_name: str, input_data: dict[str, Any]) -> str | None:
    """Best-effort one-line summary for the approval card. The full diff
    isn't worth shipping over WS; the user can read tool_use after
    approval if they want detail."""
    if tool_name == "Write":
        content = input_data.get("content")
        if isinstance(content, str):
            length = len(content)
            return f"Write {length} chars"
        return None
    if tool_name == "Edit":
        old = input_data.get("old_string", "")
        new = input_data.get("new_string", "")
        if isinstance(old, str) and isinstance(new, str):
            return f"Replace {len(old)} chars with {len(new)} chars"
        return None
    if tool_name == "MultiEdit":
        edits = input_data.get("edits")
        if isinstance(edits, list):
            return f"{len(edits)} edits"
        return None
    return None
