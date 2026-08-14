"""The shape of a message between task actors.

Once a registry of ``asyncio.Queue``s — one per session, the channel actors
talked through. That only worked when sender and receiver shared a process,
which the host stopped doing when it grew past one worker, and every message
that crossed the boundary was dropped in silence.

Delivery moved to ``mailbox_store`` (a durable table) and waking moved to
``notifier`` (a payload-free doorbell). The queue survived a while longer as a
local buffer for the leftovers of a batched drain — until draining one at a
time removed the leftovers, and with them the last reason for a module-level
dict keyed by session that nothing ever emptied.

What is left is the message itself.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Four kinds in three semantic classes — the class decides how the loop
# consumes it:
#   FREE TEXT (becomes the next turn's prompt verbatim):
#     text         — from another actor or the user (follow-up / inject / rework)
#     revise_goal  — user revised task.goal; distinct so await_member_results
#                    preempts on it (a bare DB write never reaches a running
#                    lead — the goal is baked into the session at spawn)
#   STRUCTURED REPORT (rendered first): member_done — manifest, via
#     ActorRunner._format_member_done
# Process-local only (in-memory asyncio.Queue per session; never persisted).
# NO CONTROL SIGNAL. Stopping an actor is a state transition it reads (a
# terminal task, a parked run row), not a message it is sent: this box is
# shared across a session's incarnations and read by two different consumers,
# so a queued stop could be swallowed by the wrong reader or delivered to the
# loop that replaced its target. Both happened. See
# docs/design/task-delivery-and-control.md §1.
InboxKind = Literal["text", "member_done", "revise_goal"]


@dataclass(slots=True)
class InboxMsg:
    """One message waiting in an actor's inbox."""

    kind: InboxKind
    text: str = ""
    from_session: str = ""
    # Who produced this message, for the loop's per-turn log. Diagnostic ONLY —
    # nothing branches on it. A turn is otherwise invisible in the log, so when
    # one appears that nobody expected there is no way to ask where it came
    # from; that cost hours on a production re-run regression.
    origin: str = ""
    # ``Mapping`` not ``dict``: ``member_done`` carries a ``MemberManifest``
    # TypedDict, and dict is invariant — a dict[str, Any] annotation would
    # force every producer to erase the type it just built.
    payload: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["InboxKind", "InboxMsg"]
