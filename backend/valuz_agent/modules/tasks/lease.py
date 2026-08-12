"""Task-scoped view of the generic execution lease.

The mechanism lives in ``infra/execution_lease.py`` — it is not task-specific
(session queue drains and the polling scheduler have the same defect and are
the next consumers). This module only pins the scope name and gives the task
module vocabulary it can read, so call sites say "who drives this task" rather
than "who holds ``('task', id)``".

Why a task needs one at all: the actor loops, the mailbox and the live-member
registry are PROCESS-LOCAL, while the tasks they drive live in a database that
several host processes share. See the infra module's docstring for the failures
that produced.

Scope note — this is a CROSS-process guard. Two loops racing for one session
INSIDE one process stay ``mailbox_registry.claim``'s problem, and the lease
matches its rule (a later acquisition invalidates every earlier token) so the
two agree rather than each believing it won.
"""

from __future__ import annotations

from valuz_agent.infra.execution_lease import (
    LEASE_RENEW_INTERVAL_S,
    ExecutionLease,
    LeaseState,
    acquire_lease,
    is_held_elsewhere,
    load_lease_states,
)

TASK_LEASE_SCOPE = "task"
# Re-exported so the actor loop does not need to know where the knob lives.
TASK_LEASE_RENEW_INTERVAL_S = LEASE_RENEW_INTERVAL_S

TaskLease = ExecutionLease


async def acquire_task_lease(
    *, user_id: str, task_id: str, lead_session_id: str
) -> TaskLease | None:
    """Become the driver of *task_id*, or ``None`` if someone else already is.

    ``user_id`` is deliberately NOT part of the key: a task id is already
    globally unique, and folding an owner into the key would let two owners
    each "hold" the same task. It stays in the signature so call sites read
    like every other owner-scoped task call.
    """
    del user_id  # see docstring
    return await acquire_lease(scope=TASK_LEASE_SCOPE, key=task_id, note=lead_session_id)


async def load_task_lease_states(task_ids: list[str]) -> dict[str, LeaseState]:
    """Lease rows for *task_ids*. Missing ids are absent — unknown, not dead."""
    return await load_lease_states(TASK_LEASE_SCOPE, task_ids)


async def is_driven_elsewhere(task_id: str) -> bool:
    """True when another live process drives this task. Advisory only."""
    return await is_held_elsewhere(TASK_LEASE_SCOPE, task_id)


__all__ = [
    "TASK_LEASE_RENEW_INTERVAL_S",
    "TASK_LEASE_SCOPE",
    "TaskLease",
    "acquire_task_lease",
    "is_driven_elsewhere",
    "load_task_lease_states",
]
