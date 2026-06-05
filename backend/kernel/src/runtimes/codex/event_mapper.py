"""Map Codex SDK Notifications to harness Events.

The mapping rules are documented in ``docs/design/CODEX-INTEGRATION-DESIGN.md``
section 7.4. The mapper is pure (no I/O, no SDK calls) so it can be exhaustively
unit-tested against fabricated Notification fixtures.

Inputs:
- ``notification.method``: Codex JSON-RPC method name (e.g. ``"item/agentMessage/delta"``)
- ``notification.payload``: typed pydantic model for that method (or ``UnknownNotification``)

Outputs:
- A list of ``Event`` objects (often 0 or 1, occasionally 2 — e.g. an
  ``ItemCompleted`` for a reasoning item produces both ``thinking`` and any
  pending finalization).

"""

from __future__ import annotations

from typing import Any

from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    AgentMessageThreadItem,
    CommandExecutionOutputDeltaNotification,
    CommandExecutionThreadItem,
    ErrorNotification,
    FileChangeOutputDeltaNotification,
    FileChangeThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpServerStatusUpdatedNotification,
    McpToolCallThreadItem,
    ReasoningSummaryPartAddedNotification,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
    ReasoningThreadItem,
    ThreadGoalClearedNotification,
    ThreadStartedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnPlanStepStatus,
    TurnPlanUpdatedNotification,
)
from openai_codex.models import Notification

from src.core.events import Event


def map_notification(notification: Notification) -> list[Event]:
    """Translate one SDK Notification into harness Events.

    Returns an empty list for notifications that should not surface to the
    UI (token usage, in-flight progress, model warnings, etc.).
    """
    payload = notification.payload

    if isinstance(payload, AgentMessageDeltaNotification):
        return [Event(type="text_delta", data={"text": payload.delta})]

    if isinstance(payload, ReasoningTextDeltaNotification):
        # Raw chain-of-thought stream (``item/reasoning/textDelta``).
        # OpenAI hosted reasoning models (o-series, gpt-5) encrypt CoT
        # and do NOT emit this in practice; mostly fires for
        # gpt-oss / self-hosted reasoning models where raw CoT is exposed.
        return [Event(type="thinking_delta", data={"text": payload.delta})]

    if isinstance(payload, ReasoningSummaryTextDeltaNotification):
        # Reasoning *summary* stream (``item/reasoning/summaryTextDelta``).
        # This is the channel hosted reasoning models actually use —
        # gated by the ``model_reasoning_summary`` / per-turn ``summary``
        # config (``auto`` / ``concise`` / ``detailed`` / ``none``).
        # We collapse summary chunks to the same ``thinking_delta`` event
        # as raw CoT so the frontend renders both flavors identically;
        # ``summary_index`` is bookkeeping the UI doesn't need (deltas
        # arrive in order).
        return [Event(type="thinking_delta", data={"text": payload.delta})]

    if isinstance(payload, ReasoningSummaryPartAddedNotification):
        # Summary "part" boundary — fires between summary paragraphs.
        # Emit a paragraph break so the rendered thinking text isn't
        # all glued together. No payload field carries text; we
        # synthesize the break.
        return [Event(type="thinking_delta", data={"text": "\n\n"})]

    if isinstance(payload, CommandExecutionOutputDeltaNotification):
        # Codex emits a unified `delta` text per chunk — no stdout/stderr
        # discriminator. Stream label is omitted for now; the final tool_result
        # carries the full aggregated output (and the buffered stdout/stderr
        # split, when the SDK exposes it on the item).
        return [
            Event(
                type="tool_output_delta",
                data={"id": payload.item_id, "text": payload.delta},
            )
        ]

    if isinstance(payload, FileChangeOutputDeltaNotification):
        return [
            Event(
                type="tool_output_delta",
                data={
                    "id": payload.item_id,
                    "stream": "patch",
                    "text": payload.delta,
                },
            )
        ]

    if isinstance(payload, ItemStartedNotification):
        return _map_item_started(payload.item.root)

    if isinstance(payload, ItemCompletedNotification):
        return _map_item_completed(payload.item.root)

    if isinstance(payload, ThreadTokenUsageUpdatedNotification):
        # Mid-turn usage updates: keep V1 simple — only emit usage_update at
        # turn/completed (where we also pick up the final reasoning_output_tokens).
        return []

    if isinstance(payload, TurnPlanUpdatedNotification):
        # Codex plan-mode structured snapshot (`turn/plan/updated`). Each
        # step carries the model's intent + a lifecycle status; codex
        # emits the *full* plan on every update so the front-end can
        # snapshot-replace. Mapped to `plan_update` per
        # docs/design/session-modes.md §Events. Status enum is
        # normalized to snake_case (codex wire is camelCase `inProgress`).
        return [
            Event(
                type="plan_update",
                data={
                    "plan": [
                        {
                            "step": step.step,
                            "status": _normalize_plan_step_status(step.status),
                        }
                        for step in payload.plan
                    ],
                    "explanation": payload.explanation,
                },
            )
        ]

    return []


def _normalize_plan_step_status(status: TurnPlanStepStatus) -> str:
    """Codex wire is camelCase (`pending`, `inProgress`, `completed`); the
    harness contract is snake_case across all event payloads. Map at the
    boundary so consumers don't have to."""
    if status == TurnPlanStepStatus.in_progress:
        return "in_progress"
    return str(status.value)


def _map_item_started(item: Any) -> list[Event]:
    """ItemStarted is only meaningful for tool calls; assistant text and
    reasoning are surfaced by their delta + Item Completed pair."""
    if isinstance(item, CommandExecutionThreadItem):
        return [
            Event(
                type="tool_use",
                data={
                    "id": item.id,
                    "name": "shell",
                    "input": {"command": item.command, "cwd": str(item.cwd)},
                },
            )
        ]
    if isinstance(item, FileChangeThreadItem):
        return [
            Event(
                type="tool_use",
                data={
                    "id": item.id,
                    "name": "apply_patch",
                    "input": {
                        "changes": [c.model_dump(mode="json") for c in item.changes],
                    },
                },
            )
        ]
    if isinstance(item, McpToolCallThreadItem):
        return [
            Event(
                type="tool_use",
                data={
                    "id": item.id,
                    "name": f"{item.server}/{item.tool}",
                    "input": _safe_jsonable(item.arguments),
                },
            )
        ]
    return []


def _map_item_completed(item: Any) -> list[Event]:
    if isinstance(item, AgentMessageThreadItem):
        return [Event(type="assistant_message", data={"text": item.text})]

    if isinstance(item, ReasoningThreadItem):
        text = "\n".join(item.content or []) if item.content else ""
        if not text:
            text = "\n".join(item.summary or []) if item.summary else ""
        if not text:
            return []
        return [Event(type="thinking", data={"text": text})]

    if isinstance(item, CommandExecutionThreadItem):
        is_error = item.exit_code is not None and item.exit_code != 0
        return [
            Event(
                type="tool_result",
                data={
                    "id": item.id,
                    "content": item.aggregated_output or "",
                    "is_error": is_error,
                },
            )
        ]

    if isinstance(item, FileChangeThreadItem):
        is_error = item.status.value != "completed" if hasattr(item.status, "value") else False
        return [
            Event(
                type="tool_result",
                data={
                    "id": item.id,
                    "content": _stringify_file_changes(item),
                    "is_error": is_error,
                },
            )
        ]

    if isinstance(item, McpToolCallThreadItem):
        is_error = item.error is not None
        if is_error and item.error is not None:
            content = item.error.model_dump_json()
        elif item.result is not None:
            content = item.result.model_dump_json()
        else:
            content = ""
        return [
            Event(
                type="tool_result",
                data={"id": item.id, "content": content, "is_error": is_error},
            )
        ]

    return []


def _stringify_file_changes(item: FileChangeThreadItem) -> str:
    parts: list[str] = []
    for change in item.changes:
        parts.append(change.model_dump_json())
    return "\n".join(parts)


def _safe_jsonable(value: Any) -> Any:
    """Codex's mcp tool arguments are typed as ``Any`` (JSON-RPC pass-through);
    coerce pydantic models to plain dicts so downstream JSON columns accept it."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def extract_thread_id(notification: Notification) -> str | None:
    """Pick the thread.id off a ``thread/started`` notification."""
    if isinstance(notification.payload, ThreadStartedNotification):
        return notification.payload.thread.id
    return None


def extract_error(notification: Notification) -> str | None:
    """Return the human-readable error message from an ``error`` notification."""
    if isinstance(notification.payload, ErrorNotification):
        return notification.payload.error.message
    return None


def extract_turn_completed(
    notification: Notification,
) -> TurnCompletedNotification | None:
    if isinstance(notification.payload, TurnCompletedNotification):
        return notification.payload
    return None


def extract_token_usage(
    notification: Notification,
) -> ThreadTokenUsageUpdatedNotification | None:
    if isinstance(notification.payload, ThreadTokenUsageUpdatedNotification):
        return notification.payload
    return None


def extract_goal_cleared(notification: Notification) -> bool:
    """True iff the SDK notification is `thread/goal/cleared` — the
    canonical signal that codex-core finished or aborted a goal loop.
    Slice 5/6 of session-modes uses this for runtime-initiated mode
    exits without polling.
    """
    return isinstance(notification.payload, ThreadGoalClearedNotification)


def extract_mcp_server_status(
    notification: Notification,
) -> McpServerStatusUpdatedNotification | None:
    """Pick up ``mcpServer/startupStatus/updated`` payloads so the runtime
    can surface MCP startup failures — without this, a stdio MCP that
    fails to spawn (bad command, permission error, etc.) silently never
    becomes a tool the model can call."""
    if isinstance(notification.payload, McpServerStatusUpdatedNotification):
        return notification.payload
    return None
