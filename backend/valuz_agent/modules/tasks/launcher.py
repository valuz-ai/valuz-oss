"""The ONE way a task actor comes to life.

Every path that starts an actor — kickoff, commit, dispatch, recovery — used
to hand-roll the same ceremony with local variations: create the kernel
session under the task's sandbox scope, index it, register mailboxes, seed the
live-member registry, spawn the loop. Four copies of the module's most
race-sensitive sequence; the spawn/shutdown race lived in exactly one of them.
These two primitives are now the only spelling.

:func:`create_task_session` — the awaitable half (kernel + index).
:func:`spawn_actor` — the SYNCHRONOUS half. A concurrent
``broadcast_shutdown`` drains the live-member set in one pop, so nothing may
yield between "the member is registered" and "its loop is spawned"; inside a
plain ``def``, ``await`` is a SyntaxError, so the compiler enforces the rule on
every edit. Work that must await belongs before the call, not inside it.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Literal

from valuz_agent.adapters import kernel_client
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.lease import TaskLease
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.ports.sandbox_allocator import SandboxScope

logger = logging.getLogger(__name__)


async def create_task_session(
    user_id: str,
    session: Any,
    *,
    task_id: str,
    project_id: str,
    kind: str,
) -> None:
    """Create the kernel session under the task's sandbox scope and index it.

    One task = ONE sandbox: lead and members share ``task:{task_id}`` so member
    manifests hand off to the lead through the shared filesystem.
    """
    await kernel_client.create_session(
        user_id, session, scope=SandboxScope(kind="task", id=task_id)
    )
    await project_index.record(
        project_id, session.id, kind=kind, origin="task", user_id=user_id
    )


def spawn_actor(
    actor: ActorRunner,
    *,
    session_id: str,
    prompt: str,
    role: Literal["lead", "subtask"],
    task_id: str,
    project_id: str,
    user_id: str,
    registry: LiveMemberRegistry | None = None,
    dispatch_epoch: float | None = None,
    lead_session_id: str | None = None,
    lease: TaskLease | None = None,
) -> None:
    """Register and start one actor loop — ATOMICALLY (plain ``def``, on purpose).

    ``registry`` + ``dispatch_epoch``: members only. Dispatch passes the epoch
    (manifest mtime attribution under the shared cwd); recovery passes None —
    a resumed member's artifacts predate the respawn, so attribution restarts
    from zero. The Step-1 invariant (seed the registry BEFORE the loop spawns)
    holds on both paths because both are this function.

    ``lead_session_id``: registering the lead's inbox here (idempotent)
    guarantees a member's ``member_done`` can never land on an unregistered
    inbox and vanish — even when the lead was not started via async kickoff.

    ``lease``: leads only, and only from a caller that ALREADY holds the task's
    execution lease (recovery/resume, which must own the task before it
    respawns members). The loop then adopts it instead of acquiring — acquiring
    again would bump the fence and evict the very caller that spawned it.
    Everyone else passes None and the loop acquires for itself.
    """
    if lead_session_id:
        mailbox_registry.register(lead_session_id)
    if registry is not None:
        if dispatch_epoch is not None:
            registry.add_member(task_id, session_id, dispatch_epoch=dispatch_epoch)
        else:
            registry.add_member(task_id, session_id)
    # Eager CLAIM: the box exists before the loop's first tick (a shutdown
    # racing ahead is queued, not dropped) AND any stale prior loop's pending
    # release is invalidated NOW — not at the new loop's first tick — so it
    # cannot pop the box recovery is about to seed with member_done results.
    # (run_actor_loop claims again for its own release token.)
    mailbox_registry.claim(session_id)
    loop_task = asyncio.create_task(
        actor.run_actor_loop(
            session_id=session_id,
            initial_prompt=prompt,
            role=role,
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            lease=lease,
        )
    )
    loop_task.add_done_callback(
        functools.partial(_log_actor_exit, session_id=session_id, task_id=task_id, role=role)
    )


def _log_actor_exit(
    task: asyncio.Task[None], *, session_id: str, task_id: str, role: str
) -> None:
    """Report an actor loop that died on an unhandled exception, AT the moment
    it dies and WITH its identity.

    Without this the only report is asyncio's ``Task exception was never
    retrieved``, emitted whenever the task is garbage-collected — detached from
    the session and task it belonged to, and often long after the fact. An
    escaped exception is not cosmetic: the loop's ``finally`` settles the run
    row to ``completed``, which makes both lead-side backstops
    (``_heartbeat_pending`` / ``_probe_pending_members``, both filtering on
    ``status == "active"``) treat the member as never-dispatched, and the lead
    then burns its full await window with no diagnosis to show for it.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "actor loop for %s session %s (task %s) died on an unhandled exception",
            role,
            session_id,
            task_id,
            exc_info=exc,
        )


__all__ = ["create_task_session", "spawn_actor"]
