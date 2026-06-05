"""Wire schemas for the Decision Inbox (ADR-022).

These are the shapes the frontend sees over REST + SSE. There is no DB
representation — the aggregator's snapshot is fully derived from
``kernel.events`` + business-table joins.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionEntry(BaseModel):
    """One unresolved ``requires_action(clarifying_questions)`` pending.

    Enriched with the project / task / subtask / agent metadata the
    drawer needs to render a self-contained card. The same shape is
    used for both the REST snapshot and SSE ``added`` events — the
    frontend deserialises both into the same store entry.
    """

    model_config = ConfigDict(frozen=True)

    # ---- Identity (resolution keys) -------------------------------
    pending_id: str
    """Kernel-issued pending id. Required for ``POST /v1/sessions/
    {session_id}/actions`` to resolve."""

    session_id: str
    """The session asking the question — usually a subtask run session,
    occasionally a lead session that delegated to AskUserQuestion before
    dispatching."""

    task_id: str
    """The valuz task this session belongs to (lead and all its
    subtasks share the same ``task_id``)."""

    # ---- Context (UI metadata) ------------------------------------
    project_id: str | None = None
    """The workspace the task belongs to. Plain chat sessions don't have
    this — but the aggregator filters them out anyway."""

    subtask_key: str | None = None
    """Plan node key (e.g. ``arch-design``). ``None`` when the lead
    session itself asked the question."""

    agent_slug: str
    """The agent slug that raised the question (e.g. ``architect``,
    ``research-director``)."""

    project_title: str | None = None
    project_emoji: str | None = None
    task_title: str

    subtask_label: str | None = None
    """Human label for the subtask plan node (e.g. ``游戏架构设计``).
    ``None`` when ``subtask_key`` is ``None`` or the label has been
    deleted from the plan."""

    # ---- Question payload -----------------------------------------
    question_payload: dict[str, Any] = Field(default_factory=dict)
    """The structured AskUserQuestion shape — ``{questions: [...]}``
    with each question carrying its options. Frontend reuses
    ``AskUserQuestionCard`` to render this verbatim."""

    raised_at: int
    """When the kernel emitted ``requires_action``. Drawer sorts by this
    descending."""


class _DecisionStreamAddedPayload(BaseModel):
    """SSE ``event: added`` payload shape — wraps a single ``DecisionEntry``."""

    entry: DecisionEntry


class _DecisionStreamResolvedPayload(BaseModel):
    """SSE ``event: resolved`` payload shape — just the pending_id to drop."""

    pending_id: str


class _DecisionStreamSnapshotPayload(BaseModel):
    """SSE ``event: snapshot`` initial-connect payload — full state."""

    entries: list[DecisionEntry]


class DecisionStreamEvent(BaseModel):
    """One event over the SSE stream.

    The aggregator emits these into per-subscriber asyncio queues; the
    HTTP layer serialises them into named SSE frames.
    """

    kind: Literal["snapshot", "added", "resolved"]
    """The SSE ``event:`` name."""

    payload: (
        _DecisionStreamSnapshotPayload
        | _DecisionStreamAddedPayload
        | _DecisionStreamResolvedPayload
    )
    """Shape depends on ``kind``. Pydantic discriminates per-kind."""


class DecisionPendingResponse(BaseModel):
    """REST ``GET /v1/decisions/pending`` response."""

    entries: list[DecisionEntry]


__all__ = [
    "DecisionEntry",
    "DecisionPendingResponse",
    "DecisionStreamEvent",
]
