"""Pure helpers for the codex approval bridge.

Split out of ``runtime.py`` so the runtime module can stay focused on
lifecycle / event-mapping work; the helpers below are stateless,
trivially unit-testable, and shouldn't depend on the runtime instance.

Responsibilities:

* ``_classify_codex_subject``: codex JSON-RPC method -> approval-card
  subject (``shell_command`` / ``file_change`` / ``mcp_tool_call`` /
  ``tool_input``).
* ``_build_codex_pending_payload``: per-subject payload extracted from
  the raw ``JsonObject`` params. Always defensive — these helpers run
  on the SDK's stdio reader thread (see
  ``docs/design/_scratch/codex-sdk-approval-internals.md`` §5), so any
  ``raise`` would stall the entire transport read loop.
* ``_build_approval_response``: translate the host's ``approve`` /
  ``reject`` decision into the method-specific JSON-RPC reply shape.
  Two wire formats live side-by-side: ``{"decision": ...}`` for
  ``commandExecution`` / ``fileChange``, and the MCP elicitation
  envelope ``{"action": ..., "content"?: ...}`` for
  ``mcpServer/elicitation/request``.

Names are kept underscore-prefixed because they are not part of the
runtime's public surface — call sites import them explicitly from this
module (or from ``runtime.py`` for backward-compat re-export).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Methods that respond with the MCP elicitation envelope
# (``{"action": "accept"|"decline"|"cancel", "content"?: {...}}``) per the
# MCP spec (see ``mcp.types.ElicitResult``). Codex relays these from MCP
# servers untouched — the response wire format differs from
# ``commandExecution`` / ``fileChange`` (which use ``{"decision": ...}``).
_ELICITATION_METHODS: frozenset[str] = frozenset({"mcpServer/elicitation/request"})

# EXPERIMENTAL server request codex sends when the model calls the
# ``request_user_input`` tool — the only tool enabled exclusively in plan
# mode (``ModeKind::allows_request_user_input``), and the built-in plan
# prompt pushes it hard. Params: ``ToolRequestUserInputParams``
# ``{itemId, threadId, turnId, questions: [{id, header, question,
# isOther?, isSecret?, options?: [{label, description}]}]}``. Response:
# ``ToolRequestUserInputResponse`` ``{"answers": {<question id>:
# {"answers": [<string>, ...]}}}`` — an ANSWERS envelope, never the
# ``{"decision"}`` / ``{"action"}`` approval shapes. (Shapes verified
# against the bundled binary's ``generate-json-schema --experimental``.)
_REQUEST_USER_INPUT_METHOD = "item/tool/requestUserInput"


def _is_elicitation_method(method: str) -> bool:
    return method in _ELICITATION_METHODS


def _classify_codex_subject(
    method: str,
) -> Literal[
    "shell_command", "file_change", "mcp_tool_call", "clarifying_questions", "tool_input"
]:
    """Map a codex server-request method to the approval-card subject.

    Recognises the documented ``requestApproval`` methods,
    ``mcpServer/elicitation/request`` (codex relays MCP elicitations
    through this generic dispatcher path) and the plan-mode
    ``item/tool/requestUserInput`` (surfaced as ``clarifying_questions``
    — same card Claude's ``AskUserQuestion`` renders). Anything else
    falls back to the generic ``tool_input`` subject so the front-end's
    card still renders raw method + params JSON.
    """
    if method == "item/commandExecution/requestApproval":
        return "shell_command"
    if method == "item/fileChange/requestApproval":
        return "file_change"
    if method == "mcpServer/elicitation/request":
        return "mcp_tool_call"
    if method == _REQUEST_USER_INPUT_METHOD:
        return "clarifying_questions"
    return "tool_input"


def _build_codex_pending_payload(
    subject: str,
    method: str,
    params: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Subject-specific payload for the approval card.

    ``params`` is raw ``JsonObject`` from the SDK — field shapes are
    undocumented in v2_all.py (see codex-sdk-approval-internals.md §3).
    All accesses use ``.get()`` with safe defaults so this never raises
    on the stdio reader thread.
    """
    if subject == "clarifying_questions":
        # Frontend renders the same card as Claude's ``AskUserQuestion``:
        # ``{"questions": [{question, header, options: [{label,
        # description}], multiSelect}]}``. Codex's per-question ``id`` is
        # carried through (the answers envelope is keyed by it — see
        # ``_build_request_user_input_response``); ``isSecret``/``isOther``
        # ride along for future card affordances.
        raw_questions = params.get("questions")
        questions: list[dict[str, Any]] = []
        for q in raw_questions if isinstance(raw_questions, list) else []:
            if not isinstance(q, dict):
                continue
            raw_options = q.get("options")
            options = [
                {
                    "label": str(o.get("label") or ""),
                    "description": str(o.get("description") or ""),
                }
                for o in (raw_options if isinstance(raw_options, list) else [])
                if isinstance(o, dict)
            ]
            questions.append(
                {
                    "id": str(q.get("id") or ""),
                    "question": str(q.get("question") or ""),
                    "header": str(q.get("header") or ""),
                    "options": options,
                    "multiSelect": False,
                    "isOther": bool(q.get("isOther") or False),
                    "isSecret": bool(q.get("isSecret") or False),
                }
            )
        return {"questions": questions}
    if subject == "shell_command":
        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        else:
            command = str(command) if command is not None else ""
        return {
            "command": command,
            "cwd": params.get("cwd") or workspace_root,
            "reason": params.get("reason"),
        }
    if subject == "file_change":
        return {
            "path": str(params.get("path") or params.get("file_path") or ""),
            "change_kind": str(params.get("kind") or "edit"),
            "diff_summary": params.get("summary"),
        }
    if subject == "mcp_tool_call":
        # Two distinct shapes wear this subject:
        #   - ``mcpServer/elicitation/request``: MCP elicitation —
        #     ``{message, mode, requestedSchema | url, ...}`` per
        #     ``mcp.types.ElicitRequestParams``. Codex does NOT forward
        #     the originating MCP server name in params; the server
        #     identity is conventionally embedded in the prompt message
        #     by the server itself (e.g. "Allow the reportify-stock MCP
        #     server to ...").
        #   - Future ``item/mcpToolCall/requestApproval``: tool-invocation
        #     style ``{server, tool, input}`` — kept here as a fallback
        #     read so an SDK upgrade doesn't lose the card.
        if _is_elicitation_method(method):
            # ``kind`` is a discriminator the front-end branches on;
            # ``server`` stays an empty string (NOT a fabricated
            # "unknown") so the UI hides the pill instead of showing a
            # placeholder. ``message`` is hoisted to the top level
            # because it's the user-facing prompt.
            return {
                "kind": "elicitation",
                "server": str(params.get("server") or ""),
                "tool_name": "elicitation",
                "message": params.get("message"),
                "mode": params.get("mode"),
                "url": params.get("url"),
                "requestedSchema": params.get("requestedSchema"),
            }
        # Tool-invocation style — codex's envelope nominally carries a
        # ``server`` key, but per ``codex-sdk-approval-internals.md`` §3
        # the wire shape is undocumented and we've seen codex builds
        # where it's absent. Treat that as a diagnostic signal
        # (log at WARNING) rather than surfacing a fabricated
        # ``"unknown"`` string to the user; the front-end's MCP card
        # hides the server pill when this value is empty, same way it
        # handles the deepagents path that has no server attribution at
        # all. Cross-runtime convention: ``payload.server == ""`` means
        # "not knowable for this card", not a real server name.
        server_raw = params.get("server")
        server = str(server_raw) if server_raw else ""
        if not server:
            logger.warning(
                "codex: mcpToolCall approval params missing 'server' key — "
                "rendering MCP card without server attribution. "
                "SDK shape may have shifted; raw params keys=%s",
                sorted(params.keys()),
            )
        return {
            "kind": "tool_call",
            "server": server,
            "tool_name": str(params.get("tool_name") or params.get("tool") or ""),
            "args": dict(params.get("input") or params.get("args") or {}),
        }
    # Fallback — generic tool input card
    return {"tool_name": method, "input": dict(params)}


def _extract_matcher_inputs(
    subject: str,
    method: str,
    params: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Reduce a codex approval request to a ``(tool_name, args)`` tuple
    suitable for the harness ``RuntimeApprovalRuleMatcher`` interface.

    Codex's JSON-RPC params carry a superset of what a stable rule key
    should be — for example, ``shell_command``'s ``params`` includes a
    model-generated ``reason`` that varies turn to turn for the same
    command. This helper extracts the fields that should participate in
    rule identity per the per-subject identity table in
    ``backend/src/core/rule_canonicalize.py`` (and
    ``docs/design/approve-for-session.md`` §5):

    * ``shell_command`` → keep ``command`` + ``cwd``
    * ``file_change`` → keep ``path`` only (codex's native key
      omits diff/summary)
    * ``mcp_tool_call`` (tool-call style) → keep ``server`` only; drop
      ``input``. Endpoint-level identity matches codex's own
      ``McpToolApprovalKey = (server, connector_id, tool_name)`` —
      args were never part of the key.
    * ``mcp_tool_call`` (elicitation) → keep ``server`` + ``message``.
      Elicitations are prompts FROM servers; per-message identity
      gives "always allow THIS particular elicitation".

    Returned dict is JSON-serializable; ``ExactArgsRuleMatcher`` will
    canonicalize via ``json.dumps(..., sort_keys=True)`` so insertion
    order doesn't matter.
    """
    if subject == "shell_command":
        command = params.get("command")
        if isinstance(command, list):
            command_str = " ".join(str(c) for c in command)
        else:
            command_str = str(command) if command is not None else ""
        return (
            "Shell",
            {"command": command_str, "cwd": str(params.get("cwd") or "")},
        )
    if subject == "file_change":
        # Drop ``change_kind`` from identity — Edit vs create on the
        # same path counts as the same rule (matches the per-subject
        # table in ``rule_canonicalize.py``).
        return (
            "apply_patch",
            {"path": str(params.get("path") or params.get("file_path") or "")},
        )
    if subject == "mcp_tool_call":
        if _is_elicitation_method(method):
            return (
                "elicitation",
                {
                    "server": str(params.get("server") or ""),
                    # Elicitation messages come from the MCP server itself and
                    # are typically stable per (server, prompt-template);
                    # including the message in the key gives the user "always
                    # allow THIS particular elicitation" without locking on a
                    # transient request id.
                    "message": params.get("message"),
                },
            )
        # Tool-invocation style — same (server, tool_name) key shape the
        # codex Rust core uses internally for its ApprovalStore (per
        # docs/design/_scratch/codex-sdk-approval-internals.md §2).
        # ``input`` is intentionally dropped: MCP tools are endpoints
        # (one OpenAPI op per tool) and arguments are call payload, not
        # part of the authorization grant. Different ``stock_quote``
        # symbols share one rule.
        return (
            str(params.get("tool_name") or params.get("tool") or ""),
            {"server": str(params.get("server") or "")},
        )
    # Generic fallback — preserves method + full params so an exact
    # repeat of an unrecognised RPC still hits cache.
    return (method, dict(params))


def _build_request_user_input_response(
    params: dict[str, Any],
    answers: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the ``ToolRequestUserInputResponse`` answers envelope.

    The host's ``answer`` decision arrives keyed the way the frontend's
    clarifying-questions card sends it — by question TEXT (the Claude
    ``AskUserQuestion`` contract the card was built for) — with values
    ``str | list[str]``. Codex wants ``{"answers": {<question id>:
    {"answers": [<string>, ...]}}}``, so remap through the original
    ``params.questions`` (id lookups also accepted, defensively). Every
    question gets an entry — an unanswered one maps to an empty list,
    which reads as "declined to answer" model-side. A rejected /
    timed-out / interrupted pending returns the bare empty envelope.
    """
    raw_questions = params.get("questions")
    questions = raw_questions if isinstance(raw_questions, list) else []
    if answers is None:
        return {"answers": {}}
    envelope: dict[str, Any] = {}
    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or "")
        if not qid:
            continue
        value = answers.get(str(q.get("question") or ""))
        if value is None:
            value = answers.get(qid)
        if value is None:
            selected: list[str] = []
        elif isinstance(value, list):
            selected = [str(v) for v in value]
        else:
            selected = [str(value)]
        envelope[qid] = {"answers": selected}
    return {"answers": envelope}


def _build_approval_response(
    method: str,
    decision: Literal["approve", "reject"],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Translate a host ``approve`` / ``reject`` decision into the
    method-specific JSON-RPC response shape codex expects on the wire.

    See ``CodexRuntime._approval_handler`` docstring for the wire-format
    contract. ``params`` is needed only for elicitation accepts: form
    mode wants a ``content`` object matching the requested schema (we
    send ``{}`` as a v1 best-effort — the user clicked "approve"
    without filling in anything), while URL mode omits ``content``.
    """
    if method == _REQUEST_USER_INPUT_METHOD:
        # Answered pendings are built by the runtime via
        # ``_build_request_user_input_response`` directly (it carries the
        # answers); this branch covers reject / timeout / interrupt —
        # the empty envelope is the only valid non-answer reply (the
        # ``{"decision"}`` shape reads as malformed to codex).
        return _build_request_user_input_response(params, None)
    if _is_elicitation_method(method):
        if decision == "approve":
            mode = params.get("mode")
            if mode == "url":
                return {"action": "accept"}
            # Form mode (or unknown) — send empty content. MCP servers
            # with required fields will reject this; future work is to
            # surface the form schema to the host so the user can fill
            # it in. For approval-style elicitations (most common) the
            # schema is empty / non-required and ``{}`` is accepted.
            return {"action": "accept", "content": {}}
        return {"action": "decline"}
    # decision-style methods (commandExecution / fileChange / fallback)
    if decision == "approve":
        return {"decision": "accept"}
    return {"decision": "deny"}
