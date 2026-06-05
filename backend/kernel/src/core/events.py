"""Event types and EventSink protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from src.core.time_utils import now_ms

OutboundEventType = Literal[
    "text_delta",
    "assistant_message",
    "tool_use",
    "tool_result",
    "tool_input_delta",
    "tool_output_delta",
    "thinking",
    "thinking_delta",
    "session_idle",
    "session_error",
    "session_update",
    "compaction",
    "usage_update",
    "todo_update",
    # Approval contract (slice 2): runtime emits ``requires_action`` mid-turn
    # when host approval is needed; ``action_resolved`` is emitted once the
    # decision lands (``decision`` ∈ approve/reject/expired/interrupted).
    "requires_action",
    "action_resolved",
    # Session-modes contract (see docs/design/session-modes.md):
    # ``mode_changed`` fires on every transition (user or runtime initiated)
    # carrying ``{mode, by: "user" | "runtime"}``. ``plan_update`` carries
    # the codex runtime's structured ``TurnPlanStep[]`` snapshot during
    # plan mode (Claude plan reuses ``requires_action(clarifying_questions)``
    # for its interactive surface; codex plan emits ``plan_update``).
    "mode_changed",
    "plan_update",
    # Dynamic-workflow progress (Claude ``Workflow`` tool). The runtime
    # parses the run's state-file path from the ``Workflow`` tool_result,
    # then streams the run's ``wf_<id>.json`` (phases + per-agent progress +
    # token/tool totals + status) on a poll loop while it executes. Live-only
    # (non-persisted — see ``DatabaseEventSink._NON_PERSISTED_TYPES``); the
    # ``/workflows`` TUI it mirrors is unreachable through the SDK.
    "workflow_progress",
]

InboundEventType = Literal[
    "user_message",
    "interrupt",
]

EventType = OutboundEventType | InboundEventType


# Decisions surfaced to the host on a ``requires_action`` event.
#
# ``AVAILABLE_DECISIONS_V1`` is the baseline list emitted for
# tool-approval subjects (``shell_command`` / ``file_change`` /
# ``mcp_tool_call`` / ``tool_input``) on runtimes that do NOT support
# editing the tool input — currently codex (its approval-response
# wire shape has no ``updated_input`` analog).
#
# ``AVAILABLE_DECISIONS_EDITABLE`` extends V1 with
# ``approve_with_changes`` for runtimes whose SDK natively accepts
# modified input on approval (Claude ``PermissionResultAllow(
# updated_input=...)`` and DeepAgents HITL middleware
# ``EditDecision.edited_action``). The two lists differ only by that
# verb; everything else (subject coverage, reject semantics) is
# identical.
#
# ``AVAILABLE_DECISIONS_CLARIFYING`` is the list for the
# ``clarifying_questions`` subject — Claude SDK's ``AskUserQuestion``
# tool. The user can ``answer`` (carries a structured ``answers``
# payload that maps question text → selected label(s)) or ``reject``
# (Claude sees the rejection message and proceeds without the
# clarification). There's no ``approve`` for clarifying questions
# because the SDK contract requires the structured ``updated_input``
# payload — a bare approve has no answers to feed back.
#
# Keep these in sync with the design doc §2.4 and the OpenAPI
# ``decision`` enum on ``SubmitActionRequest``.
AVAILABLE_DECISIONS_V1: tuple[Literal["approve", "reject"], ...] = (
    "approve",
    "reject",
)
AVAILABLE_DECISIONS_EDITABLE: tuple[Literal["approve", "approve_with_changes", "reject"], ...] = (
    "approve",
    "approve_with_changes",
    "reject",
)
AVAILABLE_DECISIONS_CLARIFYING: tuple[Literal["answer", "reject"], ...] = (
    "answer",
    "reject",
)

# v2 extensions — runtimes that wire ``approve_for_session`` advertise
# these instead of the v1 lists. The verb is session-scoped (rule lives
# until ``SessionOrchestrator.cleanup``); the runtime never sees it on
# its ``submit_action`` — the orchestrator translates to plain
# ``approve`` at the kernel boundary and handles the rule commit. See
# ``docs/design/approve-for-session.md`` §6.1.
AVAILABLE_DECISIONS_V1_WITH_SESSION: tuple[
    Literal["approve", "approve_for_session", "reject"], ...
] = (
    "approve",
    "approve_for_session",
    "reject",
)
AVAILABLE_DECISIONS_EDITABLE_WITH_SESSION: tuple[
    Literal["approve", "approve_with_changes", "approve_for_session", "reject"], ...
] = (
    "approve",
    "approve_with_changes",
    "approve_for_session",
    "reject",
)


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: int = field(default_factory=now_ms)  # Unix epoch ms (UTC)


class EventSink(Protocol):
    """Outbound event push — shared by all Runtimes."""

    async def emit(self, event: Event) -> None: ...
