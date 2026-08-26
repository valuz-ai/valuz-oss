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
    # Runtime-private evidence replay. Some graph middleware enriches the
    # persisted ToolMessage only after the underlying tool's ``on_tool_end``
    # callback has fired. The runtime replays that private sidecar before
    # ``session_idle`` so Citation Guard sees the exact evidence the model saw;
    # the orchestrator consumes this event without persisting/broadcasting it.
    "citation_evidence",
    "tool_input_delta",
    "tool_output_delta",
    "thinking",
    "thinking_delta",
    "session_idle",
    "session_error",
    "session_update",
    # Context compaction marker, emitted by both runtimes when the context
    # window is summarized. ``data`` is passed through raw, not normalized:
    # Claude forwards the CLI's ``compact_boundary`` ``compact_metadata`` dict
    # verbatim (e.g. ``{trigger, pre_tokens}``); codex runs ``/compact`` as a
    # real turn but has no metadata of its own, so its marker is ``{}`` (the
    # real token counts ride the following ``usage_update``). The upper layer
    # decides how/whether to render it.
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
    # Background-task lifecycle (Claude ``run_in_background`` Bash & friends).
    # The CLI tracks such processes as session tasks and pushes lifecycle
    # system messages on the SDK stream — including BETWEEN turns, when it
    # also runs a spontaneous "wake-up" turn so the agent can react. The
    # runtime maps them 1:1: ``bg_task_started`` {task_id, tool_use_id,
    # description, task_type}; ``bg_task_progress`` {task_id, usage, ...};
    # ``bg_task_updated`` {task_id, patch}; ``bg_task_finished`` {task_id,
    # status: completed|failed|stopped, summary, output_file, usage}.
    "bg_task_started",
    "bg_task_progress",
    "bg_task_updated",
    "bg_task_finished",
    # Turn-phase observability marker (persisted). Runtimes emit one event
    # per otherwise-invisible boundary of a turn so latency can be read
    # straight off the stored event timestamps — no aggregation layer:
    #   {phase: "runtime_init", duration_ms}            cold client/process/
    #                                                   graph construction;
    #                                                   deepagents adds
    #                                                   {mcp_tools_ms,
    #                                                    checkpointer_ms}
    #   {phase: "thread_init", mode: start|resume,
    #    duration_ms}                                   codex only
    #   {phase: "dispatch", duration_ms?}               request handed to the
    #                                                   SDK/CLI — the gap from
    #                                                   this row to the first
    #                                                   thinking/text delta is
    #                                                   runtime prep + model
    #                                                   TTFT
    #   {phase: "post_run_verification", state: started|completed,
    #    features: [...]}                              host-side Citation /
    #                                                   Claim Audit / Task
    #                                                   Coverage window after
    #                                                   visible primary output
    # A phase that did not run this turn emits nothing (a warm turn carries
    # only ``dispatch`` before any optional post-run phase).
    "turn_phase",
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


class GlobalEventTap(Protocol):
    """Process-wide observer of every session's live event stream.

    Registered via ``SessionOrchestrator.attach_global_tap``; receives the
    session id alongside each event so subscribers don't re-derive routing.
    """

    async def emit_session(self, session_id: str, event: Event) -> None: ...
