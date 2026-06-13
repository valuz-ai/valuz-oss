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

from valuz_agent.adapters import kernel_client

logger = logging.getLogger(__name__)


async def recover_running_sessions(*, batch_limit: int = 500) -> int:
    """Scan for stranded running sessions and finalise them.

    Returns the number of sessions transitioned to terminated. Logs
    each recovery so operators can audit a noisy restart.

    Failures inside the loop are caught per-session — one bad row
    must not stop the rest from being recovered. The function never
    raises; the caller (startup hook) treats it as best-effort.
    """
    try:
        # Cross-owner startup sweep — finalise every owner's stranded sessions.
        sessions = await kernel_client.list_all_sessions(limit=batch_limit)
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logger.exception("recover_running_sessions: failed to list kernel sessions")
        return 0

    recovered = 0
    for session in sessions:
        if session.status != "running":
            continue
        try:
            await _finalise_one(session)
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


async def _finalise_one(session: object) -> None:
    """Flip one session from ``running`` to ``terminated`` + emit an event.

    Goes through the kernel client's finalize endpoint, which applies the
    status flip and appends the explanatory ``session_error`` event in one
    supervisor call (the event is anchored onto the session's latest
    message; dropped when the session never ran a turn — there is nothing
    for SSE to replay anyway).
    """
    from app.schemas import (
        EventPayload,
        FinalizeSessionRequest,
    )

    sid = session.id  # type: ignore[attr-defined]
    owner = session.user_id  # type: ignore[attr-defined]

    await kernel_client.finalize_session(
        owner,
        sid,
        FinalizeSessionRequest(
            status="terminated",
            error_event=EventPayload(
                type="session_error",
                data={
                    "category": "ServerRestart",
                    "message": "Agent turn was interrupted by a server restart.",
                },
            ),
        ),
    )
    logger.info("Recovered stranded session %s → terminated", sid)


__all__ = ["recover_running_sessions"]
