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
disturb the streams' resume cursors. Every push is also handed to the bound
``ui_push_transport`` (OSS: none), which carries it to the other backend
processes; a push that comes back from the transport is delivered here only
if another process made it. Every push carries ``push_id`` in its
payload (a reserved key): a client with two streams open on one session
receives the push twice and drops the second copy by it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

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

    def to_wire(self, origin: str) -> dict[str, Any]:
        return {
            "origin": origin,
            "kind": self.kind,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "push_id": self.push_id,
        }

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> UiPush:
        payload = data.get("payload")
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            session_id=None if data.get("session_id") is None else str(data["session_id"]),
            payload={str(k): str(v) for k, v in dict(payload or {}).items()},
            timestamp=int(data.get("timestamp") or 0),
            push_id=str(data["push_id"]),
        )


logger = logging.getLogger(__name__)


class UiPushHub:
    def __init__(self) -> None:
        self._subscribers: dict[tuple[str, str | None], set[asyncio.Queue[UiPush]]] = {}
        #: This process, so a push the transport echoes back is not delivered twice.
        self.origin = uuid.uuid4().hex
        self._publishing: set[asyncio.Task[None]] = set()

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
        delivered = self._fan_out(message)
        self._publish(message)
        return delivered

    def deliver_remote(self, data: Mapping[str, Any]) -> int:
        """A push from the transport: deliver it here unless this process made it."""
        if data.get("origin") == self.origin:
            return 0
        try:
            message = UiPush.from_wire(data)
        except (KeyError, TypeError, ValueError):
            logger.warning("dropping a malformed UI push from the transport")
            return 0
        if message.kind not in KINDS:
            return 0
        return self._fan_out(message)

    def _fan_out(self, message: UiPush) -> int:
        delivered = 0
        for queue in list(self._subscribers.get((message.user_id, message.session_id), ())):
            try:
                queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                pass
        return delivered

    def _publish(self, message: UiPush) -> None:
        from valuz_agent.ports.extensions import ext
        from valuz_agent.ports.ui_push_transport import is_local

        transport = ext.ui_push_transport
        if is_local(transport):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._send(transport, message.to_wire(self.origin)))
        self._publishing.add(task)
        task.add_done_callback(self._publishing.discard)

    async def _send(self, transport: Any, wire: dict[str, Any]) -> None:
        try:
            await transport.publish(wire)
        except Exception:  # noqa: BLE001 — a lost push is a missed toast, never correctness
            logger.warning("UI push transport publish failed", exc_info=True)

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
