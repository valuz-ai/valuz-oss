"""The ONE way a task actor comes to life.

Every path that starts an actor — kickoff, commit, dispatch, recovery — used
to hand-roll the same ceremony with local variations: create the kernel
session under the task's sandbox scope, index it, spawn the loop. Four copies
of the module's most race-sensitive sequence. These two primitives are now the
only spelling.

:func:`create_task_session` — the awaitable half (kernel + index).
:func:`spawn_actor` — the synchronous half: it only starts the loop.

It stays a plain ``def`` for a weaker reason than it used to. There was a
process-local live-member set to seed here, and a concurrent halt draining that
set between "member exists" and "member is tracked" would lose the member — so
no ``await`` could appear between the two. Live membership is now the run row,
written before this is ever called, so that race is gone. Sync remains because
the caller has already done everything that needs awaiting, and keeping it that
way means a future edit cannot quietly reintroduce a yield point here.
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
from valuz_agent.modules.tasks.lease import ActorLease
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
    lead_session_id: str | None = None,
    lease: ActorLease | None = None,
) -> None:
    """Register and start one actor loop — ATOMICALLY (plain ``def``, on purpose).

    ``lead_session_id``: registering the lead's inbox here (idempotent)
    guarantees a member's ``member_done`` can never land on an unregistered
    inbox and vanish — even when the lead was not started via async kickoff.

    ``lease``: leads only, and only from a caller that ALREADY holds the task's
    execution lease (recovery/resume, which must own the task before it
    respawns members). The loop then adopts it instead of acquiring — acquiring
    again would bump the fence and evict the very caller that spawned it.
    Everyone else passes None and the loop acquires for itself.
    """
    # The box must exist before the loop's first tick so a message racing
    # ahead is queued rather than dropped. It used to be CLAIMED here as well,
    # to invalidate a stale prior loop's pending release before it could pop
    # the box recovery was about to seed. Ownership is the execution lease's
    # job now, and nothing releases a box on the way out, so registering is
    # all that is left to do.
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
