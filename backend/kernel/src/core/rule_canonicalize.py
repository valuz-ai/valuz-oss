"""Per-subject args reduction for the session approval cache.

Phase 4 of ``approve_for_session`` (see
``docs/design/approve-for-session.md`` §5 "Per-subject rule keying"):
not every arg in a tool call should participate in rule identity.

The default ``ExactArgsRuleMatcher`` canonicalizes the *whole* args
dict, so before Phase 4 a second call to ``stock_quote("000568")`` did
not match a stored rule from ``stock_quote("000858")`` — the JSON
strings diverged. The user's intuition is that an MCP tool is an
endpoint (one OpenAPI operation); authorizing it should cover all
invocations regardless of arguments. Codex bakes the same intuition
into its native ``McpToolApprovalKey = (server, connector_id, tool_name)``
— args are pointedly absent from the key.

This module exposes one helper, ``reduce_args_for_subject``, that the
three runtime bridges call after extracting their SDK-specific args
shape and before handing them to the matcher. The reducer takes
``(subject, tool_name, args)`` and returns ``(reduced_args, display)``:

* ``reduced_args`` — the dict that participates in rule identity. The
  matcher canonicalizes this (exact path) or reads named keys from it
  (Claude pattern path's ``_match_bash`` reads ``args["command"]``,
  ``_match_file_glob`` reads ``args["file_path"]``), so each reduction
  must keep the keys the matcher needs.
* ``display`` — user-facing label for the "Always for this session"
  button when the matcher returns an ``exact``-kind derivation. The
  runtime overrides ``RuleDerivation.display`` with this when the
  matcher fell through to the exact-args path; Claude pattern
  derivations (e.g. ``Bash(npm test:*)``) keep their richer SDK-
  proposed labels.

Subject-specific identity rules:

* ``mcp_tool_call`` — drop all args (endpoint-level identity). One
  exception: codex MCP *elicitations* (tool_name == "elicitation") are
  prompts FROM the server, not tool calls MADE BY the model; they keep
  ``server + message`` in identity so "always allow this particular
  prompt" stays meaningful.
* ``file_change`` — keep ``file_path`` only. Different content on the
  same file = same rule (the user authorized "changes to this file").
  Looks for ``file_path`` first then ``path`` (Claude/DeepAgents vs
  codex use different keys; we normalize to ``file_path``). Drops
  ``change_kind`` — Edit and Write on the same file count as the same
  rule.
* ``shell_command`` — keep ``command`` + ``cwd``. Exact identity
  preserved on purpose: a one-char difference in the command can
  change effect dramatically. Broader matching is the user's job (use
  Claude's ``Bash(prefix:*)`` pattern grammar, which lives in
  ``ClaudePermissionUpdateRuleMatcher`` and is unaffected by this
  reducer).
* ``tool_input`` / unknown / ``clarifying_questions`` — pass through.
  ``clarifying_questions`` shouldn't reach this reducer in practice
  (the orchestrator gates ``approve_for_session`` away from that
  subject at the API layer), but a verbatim args round-trip is the
  safe fallback if it ever does.

All branches are defensive on missing / wrong-typed values — this
helper runs on the runtime's hot path on every approval request, and
a raised exception there would break the SDK reader thread (codex) or
the langgraph stream (deepagents).
"""

from __future__ import annotations

from typing import Any

# Subject literals match ``SessionRuleSubject`` in
# ``session_approval_cache.py``. We don't import the Literal here to
# avoid a hot-path import cost; the values are stable.

# Codex sets ``tool_name`` to this for MCP elicitation requests. The
# bridge classifies elicitations as subject=mcp_tool_call (no separate
# subject), so we discriminate inside the mcp branch on tool_name.
_ELICITATION_TOOL_NAME = "elicitation"


def reduce_args_for_subject(
    subject: str,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Return ``(reduced_args, display)`` for the given approval subject.

    See module docstring for the per-subject rules.

    The returned ``reduced_args`` is always a fresh dict — callers can
    mutate it without aliasing the input.
    """
    if subject == "mcp_tool_call":
        if tool_name == _ELICITATION_TOOL_NAME:
            # Elicitations are prompts FROM the MCP server (codex relays
            # them via ``mcpServer/elicitation/request``); per-(server,
            # message) identity matches what a user would mean by
            # "always allow this particular prompt".
            return (dict(args), "this elicitation")
        return ({}, f"any {tool_name} call")

    if subject == "file_change":
        path = ""
        for key in ("file_path", "path"):
            v = args.get(key)
            if isinstance(v, str) and v:
                path = v
                break
        reduced: dict[str, Any] = {}
        if path:
            # Normalize on ``file_path`` for downstream canonical-JSON
            # comparison. Claude / DeepAgents pre-reduction args already
            # use this key; codex uses ``path`` — we lift it here so a
            # rule derived from one runtime's args would still match if
            # the same session ever had cross-shape args (in practice
            # ``Session.runtime_provider`` is immutable so this is just
            # belt-and-suspenders).
            reduced["file_path"] = path
        display = f"{tool_name} on {path}" if path else f"any {tool_name} call"
        return (reduced, display)

    if subject == "shell_command":
        command = ""
        raw = args.get("command")
        if isinstance(raw, str):
            command = raw
        elif isinstance(raw, list):
            command = " ".join(str(c) for c in raw)
        kept: dict[str, Any] = {"command": command}
        cwd = args.get("cwd")
        if cwd:
            kept["cwd"] = str(cwd)
        display = f"Shell({command})" if command else f"this exact {tool_name} call"
        return (kept, display)

    # Generic fallback — tool_input, clarifying_questions, unknown.
    # Keep the full args dict so exact-args identity stays as-is.
    return (dict(args), f"this exact {tool_name} call")
