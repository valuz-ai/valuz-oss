"""Port: user outcome signals on assistant turns (rating / copy / regenerate / fork / share).

Feedback is a user action on a kernel ``Message`` — relational data that
lives in the HOST (``valuz_feedback``, host alembic chain), never on the
kernel event stream and never in ``kernel.db``: it happens after the turn
ends, when a sandboxed kernel may already be gone, and evaluation joins it
against the DataService durable copy of ``messages`` in the same store.

Write invariant (every backend must honour it): **one row per
``(user_id, message_id, action, block_ref)``**. A repeat bumps
``occurrences`` + ``updated_at``; ``value`` / ``reason*`` / ``target`` /
``surface`` are last-write-wins; withdrawing a rating deletes the row.
That single rule covers the state-like ``rating`` (toggle / withdraw) and
the event-like actions (``copy`` counted, ``regenerate`` twice = stronger)
and makes every write idempotent under client retries and server replays.

OSS binds :class:`~valuz_agent.integrations.feedback_local.LocalFeedbackProvider`
(local ``valuz_feedback`` table). A managed edition decorates it via
``set_feedback_port()`` — org scoping, forwarding, retention — without
touching the service that validates requests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

FeedbackAction = Literal["rating", "copy", "regenerate", "fork", "share", "research_share"]
FeedbackValue = Literal["up", "down"]
FeedbackSource = Literal["ui", "api", "server"]
FeedbackTargetType = Literal["message", "session", "share", "research_message"]

FEEDBACK_ACTIONS: tuple[str, ...] = (
    "rating",
    "copy",
    "regenerate",
    "fork",
    "share",
    "research_share",
)
#: Actions a client may submit; the rest are server-emitted only.
CLIENT_FEEDBACK_ACTIONS: tuple[str, ...] = ("rating", "copy")
FEEDBACK_VALUES: tuple[str, ...] = ("up", "down")
FEEDBACK_SOURCES: tuple[str, ...] = ("ui", "api", "server")
FEEDBACK_TARGET_TYPES: tuple[str, ...] = ("message", "session", "share", "research_message")
#: Closed sets shared with the frontend reason chips (``conversation.feedback.reason.*``).
#: A 👍 offers the positive set, a 👎 the negative set; ``other`` is in both.
FEEDBACK_POSITIVE_REASON_CODES: tuple[str, ...] = (
    "solved",
    "followed_instructions",
    "good_quality",
    "fast",
    "helpful_autonomy",
    "other",
)
FEEDBACK_NEGATIVE_REASON_CODES: tuple[str, ...] = (
    "inaccurate_or_incomplete",
    "ignored_instructions",
    "off_topic",
    "lost_context",
    "slow_or_broken",
    "safety_or_legal",
    "other",
)
FEEDBACK_REASON_CODES: tuple[str, ...] = tuple(
    dict.fromkeys(FEEDBACK_POSITIVE_REASON_CODES + FEEDBACK_NEGATIVE_REASON_CODES)
)
#: ``metadata["reason_codes"]`` carries the full multi-select; ``reason_code`` is its first entry.
FEEDBACK_REASON_CODES_METADATA_KEY = "reason_codes"
FEEDBACK_REASON_MAX_LEN = 500
FEEDBACK_BLOCK_REF_MAX_LEN = 128


@dataclass(frozen=True)
class FeedbackActor:
    """Who acted — the kernel owner id (threaded explicitly, never ambient)."""

    user_id: str


@dataclass(frozen=True)
class FeedbackSubject:
    """What was acted on — a kernel Message, optionally a sub-block of it."""

    session_id: str
    message_id: str
    #: ``""`` = the whole turn; a code block / artifact / citation ref otherwise.
    block_ref: str = ""


@dataclass(frozen=True)
class FeedbackTarget:
    """What the action produced (the regenerated turn, the forked session, the share)."""

    type: FeedbackTargetType
    id: str


@dataclass(frozen=True)
class FeedbackRecord:
    """Row projection returned by every port method."""

    id: str
    user_id: str
    session_id: str
    message_id: str
    action: str
    block_ref: str
    value: str | None
    reason_code: str | None
    reason: str | None
    target: FeedbackTarget | None
    source: str
    surface: str | None
    occurrences: int
    created_at: int  # Unix epoch ms (UTC) — first occurrence
    updated_at: int  # Unix epoch ms (UTC) — latest occurrence
    metadata: dict[str, Any] = field(default_factory=dict)


class FeedbackPort(ABC):
    """Persistence + fan-out seam for feedback rows (see module docstring)."""

    @abstractmethod
    async def record(
        self,
        actor: FeedbackActor,
        subject: FeedbackSubject,
        action: str,
        *,
        value: str | None = None,
        reason_code: str | None = None,
        reason: str | None = None,
        target: FeedbackTarget | None = None,
        source: str = "ui",
        surface: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        """Upsert one ``(actor, subject, action)`` row and return its projection."""
        ...

    @abstractmethod
    async def withdraw(self, actor: FeedbackActor, subject: FeedbackSubject, action: str) -> bool:
        """Delete the row; ``True`` when one existed."""
        ...

    @abstractmethod
    async def list_for_session(self, actor: FeedbackActor, session_id: str) -> list[FeedbackRecord]:
        """Every row the actor wrote in ``session_id`` (per-user visibility only)."""
        ...


def get_feedback_port() -> FeedbackPort:
    from valuz_agent.ports.extensions import ext

    return ext.feedback


def set_feedback_port(port: FeedbackPort) -> None:
    """Replace the feedback provider (called by an overlay at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.feedback = port


__all__ = [
    "CLIENT_FEEDBACK_ACTIONS",
    "FEEDBACK_ACTIONS",
    "FEEDBACK_BLOCK_REF_MAX_LEN",
    "FEEDBACK_NEGATIVE_REASON_CODES",
    "FEEDBACK_POSITIVE_REASON_CODES",
    "FEEDBACK_REASON_CODES",
    "FEEDBACK_REASON_CODES_METADATA_KEY",
    "FEEDBACK_REASON_MAX_LEN",
    "FEEDBACK_SOURCES",
    "FEEDBACK_TARGET_TYPES",
    "FEEDBACK_VALUES",
    "FeedbackAction",
    "FeedbackActor",
    "FeedbackPort",
    "FeedbackRecord",
    "FeedbackSource",
    "FeedbackSubject",
    "FeedbackTarget",
    "FeedbackTargetType",
    "FeedbackValue",
    "get_feedback_port",
    "set_feedback_port",
]
