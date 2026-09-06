"""Background agent-turn execution + post-turn finalization.

Drives one turn through the shared :func:`ActorRunner.run_session_to_idle`
runtime primitive (ADR-023 AC#5 — ONE turn-to-idle engine for both chat
sessions and task members/leads), adding only the chat-path billing meter via
an ``on_message`` hook. Post-turn finalization (``_finalize_session``) stays
here as the single finalize sink that the runtime primitive imports and calls.
Split out of ``service`` so the god module keeps only the SessionService
surface; the task orchestrator reuses ``_finalize_session`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Mapping
from typing import Any

# Side-effect: puts the kernel on sys.path so ``src.core`` / ``app.*``
# resolve at call time.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.execution_lease import hold_lease, load_lease_states
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.sessions.pre_turn import chat_capability_hook
from valuz_agent.modules.sessions.task_checks import queued_check_input
from valuz_agent.modules.sessions.turn_driver import _INTERRUPT_CATEGORIES, run_session_to_idle
from valuz_agent.ports.capability_policy import TaskCheckConfig
from valuz_agent.ports.message_context import HostRef

logger = logging.getLogger(__name__)

# Sessions with a queue-drain chain currently in flight. Single-flights the
# drain (an idle-kick enqueue must not race the post-turn drain) AND doubles as
# a host-side "busy" signal so ``send_message`` 409s during the brief idle
# windows *between* drained items (see ``is_draining_queue``). In-memory by
# design — the queue itself is durable (DB), the drain task is transient.
_active_drains: set[str] = set()

# Lease namespace for "who is draining this session's input queue".
DRAIN_LEASE_SCOPE = "session-drain"

# session_id → queued-row id the drain is executing RIGHT NOW. Set just before
# the row is marked ``dispatched`` (which drops it from ``list_queued``) and
# cleared when its turn returns — bridging the window where the item is in
# neither the queue list nor the transcript, so ``list_queue`` can keep it
# visible to clients (see docs/design/session-input-queue.md §14.5).
_dispatching_heads: dict[str, str] = {}


def _require_user_id(user_id: str | None) -> str:
    if user_id is None:
        raise ValueError("user_id is required")
    return user_id


async def _resolve_session_owner(session_id: str) -> str | None:
    sessions = await data_reader().list_all_sessions(ids=[session_id], limit=1)
    return sessions[0].user_id if sessions else None


def is_draining_queue(session_id: str) -> bool:
    """True while a queue-drain chain is in flight for this session.

    ``send_message`` treats this like ``status=="running"`` so a new turn can't
    slip into the sub-second idle gap between two drained queue items.
    """
    return session_id in _active_drains


async def is_draining_queue_anywhere(session_id: str) -> bool:
    """Like :func:`is_draining_queue`, but true for a drain in ANY host process.

    The set is per-process, so with several of them the plain check answered
    "not draining" for a session another worker was actively draining — which
    let ``send_message`` slip a turn into the middle of someone else's drain,
    and left the steer path skipping the interrupt that hands the promoted head
    over. The shared lease is the cross-process half of the same signal.

    Local first: a drain we are running ourselves needs no query, so the hot
    ``list_queue`` poll only reaches the database when the answer is not
    already known here.
    """
    if session_id in _active_drains:
        return True
    state = (await load_lease_states(DRAIN_LEASE_SCOPE, [session_id])).get(session_id)
    return state is not None and state.is_live(now_ms())


def get_dispatching_queue_id(session_id: str) -> str | None:
    """Id of the queued row the drain is currently executing, if any.

    The row is already ``dispatched`` (invisible in ``list_queued``) but its
    turn may not have landed a durable user message yet — callers use this to
    keep the item visible across that gap.
    """
    return _dispatching_heads.get(session_id)


# How often the drain re-checks a busy session before dispatching the next
# queued item, and how many polls between "still waiting" log lines.
_BUSY_POLL_SECONDS = 2.0
_BUSY_LOG_EVERY_POLLS = 30  # ≈ once a minute


async def _session_busy(user_id: str, session_id: str) -> bool:
    """True while the session is NOT genuinely done with the previous message.

    "上一条处理完" for the queue gate means BOTH:
    - no turn in flight (``status == "running"`` — covers a turn started by
      another client / any dispatch race), AND
    - no live ``run_in_background`` work from the previous turn
      (``bg_busy_session_ids``). A turn that spawns a background task goes
      kernel-idle the moment its reply streams out, but the user still sees
      "processing" (the bg-task chip) — dispatching the next queued item at
      that instant reads as the queue interrupting the previous message.

    Best-effort on the bg probe: if it fails, treat as not busy — a drain that
    can never dispatch is worse than an occasional early dispatch.
    """
    session = await kernel_client.get_session(user_id, session_id)
    if session is not None and str(getattr(session, "status", "")) == "running":
        return True
    try:
        return session_id in await kernel_client.bg_busy_session_ids()
    except Exception:  # noqa: BLE001
        return False


def _chat_billing_meter(session_id: str, user_id: str | None = None) -> Any:
    """Build the chat-path billing meter callback for a session.

    Shared by the initial turn and every drained queue item so each metered
    turn bills identically.
    """

    async def _meter(message: Any, after_run: Any) -> None:
        if message.input_tokens is not None or message.output_tokens is not None:
            from valuz_agent.ports.billing import MeterEvent
            from valuz_agent.ports.extensions import ext

            uid = (
                getattr(after_run, "user_id", None)
                or (after_run.metadata if after_run else {}).get("owner_user_id")
                or user_id
            )
            try:
                if uid is None:
                    raise LookupError("no owner user_id for billing meter")
                await ext.billing.meter(
                    MeterEvent(
                        user_id=uid,
                        event_type="llm_call",
                        cost_usd=0.0,
                        metadata={
                            "message_id": message.id,
                            "session_id": session_id,
                            "input_tokens": message.input_tokens or 0,
                            "output_tokens": message.output_tokens or 0,
                            "cache_read_tokens": message.cache_read_tokens or 0,
                            "cache_write_tokens": message.cache_write_tokens or 0,
                            "model_usage": message.model_usage,
                        },
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning("Billing meter failed for session %s", session_id)

    return _meter


async def _run_agent_background(
    session_id: str,
    content: str,
    event_bus: EventBus,
    user_id: str | None = None,
    host_ref: HostRef | None = None,
    task_check_config: TaskCheckConfig | None = None,
) -> None:
    """Drive one agent turn in the background, then drain any queued follow-ups.

    Thin wrapper over the shared :func:`run_session_to_idle` runtime primitive
    (ADR-023): the primitive owns the attach-sink → build UserMessage →
    run_turn → read-back-status → finalize → consume-attachments →
    detach/cleanup → publish SESSION_FINISHED shape with the layered failure
    handling that guarantees a session never gets stranded in
    ``status="running"``. This wrapper adds the chat-path billing meter and the
    post-turn queue drain (docs/design/session-input-queue.md).
    """
    owner_user_id = _require_user_id(user_id)
    # A new turn is new activity — float this chat back to the top of the
    # activity feed (project home + 动态) by bumping its index ``updated_at``.
    # Best-effort; a session with no chat index row is a silent no-op.
    from valuz_agent.modules.sessions import project_index

    await project_index.touch_activity(session_id)
    meter = _chat_billing_meter(session_id, user_id=owner_user_id)
    await run_session_to_idle(
        session_id,
        content,
        event_bus,
        on_message=meter,
        pre_turn=chat_capability_hook(
            session_id, owner_user_id, host_ref=host_ref, task_check_config=task_check_config
        ),
        user_id=owner_user_id,
        host_ref=host_ref,
    )
    await _drain_queue_after_turn(
        session_id,
        event_bus,
        on_message=meter,
        user_id=owner_user_id,
    )


async def _drain_queue_after_turn(
    session_id: str,
    event_bus: EventBus,
    on_message: Any | None = None,
    user_id: str | None = None,
    *,
    claimed: bool = False,
) -> None:
    """Run queued follow-up inputs FIFO after a turn finishes (host-driven).

    Per item: bail if the app is shutting down or the queue is paused (an
    interrupt soft-pauses — items stay until an explicit resume); budget
    pre-check (mark ``blocked`` + stop on failure, preserving the 402 path);
    else mark ``dispatched`` and drive it through ``run_session_to_idle`` with
    its frozen attachment snapshot, then loop. Single-flighted via
    ``_active_drains`` so an idle-kick enqueue can't double-run an item.

    ``claimed=True`` means the caller (``schedule_drain``) already put the
    session in ``_active_drains`` — skip the claim, keep the release.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.lifecycle import is_draining
    from valuz_agent.modules.sessions import project_index
    from valuz_agent.modules.sessions.datastore import SessionDatastore
    from valuz_agent.modules.sessions.errors import BudgetExceeded
    from valuz_agent.modules.sessions.events import SESSION_FINISHED

    owner_user_id = _require_user_id(user_id)
    if not claimed:
        if session_id in _active_drains:
            return
        _active_drains.add(session_id)
    busy_polls = 0
    try:
        while True:
            if is_draining():
                return
            if await project_index.get_queue_paused_at(session_id) is not None:
                return

            async with async_unit_of_work(commit=False) as db:
                head = await SessionDatastore(db).peek_next_queued(session_id)
            if head is None:
                return

            # Busy gate: dispatch only when the previous message is GENUINELY
            # done — no turn in flight AND no live background tasks (see
            # ``_session_busy``). Poll rather than exit so the claim stays held
            # (``draining=true`` keeps followers/routing coherent) and every
            # iteration re-checks shutdown / pause / queue mutations, which are
            # the user's escape hatches (Stop soft-pauses; steer interrupts).
            if await _session_busy(owner_user_id, session_id):
                busy_polls += 1
                if busy_polls % _BUSY_LOG_EVERY_POLLS == 0:
                    logger.info(
                        "queue drain for %s waiting on busy session (%.0fs)",
                        session_id,
                        busy_polls * _BUSY_POLL_SECONDS,
                    )
                await asyncio.sleep(_BUSY_POLL_SECONDS)
                continue
            busy_polls = 0

            head_id = head.id
            payload = head.input or {}
            check_config, host_ref = queued_check_input(payload, head_id)
            text = str(payload.get("text") or "")
            attachments = list(payload.get("attachments") or [])

            session = await kernel_client.get_session(owner_user_id, session_id)
            if session is None:
                return

            try:
                from valuz_agent.modules.sessions.service import _enforce_budget

                await _enforce_budget(session, user_id=owner_user_id)
            except BudgetExceeded as exc:
                async with async_unit_of_work() as db:
                    await SessionDatastore(db).mark_queued_status(
                        head_id,
                        "blocked",
                        error_message=getattr(exc, "message_key", None) or str(exc),
                    )
                # Surface the stall: followers refetch the queue on a finish.
                event_bus.publish(SESSION_FINISHED, session_id=session_id, status="idle")
                return

            # Worktree re-entry guard (best-effort in the drain path — a heal
            # failure here should not strand the queue; the turn will surface
            # the real error).
            try:
                from valuz_agent.modules.worktrees.service import worktree_service

                wt_snapshot = (session.metadata.get("valuz") or {}).get("worktree")
                if isinstance(wt_snapshot, dict):
                    await worktree_service.heal_from_snapshot(wt_snapshot)
            except Exception:  # noqa: BLE001
                logger.warning("drain: worktree heal failed for %s", session_id, exc_info=True)

            # Point at the head BEFORE it flips to ``dispatched`` so there is
            # no instant where the item is gone from ``list_queued`` but not
            # yet exposed as the in-flight one.
            _dispatching_heads[session_id] = head_id
            async with async_unit_of_work() as db:
                await SessionDatastore(db).mark_queued_status(head_id, "dispatched")

            try:
                await run_session_to_idle(
                    session_id,
                    text,
                    event_bus,
                    on_message=on_message,
                    queued_attachments=attachments,
                    # A queued follow-up is a chat turn like any other, and it
                    # can run arbitrarily long after the send that enqueued it
                    # — so it needs the same per-turn convergence, not just the
                    # credential re-stamp the default would give it.
                    pre_turn=chat_capability_hook(
                        session_id, owner_user_id, host_ref=host_ref,
                        task_check_config=check_config,
                    ),
                    user_id=owner_user_id,
                    host_ref=host_ref,
                )
            finally:
                _dispatching_heads.pop(session_id, None)
    finally:
        _dispatching_heads.pop(session_id, None)
        _active_drains.discard(session_id)


def schedule_drain(session_id: str, event_bus: EventBus) -> None:
    """Spawn a background queue drain for an idle session (idle-kick / resume).

    Background path: resolve the owner from ``session_id`` before draining; do
    not rely on request ContextVar propagation.
    A no-op if a drain is already in flight for the session.

    Claims ``_active_drains`` SYNCHRONOUSLY (released by the spawned task) so
    the caller's own ``list_queue`` response already reports ``draining=true``.
    The previous late claim (inside the task, after an awaited owner lookup)
    let an idle-kick enqueue answer ``items=[], draining=false`` — the client's
    drain-follower then never armed and the turn ran invisibly until reload.

    ``_active_drains`` single-flights within THIS process only. That was the
    whole guard until now, and it does not hold across processes: ``uvicorn
    --workers N`` (or several replicas) meant every one of them re-kicked the
    same drain at boot from ``resume_queued_drains``, so one queued item ran N
    times — N real turns, N× model spend, N assistant replies the user did not
    ask for. The execution lease below is the cross-process half; the set stays
    as the cheap local short-circuit and as the ``is_draining_queue`` signal.
    """
    if session_id in _active_drains:
        return
    _active_drains.add(session_id)

    async def _spawn() -> None:
        try:
            owner_user_id = await _resolve_session_owner(session_id)
            if not owner_user_id:
                logger.warning("skip queue drain for %s: unknown session owner", session_id)
                return
            async with hold_lease(scope=DRAIN_LEASE_SCOPE, key=session_id) as lease:
                if lease is None:
                    logger.info(
                        "skip queue drain for %s: another process is already draining it",
                        session_id,
                    )
                    return
                meter = _chat_billing_meter(session_id, user_id=owner_user_id)
                await _drain_queue_after_turn(
                    session_id,
                    event_bus,
                    on_message=meter,
                    user_id=owner_user_id,
                    claimed=True,
                )
        finally:
            # ``_drain_queue_after_turn`` releases on its own; this covers the
            # early returns/raises before it runs. discard is idempotent.
            _active_drains.discard(session_id)

    try:
        asyncio.create_task(_spawn())
    except RuntimeError:
        # No running loop (shutdown) — never leak the claim: it gates
        # ``send_message`` 409s and future drain kicks for this session.
        _active_drains.discard(session_id)
        raise


# Strip leading skill-trigger tokens (``/<slug>``) when deriving a
# session title from the user's first message. Composer ships these
# inline as part of the prompt, but they're routing metadata — using
# "/stock-screener 找股票" as the chat title leaks scaffolding into
# the sidebar. Only the *prefix* is stripped; ``/`` mentions later
# in the prose are kept verbatim because they're presumably part of
# the intent. (CN-IME ``、`` is normalized to ``/`` in the Composer
# before send, so we only need to handle the canonical form here.)
_SKILL_PREFIX_RE = re.compile(r"^\s*(?:/[a-zA-Z0-9_-]+\s+)+")


def _derive_session_name(content: str) -> str:
    cleaned = _SKILL_PREFIX_RE.sub("", content)
    return cleaned[:40].replace("\n", " ").strip()


async def _project_conversation_run_result(
    session: Any, owner_user_id: str, session_id: str, error_event: Any
) -> None:
    """Notify (or clear) a NON-task conversation run failure.

    The task-failure projector
    (``notifications/projectors.record_task_failure_notification``) covers
    lead/subtask runs; this is its conversation analog, keyed on the
    session and routed to ``/conversation/{id}``. ``error_event`` is the exact
    ``session_error`` payload finalize writes durably — passing it means we fire
    precisely when a real failure is recorded (raised exception OR terminal
    transport error), never on a clean idle.

    - failure (``error_event`` present) → ingest one ``run_failed`` item;
    - clean turn (``error_event`` None) → resolve any prior ``run_failed`` so a
      recovered conversation doesn't keep the badge lit.

    Task-driven sessions are skipped (they own the ``task_failed`` path).
    Best-effort: the caller wraps this so a ledger hiccup never fails a turn.
    """
    from valuz_agent.modules.decisions.service import is_task_driven

    if is_task_driven(session):
        return

    from valuz_agent.modules.notifications.service import notification_service

    if error_event is None:
        await notification_service.resolve_session_failures(owner_user_id, session_id)
        return
    if str((getattr(error_event, "data", None) or {}).get("category") or "") in (
        _INTERRUPT_CATEGORIES
    ):
        # An interrupted turn (user stop / host teardown / cancelled task) is
        # resumable intent, not a failure — no badge, no OS notification.
        return

    meta = getattr(session, "metadata", None) or {}
    valuz = meta.get("valuz") if isinstance(meta, dict) else None
    valuz = valuz if isinstance(valuz, dict) else {}
    project_id = valuz.get("project_id")
    project_id = project_id if isinstance(project_id, str) and project_id else None
    data = getattr(error_event, "data", None) or {}
    await notification_service.ingest(
        owner_user_id,
        # Unique per failure occurrence (a later failure is a fresh item); a
        # clean turn resolves the whole open set for this session. finalize is
        # called once per turn so there's no re-fire to dedupe against.
        dedup_key=f"e:{session_id}:{uuid.uuid4().hex}",
        kind="run_failed",
        title=str(valuz.get("agent_slug") or valuz.get("name") or ""),
        body=str(data.get("message") or ""),
        route=f"/conversation/{session_id}",
        action="none",
        session_id=session_id,
        project_id=project_id,
        payload={
            "category": str(data.get("category") or ""),
            "session_name": valuz.get("name"),
        },
    )


async def _finalize_session(
    session_id: str,
    content: str,
    final_status: str,
    error: BaseException | None = None,
) -> None:
    """Persist post-turn valuz metadata and the resolved kernel status.

    Split out so both the success and failure paths in ``_run_agent_background``
    can share it. Builds a fresh ``Session`` dataclass because the kernel's
    types are frozen.

    When ``error`` is supplied (the turn raised), a ``session_error`` event is
    appended **durably** as part of the same finalize call — the live
    ``emit_live_event`` only reaches SSE followers connected at the moment of
    failure, so without this the actual reason is lost on reload and the UI
    shows a bare "Run failed". ``stop_reason_*`` mark the terminal state as an
    error rather than a clean idle.
    """
    owner_user_id = await _resolve_session_owner(session_id)
    if not owner_user_id:
        logger.warning("skip finalize for %s: unknown session owner", session_id)
        return
    session = await kernel_client.get_session(owner_user_id, session_id)
    if session is None:
        return

    meta = dict(session.metadata)
    valuz = dict(meta.get("valuz") or {})
    valuz["last_user_message_text"] = content
    if not valuz.get("name"):
        valuz["name"] = content[:40].replace("\n", " ").strip()
    meta["valuz"] = valuz

    from app.schemas import EventPayload, FinalizeSessionRequest

    error_event = None
    stop_reason_type = None
    stop_reason_message = None
    if isinstance(error, asyncio.CancelledError):
        # The turn was interrupted, not failed (``run_session_to_idle`` maps
        # this onto the loop-local ``interrupted`` status). Still record a
        # durable terminal marker — the kernel may not have emitted one when
        # the cancellation cut the turn short, and the client needs a bracket
        # to close the turn on reload — but as an INTERRUPTION category the
        # client renders quietly (no retry / switch-model card), and without
        # stamping the session's stop_reason as an error: the session stays
        # a clean, resumable idle. ``_project_conversation_run_result``
        # skips interruption categories, so no failure notification fires.
        error_event = EventPayload(
            type="session_error",
            data={"category": "interrupted", "message": "turn interrupted"},
        )
    elif error is not None:
        message = str(error) or "agent turn failed"
        error_event = EventPayload(
            type="session_error",
            data={"category": type(error).__name__, "message": message},
        )
        stop_reason_type = "error"
        stop_reason_message = message
    elif final_status == "terminated":
        stop_reason = getattr(session, "stop_reason", None)
        category = None
        message = None
        if isinstance(stop_reason, Mapping):
            category = stop_reason.get("category") or stop_reason.get("type")
            message = stop_reason.get("message")
        else:
            category = getattr(stop_reason, "category", None) or getattr(stop_reason, "type", None)
            message = getattr(stop_reason, "message", None)
        if category is not None or message is not None:
            error_event = EventPayload(
                type="session_error",
                data={
                    "category": str(category or "Error"),
                    "message": str(message or "agent turn failed"),
                },
            )

    await kernel_client.finalize_session(
        owner_user_id,
        session_id,
        FinalizeSessionRequest(
            status=final_status,  # type: ignore[arg-type]
            metadata=meta,
            error_event=error_event,
            stop_reason_type=stop_reason_type,  # type: ignore[arg-type]
            stop_reason_message=stop_reason_message,
        ),
    )

    # Mirror a conversation run failure into the notification ledger (badge + OS
    # notification when the window is backgrounded), or clear a prior one on
    # recovery. Task sessions are handled by the task-failure projector — the
    # helper self-skips them. Best-effort: never let it fail a turn.
    try:
        await _project_conversation_run_result(session, owner_user_id, session_id, error_event)
    except Exception:  # noqa: BLE001
        logger.debug("run-failure notification skipped for %s", session_id, exc_info=True)

    # Arm the idle memory-extraction trigger (memory-system-design §7.1). The
    # scheduler debounces, so it fires once the conversation goes quiet — not per
    # turn — and is a no-op until the runner is wired at boot. Never blocks a turn.
    try:
        from valuz_agent.modules.memory.scheduler import idle_scheduler

        idle_scheduler.notify_turn(session_id, owner_user_id)
    except Exception:  # noqa: BLE001 — memory triggering must never fail a turn
        logger.debug("memory idle trigger skipped for %s", session_id, exc_info=True)
