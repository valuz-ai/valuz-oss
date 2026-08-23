"""Wire schemas for the unified activity feed (``GET /v1/activity``).

One model for every history list: the project-home 全部/对话/任务/自动化/Playbook tabs
(``project_id`` set) and the global 动态 list (``project_id`` omitted). An item
is a user chat session, task entity, or PlaybookRun — entity views keep their
own ids/statuses/links rather than being flattened into conversations.
Live "running now" cards stay on ``/v1/runs`` (they need per-run session state).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ActivityItem(BaseModel):
    kind: str = Field(description='"chat", "task", or "playbook".')
    id: str = Field(description="session id for chats, task id for tasks.")
    title: str
    status: str = Field(description="session status for chats, task status for tasks.")
    is_automation: bool = Field(
        description="Fired by an automation (session origin / task trigger)."
    )
    project_id: str
    project_name: str | None = Field(default=None, description="null for non-project quick chats.")
    linked_session_id: str | None = Field(
        default=None,
        description="Conversation executing a PlaybookRun, when present.",
    )
    sort_at: int = Field(description="Unix epoch ms used for interleaving + the keyset cursor.")


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque keyset cursor for the next page; null when exhausted.",
    )
