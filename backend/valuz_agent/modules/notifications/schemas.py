"""Notification wire schemas (docs/design/notifications.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class NotificationEntry(BaseModel):
    """One notification as the frontend renders it (badge/drawer/notify)."""

    id: str
    kind: str
    title: str
    body: str
    route: str | None = None
    action: str
    urgency: str
    task_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    pending_id: str | None = None
    payload: dict[str, Any] = {}
    created_at: int
    read_at: int | None = None
    resolved_at: int | None = None

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    entries: list[NotificationEntry]
    unread: int


class NotificationHistoryResponse(BaseModel):
    """One page of resolved notifications (the drawer's History tab)."""

    entries: list[NotificationEntry]
    has_more: bool = False


class NotificationStreamEvent(BaseModel):
    """SSE frame. ``snapshot`` carries the full open set; ``added`` / ``updated``
    (read) / ``resolved`` are deltas."""

    kind: Literal["snapshot", "added", "updated", "resolved"]
    # For snapshot: {entries:[…], unread:int}; added/updated: {entry:{…}};
    # resolved: {id:str}
    payload: dict[str, Any]
