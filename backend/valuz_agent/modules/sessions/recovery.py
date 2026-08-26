"""Boot-time session recovery — clear stranded ``running`` rows, liveness-aware.

When a process dies mid-turn (crash, SIGKILL, hard restart), kernel
``sessions`` rows that were ``status="running"`` at the time stay that
way forever in the DB. The next time a user tries to send a message,
``SessionService.send_message`` short-circuits with a 409 ``Session is
already running`` and they're stuck.

``recover_running_sessions`` runs from the host startup chain after the
kernel store is reachable. For every ``running`` row it decides whether
the session is GENUINELY stranded before touching it:

- **Sandbox-liveness check** (scoped deployments): resolve the session's
  sandbox scope (task sessions → ``task:{task_id}``; else
  ``session:{id}``) and ``ext.sandbox_allocator.peek`` it. A live REMOTE
  sandbox (lease with an endpoint) means the turn may still be executing
  there — a booting host replica must NOT clobber it. Skip.
- **No live sandbox** (no allocator / boot-singleton lease / peek miss):
  the session could only have been running in a process that is gone —
  stranded. Reset it via ``kernel_client.reset_stranded_session``, which
  applies the kernel's reset semantics (``src.core.recovery``) directly
  to the DURABLE through the host data plane — no kernel round-trip:
  seal open pendings, stamp ``idle`` + resumable
  ``stop_reason(host_restart)`` (task recovery's ``classify_member`` maps
  ``host_restart`` → resume, so interrupted task members are re-driven),
  and flip running messages to errored so history never renders a
  perpetual spinner.

This replaces the old blind host finalize (``terminated`` — which made
task members unresumable). The kernel's own boot scans still run in
every deployment, but only over the kernel's OWN runtime sqlite — a
session stranded on a dead sandbox exists only in the durable, and only
the host — the party that can check sandbox liveness — may reset it.
"""

from __future__ import annotations

import logging

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader

logger = logging.getLogger(__name__)


async def recover_running_sessions(*, batch_limit: int = 500) -> int:
    """Reset genuinely-stranded running sessions (liveness-checked).

    Returns the number of sessions reset. Logs each recovery so operators
    can audit a noisy restart.

    Failures inside the loop are caught per-session — one bad row must not
    stop the rest from being recovered. The function never raises; the
    caller (startup hook) treats it as best-effort. Idempotent and safe
    under multiple booting host replicas: live-sandbox sessions are
    skipped by every replica, and a second reset of the same session
    no-ops (it is no longer ``running``).
    """
    try:
        # Cross-owner startup sweep — every owner's stranded sessions.
        sessions = await data_reader().list_all_sessions(limit=batch_limit)
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logger.exception("recover_running_sessions: failed to list kernel sessions")
        return 0

    recovered = 0
    for session in sessions:
        if session.status != "running":
            continue
        sid = session.id
        owner = session.user_id
        try:
            if await _sandbox_alive(owner, sid):
                logger.info(
                    "recover_running_sessions: session %s has a live sandbox — skipping", sid
                )
                continue
            if await kernel_client.reset_stranded_session(owner, sid):
                recovered += 1
                logger.info("Recovered stranded session %s → idle (host_restart)", sid)
        except Exception:  # noqa: BLE001
            logger.exception(
                "recover_running_sessions: failed to reset session %s",
                sid,
            )

    if recovered:
        logger.warning(
            "recover_running_sessions: reset %d stranded session(s) to idle (host_restart)",
            recovered,
        )
    return recovered


async def _sandbox_alive(user_id: str, session_id: str) -> bool:
    """Whether ``session_id``'s sandbox scope holds a LIVE remote sandbox.

    Resolution mirrors the exec-path scope routing: task sessions map to their
    task's shared sandbox (``task:{task_id}``), everything else to
    ``session:{id}``. Then ``ext.sandbox_allocator.peek`` — never provision:

    - no allocator bound, or an allocator without ``peek``  → False (single-
      process deployment: this freshly-booted process runs everything, so a
      ``running`` row is by definition stranded — the pre-liveness behaviour);
    - ``peek`` returns no lease                              → False (sandbox
      reclaimed/gone);
    - lease with ``endpoint=None`` (boot-singleton)          → False (the
      session would run in THIS process, which just booted);
    - lease with a real endpoint                             → True (the turn
      may still be executing on that sandbox — do not touch).

    Failures resolve to True (fail-closed: wrongly skipping a stranded session
    delays its recovery to the next boot; wrongly resetting a live one kills
    the user's in-flight turn).
    """
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.sandbox_allocator import SandboxScope

    try:
        alloc = getattr(ext, "sandbox_allocator", None)
        if alloc is None:
            return False
        peek = getattr(alloc, "peek", None)
        if peek is None:
            return False
        # Task sessions execute in their task's shared sandbox — resolve
        # through the tasks module's service-level lookup (same source the
        # exec-path scope resolver uses).
        from valuz_agent.modules.tasks.sandbox_scope import resolve_sandbox_scope

        scope = await resolve_sandbox_scope(user_id, session_id) or SandboxScope(
            kind="session", id=session_id
        )
        import inspect

        if "scope" in inspect.signature(peek).parameters:
            lease = await peek(owner_user_id=user_id, scope=scope)
        else:
            lease = await peek(owner_user_id=user_id)
        return lease is not None and getattr(lease, "endpoint", None) is not None
    except Exception:  # noqa: BLE001 — fail closed (skip the reset this boot)
        logger.exception(
            "recover_running_sessions: liveness probe failed for %s — skipping reset",
            session_id,
        )
        return True


async def resume_queued_drains() -> int:
    """Resume host-driven queue drains after a restart (durable-queue §9 ②).

    The input queue is persisted, so a restart preserves queued follow-ups.
    ``recover_running_sessions`` (① above) already terminated any session that
    was mid-turn at crash time; this step picks up the remaining alive sessions
    that still have ``queued`` items and re-kicks their drain so a long-running
    workflow's follow-ups continue without the user re-issuing them.

    Conservative on purpose:
    - Runs under each session's own owner context (drain reads are owner-scoped).
    - Skips paused queues (an interrupt soft-pause survives restart — the user
      resumes explicitly).
    - Only re-kicks **alive** sessions (``idle`` / ``created``). Items on a
      session terminated by ① stay ``queued`` and drain on the user's next
      interaction rather than auto-running onto a dead turn here.

    Best-effort; never raises (boot must not block).
    """
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.sessions import project_index
    from valuz_agent.modules.sessions.datastore import SessionDatastore
    from valuz_agent.modules.sessions.mappers import _map_kernel_status
    from valuz_agent.modules.sessions.run_orchestrator import schedule_drain

    try:
        async with async_unit_of_work(commit=False) as db:
            pairs = await SessionDatastore(db).list_queued_session_owners()
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logger.exception("resume_queued_drains: failed to list queued sessions")
        return 0

    resumed = 0
    for session_id, owner in pairs:
        token = set_current_user_id(owner)
        try:
            if await project_index.get_queue_paused_at(session_id) is not None:
                continue
            session = await kernel_client.get_session(owner, session_id)
            status = _map_kernel_status(session.status) if session else None
            if status in ("idle", "created"):
                schedule_drain(session_id, event_bus)
                resumed += 1
        except Exception:  # noqa: BLE001 — one bad session must not stop the rest
            logger.exception("resume_queued_drains: failed for session %s", session_id)
        finally:
            reset_current_user_id(token)

    if resumed:
        logger.info("resume_queued_drains: re-kicked %d queued session drain(s)", resumed)
    return resumed


__all__ = ["recover_running_sessions", "resume_queued_drains"]
