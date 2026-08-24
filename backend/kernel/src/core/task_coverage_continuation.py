"""Passive Task Coverage continuation contract used by production runtimes."""

from __future__ import annotations

from typing import Any

from src.core.tools import ExecContext, ToolDef, ToolResult

TASK_COVERAGE_NOOP_TOOL_NAME = "valuz_task_coverage_noop"


TASK_COVERAGE_CONTINUATION_PROMPT = (
    "This is an append-only completion pass, not a request for a review report.\n\n"
    "MANDATORY RESPONSE PROTOCOL — follow this before producing any visible reasoning, "
    "preamble, checklist, summary, or answer:\n"
    f"- If no material user-facing supplement or correction is needed, your first and only "
    f"observable action must be a call to `{TASK_COVERAGE_NOOP_TOOL_NAME}`.\n"
    "- If a material supplement or correction is needed, your first observable action must "
    "begin that user-facing continuation or a tool call required to produce it.\n"
    "- Perform the completeness decision silently. Never transcribe the decision process, "
    "the requirements you checked, checkmarks, scores, or a completion conclusion.\n"
    "- `No response requested`, `the answer is complete`, and similar meta-responses are "
    "protocol violations, not valid completion responses.\n\n"
    "Review the original user request and every visible assistant and tool message "
    "from this turn.\n"
    "Preserve every explicit user constraint, prohibition, scope boundary, and requested "
    "output shape. If filling an apparent omission would require an action or derivation "
    "the user prohibited, you must not add it; treat the constrained answer as complete.\n"
    "Decide whether the user still needs an important omission filled, an unfinished "
    "requirement completed, or a material correction.\n\n"
    "If important user-facing content is missing, use the same available tools and "
    "context when needed, then append only that missing information or correction. "
    "Do not repeat or replace the completed answer. Do not summarize or evaluate it. "
    "An uncited restatement or reformatting of facts already present is not a supplement. "
    "A necessary clarification request is also a complete result for this turn when the "
    "Agent cannot safely continue without user input. In that case, call the private "
    "completion tool: do not infer the missing entity, period, scope, or other input; do "
    "not search memory, workspace state, followed items, or tools for a guess; and do not "
    "answer the clarification on the user's behalf. "
    "When a factual supplement uses evidence, preserve the normal evidence bindings: reuse "
    "the matching evidence links already present in this turn when they support the new "
    "claim, or use the available tools to obtain supporting evidence.\n\n"
    f"If no important user-facing content is missing, call "
    f"`{TASK_COVERAGE_NOOP_TOOL_NAME}` exactly once and then end. Do not generate any "
    "assistant text, visible reasoning, or preamble before or after that private completion "
    "call. Do not say "
    '"nothing was omitted", "the response is complete", "no correction is needed", '
    '"no response requested", or any equivalent review conclusion.\n'
    'Do not print the word "empty", an "(empty)" placeholder, or a description of '
    "an empty response.\n\n"
    "Never output analysis about whether the prior response is complete. Do not mention "
    "Task Coverage, internal auditing, Host contracts, plans, manifests, candidate "
    "selection, or protocol fields."
)


async def _task_coverage_noop_handler(
    _arguments: dict[str, Any],
    _context: ExecContext,
) -> ToolResult:
    """A private terminal signal, not user-facing answer content."""

    return ToolResult(content='{"status":"complete","supplemented":false}')


def build_task_coverage_noop_tool() -> ToolDef:
    """Build the turn-scoped private no-gap signal exposed only to Coverage."""

    return ToolDef(
        name=TASK_COVERAGE_NOOP_TOOL_NAME,
        description=(
            "Private completion signal for the current Task Coverage pass. "
            "Call exactly once only when the completed user turn has no "
            "important omission or material correction to append."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_task_coverage_noop_handler,
        read_only=True,
        permission="auto",
    )


def build_task_coverage_continuation_prompt(
    policy: dict[str, Any] | None,
) -> str:
    """Append trusted, static review vocabulary to the generic prompt.

    The builder cannot express a request-specific requirement, retrieval
    order, tool choice or remediation command. Those decisions remain with
    the normal Agent on its native thread.
    """

    if not isinstance(policy, dict):
        return TASK_COVERAGE_CONTINUATION_PROMPT
    guidance = policy.get("review_guidance")
    if not isinstance(guidance, dict):
        return TASK_COVERAGE_CONTINUATION_PROMPT

    sections: list[str] = []
    for key, label in (
        ("material_gap_types", "Material gap categories"),
        ("completion_dimensions", "Completion dimensions"),
        ("source_boundary_notes", "Source-boundary reminders"),
    ):
        values = guidance.get(key)
        if isinstance(values, list):
            cleaned = [
                value.strip() for value in values if isinstance(value, str) and value.strip()
            ]
            if cleaned:
                sections.append(f"- {label}: " + "; ".join(cleaned))
    if not sections:
        return TASK_COVERAGE_CONTINUATION_PROMPT
    return (
        TASK_COVERAGE_CONTINUATION_PROMPT
        + "\n\nUse this static distribution guidance only while reviewing the completed turn; "
        "it is not a plan or a required tool sequence:\n" + "\n".join(sections)
    )


def should_run_task_coverage(
    *,
    enabled: bool,
    skip_genui_post_run: bool,
    called_external_tool: bool,
    has_assistant_text: bool,
    stop_reason_type: str | None,
) -> bool:
    """The situational gate Task Coverage SHARES with Citation/Audit.

    The two post-run features keep independent user toggles (``enabled``), but
    every situational condition is judged together: a turn that attempted
    ``generate_ui``, brought in no external information, or produced no
    assistant prose skips BOTH — Citation/Audit through the observer's own
    guards, Task Coverage through this predicate. ``end_turn`` is the one
    coverage-mechanical extra: a continuation can only ride a cleanly ended
    native thread.
    """

    return (
        enabled
        and not skip_genui_post_run
        and called_external_tool
        and has_assistant_text
        and stop_reason_type == "end_turn"
    )


__all__ = [
    "TASK_COVERAGE_CONTINUATION_PROMPT",
    "TASK_COVERAGE_NOOP_TOOL_NAME",
    "build_task_coverage_continuation_prompt",
    "build_task_coverage_noop_tool",
    "should_run_task_coverage",
]
