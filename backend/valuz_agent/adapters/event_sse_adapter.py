"""Stream kernel session events to clients as Server-Sent Events.

The valuz frontend talks SSE (``/v1/sessions/{id}/events/stream``) in the
legacy pre-V5 frame shape; the kernel exposes events through the
``KernelClient`` seam — cursor reads (``get_events(after_seq=...)`` /
``get_events_window``) plus the live subscription
(``subscribe_session_events``). This adapter keeps the SSE shell and the
kernel→legacy event-type translation, sourcing every frame from the seam
(no direct kernel storage access).

This module gives the session router three helpers:

- ``list_events_after`` — one-shot cursor fetch for the polling
  ``GET /v1/sessions/{id}/events?after_seq=N`` endpoint.
- ``list_events_window`` — turn-aligned history pagination.
- ``iter_events_sse`` — async generator yielding ``EventSourceResponse``-
  shaped frames; merges the live subscription with a DB-poll fallback and
  reconnects gracefully when the client provides ``after_seq``.

The kernel exposes the events row id as ``seq`` — the frontend's paging
cursor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.sse import shielded

logger = logging.getLogger(__name__)

# Wire marker for a frame carrying absolute state instead of an increment.
# Duplicated rather than imported: the module boundary forbids the host
# from reaching into ``src.core``, and a wire constant is exactly what an
# adapter is supposed to own. Kernel side: ``live_partial.SNAPSHOT_FLAG``
# (``test_live_snapshot_flag_matches_kernel`` pins the two together).
LIVE_SNAPSHOT_FLAG = "live_snapshot"

POLL_INTERVAL_SECONDS = 0.3
# The DB backfill on queue-timeout exists to cover the subscribe/backfill race
# and missed events — not as the primary delivery path (the live subscription
# is). Running it on EVERY 0.3s timeout made each idle SSE stream issue ~3.3
# empty reads/sec continuously; throttle it while keeping the first idle tick
# immediate (post-subscribe race coverage) and the queue wait at 0.3s for
# live responsiveness.
DB_BACKFILL_INTERVAL_SECONDS = 2.0
IDLE_HEARTBEAT_SECONDS = 15.0
# How often an attached live tap re-checks that it is still bound to the kernel
# serving its session (see ``_follow_session_kernel``). This is the ceiling on
# how long a per-turn instance swap can starve the stream of live-only frames,
# so it tracks the backfill cadence rather than being cheaper: one peek is a
# single registry row read, against the durable page this stream already reads
# on the same interval.
KERNEL_REBIND_POLL_SECONDS = 2.0


# History read-routing. Reads are UNIFIED through the DataService: whenever a
# durable DataService is configured (any non-local mode), the host reads event
# HISTORY straight from it (in-process), independent of whether the sandbox
# kernel is alive — the sandbox owns execution + live deltas, the DataService
# owns history. In local mode there is no DataService, so reads go through the
# kernel seam (the in-process kernel store). History reads share the one typed
# DataReader port with session reads.
def _history_reader() -> Any:
    """The transport for reading event HISTORY — the shared ``DataReader`` port."""
    from valuz_agent.adapters.data_reader import data_reader

    return data_reader()


# Cap on the shared live/history ``event_uid`` dedup set. Sized to comfortably
# cover the subscribe/backfill overlap window (a backfill page is ≤1000 rows);
# an infinite stream must not grow the set unbounded.
_SEEN_UIDS_MAX = 4096


class _SeenUids:
    """Bounded remember-set for ``event_uid``-keyed dedup.

    ``seq`` is a PER-STORE counter — the kernel's LOCAL seq (live frames) and
    the durable store's seq (history reads) are independent and must never be
    compared. The store-independent ``event_uid`` is the only identity that
    bridges the two paths, so the SSE merge dedups on it: one shared set covers
    both directions (live frame later re-read by a history backfill, and a
    history frame later re-delivered live).

    ``add(uid)`` returns ``True`` when the uid is new (emit the frame) and
    ``False`` when already seen (duplicate — skip). FIFO-trimmed at
    ``_SEEN_UIDS_MAX`` entries so a long-lived stream stays bounded.
    """

    def __init__(self, max_size: int = _SEEN_UIDS_MAX) -> None:
        self._max = max_size
        self._set: set[str] = set()
        self._order: deque[str] = deque()

    def add(self, uid: str) -> bool:
        if uid in self._set:
            return False
        self._set.add(uid)
        self._order.append(uid)
        while len(self._order) > self._max:
            self._set.discard(self._order.popleft())
        return True


@dataclass(frozen=True)
class SessionEventFrame:
    """One row of ``events`` shaped for the existing SSE wire format."""

    seq: int
    event_type: str
    payload: dict[str, Any]
    timestamp: int | None  # Unix epoch ms (UTC); frontend formats via new Date(ms)
    # Store-independent identity (see ``_SeenUids``). ``None`` on live-only
    # frames (deltas) and legacy rows persisted before uid minting.
    event_uid: str | None = None

    def to_sse_data(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "event_type": self.event_type,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "event_uid": self.event_uid,
            },
            default=str,
        )


def _stringify(value: Any) -> str:
    """Coerce arbitrary values to strings the legacy frontend expects.

    The pre-V5 SSE contract typed payload values as ``Record<string, string>``;
    the desktop event renderer reads ``payload.text`` / ``payload.input``
    / etc. as strings (sometimes JSON-parsing them). We preserve that
    contract by stringifying everything at the wire boundary.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _with_message_id(payload: dict[str, str], data: dict[str, Any]) -> dict[str, str]:
    """Tack ``message_id`` onto an outgoing SSE payload when the kernel
    event carries one.

    Kernel V5+messages stamps every outbound event with the active
    Message id (via ``_MessageIdStampSink`` inside the orchestrator).
    Preserving it on the wire lets the frontend group events per-message
    later if it ever adopts upstream's chat-from-messages renderer.
    """
    msg_id = data.get("message_id")
    if msg_id is not None and "message_id" not in payload:
        payload["message_id"] = _stringify(msg_id)
    return payload


def _with_parent_tool_use_id(payload: dict[str, str], data: dict[str, Any]) -> dict[str, str]:
    """Tack ``parent_tool_use_id`` onto an outgoing SSE payload when present.

    Set by runtimes on events produced INSIDE a subagent (Task/Agent tool
    run). The frontend uses it to treat such events as out-of-band activity
    — a background agent's tool calls arrive interleaved with the lead's
    live text stream and must not split it into fragments.
    """
    parent_id = data.get("parent_tool_use_id")
    if parent_id is not None and "parent_tool_use_id" not in payload:
        payload["parent_tool_use_id"] = _stringify(parent_id)
    return payload


def _with_live_snapshot(payload: dict[str, str], data: dict[str, Any]) -> dict[str, str]:
    """Mark a frame that carries absolute state rather than an increment.

    Set by the kernel's live-partial accumulator when a subscriber joins
    mid-turn (see ``kernel/src/core/live_partial.py``). The frame reuses
    the ordinary delta type — same routing, same payload shape — so the
    only thing consumers need is the rule that a marked frame REPLACES the
    open block's text instead of appending to it.
    """
    if data.get(LIVE_SNAPSHOT_FLAG):
        payload[LIVE_SNAPSHOT_FLAG] = "true"
    return payload


def _with_row_message_id(data: dict[str, Any], message_id: Any) -> dict[str, Any]:
    """Attach the DB row's message id before translating persisted events.

    The kernel stores ``message_id`` as a column on ``events`` rather than
    duplicating it inside the JSON payload. Live broadcast events already
    carry it because the orchestrator stamps them before enqueueing.
    """
    if message_id is None or data.get("message_id") is not None:
        return data
    return {**data, "message_id": message_id}


def _translate_kernel_event(
    kernel_type: str, kernel_data: dict[str, Any]
) -> tuple[str, dict[str, str]] | None:
    """Translate a kernel-native event into the legacy frontend shape.

    The valuz desktop renderer was authored against the pre-V5 event names
    (``message.user``, ``message.assistant.delta``, ``tool.call.started``,
    ``tool.call.completed``, ``run.failed``). Rather than rewrite the
    renderer, we map kernel events back to those at the SSE boundary.
    Returns ``None`` when the event has no legacy counterpart and should
    be filtered out.

    Mapping:
      - ``user_message``      → ``message.user``
        ``data.message`` → ``payload.text``
        ``data.attachments`` → ``payload.attachments`` (JSON-stringified list)
      - ``assistant_message`` → ``message.assistant.delta``
        ``data.text`` → ``payload.text``
      - ``thinking``          → ``message.assistant.thinking``
      - ``thinking_delta``    → ``message.assistant.thinking_delta``  (V5+streaming:
        per-token reasoning chunks; full ``thinking`` event still
        carries the canonical record)
      - ``tool_use``          → ``tool.call.started``
      - ``tool_result``       → ``tool.call.completed``
      - ``tool_input_delta``  → ``tool.call.input_delta``  (live-only: partial
        tool-call input JSON streaming in *before* ``tool_use`` — the first
        delta is the frontend's build-the-card signal, so large-file writes
        show progress instead of a dead wait)
      - ``tool_output_delta`` → ``tool.call.output_delta`` (live-only: streamed
        tool output between started and completed; ``stream`` discriminates
        codex patch vs stdout)
      - ``tool_thinking_delta`` → ``tool.call.thinking_delta`` (live-only:
        tool-scoped reasoning stream — the ephemeral ``generate_ui``
        session's thinking, forwarded onto the calling session)
      - ``session_error``     → ``run.failed``
      - ``usage_update``      → ``runtime.engine.usage``  (V5+messages: replaces
        the dropped ``cost_update`` event; carries token counts +
        per-model ``model_usage``)
      - ``todo_update``       → ``session.todos.update``  (V5+messages: lets
        the frontend hydrate a Todos panel from live agent planning)
      - ``workflow_progress`` → ``session.workflow_progress``  (live-only:
        Claude ``Workflow`` tool run progress — phases + per-agent state +
        status, keyed by the launch tool_use_id so the frontend attaches a
        progress card to the matching tool call)
      - ``session_idle`` / ``session_update`` → surfaced for status display
      - Every translated payload also carries ``message_id`` when the
        kernel event was stamped with one (most events during a turn).
    """
    data = kernel_data or {}

    if kernel_type == "user_message":
        return "message.user", _with_message_id(
            {
                "text": _stringify(data.get("message") or data.get("text") or ""),
                "attachments": _stringify(data.get("attachments") or []),
            },
            data,
        )

    if kernel_type == "assistant_message":
        payload = {
            "text": _stringify(data.get("text") or data.get("content") or ""),
        }
        citation_bundle = data.get("citation_bundle")
        if isinstance(citation_bundle, dict):
            payload["citation_bundle"] = _stringify(citation_bundle)
        task_coverage = data.get("task_coverage")
        if isinstance(task_coverage, dict):
            payload["task_coverage"] = _stringify(task_coverage)
        return "message.assistant.delta", _with_parent_tool_use_id(
            _with_message_id(
                payload,
                data,
            ),
            data,
        )

    if kernel_type == "assistant_message_sidecar":
        payload = {
            "assistant_segment_index": _stringify(
                data.get("assistant_segment_index") or 0
            ),
        }
        citation_bundle = data.get("citation_bundle")
        if isinstance(citation_bundle, dict):
            payload["citation_bundle"] = _stringify(citation_bundle)
        claim_audit = data.get("claim_audit")
        if isinstance(claim_audit, dict):
            payload["claim_audit"] = _stringify(claim_audit)
        task_coverage = data.get("task_coverage")
        if isinstance(task_coverage, dict):
            payload["task_coverage"] = _stringify(task_coverage)
        return "message.assistant.sidecar", _with_parent_tool_use_id(
            _with_message_id(payload, data),
            data,
        )

    if kernel_type == "thinking":
        # Separate event type so the renderer can show thinking with a dimmed
        # italic style instead of mixing it into the assistant turn body.
        return "message.assistant.thinking", _with_parent_tool_use_id(
            _with_message_id(
                {
                    "text": _stringify(data.get("text") or data.get("content") or ""),
                },
                data,
            ),
            data,
        )

    if kernel_type == "tool_use":
        return "tool.call.started", _with_parent_tool_use_id(
            _with_message_id(
                {
                    "id": _stringify(data.get("id") or ""),
                    "tool_use_id": _stringify(data.get("id") or ""),
                    "name": _stringify(data.get("name") or ""),
                    "input": _stringify(data.get("input") or {}),
                },
                data,
            ),
            data,
        )

    if kernel_type == "tool_result":
        return "tool.call.completed", _with_parent_tool_use_id(
            _with_message_id(
                {
                    "id": _stringify(data.get("id") or ""),
                    "tool_use_id": _stringify(data.get("id") or ""),
                    "content": _stringify(data.get("content") or ""),
                    "is_error": _stringify(data.get("is_error", False)),
                },
                data,
            ),
            data,
        )

    if kernel_type == "session_error":
        return "run.failed", _with_message_id(
            {
                "message": _stringify(
                    data.get("message") or data.get("category") or "agent run failed"
                ),
                "category": _stringify(data.get("category") or ""),
            },
            data,
        )

    if kernel_type == "usage_update":
        # V5+messages: replaces ``cost_update``. Carries the kernel's
        # post-turn token usage roll-up. ``model_usage`` is the SDK-native
        # per-model breakdown (sub-agent attribution, reasoning tokens) —
        # JSON-stringified so the legacy ``Record<string,string>`` SSE
        # contract holds.
        input_tokens = int(data.get("input_tokens") or 0)
        output_tokens = int(data.get("output_tokens") or 0)
        # Billing meter call — best-effort, never breaks the SSE stream.
        # Cost estimate uses claude-sonnet-4-6 rates: $3/M input, $15/M output.
        # ``meter`` is async (it may do network I/O in commercial overlays);
        # this translation helper is sync but always runs on the event loop
        # (both callers are async), so fire-and-forget via ``create_task`` —
        # metering must never block or break the SSE stream.
        try:
            from valuz_agent.ports.billing import MeterEvent
            from valuz_agent.ports.extensions import ext

            uid = data.get("user_id")
            if uid is None:
                # Explicitly-anonymous context — nothing to attribute the
                # usage to; surfaces via the best-effort except below.
                raise LookupError("usage_update without an owner user_id")
            cost_usd = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            coro = ext.billing.meter(
                MeterEvent(
                    user_id=uid,
                    event_type="llm_call",
                    cost_usd=cost_usd,
                    metadata={"input_tokens": input_tokens, "output_tokens": output_tokens},
                )
            )
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                coro.close()  # no running loop — drop the meter event
        except Exception:
            pass  # billing is best-effort; never break the SSE stream
        return "runtime.engine.usage", _with_message_id(
            {
                "input_tokens": _stringify(input_tokens),
                "output_tokens": _stringify(output_tokens),
                "cache_read_tokens": _stringify(data.get("cache_read_tokens") or 0),
                "cache_write_tokens": _stringify(data.get("cache_write_tokens") or 0),
                "model_usage": _stringify(data.get("model_usage") or {}),
            },
            data,
        )

    if kernel_type == "todo_update":
        # V5+messages: emitted by the runtime whenever the agent calls
        # TodoWrite. ``data.todos`` is a list of
        # ``{content, status, activeForm?}`` dicts. JSON-stringified for
        # the legacy SSE contract; the frontend re-parses on receipt.
        return "session.todos.update", _with_message_id(
            {
                "todos": _stringify(data.get("todos") or []),
            },
            data,
        )

    if kernel_type == "session_idle":
        return "session.idle", _with_message_id(
            {
                "stop_reason": _stringify(data.get("stop_reason") or ""),
            },
            data,
        )

    if kernel_type == "session_update":
        # V5+messages: orchestrator's ``session_update`` carries only
        # ``status`` and ``message_id`` now (turn counts and cost moved to
        # the Message row). Preserve ``message_id`` so the frontend can
        # close out the per-message stream.
        return "session.update", _with_message_id(
            {
                "status": _stringify(data.get("status") or ""),
            },
            data,
        )

    if kernel_type == "compaction":
        return "session.compaction", _with_message_id(
            {
                "summary": _stringify(data.get("summary") or ""),
            },
            data,
        )

    if kernel_type == "text_delta":
        return "message.assistant.text_delta", _with_live_snapshot(
            _with_parent_tool_use_id(
                _with_message_id(
                    {
                        "text": _stringify(data.get("text") or data.get("delta") or ""),
                    },
                    data,
                ),
                data,
            ),
            data,
        )

    if kernel_type == "thinking_delta":
        return "message.assistant.thinking_delta", _with_live_snapshot(
            _with_parent_tool_use_id(
                _with_message_id(
                    {
                        "text": _stringify(data.get("text") or data.get("delta") or ""),
                    },
                    data,
                ),
                data,
            ),
            data,
        )

    if kernel_type == "tool_input_delta":
        # Live, non-persisted: partial tool-call input JSON streaming in as
        # the model emits it. Arrives BEFORE the canonical ``tool_use``
        # (tool.call.started) — the first delta is the frontend's
        # build-the-card signal, so large-file writes show progress instead
        # of a dead wait. ``id`` is the tool_use_id that started/completed
        # also key on; ``name`` lets the card render its real title at once.
        return "tool.call.input_delta", _with_parent_tool_use_id(
            _with_message_id(
                {
                    "tool_use_id": _stringify(data.get("id") or ""),
                    "name": _stringify(data.get("name") or ""),
                    "text": _stringify(data.get("text") or data.get("delta") or ""),
                },
                data,
            ),
            data,
        )

    if kernel_type == "tool_output_delta":
        # Live, non-persisted: streamed tool output (codex command stdout /
        # file-change patch) arriving between started and completed. ``stream``
        # discriminates patch vs stdout when the runtime supplies it.
        return "tool.call.output_delta", _with_message_id(
            {
                "tool_use_id": _stringify(data.get("id") or ""),
                "stream": _stringify(data.get("stream") or ""),
                "text": _stringify(data.get("text") or data.get("delta") or ""),
            },
            data,
        )

    if kernel_type == "tool_thinking_delta":
        # Live, non-persisted: a tool-scoped reasoning stream — today the
        # ephemeral ``generate_ui`` session's ``thinking_delta``, forwarded
        # onto the calling session between started and completed. A SEPARATE
        # type from ``tool.call.output_delta`` on purpose: the frontend
        # concatenates output deltas into the tool card's output stream
        # unconditionally (the OpenUI code the <Renderer> paints), so
        # reasoning text through that channel would corrupt the render.
        # Frontends that don't know this type ignore it.
        return "tool.call.thinking_delta", _with_message_id(
            {
                "tool_use_id": _stringify(data.get("id") or ""),
                "text": _stringify(data.get("text") or data.get("delta") or ""),
            },
            data,
        )

    if kernel_type == "requires_action":
        # V5+1aae940 (approval contract v1): the runtime parks mid-turn
        # waiting for the user to ``approve`` / ``reject`` (or ``answer``
        # for ``clarifying_questions``). The frontend renders the
        # subject-specific approval card from these fields, then calls
        # ``POST /v1/sessions/{id}/actions`` with the decision. We
        # JSON-stringify the structured fields to honour the legacy
        # ``Record<string, string>`` SSE contract; the frontend re-parses
        # on receipt.
        #
        # V5+d008b53 (approval contract v2): ``available_decisions`` may
        # now include ``approve_with_changes`` (A1) and
        # ``approve_for_session`` (v2); two new structured fields land in
        # the payload:
        #   - ``session_rule_preview`` (dict): present for tool-approval
        #     subjects whose runtime advertises ``approve_for_session``.
        #     Shape: ``{kind, display, runtime_kind, rule_data}``. NOT
        #     present for ``clarifying_questions``.
        #   - ``original_input`` (dict): tool args the runtime parked on.
        #     Used by the frontend's "Edit & Approve" JSON editor to
        #     seed from the full args dict before the user mutates.
        # Forward-compat: ``_stringify`` on missing keys returns ``""``
        # so older kernels (no v2 fields) keep working.
        return "session.requires_action", _with_message_id(
            {
                "pending_id": _stringify(data.get("pending_id") or ""),
                "subject": _stringify(data.get("subject") or ""),
                "runtime_provider": _stringify(data.get("runtime_provider") or ""),
                "available_decisions": _stringify(data.get("available_decisions") or []),
                "payload": _stringify(data.get("payload") or {}),
                "expires_at": _stringify(data.get("expires_at") or ""),
                "session_rule_preview": _stringify(data.get("session_rule_preview") or {}),
                "original_input": _stringify(data.get("original_input") or {}),
            },
            data,
        )

    if kernel_type == "action_resolved":
        # Paired with ``requires_action``. ``decision`` is one of:
        #   approve / reject / answer / expired / interrupted   (v1)
        #   approve_with_changes / approve_for_session          (v2 user)
        #   auto_approved                                       (v2 cache-hit, kernel-synth)
        # ``resolved_by`` is ``user`` for the synchronous decision path
        # and ``system`` for synthetic seals (host restart, interrupt,
        # cache-hit auto-approve).
        #
        # V5+d008b53 adds two optional fields:
        #   - ``rule_id``: populated when decision == ``approve_for_session``;
        #     the kernel-assigned UUID for the just-committed session rule.
        #   - ``auto_resolved_by_rule_id``: populated when decision ==
        #     ``auto_approved``; points back to the rule that fired.
        # Both are stringified as empty when absent so the frontend's
        # parser sees a stable shape across decision verbs.
        return "session.action_resolved", _with_message_id(
            {
                "pending_id": _stringify(data.get("pending_id") or ""),
                "decision": _stringify(data.get("decision") or ""),
                "resolved_by": _stringify(data.get("resolved_by") or ""),
                "message": _stringify(data.get("message") or ""),
                "answers": _stringify(data.get("answers") or {}),
                "rule_id": _stringify(data.get("rule_id") or ""),
                "auto_resolved_by_rule_id": _stringify(data.get("auto_resolved_by_rule_id") or ""),
            },
            data,
        )

    if kernel_type == "mode_changed":
        # Session-modes contract (docs/design/session-modes.md): fires on
        # every transition. ``mode`` ∈ default|plan|goal; ``by`` ∈
        # user|runtime (runtime = goal auto-exit / plan lift). The frontend
        # renders the mode chip and clears it when mode flips to "default".
        return "session.mode_changed", _with_message_id(
            {
                "mode": _stringify(data.get("mode") or "default"),
                "by": _stringify(data.get("by") or ""),
            },
            data,
        )

    if kernel_type == "plan_update":
        # Codex plan-mode structured ``TurnPlanStep[]`` snapshot. JSON-
        # stringified for the legacy SSE contract; the frontend re-parses.
        return "session.plan_update", _with_message_id(
            {
                "steps": _stringify(data.get("steps") or data.get("plan") or []),
            },
            data,
        )

    if kernel_type == "workflow_progress":
        # Claude dynamic-workflow (``Workflow`` tool) live progress. The
        # kernel streams a snapshot of the run's phases / per-agent progress /
        # status while the background runtime executes. ``id`` is the
        # ``Workflow`` tool_use_id (so the frontend can attach the progress
        # card to the matching tool call); ``state`` is the nested progress
        # dict (runId / workflowName / status / agentCount / agentsDone /
        # workflowProgress[] / optional script). JSON-stringified for the
        # legacy ``Record<string, string>`` SSE contract; the frontend re-
        # parses. Live-only (non-persisted in the kernel) — it arrives only
        # over the live subscription, never on history replay.
        return "session.workflow_progress", _with_message_id(
            {
                "id": _stringify(data.get("id") or ""),
                "run_id": _stringify(data.get("run_id") or ""),
                "state": _stringify(data.get("state") or {}),
            },
            data,
        )

    if kernel_type in (
        "bg_task_started",
        "bg_task_progress",
        "bg_task_updated",
        "bg_task_finished",
    ):
        # Background-task lifecycle (``run_in_background`` Bash & friends).
        # The runtime maps the CLI's task_started / task_progress /
        # task_updated / task_notification pushes 1:1; these arrive DURING a
        # turn and — via the runtime's idle drainer — BETWEEN turns, which is
        # the whole point: a finished background job reaches the waiting
        # session live. Payload keys vary per subtype (started: description /
        # task_type; progress: usage; updated: patch; finished: status /
        # summary / output_file / usage) so the whole payload is forwarded,
        # JSON-stringified per the legacy ``Record<string, string>`` SSE
        # contract; the frontend re-parses what it renders.
        suffix = kernel_type.removeprefix("bg_task_")
        return f"session.bg_task.{suffix}", _with_message_id(
            {
                "task_id": _stringify(data.get("task_id") or ""),
                **{
                    key: _stringify(value)
                    for key, value in data.items()
                    if key not in ("task_id", "message_id") and value is not None
                },
            },
            data,
        )

    return None


def _items_to_frames(items: list[Any]) -> list[SessionEventFrame]:
    """Translate kernel wire events (``EventData``) into legacy-shaped frames.

    Shared by the cursor fetch and the turn-windowed paging helper:
    message-id stamping, kernel → legacy type translation, dropping frames
    the legacy renderer doesn't know about.
    """
    frames: list[SessionEventFrame] = []
    for item in items:
        kernel_data = dict(item.data) if item.data is not None else {}
        kernel_data = _with_row_message_id(kernel_data, item.message_id)
        # Kernel event timestamps are Unix epoch ms. Pass straight through;
        # the frontend formats via new Date(ms).
        ts_ms: int | None = int(item.timestamp) if item.timestamp is not None else None

        translated = _translate_kernel_event(str(item.type), kernel_data)
        if translated is None:
            continue
        legacy_type, legacy_payload = translated
        frames.append(
            SessionEventFrame(
                seq=int(item.seq or 0),
                event_type=legacy_type,
                payload=legacy_payload,
                timestamp=ts_ms,
                event_uid=getattr(item, "event_uid", None),
            )
        )
    return frames


@dataclass(frozen=True)
class TurnWindow:
    """One page of events sliced on whole-turn boundaries.

    A "turn" here = one ``user_message`` row plus every event that follows
    it until the next ``user_message`` (or session end). The frontend
    paginates upward through history one turn-window at a time, so each
    response must start on a ``user_message`` boundary — never mid-turn.

    ``has_more`` tells the frontend whether there is at least one more
    user_message strictly older than the earliest event in this window.
    Without it the renderer would have to issue a probe call to detect
    the end of history.
    """

    items: list[SessionEventFrame]
    has_more: bool


# The kernel's GET events route caps ``limit`` at 1000 (FastAPI Query
# le=1000). Page under that so callers can ask for more without tripping
# the cap — which the in-process client silently dodged (it called the
# route function directly, skipping Query validation) but the HTTP
# transport rightly rejects.
_EVENTS_PAGE = 1000


async def list_events_after(
    session_id: str,
    *,
    user_id: str,
    after_seq: int = 0,
    limit: int = 200,
) -> list[SessionEventFrame]:
    """Return the session's events with ``seq > after_seq``, translated.

    Pages in chunks of ``_EVENTS_PAGE`` so a request larger than the
    kernel's per-call cap returns the full set (not a silently truncated
    first page) over both transports.
    """
    items: list = []
    cursor = after_seq
    while len(items) < limit:
        want = min(_EVENTS_PAGE, limit - len(items))
        page = await _history_reader().get_events(user_id, session_id, after_seq=cursor, limit=want)
        if not page:
            break
        items.extend(page)
        last_seq = page[-1].seq
        if last_seq is None or len(page) < want:
            break  # drained (or no advanceable cursor — persisted events
            # always carry a seq, but guard against a non-advancing loop)
        cursor = last_seq
    return _items_to_frames(items)


async def list_events_window(
    session_id: str,
    *,
    user_id: str,
    before_seq: int | None = None,
    turn_limit: int = 20,
) -> TurnWindow:
    """Return a turn-aligned window of events ending strictly before ``before_seq``.

    Walks the events table backward from ``before_seq`` (or the end of
    the session when ``None``), picks the most recent ``turn_limit``
    ``user_message`` rows, and returns every event with id in
    [min(those_user_msg_ids), before_seq). Result is ordered ascending,
    so the frontend can prepend it directly without re-sort.

    No event cap: the requested turns are returned in full. Tool-heavy
    sessions can produce thousands of events per turn but the response
    is still bounded by ``turn_limit`` at the user's chosen granularity.
    The earlier event-count safety belt silently dropped recent turns
    when a single turn happened to be larger than the cap.

    ``has_more`` is true iff at least one ``user_message`` row exists
    with id strictly less than the earliest seq we returned. The
    frontend uses ``items[0].seq`` as the cursor for the next call.
    """
    if turn_limit <= 0:
        return TurnWindow(items=[], has_more=False)

    window = await _history_reader().get_events_window(
        user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
    )
    return TurnWindow(items=_items_to_frames(window.items), has_more=window.has_more)


async def _follow_session_kernel(user_id: str, session_id: str, queue: asyncio.Queue[Any]) -> None:
    """Drain the session's CURRENT kernel until it stops being the current one.

    A subscription is not self-correcting: ``subscribe_session_events_existing``
    resolves the kernel ONCE, the kernel's ``events/stream`` is an unbounded
    loop, and the host's HTTP client sets no read timeout — so the iterator only
    ends when that sandbox dies. Under per-turn chat allocation the session is
    handed to a NEW instance between turns while the previous one stays RUNNING
    (it is reclaimed by its TTL, not stopped at swap time — stopping it would
    kill the previous turn's tail work, which is exactly what the post-turn
    bg-linger clamp protects). The old subscription therefore never errors: it
    just goes silent forever while the new instance streams to nobody, and every
    live-only frame — ``text_delta`` and the rest, which the DB sink drops — is
    lost for the rest of the connection. What the client still gets is the 2s
    durable backfill, i.e. persisted events in batches and no token streaming.

    Racing the drain against an identity watch is what turns "subscribed" back
    into "subscribed to the RIGHT kernel". Returning hands control to the
    caller's retry loop, which re-peeks and binds to the new instance.
    """
    bound = await kernel_client.current_kernel_id(user_id, session_id)
    if bound is None:
        return  # no live kernel — caller backs off and re-peeks

    async def _drain() -> None:
        async for item in kernel_client.subscribe_session_events_existing(user_id, session_id):
            await queue.put(item)

    async def _watch_for_rebind() -> None:
        while True:
            await asyncio.sleep(KERNEL_REBIND_POLL_SECONDS)
            if await kernel_client.current_kernel_id(user_id, session_id) != bound:
                return

    drain = asyncio.create_task(_drain(), name=f"sse-drain-{session_id}")
    watch = asyncio.create_task(_watch_for_rebind(), name=f"sse-rebind-{session_id}")
    try:
        done, _pending = await asyncio.wait({drain, watch}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # surface a drain failure to the caller's backoff
    finally:
        for task in (drain, watch):
            task.cancel()
        await asyncio.gather(drain, watch, return_exceptions=True)


async def iter_events_sse(
    session_id: str,
    user_id: str,
    *,
    after_seq: int = 0,
    is_disconnected: callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield ``EventSourceResponse``-shaped dicts (``{"data": ...}``) forever.

    Live events arrive through the kernel seam's session subscription
    (``subscribe_session_events``) — including ``text_delta`` which is
    never persisted to the DB. When the session is idle or on reconnect,
    falls back to DB polling so historical events are always available.

    The caller is expected to wrap this with ``EventSourceResponse``.

    Two INDEPENDENT seq spaces meet in this merge (the kernel store is
    LOCAL-authority): history reads carry the DURABLE store's seq, live kernel
    frames carry the kernel's LOCAL seq. The two must never be compared, so:

    - ``history_cursor`` lives purely in durable-seq space — seeded from the
      client's ``after_seq``, advanced ONLY by history-sourced frames, and used
      ONLY as ``after_seq`` for durable backfill reads (and heartbeats).
    - live/history dedup keys on the store-independent ``event_uid`` via one
      shared ``_SeenUids`` covering both directions. A live frame without a
      uid (live-only deltas; legacy kernels) always passes through.
    """
    history_cursor = after_seq
    seen_uids = _SeenUids()
    last_emit = asyncio.get_event_loop().time()

    # First, drain any DB events we missed (replay on reconnect).
    # ``shielded``: a client disconnect cancels this generator; landing that
    # cancellation inside an in-flight DB read would tear the pooled
    # connection down mid-checkin (see ``infra.sse.shielded``).
    owner_id = user_id
    frames = await shielded(
        list_events_after(session_id, user_id=owner_id, after_seq=history_cursor)
    )
    for frame in frames:
        history_cursor = frame.seq
        if frame.event_uid is not None and not seen_uids.add(frame.event_uid):
            continue  # already delivered (uid seen)
        yield {"event": frame.event_type, "data": frame.to_sse_data()}
        last_emit = asyncio.get_event_loop().time()

    # Subscribe to the kernel's live stream. A pump task moves frames into
    # a local queue so the merge loop below can use timeouts without
    # cancelling (and thereby closing) the subscription iterator.
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=4096)

    async def _pump() -> None:
        # Live deltas always come from the kernel's live bus (never persisted).
        # PEEK, never provision: opening a (historical) conversation must not
        # spin up a sandbox just to look at it. And RE-peek forever: when a
        # later run_turn starts the session's kernel (possibly on another
        # replica — peek reads the shared instance registry), the tap attaches
        # within ~DB_BACKFILL_INTERVAL_SECONDS; without the retry, live-only
        # deltas (text_delta & co) would be lost for this already-open stream.
        # A dead/unreachable sandbox degrades to history-only (the poll loop
        # below serves persisted events from the DataService).
        while True:
            try:
                await _follow_session_kernel(owner_id, session_id, queue)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — unreachable sandbox → retry later
                logger.debug(
                    "live event subscription unavailable; serving history only", exc_info=True
                )
            # No live kernel for this session's scope (or the subscription
            # ended — e.g. its sandbox was stopped): wait, then re-peek.
            await asyncio.sleep(DB_BACKFILL_INTERVAL_SECONDS)

    pump_task = asyncio.create_task(_pump(), name=f"sse-pump-{session_id}")
    # 0.0 → 连接后的第一个空闲 tick 立即回读一次(订阅竞态窗口)。
    last_db_poll = 0.0
    try:
        while True:
            if is_disconnected is not None and is_disconnected():
                break

            # Try to read from the live queue first (real-time path).
            try:
                event = await asyncio.wait_for(queue.get(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                event = None

            if event is None:
                # Queue timeout. Poll DB for any events we might have
                # missed (covers the subscribe/backfill race), then
                # heartbeat if idle. Throttled: the first idle tick reads
                # immediately, after that at most every
                # ``DB_BACKFILL_INTERVAL_SECONDS`` while the live
                # subscription stays the real-time path.
                now = asyncio.get_event_loop().time()
                if now - last_db_poll < DB_BACKFILL_INTERVAL_SECONDS:
                    if now - last_emit >= IDLE_HEARTBEAT_SECONDS:
                        # Heartbeat ``seq`` is the HISTORY (durable) cursor —
                        # the only seq-space the client may echo back as
                        # ``after_seq`` on reconnect. Never a live/local seq.
                        yield {"event": "heartbeat", "data": json.dumps({"seq": history_cursor})}
                        last_emit = now
                    continue
                last_db_poll = now
                db_frames = await shielded(
                    list_events_after(session_id, user_id=owner_id, after_seq=history_cursor)
                )
                for frame in db_frames:
                    # History frames advance the durable cursor even when the
                    # uid was already delivered live — the cursor tracks how
                    # far the durable log has been READ, not what was emitted.
                    history_cursor = frame.seq
                    if frame.event_uid is not None and not seen_uids.add(frame.event_uid):
                        continue  # already delivered live (uid dedup)
                    yield {"event": frame.event_type, "data": frame.to_sse_data()}
                    last_emit = asyncio.get_event_loop().time()

                if asyncio.get_event_loop().time() - last_emit >= IDLE_HEARTBEAT_SECONDS:
                    # HISTORY cursor only (see the heartbeat comment above).
                    yield {"event": "heartbeat", "data": json.dumps({"seq": history_cursor})}
                    last_emit = asyncio.get_event_loop().time()
                continue

            # Live event from the subscription — translate and yield.
            # Live ``seq`` is the KERNEL-LOCAL counter (LOCAL-authority store)
            # and is NEVER compared against — or written into — the durable
            # ``history_cursor``. Dedup keys on ``event_uid`` instead: skip a
            # uid the backfill (or an earlier live copy) already delivered,
            # and remember it so a later durable backfill of the same event
            # is skipped too. Uid-less frames (live-only deltas, legacy
            # kernels) always flow — deltas were never deduped.
            uid = getattr(event, "event_uid", None)
            if uid is not None and not seen_uids.add(uid):
                continue
            translated = _translate_kernel_event(event.type, event.data)
            if translated is not None:
                legacy_type, legacy_payload = translated
                frame = SessionEventFrame(
                    seq=event.seq if event.seq is not None else 0,
                    event_type=legacy_type,
                    payload=legacy_payload,
                    timestamp=event.timestamp,  # Unix epoch ms (UTC)
                    event_uid=uid,
                )
                yield {"event": frame.event_type, "data": frame.to_sse_data()}
                last_emit = asyncio.get_event_loop().time()
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# User-level control plane (the always-on multiplexed stream)
#
# Carries ONLY low-frequency lifecycle events across ALL of one owner's
# sessions — never token deltas — so a user with a task lead + N members
# streaming at once does not multiplex M token firehoses onto one connection.
#
# Delivery mirrors the per-session ``iter_events_sse``: the owner's live
# cross-session tap (``subscribe_all_events_for`` — routed to that owner's
# kernel, already user-scoped) is the PRIMARY path, so an idle stream parks on
# the queue and costs ~0 DB queries. A throttled durable backfill is the
# correctness FLOOR (initial catch-up + covering the drop-tolerant tap's rare
# overflow), NOT a per-second poll — the earlier pure-1s-poll made every
# always-on connection issue 1 query/sec/user even when idle (a real SaaS
# query-rate cost). Each backfill is a discrete open→read→close (no pooled DB
# session held — §9.2). The client still polls nothing.
# ---------------------------------------------------------------------------

# The lifecycle set the control plane reads. ``user_message`` brackets a run's
# start (status just flipped to "running"); ``session_idle`` / ``session_error``
# bracket its end; ``session_update`` carries interim status. Everything else —
# deltas, tool calls, assistant text — stays on the per-session data plane.
CONTROL_LIFECYCLE_TYPES: tuple[str, ...] = (
    "user_message",
    "session_idle",
    "session_error",
    "session_update",
)

# Backfill FLOOR cadence for the control plane. The live tap is the primary
# path; this throttled durable re-read only covers the connect race + the rare
# tap overflow, so it can be generous (idle ⇒ ~0.2 queries/sec/user, vs the
# per-second poll it replaced). Lifecycle latency on the floor-only path (dead
# kernel) is a few seconds — fine for badges/lists.
CONTROL_BACKFILL_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class UserEventFrame:
    """One control-plane lifecycle frame, multiplexed across a user's sessions.

    Unlike :class:`SessionEventFrame` this carries ``session_id`` — the stream
    is user-scoped, so every frame names the run it belongs to.
    """

    seq: int
    event_type: str
    session_id: str
    payload: dict[str, str]
    timestamp: int | None
    # Store-independent identity (see ``_SeenUids``). ``None`` on live-only
    # frames and legacy rows persisted before uid minting.
    event_uid: str | None = None

    def to_sse_data(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "event_type": self.event_type,
                "session_id": self.session_id,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "event_uid": self.event_uid,
            },
            default=str,
        )


def _translate_control_event(
    kernel_type: str, data: dict[str, Any]
) -> tuple[str, dict[str, str]] | None:
    """Lean lifecycle projection for the control plane — NO prompt text, NO
    deltas. Returns ``(event_type, payload)`` or ``None`` to drop.

    Wire types the client reduces into running/finished lists:
      - ``user_message``   → ``run.started``   (text-free start marker)
      - ``session_idle``   → ``run.finished``  {status: idle, stop_reason}
      - ``session_error``  → ``run.finished``  {status: failed, message}
      - ``session_update`` → ``run.status``    {status}
    """
    d = data or {}
    if kernel_type == "user_message":
        return "run.started", {}
    if kernel_type == "session_idle":
        return "run.finished", {
            "status": "idle",
            "stop_reason": _stringify(d.get("stop_reason") or ""),
        }
    if kernel_type == "session_error":
        return "run.finished", {
            "status": "failed",
            "message": _stringify(d.get("message") or d.get("category") or "agent run failed"),
        }
    if kernel_type == "session_update":
        return "run.status", {"status": _stringify(d.get("status") or "")}
    return None


async def list_user_events_after(
    user_id: str,
    *,
    after_seq: int = 0,
    limit: int = 200,
) -> list[UserEventFrame]:
    """Return one owner's lifecycle events with ``seq > after_seq``, translated
    to lean control-plane frames. Pages under the kernel's per-call cap."""
    items: list[Any] = []
    cursor = after_seq
    while len(items) < limit:
        want = min(_EVENTS_PAGE, limit - len(items))
        page = await _history_reader().get_events_after_for_user(
            user_id, after_seq=cursor, types=CONTROL_LIFECYCLE_TYPES, limit=want
        )
        if not page:
            break
        items.extend(page)
        last_seq = page[-1].seq
        if last_seq is None or len(page) < want:
            break
        cursor = last_seq

    frames: list[UserEventFrame] = []
    for item in items:
        translated = _translate_control_event(
            str(item.type), dict(item.data) if item.data is not None else {}
        )
        if translated is None:
            continue
        event_type, payload = translated
        frames.append(
            UserEventFrame(
                seq=int(item.seq or 0),
                event_type=event_type,
                session_id=str(getattr(item, "session_id", "") or ""),
                payload=payload,
                timestamp=int(item.timestamp) if item.timestamp is not None else None,
                event_uid=getattr(item, "event_uid", None),
            )
        )
    return frames


def _control_frame_from_live(event: Any) -> UserEventFrame | None:
    """Translate a live ``EventData`` (from the owner's cross-session tap) into
    a lean control-plane frame, or ``None`` to drop (non-lifecycle types)."""
    if str(event.type) not in CONTROL_LIFECYCLE_TYPES:
        return None
    translated = _translate_control_event(
        str(event.type), dict(event.data) if event.data is not None else {}
    )
    if translated is None:
        return None
    event_type, payload = translated
    return UserEventFrame(
        seq=int(event.seq) if event.seq is not None else 0,
        event_type=event_type,
        session_id=str(getattr(event, "session_id", "") or ""),
        payload=payload,
        timestamp=event.timestamp,
        event_uid=getattr(event, "event_uid", None),
    )


async def iter_user_events_sse(
    user_id: str,
    *,
    after_seq: int = 0,
    is_disconnected: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield ``EventSourceResponse``-shaped control-plane frames forever.

    One always-on connection carrying ALL of ``user_id``'s lifecycle events
    (across every session), multiplexed and projected text-free. Mirrors
    ``iter_events_sse``: backfill the durable log first (replay on reconnect),
    then follow the owner's live cross-session tap
    (``kernel_client.subscribe_all_events_for``) as the primary path, with a
    throttled durable backfill as the correctness floor. ``shielded`` keeps a
    client disconnect from tearing a pooled connection down mid-read. The caller
    wraps this with ``EventSourceResponse``.

    Cursor discipline mirrors ``iter_events_sse``: ``history_cursor`` is
    durable-seq space only (advanced by backfill frames, used as ``after_seq``
    for backfill reads and heartbeats); the live tap's kernel-LOCAL seqs never
    touch it — live/history dedup keys on ``event_uid`` via one shared
    ``_SeenUids`` covering both directions.
    """
    history_cursor = after_seq
    seen_uids = _SeenUids()
    last_emit = asyncio.get_event_loop().time()

    # Replay anything the client missed before the tap attaches.
    for frame in await shielded(list_user_events_after(user_id, after_seq=history_cursor)):
        history_cursor = frame.seq
        if frame.event_uid is not None and not seen_uids.add(frame.event_uid):
            continue  # already delivered (uid seen)
        yield {"event": frame.event_type, "data": frame.to_sse_data()}
        last_emit = asyncio.get_event_loop().time()

    # Live cross-session tap for this owner. A pump task moves frames into a
    # local queue so the merge loop can use timeouts without cancelling (and
    # thereby closing) the subscription iterator.
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=4096)

    async def _pump() -> None:
        # In remote mode the owner's sandbox kernel may be GONE — the tap then
        # yields nothing / fails and we degrade to the durable backfill floor.
        # Re-peek forever (subscribe_all_events_for routes via peek — never
        # provisions): a kernel that comes up later gets its tap attached
        # within ~CONTROL_BACKFILL_INTERVAL_SECONDS instead of never.
        # Lifecycle-only at the SOURCE: without the allowlist the owner's
        # kernel ships every token delta across the wire (lead + N members =
        # M firehoses) just for _control_frame_from_live to discard them.
        while True:
            try:
                async for item in kernel_client.subscribe_all_events_for(
                    user_id, types=CONTROL_LIFECYCLE_TYPES
                ):
                    await queue.put(item)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — unreachable kernel → floor-only
                logger.debug(
                    "user live tap unavailable; serving backfill floor only", exc_info=True
                )
            await asyncio.sleep(CONTROL_BACKFILL_INTERVAL_SECONDS)

    pump_task = asyncio.create_task(_pump(), name=f"user-sse-pump-{user_id}")
    last_backfill = asyncio.get_event_loop().time()
    try:
        while True:
            if is_disconnected is not None and is_disconnected():
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                event = None

            if event is None:
                now = asyncio.get_event_loop().time()
                if now - last_backfill >= CONTROL_BACKFILL_INTERVAL_SECONDS:
                    last_backfill = now
                    for frame in await shielded(
                        list_user_events_after(user_id, after_seq=history_cursor)
                    ):
                        # Backfill frames advance the durable cursor even when
                        # the uid was already delivered live — the cursor
                        # tracks how far the durable log has been read.
                        history_cursor = frame.seq
                        if frame.event_uid is not None and not seen_uids.add(frame.event_uid):
                            continue  # already delivered live (uid dedup)
                        yield {"event": frame.event_type, "data": frame.to_sse_data()}
                        last_emit = asyncio.get_event_loop().time()
                if asyncio.get_event_loop().time() - last_emit >= IDLE_HEARTBEAT_SECONDS:
                    # Heartbeat ``seq`` is the HISTORY (durable) cursor — the
                    # only seq-space the client may echo back as ``after_seq``
                    # on reconnect. Never a live/local seq.
                    yield {"event": "heartbeat", "data": json.dumps({"seq": history_cursor})}
                    last_emit = asyncio.get_event_loop().time()
                continue

            # Live frame. ONLY lifecycle events matter to the control plane —
            # translate first, a ``None`` (non-lifecycle) is ignored entirely.
            # The tap's seq is the KERNEL-LOCAL counter (LOCAL-authority
            # store): it never advances — and is never compared against — the
            # durable ``history_cursor``. Dedup keys on ``event_uid`` via the
            # shared seen-set, in both directions (a uid delivered live is
            # skipped when the backfill floor re-reads it, and vice versa).
            # Uid-less live frames (legacy kernels) always pass through.
            live_frame = _control_frame_from_live(event)
            if live_frame is None:
                continue
            if live_frame.event_uid is not None and not seen_uids.add(live_frame.event_uid):
                continue  # already delivered by backfill or an earlier live copy
            yield {"event": live_frame.event_type, "data": live_frame.to_sse_data()}
            last_emit = asyncio.get_event_loop().time()
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


__all__ = [
    "SessionEventFrame",
    "TurnWindow",
    "UserEventFrame",
    "list_events_after",
    "list_events_window",
    "list_user_events_after",
    "iter_events_sse",
    "iter_user_events_sse",
    "POLL_INTERVAL_SECONDS",
    "IDLE_HEARTBEAT_SECONDS",
    "CONTROL_BACKFILL_INTERVAL_SECONDS",
    "CONTROL_LIFECYCLE_TYPES",
]
