"""Boot-time session recovery — clear stranded ``running`` rows.

When the host process dies mid-turn (crash, SIGKILL, hard restart),
kernel ``sessions`` rows that were ``status="running"`` at the time
stay that way forever in the DB. The next time a user tries to send a
message, ``SessionService.send_message`` short-circuits with a 409
``Session is already running`` and they're stuck.

This module provides a single function, ``recover_running_sessions``,
called from ``api/app.py``'s startup chain after the kernel has come
back up. Its contract:

- Find every kernel session row whose ``status == "running"``.
- Mark it ``terminated`` with a ``stop_reason`` that records the
  recovery event so SSE replay shows a clear failure rather than a
  silent hang.
- Append a ``session_error`` event into the kernel events table so
  any client that reconnects with ``after_seq`` sees the explanation.

The agent turn itself can't be resumed — its in-process orchestrator
state died with the previous process. Cleanly marking the session
``terminated`` is the most we can do without the user re-issuing the
prompt.

This is conservative on purpose: we never touch ``idle`` / ``created``
rows, so a session legitimately running in another worker (in some
future multi-process deployment) wouldn't be racey-killed. Today the
host is single-process, so any ``running`` row at startup is by
definition stranded.
"""

from __future__ import annotations

import logging

from valuz_agent.adapters import kernel_sync

logger = logging.getLogger(__name__)


def recover_running_sessions(*, batch_limit: int = 500) -> int:
    """Scan for stranded running sessions and finalise them.

    Returns the number of sessions transitioned to terminated. Logs
    each recovery so operators can audit a noisy restart.

    Failures inside the loop are caught per-session — one bad row
    must not stop the rest from being recovered. The function never
    raises; the caller (startup hook) treats it as best-effort.
    """
    try:
        sessions = kernel_sync.list_sessions_sync(limit=batch_limit)
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logger.exception("recover_running_sessions: failed to list kernel sessions")
        return 0

    recovered = 0
    for session in sessions:
        if session.status != "running":
            continue
        try:
            _finalise_one(session)
            recovered += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "recover_running_sessions: failed to finalise session %s",
                session.id,
            )

    if recovered:
        logger.warning(
            "recover_running_sessions: marked %d stranded session(s) as terminated",
            recovered,
        )
    return recovered


def _finalise_one(session: object) -> None:
    """Flip one session from ``running`` to ``terminated`` + emit an event.

    Imports are deferred so this module doesn't load the kernel SDK at
    import time (matters for tests that patch the kernel boundary).
    """
    from src.core.events import Event as KernelEvent  # type: ignore[import-not-found]
    from src.core.types import Session as KS  # type: ignore[import-not-found]  # noqa: N814

    sid = session.id  # type: ignore[attr-defined]

    updated = KS(
        id=sid,
        project_id=session.project_id,  # type: ignore[attr-defined]
        agent_id=session.agent_id,  # type: ignore[attr-defined]
        runtime_provider=getattr(session, "runtime_provider", "claude_agent"),
        model=session.model,  # type: ignore[attr-defined]
        model_provider=getattr(session, "model_provider", None),
        model_settings=getattr(session, "model_settings", None),
        instructions=getattr(session, "instructions", ""),
        skills=session.skills,  # type: ignore[attr-defined]
        mcp_servers=session.mcp_servers,  # type: ignore[attr-defined]
        # V5+1aae940: forward ``permission_mode`` so the status flip
        # doesn't silently demote a default-mode session back to
        # full_access on the next save. ``getattr`` with a safe default
        # keeps the recovery path resilient against rows that pre-date
        # the migration (e.g. mid-upgrade test fixtures).
        permission_mode=getattr(session, "permission_mode", "full_access"),
        status="terminated",
        # We can't synthesise a ``StopReason`` dataclass from outside the
        # turn, so leave whatever the kernel already had (likely None).
        # The session_error event below carries the human-readable cause.
        stop_reason=getattr(session, "stop_reason", None),
        created_at=session.created_at,  # type: ignore[attr-defined]
        metadata=session.metadata,  # type: ignore[attr-defined]
        runtime_session_id=getattr(session, "runtime_session_id", None),
        todos=getattr(session, "todos", None),
    )
    kernel_sync.save_session_sync(updated)

    # SSE replay needs *something* in the events table — otherwise a
    # client reconnecting with after_seq=last would see the stream end
    # cleanly with no explanation. ``session_error`` already has a SSE
    # translation (``run.failed``) the renderer knows how to surface.
    # V5+messages: events table requires a message_id. Anchor onto the
    # latest message for the session; if none exists (session was created
    # but never ran a turn), the error is silently dropped — there's
    # nothing for SSE to replay anyway.
    persisted = kernel_sync.append_session_scoped_event_sync(
        sid,
        KernelEvent(
            type="session_error",
            data={
                "category": "ServerRestart",
                "message": "Agent turn was interrupted by a server restart.",
            },
        ),
    )
    if not persisted:
        logger.info(
            "recover_running_sessions: session %s has no messages; skipping session_error event",
            sid,
        )

    logger.info("Recovered stranded session %s → terminated", sid)


__all__ = ["recover_running_sessions"]
