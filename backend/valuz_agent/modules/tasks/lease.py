"""Per-actor view of the generic execution lease — the right to run a session.

The mechanism lives in ``infra/execution_lease.py`` and is not task-specific
(session queue drains and the polling scheduler are the other consumers). This
module pins the scope name and gives the task module vocabulary it can read, so
call sites say "who is running this actor" rather than "who holds
``('actor', id)``".

**One lease per actor session, lead and member alike.** It answers four
questions at once, which is why it replaces four separate mechanisms:

* who is running this session — ``holder_id``
* WHICH incarnation of it — ``fence_token``, bumped on every acquisition
* is it still alive — the TTL its holder renews
* stop running — **revoke**, which acquiring already does to the predecessor

That last point is the design. Stopping an actor is not a message you send it;
it is the withdrawal of its right to run (see
docs/design/task-delivery-and-control.md §1). A message can be replayed to the
loop that REPLACED the one you meant to stop — that happened, and was patched
with a process-local claim token that this scope makes unnecessary.

It supersedes ``MailboxRegistry``'s ``claim`` / ``release`` /
``is_claim_current`` / ``is_owned``: those tracked the same thing in one
process's memory, which is where this whole family of bugs started. Taken over
and told to stop are now the same event, handled the same way — leave without
finalizing, because the terminal state is someone else's to write.
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

ACTOR_LEASE_SCOPE = "actor"
# Re-exported so the actor loop does not need to know where the knob lives.
ACTOR_LEASE_RENEW_INTERVAL_S = LEASE_RENEW_INTERVAL_S

ActorLease = ExecutionLease


async def acquire_actor_lease(*, session_id: str, task_id: str) -> ActorLease | None:
    """Take the right to run *session_id*, or ``None`` if a live holder has it.

    Acquiring bumps the fence token, and that IS the revocation of the
    predecessor — there is no separate "stop" to send.

    ``task_id`` rides along as the note so a lease row reads back to its task
    without a join. It is deliberately not part of the key: a session id is
    already globally unique, and folding anything else in would let two holders
    each believe they won.
    """
    return await acquire_lease(scope=ACTOR_LEASE_SCOPE, key=session_id, note=task_id)


async def load_actor_lease_states(session_ids: list[str]) -> dict[str, LeaseState]:
    """Lease rows for *session_ids*. Missing ids are absent — unknown, not dead.

    That distinction is load-bearing during a rolling deploy: a loop on an
    older build holds no actor lease, and reading its absence as "dead" would
    have the watchdog block tasks that are running perfectly well.
    """
    return await load_lease_states(ACTOR_LEASE_SCOPE, session_ids)


async def is_running_elsewhere(session_id: str) -> bool:
    """True when another live process is running this actor. Advisory only."""
    return await is_held_elsewhere(ACTOR_LEASE_SCOPE, session_id)


__all__ = [
    "ACTOR_LEASE_RENEW_INTERVAL_S",
    "ACTOR_LEASE_SCOPE",
    "ActorLease",
    "acquire_actor_lease",
    "is_running_elsewhere",
    "load_actor_lease_states",
]
