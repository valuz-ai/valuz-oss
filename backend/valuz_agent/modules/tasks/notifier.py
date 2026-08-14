"""Waking a parked actor — a doorbell, never a channel.

The durable mailbox (``mailbox_store``) is where messages live. It is read on a
timer, which is correct but slow: a person who just typed an instruction waits
out the poll interval before the lead notices. This is the thing that removes
that wait.

**It carries no payload, and that is the whole safety argument.** A signal that
cannot contain a message cannot lose one. Concretely:

* a lost signal degrades to the poll — the timeout fires and the loop drains
  anyway;
* a duplicate signal is free — draining is idempotent (at-most-once by
  conditional UPDATE);
* a signal for the wrong incarnation is harmless — whoever is running the
  session drains it, and the lease decides who that is;
* the transport dying entirely degrades to today's behaviour, which is why the
  commercial Redis implementation is allowed to be best-effort.

Contrast with the ``shutdown`` message this design removed
(docs/design/task-delivery-and-control.md §1): that carried meaning, so losing
it or delivering it to the wrong reader changed what happened. A doorbell can
do neither.

The default implementation is in-process and is what the desktop runs: one
process, so a local event is the whole truth and there is nothing to configure.
A deployment whose actors span processes injects one that spans them too.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class MailboxNotifier(Protocol):
    """Wake whoever is waiting on a session. Best-effort by contract."""

    async def notify(self, session_id: str) -> None:
        """Ring for *session_id*. Never raises; a failure just means a poll."""
        ...

    async def wait(self, session_id: str, timeout: float) -> None:
        """Return on a ring for *session_id*, or when *timeout* elapses.

        Returns either way — the caller cannot tell the difference and must not
        need to, because it re-reads the durable state regardless.
        """
        ...


class InProcessNotifier:
    """The default: per-wait futures, one set per session.

    Complete on a single-process host and free of dependencies, which is what
    the desktop build needs. On a multi-process host it still helps — producer
    and loop share a process a fraction of the time — and the poll covers the
    rest.

    Futures rather than a long-lived ``asyncio.Event`` on purpose: an Event
    kept in a module-level singleton outlives the loop it was first awaited on,
    and a waiter attached to a dead loop is never woken. A future is created by
    the waiter, on the loop that is actually running, and discarded when it
    fires — so nothing survives to be stale.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Future[None]]] = {}

    async def notify(self, session_id: str) -> None:
        for fut in self._waiters.pop(session_id, set()):
            if not fut.done():
                fut.set_result(None)

    async def wait(self, session_id: str, timeout: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(session_id, set()).add(fut)
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(fut, timeout=timeout)
        finally:
            waiters = self._waiters.get(session_id)
            if waiters is not None:
                waiters.discard(fut)
                if not waiters:
                    self._waiters.pop(session_id, None)


# The bound implementation. Replaced at composition time by a deployment whose
# actors span processes; never replaced with something that carries payloads.
_notifier: MailboxNotifier = InProcessNotifier()


def bind_notifier(notifier: MailboxNotifier) -> None:
    """Install the notifier this deployment uses. Called at the composition root."""
    global _notifier
    _notifier = notifier
    logger.info("mailbox notifier bound: %s", type(notifier).__name__)


async def ring(session_id: str) -> None:
    """Wake whoever is running *session_id*. Never raises.

    Swallowing failures is deliberate and safe: the message is already durable
    by the time anyone rings, so the worst a failure costs is one poll.
    """
    try:
        await _notifier.notify(session_id)
    except Exception:  # noqa: BLE001
        logger.debug("notifier ring failed for %s — the poll will cover it", session_id)


async def wait_for_ring(session_id: str, timeout: float) -> None:
    """Park until someone rings for *session_id*, or *timeout* elapses.

    A failure falls back to sleeping out the timeout rather than returning
    early, so a broken notifier cannot spin the loop.
    """
    try:
        await _notifier.wait(session_id, timeout)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("notifier wait failed for %s — falling back to the poll", session_id)
        await asyncio.sleep(timeout)


__all__ = [
    "InProcessNotifier",
    "MailboxNotifier",
    "bind_notifier",
    "ring",
    "wait_for_ring",
]
