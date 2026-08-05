"""Passive Task Coverage continuation contract used by production runtimes."""

from __future__ import annotations

from typing import Any

from src.core.tools import ExecContext, ToolDef, ToolResult

TASK_COVERAGE_NOOP_TOOL_NAME = "valuz_task_coverage_noop"


TASK_COVERAGE_CONTINUATION_PROMPT = (
    "This is an append-only completion pass, not a request for a review report.\n"
    "Review the original user request and every visible assistant and tool message "
    "from this turn.\n"
    "Decide whether the user still needs an important omission filled, an unfinished "
    "requirement completed, or a material correction.\n\n"
    "If important user-facing content is missing, use the same available tools and "
    "context when needed, then append only that missing information or correction. "
    "Do not repeat or replace the completed answer. Do not summarize or evaluate it.\n\n"
    f"If no important user-facing content is missing, call "
    f"`{TASK_COVERAGE_NOOP_TOOL_NAME}` exactly once and then end. Do not generate any "
    "assistant text before or after that private completion call. Do not say "
    '"nothing was omitted", "the response is complete", "no correction is needed", '
    "or any equivalent review conclusion.\n"
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
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            ]
            if cleaned:
                sections.append(f"- {label}: " + "; ".join(cleaned))
    if not sections:
        return TASK_COVERAGE_CONTINUATION_PROMPT
    return (
        TASK_COVERAGE_CONTINUATION_PROMPT
        + "\n\nUse this static distribution guidance only while reviewing the completed turn; "
        "it is not a plan or a required tool sequence:\n"
        + "\n".join(sections)
    )


__all__ = [
    "TASK_COVERAGE_CONTINUATION_PROMPT",
    "TASK_COVERAGE_NOOP_TOOL_NAME",
    "build_task_coverage_continuation_prompt",
    "build_task_coverage_noop_tool",
]
