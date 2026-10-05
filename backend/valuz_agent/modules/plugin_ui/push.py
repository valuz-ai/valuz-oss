"""UI bus pushes: plugin → frontend messages outside a render.

A push names a session — it rides that session's event stream — or none,
and rides the user's global stream (``/v1/stream``). Kinds and the payload
keys the frontend reads (all strings; ``owner`` is stamped by ``ctx.ui``):

- ``invalidate`` ``{site}`` — redraw that site (``*`` or none: every site);
- ``toast`` ``{text, tone?}`` — a toast (tone ``success`` / ``warning`` /
  ``error``, else neutral);
- ``status`` ``{text}`` — the owner's status line under the session's
  composer; an empty ``text`` clears it;
- ``log`` ``{text, tone?}`` — a grey line in the session's transcript, at the
  turn it arrived in;
- ``notice`` ``{tool_use_id, text, tone?}`` — a line under one tool call.

In-process fan-out to the SSE generators of this process; frames are
live-only (never persisted, ``seq`` 0, no ``event_uid``), so they never
disturb the streams' resume cursors. Every push carries ``push_id`` in its
payload (a reserved key): a client with two streams open on one session
receives the push twice and drops the second copy by it.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

KINDS = frozenset({"invalidate", "toast", "status", "log", "notice"})


@dataclass(frozen=True)
class UiPush:
    kind: str
    user_id: str
    session_id: str | None
    payload: dict[str, str]
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    push_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def event_type(self) -> str:
        return f"ui.{self.kind}"

    def wire_payload(self) -> dict[str, str]:
        return {**self.payload, "push_id": self.push_id}


class UiPushHub:
    def __init__(self) -> None:
        self._subscribers: dict[tuple[str, str | None], set[asyncio.Queue[UiPush]]] = {}

    def push(
        self,
        user_id: str,
        kind: str,
        payload: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> int:
        """Deliver to every open stream of this user/session; returns how many."""
        if kind not in KINDS:
            raise ValueError(f"unknown UI push {kind!r}; known: {sorted(KINDS)}")
        message = UiPush(
            kind=kind,
            user_id=user_id,
            session_id=session_id,
            payload={str(k): "" if v is None else str(v) for k, v in (payload or {}).items()},
        )
        delivered = 0
        for queue in list(self._subscribers.get((user_id, session_id), ())):
            try:
                queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                pass
        return delivered

    async def subscribe(self, user_id: str, session_id: str | None) -> AsyncIterator[UiPush]:
        queue: asyncio.Queue[UiPush] = asyncio.Queue(maxsize=256)
        key = (user_id, session_id)
        self._subscribers.setdefault(key, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers = self._subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(key, None)


ui_push_hub = UiPushHub()


__all__ = ["KINDS", "UiPush", "UiPushHub", "ui_push_hub"]
