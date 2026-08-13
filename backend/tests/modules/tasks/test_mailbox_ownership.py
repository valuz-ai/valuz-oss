"""What the in-memory mailbox still guarantees, now that it owns nothing.

Ownership of a session — who runs it, which incarnation, and whether that
right has been revoked — moved to the execution lease
(``modules/tasks/lease.py``). The two tests that used to live here covered
``claim`` / ``release``: a stale loop's teardown stealing the box a resumed
loop was reading from, and a non-owning ``register`` not disturbing the owner.
Neither method exists, and neither can the bug — nothing pops a box on the way
out any more.

What remains is the queue's own contract.
"""

from __future__ import annotations

import asyncio

from valuz_agent.modules.tasks.mailbox import MailboxRegistry


def test_keyerror_from_get_when_box_dropped() -> None:
    async def _run() -> None:
        reg = MailboxRegistry()
        reg.register("s1")
        reg.unregister("s1")  # external drop
        try:
            await reg.get("s1", timeout=0.01)
        except KeyError:
            return
        raise AssertionError("get on a dropped box must raise KeyError")

    asyncio.run(_run())
