"""Session turn driver — the one-shot turn-to-idle primitive + turn semantics.

Moved from ``tasks/actor_runner.py``: after the legacy sync-kickoff path was
retired, :func:`run_session_to_idle`'s only caller is the chat ``send`` path
(``sessions/run_orchestrator``) — it is sessions-domain code (attachments,
``SESSION_FINISHED``, billing hook) and lives here now.

Shared turn semantics (:func:`_resolve_turn_status` — elevate an idle-but-
errored turn, detect user interrupts) are consumed by BOTH this driver and the
task actor loop (``tasks/actor_runner.ActorRunner`` imports them from here).
Per-turn capability convergence lives in ``sessions/pre_turn`` and is handed to
``kernel_client.run_turn`` as its ``pre_turn`` hook — it must run AFTER the
turn's kernel is allocated, so no turn-driving primitive may call a refresher
itself.
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.modules.sessions.pre_turn import PreTurnHook, always_on_mcp_hook

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# run_session_to_idle — the shared one-shot turn-to-idle primitive
# (extracted from SessionService._run_agent_background)
# ---------------------------------------------------------------------------


# Stop-reason categories that mean "the turn was cancelled on purpose", not
# "the turn failed". ``user_interrupt`` is what every runtime stamps on an
# explicit user interrupt; ``interrupted`` is a graceful host teardown. Both
# are resumable intent — never a subtask failure (mirrors
# ``recovery.classify_member``).
_INTERRUPT_CATEGORIES = ("user_interrupt", "interrupted")


def _resolve_turn_status(message: Any) -> str:
    """Classify a just-run turn onto the actor-loop ``final_status`` from the
    AUTHORITATIVE ``run_turn`` result — NOT a re-read of the durable session.

    ``message`` is what ``kernel_client.run_turn`` returned: the kernel builds
    it in-process from the live session, so ``message.stop_reason`` is the
    turn's TRUE outcome. Re-reading the session back off the DataService instead
    (as this used to) races a lagging mirror — the durable copy trails the
    kernel's local authority (the RuntimeStore dual-write is best-effort), so
    the read-back can observe a stale ``running`` (or a previous turn's
    ``stop_reason``). Threading that stale status into ``finalize_session``
    (below) authoritatively writes it over the kernel's real ``idle``, stranding
    the session ``running`` forever (409 on the user's next message). Off the
    message, a resolved turn is structurally never ``running`` — the whole class
    of stale-read mis-finalization is gone.

    Classification (identical to the historical session-based rules, applied to
    the authoritative ``stop_reason``):

    - a failure lives ONLY in ``stop_reason`` (an ``Error``): the SDK surfaces
      ``ECONNRESET`` / dropped socket / 5xx as a ``ResultMessage(is_error=True)``,
      so ``run_turn`` returns normally and the kernel leaves the session idle.
      Elevate to ``"terminated"`` — the one valid persistable failure status
      every consumer treats as a failure (loop break, ``_finalize_actor``
      ``ok=False``, lead auto-finalize error branch).
    - an error whose ``category`` is a cancellation (``user_interrupt`` /
      ``interrupted``) is user/host intent, not a failure → ``"interrupted"`` so
      the loop takes the user-stop path (node → rework, ``subtask_stopped``).
      ``message.status`` collapses a host ``interrupted`` into ``errored``, so we
      MUST branch on ``stop_reason.category``, not ``message.status``.
      ``"interrupted"`` never reaches the kernel store — ``_finalize_actor``
      maps it back to ``"idle"``.
    - otherwise (``end_turn`` / budget / no stop_reason) → ``"idle"``.
    """
    if message is None:
        return "idle"
    sr = getattr(message, "stop_reason", None)
    sr_type = sr.get("type") if isinstance(sr, dict) else getattr(sr, "type", None)
    if isinstance(sr_type, str) and "error" in sr_type:
        category = sr.get("category") if isinstance(sr, dict) else getattr(sr, "category", None)
        if category in _INTERRUPT_CATEGORIES:
            return "interrupted"
        return "terminated"
    return "idle"


def _is_error_turn(message: Any, session: Any) -> bool:
    """True when the runtime returned normally but the turn itself failed."""
    if str(getattr(message, "status", "") or "").lower() in {"errored", "failed"}:
        return True
    sr = getattr(session, "stop_reason", None)
    sr_type = sr.get("type") if isinstance(sr, dict) else getattr(sr, "type", None)
    return isinstance(sr_type, str) and "error" in sr_type


async def run_session_to_idle(
    session_id: str,
    content: str,
    event_bus: EventBus,
    on_message: Any | None = None,
    *,
    queued_attachments: list[dict[str, Any]] | None = None,
    pre_turn: PreTurnHook | None = None,
    user_id: str,
) -> str:
    """Drive one agent turn to completion and return the final session status.

    Equivalent to _run_agent_background but awaitable — callers get back the
    final status string (e.g. "idle", "terminated", "budget_exceeded") instead
    of fire-and-forget void.

    Attaches a BroadcastEventSink so SSE clients following the session still
    receive live events. Cleans up the sink on exit (success or failure).

    ``on_message`` is an optional async callback invoked with the kernel
    ``run_turn`` result message after a successful turn — the chat path uses
    it to meter billing; the task member/lead path leaves it ``None`` so its
    behaviour is byte-identical.

    ``queued_attachments`` is set only by the session input-queue drain
    (docs/design/session-input-queue.md): the per-item attachment snapshot
    (``[{source_path, parsed_path}]``) frozen + consumed at enqueue time. When
    provided the pending-set load and the post-turn consume are BOTH skipped —
    the files already left the staging area at enqueue — and the additional
    context announces these snapshotted files instead. ``None`` (the default)
    keeps the existing pending-set behaviour byte-identical for task paths.

    ``pre_turn`` is the capability-convergence hook forwarded to
    ``kernel_client.run_turn`` (which runs it AFTER allocating the turn's
    kernel — see that docstring). Defaults to the credential re-stamp alone,
    which is what the task lead / member paths need; the chat paths pass the
    full ``chat_capability_hook``.

    Used by:
      - dispatch handler via asyncio.create_task (sibling task, not recursive)
      - TaskOrchestrator.kickoff for the lead session background turn
      - sessions/run_orchestrator._run_agent_background (chat path, with meter)
      - sessions/run_orchestrator._drain_queue_after_turn (queue drain)
    """
    from valuz_agent.modules.sessions.events import SESSION_FINISHED

    final_status: str = "idle"
    encountered_error = False
    turn_error: BaseException | None = None

    consumed_attachment_ids: list[str] = []
    try:
        # Dispatch sessions have no pending attachments (they are built
        # fresh by build_member_session), so the pending attachment block
        # is a no-op for subtasks. Keep it for lead sessions started via
        # kickoff which may carry user-staged attachments.
        try:
            from valuz_agent.modules.sessions.attachments import (
                _attachment_specs,
                _load_pending_attachments,
            )
            from valuz_agent.modules.sessions.context_builder import (
                _build_additional_context,
                worktree_name_of,
            )

            if queued_attachments is not None:
                # Queue drain: rebuild minimal (detached) attachment rows from
                # the per-item snapshot so the additional-context announcement
                # works; skip the pending load + the post-turn consume (the
                # rows were consumed at enqueue, see §8.6).
                from valuz_agent.modules.sessions.models import SessionAttachmentRow

                pending_attachments = [
                    SessionAttachmentRow(
                        session_id=session_id,
                        filename=Path(str(a.get("source_path") or "")).name
                        or str(a.get("source_path") or ""),
                        stored_path=str(a.get("source_path") or ""),
                        parsed_path=a.get("parsed_path"),
                        parse_status="ready" if a.get("parsed_path") else "uploaded",
                        source_kind="local",
                    )
                    for a in queued_attachments
                ]
                consumed_attachment_ids = []
                attachment_specs = _attachment_specs(pending_attachments, user_id)
            else:
                pending_attachments = await _load_pending_attachments(session_id, user_id)
                consumed_attachment_ids = [row.id for row in pending_attachments]
                attachment_specs = _attachment_specs(pending_attachments, user_id)
        except Exception:  # noqa: BLE001
            pending_attachments = []
            consumed_attachment_ids = []
            attachment_specs = ()

        loaded_session = await data_reader().get_session(user_id, session_id)
        # Kernel ``run_turn`` persists ``session.status="running"`` to the DB
        # before handing off to the runtime (agent-harness 3e742fc), so the
        # detail fetch returns ``running`` and the frontend live view engages
        # on open. No host-side pre-persist needed. Live events reach SSE
        # followers through the kernel's bus taps — no per-run sink attach.
        project_id = str(
            (((loaded_session.metadata or {}).get("valuz", {}) or {}).get("project_id") or "")
            if loaded_session
            else ""
        )
        try:
            additional_context = await _build_additional_context(
                session_id,
                project_id,
                pending_attachments,
                user_id=user_id,
                worktree=worktree_name_of(loaded_session),
            )
        except Exception:  # noqa: BLE001
            additional_context = ""

        try:
            message = await kernel_client.run_turn(
                user_id,
                session_id,
                content,
                attachments=[
                    {"source_path": source, "parsed_path": parsed}
                    for source, parsed in attachment_specs
                ],
                additional_context=additional_context,
                # Converge capabilities INSIDE run_turn — after the turn's
                # kernel is allocated, so the write reaches the instance that
                # runs the turn instead of only the durable.
                pre_turn=pre_turn or always_on_mcp_hook(session_id, user_id),
            )
            # The turn's outcome comes from the AUTHORITATIVE run_turn result
            # (``message``), never a re-read of the lagging durable session — see
            # ``_resolve_turn_status``. ``after_run`` is still fetched only as a
            # secondary owner/stop_reason signal for the meter + error check
            # (both already prefer ``message``); it does NOT decide the status
            # that gets finalized.
            after_run = await data_reader().get_session(user_id, session_id)
            final_status = _resolve_turn_status(message)
            if _is_error_turn(message, after_run):
                encountered_error = True
            if on_message is not None:
                await on_message(message, after_run)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "run_session_to_idle: agent turn failed for session %s: %s",
                session_id,
                exc,
            )
            final_status = "terminated"
            encountered_error = True
            turn_error = exc
            try:
                await kernel_client.emit_live_event(
                    user_id,
                    session_id,
                    "session_error",
                    {
                        "category": type(exc).__name__,
                        "message": str(exc) or "agent turn failed",
                    },
                )
            except Exception:  # noqa: BLE001
                pass

    except BaseException as exc:  # noqa: BLE001
        logger.exception("run_session_to_idle: unexpected error for session %s", session_id)
        final_status = "terminated"
        encountered_error = True
        turn_error = exc

    # Finalise session metadata + status. Passing ``turn_error`` makes the
    # failure durable: ``_finalize_session`` appends a ``session_error`` event in
    # the same call so the reason survives reload (the ``emit_live_event`` above
    # is live-only and is missed by any client not connected at failure time).
    if is_draining():
        # App shutting down — leave the session ``running`` for boot recovery
        # rather than racing the kernel-store teardown. (The ``KernelUnavailable``
        # catch below is the belt-and-suspenders for a finalize already in
        # flight when draining flips.)
        logger.debug("run_session_to_idle: draining, skipping finalize for %s", session_id)
    else:
        try:
            from valuz_agent.adapters.kernel_client import KernelUnavailableError
            from valuz_agent.modules.sessions.run_orchestrator import _finalize_session

            # ``interrupted`` is a loop-local status (user/host cancelled the
            # turn) — not a persistable kernel status (``FinalizeSessionRequest``
            # accepts ``running|idle|terminated`` only). Mirror ``_finalize_actor``:
            # the session is idle and resumable; the kernel already stamped the
            # cancellation stop_reason itself.
            kernel_status = "idle" if final_status == "interrupted" else final_status
            try:
                await _finalize_session(session_id, content, kernel_status, error=turn_error)
            except KernelUnavailableError:
                # Backend shutting down — kernel store already torn down. Finalize
                # is pointless; boot recovery reconciles this session. Skip quietly
                # rather than logging a shutdown-race traceback.
                logger.debug(
                    "run_session_to_idle: kernel unavailable (shutdown), skipping finalize for %s",
                    session_id,
                )
        except Exception:  # noqa: BLE001
            logger.exception("run_session_to_idle: finalize failed for session %s", session_id)

    # Mark attachments consumed
    if consumed_attachment_ids:
        try:
            from valuz_agent.modules.sessions.attachments import _mark_attachments_consumed

            await _mark_attachments_consumed(consumed_attachment_ids)
        except Exception:  # noqa: BLE001
            pass

    event_bus.publish(
        SESSION_FINISHED,
        session_id=session_id,
        status="failed" if encountered_error else final_status,
    )

    return final_status
