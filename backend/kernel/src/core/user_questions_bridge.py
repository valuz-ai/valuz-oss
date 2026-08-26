"""Per-token registry for the dsh user-questions HTTP bridge.

The dsh subprocess's ``valuz-dsh-kernel-bridge`` plugin forwards
``ctx.userQuestions.ask()`` calls (the ``exit_plan_mode`` plan review and
``ask_user_question`` clarifying batches) to the kernel over HTTP — the dsh
SDK JSON-RPC wire has no user-questions channel, so this loopback endpoint
is the only path back to a human. Same layering as ``mcp_bridge``: the
registry lives in ``src.core`` so the transport layer
(``app/dsh_user_questions_router.py``) never imports ``src.runtimes``, and
the runtime registers per-spawn callables here.

Auth model mirrors PTC's execution registry: the bridge token IS the
credential — random, minted per subprocess spawn, revoked at close. The
standalone kernel's bearer middleware exempts the route for exactly that
reason (the subprocess holds no kernel bearer token).
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UserQuestionsBridgeRecord:
    """One spawn's contribution to the bridge registry.

    ``start_ask(questions)`` registers the park (emits ``requires_action``)
    and returns an ``ask_id``. ``wait_answer(ask_id, wait_seconds)`` blocks
    up to ``wait_seconds`` and returns the terminal state dict (``status``
    ``answered`` / ``error``) or ``None`` while still pending; it raises
    ``KeyError`` for an unknown ``ask_id``.
    """

    start_ask: Callable[[list[dict[str, Any]]], Awaitable[str]]
    wait_answer: Callable[[str, float], Awaitable[dict[str, Any] | None]]


_REGISTRY: dict[str, UserQuestionsBridgeRecord] = {}
_REGISTRY_LOCK = threading.Lock()


def register_user_questions_bridge(token: str, record: UserQuestionsBridgeRecord) -> None:
    """Add (or replace) a bridge token's record."""
    with _REGISTRY_LOCK:
        _REGISTRY[token] = record


def unregister_user_questions_bridge(token: str) -> None:
    """Drop a bridge token's record. No-op if absent."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop(token, None)


def get_user_questions_bridge(token: str) -> UserQuestionsBridgeRecord | None:
    """Lookup helper used by the transport layer."""
    with _REGISTRY_LOCK:
        return _REGISTRY.get(token)


def reset_user_questions_registry_for_tests() -> None:
    """Drop all registry entries — pytest cleanup hook only."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
