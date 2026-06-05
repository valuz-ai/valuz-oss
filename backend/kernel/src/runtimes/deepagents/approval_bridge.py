"""Pure helpers for the DeepAgents approval bridge.

Split out of ``runtime.py`` for parity with the Claude / Codex siblings:
the runtime module focuses on lifecycle + event mapping, these helpers
stay stateless and trivially unit-testable.

Responsibilities (parallels ``claude_agent/approval_bridge.py``):

* ``_classify_subject``: HITL ``ActionRequest`` tool name → approval-card
  subject (``file_change`` / ``mcp_tool_call`` / ``tool_input``). DeepAgents
  has no built-in ``Bash``-style shell tool, so ``shell_command`` is never
  produced from this path; if a user wires a shell-shaped custom tool it
  falls through to ``tool_input`` and the front-end's generic card renders
  it. Heuristic — falls back to ``tool_input`` for anything not recognised.
* ``_build_pending_payload``: subject-specific payload extracted from an
  ``ActionRequest.args`` dict. Per-tool field names track the DeepAgents
  built-in tool contract (``file_path`` / ``content`` for ``write_file``,
  ``file_path`` / ``old_string`` / ``new_string`` for ``edit_file``).
* ``_summarize_file_change``: best-effort one-line summary for the
  approval card's ``diff_summary`` field. Returns ``None`` when we can't
  make a useful summary — the front-end falls back to showing just path +
  change_kind.

DeepAgents-specific notes vs the Claude bridge:

* ``write_file`` / ``edit_file`` are the canonical built-in file-change
  tool names (vs Claude's ``Write`` / ``Edit``). Custom tools that happen
  to be named one of these fall through the same path — fine, they have
  the same args contract by convention.
* The Claude bridge classifies MCP by ``tool_name.startswith("mcp__")``;
  langchain-mcp's ``MultiServerMCPClient.get_tools()`` does NOT prefix
  tool names with their server, so we can't recover server attribution
  from the name alone. The runtime tracks MCP-origin names in a set
  passed in at classification time; payload-side ``server`` is the
  empty string — the cross-runtime convention for "attribution
  unavailable", which the front-end's MCP card hides as a pill (vs
  rendering a fabricated ``"unknown"`` placeholder). Full attribution
  is v2+ work.

Names stay underscore-prefixed because they're not part of the runtime's
public surface — call sites import them explicitly from this module.
"""

from __future__ import annotations

from typing import Any, Literal

# DeepAgents built-in file-mutation tool names. Custom user tools with
# the same names are intentionally treated the same way — args contract
# is conventional, and falling through to ``tool_input`` would be a
# worse UX than rendering a file-change card with possibly-missing
# fields.
_FILE_CHANGE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})


def _classify_subject(
    tool_name: str,
    mcp_tool_names: frozenset[str] | set[str],
) -> Literal["shell_command", "file_change", "mcp_tool_call", "tool_input"]:
    """Map a HITL ``ActionRequest`` tool name to the approval-card subject.

    ``mcp_tool_names`` is the set captured at ``_ensure_graph`` time —
    membership is the only signal we have for MCP origin since
    langchain-mcp doesn't carry server prefixes in tool names.
    """
    if tool_name in _FILE_CHANGE_TOOLS:
        return "file_change"
    if tool_name in mcp_tool_names:
        return "mcp_tool_call"
    return "tool_input"


def _build_pending_payload(
    subject: str,
    tool_name: str,
    args: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Subject-specific payload for the approval card.

    ``args`` is the ``ActionRequest.args`` dict — for built-in tools the
    shape is contractual, for custom tools it's whatever the
    ``StructuredTool.args_schema`` advertised. We use ``.get()`` with
    defensive defaults so unknown shapes still produce a renderable
    card. Every tool-approval subject ships ``original_input`` so the
    front-end can seed a JSON editor for the ``approve_with_changes``
    verb (matches the Claude bridge convention).
    """
    if subject == "file_change":
        change_kind = "create" if tool_name == "write_file" else "edit"
        return {
            "path": str(args.get("file_path", "")),
            "change_kind": change_kind,
            "diff_summary": _summarize_file_change(tool_name, args),
            "original_input": dict(args),
        }
    if subject == "mcp_tool_call":
        # langchain-mcp tool names don't carry a server prefix, so we
        # can't recover server attribution at the approval boundary.
        # Empty ``server`` is the cross-runtime convention for
        # "attribution unavailable" (same as the codex tool-call fallback
        # path) — the front-end's MCP card hides the server pill on
        # empty rather than fabricating an ``"unknown"`` placeholder
        # that looks like a real server name. Full attribution is v2+
        # work, paired with cross-runtime sub-agent attribution.
        return {
            "server": "",
            "tool_name": tool_name,
            "args": dict(args),
            "original_input": dict(args),
        }
    # ``workspace_root`` is plumbed in to mirror the Claude/Codex
    # signature even though the tool_input fallback doesn't currently
    # use it; keeps the per-runtime helper contract uniform.
    _ = workspace_root  # explicit no-op to silence unused-arg lint if it appears
    return {"tool_name": tool_name, "input": dict(args), "original_input": dict(args)}


def _summarize_file_change(tool_name: str, args: dict[str, Any]) -> str | None:
    """Best-effort one-line summary for the approval card. Full diff is
    not worth shipping over WS; the user can read tool_result after
    approval if they want detail."""
    if tool_name == "write_file":
        content = args.get("content")
        if isinstance(content, str):
            return f"Write {len(content)} chars"
        return None
    if tool_name == "edit_file":
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        if isinstance(old, str) and isinstance(new, str):
            return f"Replace {len(old)} chars with {len(new)} chars"
        return None
    return None
