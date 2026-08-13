"""Session-fork domain mechanics (docs/design/session-fork.md).

The fork route composes these pieces; they carry no HTTP concerns so any
server-side copy flow (future claude_agent / deepagents forks, archival
exports) can reuse them. All reads and writes go through the owner-scoped
``StorePort`` — writes therefore ride the store composition's durable
mirror like every other kernel write.
"""

from __future__ import annotations

import copy
import dataclasses
import uuid
from typing import Any

from src.core.events import Event
from src.core.store_port import StorePort
from src.core.types import Message, Session, now_ms

# Paging sizes for the copy loops. Fork is a synchronous server-side copy;
# pages just bound single-query result sizes on long sessions.
_MESSAGE_PAGE = 200
_EVENT_PAGE = 500


class MissingNativeAnchorError(ValueError):
    """The anchor message carries no usable runtime-native fork anchor."""


@dataclasses.dataclass(frozen=True)
class NativeForkSource:
    """What ``RuntimePort.fork_session`` needs: the source native thread and
    the runtime-native cut point (``None`` = branch at the tail)."""

    provider: str
    native_session_id: str
    anchor: str | None


# Per-provider key names inside ``messages.metadata["runtime_native"]`` —
# the anchor each runtime's ``fork_session`` cuts on, and the native
# thread/session id it belongs to.
_ANCHOR_KEY = {
    "codex": "turn_id",
    "claude_agent": "message_uuid",
    "deepagents": "checkpoint_id",
}
_THREAD_KEY = {
    "codex": "thread_id",
    "claude_agent": "native_session_id",
    "deepagents": "thread_id",
}


def resolve_native_fork_source(source: Session, anchor: Message | None) -> NativeForkSource | None:
    """Resolve the provider-native fork source for a fork of ``source``.

    With an ``anchor`` message, everything comes from its stored
    ``metadata["runtime_native"]`` stamp — self-describing (it carries the
    thread id it belongs to), so a fork-of-a-fork at an old message still
    points at the right native history. Without one, the fork targets the
    source's current thread tail. ``None`` means the source never ran: a
    plain config copy needs no native fork.

    Raises :class:`MissingNativeAnchorError` when ``anchor`` is given but
    carries no usable stamp (recorded before anchor persistence, or its
    turn never started).
    """
    if anchor is not None:
        native = anchor.metadata.get("runtime_native")
        native = native if isinstance(native, dict) else {}
        provider = str(native.get("provider") or "")
        anchor_value = native.get(_ANCHOR_KEY.get(provider, ""))
        thread_id = native.get(_THREAD_KEY.get(provider, "")) or source.runtime_session_id
        if not provider or not anchor_value or not thread_id:
            raise MissingNativeAnchorError(
                "Anchor message has no native fork anchor "
                "(recorded before anchor persistence, or the turn never started)"
            )
        return NativeForkSource(
            provider=provider,
            native_session_id=str(thread_id),
            anchor=str(anchor_value),
        )
    if source.runtime_session_id:
        return NativeForkSource(
            provider=source.runtime_provider,
            native_session_id=str(source.runtime_session_id),
            anchor=None,
        )
    return None


async def collect_history(
    store: StorePort,
    owner: str,
    session_id: str,
    *,
    until_message_id: str | None = None,
) -> list[Message]:
    """Return the session's messages oldest-first, cut for copying.

    ``until_message_id`` cuts inclusively at that message (raises
    ``LookupError`` if it is not in the session's history). Without it,
    in-flight ``running`` rows are dropped — a tail copy only carries
    settled turns (crashed-orphan rows have no assistant output worth
    copying).
    """
    messages: list[Message] = []
    offset = 0
    while True:
        page = await store.list_messages_for_session(
            owner, session_id, limit=_MESSAGE_PAGE, offset=offset
        )
        messages.extend(page)
        if len(page) < _MESSAGE_PAGE:
            break
        offset += _MESSAGE_PAGE
    messages.sort(key=lambda m: (m.started_at, m.id))
    if until_message_id is None:
        return [m for m in messages if m.status != "running"]
    cut = next((i for i, m in enumerate(messages) if m.id == until_message_id), None)
    if cut is None:
        raise LookupError(f"message {until_message_id} is not in session {session_id}")
    return messages[: cut + 1]


def build_forked_session(
    source: Session,
    *,
    anchor_message_id: str | None,
    caller_metadata: dict[str, Any] | None = None,
    copied_messages: list[Message] | None = None,
) -> Session:
    """A fresh session carrying the source's config surface.

    Provenance (``forked_from``) is stamped OVER the caller-supplied
    metadata, so a caller cannot spoof it. Todos follow carry-forward
    semantics: the fork's "current" todos are the last snapshot within
    the copied history, not the source's live tail.

    Status: a fork that carries history is born ``idle`` with the anchor
    turn's ``stop_reason`` — it is a settled conversation, not a
    never-ran placeholder, and ``created`` would hide it from every
    "has run" surface (the host runs overview deliberately excludes
    ``created``). Only a plain config copy of a never-ran source stays
    ``created``.
    """
    metadata: dict[str, Any] = {
        **copy.deepcopy(caller_metadata or {}),
        "forked_from": {
            "session_id": source.id,
            "message_id": anchor_message_id,
        },
    }
    messages = copied_messages or []
    return dataclasses.replace(
        source,
        id=str(uuid.uuid4()),
        agent_config=copy.deepcopy(source.agent_config),
        status="idle" if messages else "created",
        stop_reason=(copy.deepcopy(messages[-1].stop_reason) if messages else None),
        created_at=now_ms(),
        metadata=metadata,
        runtime_session_id=None,
        todos=next(
            (list(m.todos) for m in reversed(messages) if m.todos),
            None,
        ),
    )


async def copy_history(
    store: StorePort,
    owner: str,
    messages: list[Message],
    target_session_id: str,
) -> list[Message]:
    """Re-mint ``messages`` (order preserved) into the target session.

    Every message gets a fresh id (the id column is a global PK) and its
    stored events are re-homed onto the new coordinates — both the
    ``message_id`` column and the stamped ``data["message_id"]``. Message
    metadata rides along unchanged: the ``runtime_native`` anchors are
    self-describing, so they stay valid for forking the copy itself.
    """
    copied: list[Message] = []
    for message in messages:
        new_message = dataclasses.replace(
            copy.deepcopy(message),
            id=str(uuid.uuid4()),
            session_id=target_session_id,
        )
        await store.save_message(owner, new_message)
        copied.append(new_message)
        offset = 0
        while True:
            events = await store.get_events_for_message(
                owner, message.id, limit=_EVENT_PAGE, offset=offset
            )
            for event in events:
                data = dict(event.data)
                if "message_id" in data:
                    data["message_id"] = new_message.id
                await store.append_event(
                    owner,
                    target_session_id,
                    new_message.id,
                    Event(type=event.type, data=data, timestamp=event.timestamp),
                    request_id=str(uuid.uuid4()),
                )
            if len(events) < _EVENT_PAGE:
                break
            offset += _EVENT_PAGE
    return copied
