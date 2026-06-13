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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.sse import shielded

POLL_INTERVAL_SECONDS = 0.3
IDLE_HEARTBEAT_SECONDS = 15.0


@dataclass(frozen=True)
class SessionEventFrame:
    """One row of ``events`` shaped for the existing SSE wire format."""

    seq: int
    event_type: str
    payload: dict[str, Any]
    timestamp: int | None  # Unix epoch ms (UTC); frontend formats via new Date(ms)

    def to_sse_data(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "event_type": self.event_type,
                "payload": self.payload,
                "timestamp": self.timestamp,
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
      - ``session_error``     → ``run.failed``
      - ``usage_update``      → ``runtime.engine.usage``  (V5+messages: replaces
        the dropped ``cost_update`` event; carries token counts +
        per-model ``model_usage``)
      - ``todo_update``       → ``session.todos.update``  (V5+messages: lets
        the frontend hydrate a Todos panel from live agent planning)
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
        return "message.assistant.delta", _with_message_id(
            {
                "text": _stringify(data.get("text") or data.get("content") or ""),
            },
            data,
        )

    if kernel_type == "thinking":
        # Separate event type so the renderer can show thinking with a dimmed
        # italic style instead of mixing it into the assistant turn body.
        return "message.assistant.thinking", _with_message_id(
            {
                "text": _stringify(data.get("text") or data.get("content") or ""),
            },
            data,
        )

    if kernel_type == "tool_use":
        return "tool.call.started", _with_message_id(
            {
                "id": _stringify(data.get("id") or ""),
                "tool_use_id": _stringify(data.get("id") or ""),
                "name": _stringify(data.get("name") or ""),
                "input": _stringify(data.get("input") or {}),
            },
            data,
        )

    if kernel_type == "tool_result":
        return "tool.call.completed", _with_message_id(
            {
                "id": _stringify(data.get("id") or ""),
                "tool_use_id": _stringify(data.get("id") or ""),
                "content": _stringify(data.get("content") or ""),
                "is_error": _stringify(data.get("is_error", False)),
            },
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
            from valuz_agent.infra.auth_context import get_current_user_id
            from valuz_agent.ports.billing import MeterEvent
            from valuz_agent.ports.extensions import ext

            uid = data.get("user_id") or get_current_user_id()
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
        return "message.assistant.text_delta", _with_message_id(
            {
                "text": _stringify(data.get("text") or data.get("delta") or ""),
            },
            data,
        )

    if kernel_type == "thinking_delta":
        return "message.assistant.thinking_delta", _with_message_id(
            {
                "text": _stringify(data.get("text") or data.get("delta") or ""),
            },
            data,
        )

    if kernel_type == "tool_input_delta":
        # Live, non-persisted: partial tool-call input JSON streaming in as
        # the model emits it. Arrives BEFORE the canonical ``tool_use``
        # (tool.call.started) — the first delta is the frontend's
        # build-the-card signal, so large-file writes show progress instead
        # of a dead wait. ``id`` is the tool_use_id that started/completed
        # also key on; ``name`` lets the card render its real title at once.
        return "tool.call.input_delta", _with_message_id(
            {
                "tool_use_id": _stringify(data.get("id") or ""),
                "name": _stringify(data.get("name") or ""),
                "text": _stringify(data.get("text") or data.get("delta") or ""),
            },
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
    after_seq: int = 0,
    limit: int = 200,
) -> list[SessionEventFrame]:
    """Return the session's events with ``seq > after_seq``, translated.

    Pages in chunks of ``_EVENTS_PAGE`` so a request larger than the
    kernel's per-call cap returns the full set (not a silently truncated
    first page) over both transports.
    """
    user_id = require_current_user_id()
    items: list = []
    cursor = after_seq
    while len(items) < limit:
        want = min(_EVENTS_PAGE, limit - len(items))
        page = await kernel_client.get_events(user_id, session_id, after_seq=cursor, limit=want)
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

    window = await kernel_client.get_events_window(
        require_current_user_id(), session_id, before_seq=before_seq, turn_limit=turn_limit
    )
    return TurnWindow(items=_items_to_frames(window.items), has_more=window.has_more)


async def iter_events_sse(
    session_id: str,
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
    """
    cursor = after_seq
    last_emit = asyncio.get_event_loop().time()

    # First, drain any DB events we missed (replay on reconnect).
    # ``shielded``: a client disconnect cancels this generator; landing that
    # cancellation inside an in-flight DB read would tear the pooled
    # connection down mid-checkin (see ``infra.sse.shielded``).
    frames = await shielded(list_events_after(session_id, after_seq=cursor))
    for frame in frames:
        yield {"event": frame.event_type, "data": frame.to_sse_data()}
        cursor = frame.seq
        last_emit = asyncio.get_event_loop().time()

    # Subscribe to the kernel's live stream. A pump task moves frames into
    # a local queue so the merge loop below can use timeouts without
    # cancelling (and thereby closing) the subscription iterator.
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=4096)

    async def _pump() -> None:
        async for item in kernel_client.subscribe_session_events(
            require_current_user_id(), session_id
        ):
            await queue.put(item)

    pump_task = asyncio.create_task(_pump(), name=f"sse-pump-{session_id}")
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
                # heartbeat if idle.
                db_frames = await shielded(list_events_after(session_id, after_seq=cursor))
                for frame in db_frames:
                    yield {"event": frame.event_type, "data": frame.to_sse_data()}
                    cursor = frame.seq
                    last_emit = asyncio.get_event_loop().time()

                if asyncio.get_event_loop().time() - last_emit >= IDLE_HEARTBEAT_SECONDS:
                    yield {"event": "heartbeat", "data": json.dumps({"seq": cursor})}
                    last_emit = asyncio.get_event_loop().time()
                continue

            # Live event from the subscription — translate and yield.
            # Persisted events arrive with their row id in ``seq`` (see
            # the kernel's PersistThenBroadcastSink): skip anything the
            # cursor already covers (no duplicates against backfill or a
            # previous idle poll) and ADVANCE the cursor so the idle poll
            # below never re-reads what was already delivered live —
            # fixing the legacy double-delivery after busy turns.
            if event.seq is not None:
                # ``cursor`` is int-typed today (after_seq defaults to 0),
                # but guard anyway so a future None-cursor caller degrades
                # to no-dedup instead of a TypeError in the SSE pump.
                if cursor is not None and event.seq <= cursor:
                    continue
                cursor = event.seq
            translated = _translate_kernel_event(event.type, event.data)
            if translated is not None:
                legacy_type, legacy_payload = translated
                frame = SessionEventFrame(
                    seq=event.seq if event.seq is not None else 0,
                    event_type=legacy_type,
                    payload=legacy_payload,
                    timestamp=event.timestamp,  # Unix epoch ms (UTC)
                )
                yield {"event": frame.event_type, "data": frame.to_sse_data()}
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
    "list_events_after",
    "list_events_window",
    "iter_events_sse",
    "POLL_INTERVAL_SECONDS",
    "IDLE_HEARTBEAT_SECONDS",
]
