"""Validation shared by automation writes and execution admission."""

from __future__ import annotations

from typing import Any

from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.modules.sessions.errors import SessionNotFound, SessionNotRunnable


async def validate_chat_target(
    user_id: str,
    session_id: str,
    *,
    project_id: str | None = None,
    agent_slug: str | None = None,
    worktree: bool = False,
) -> Any:
    if not user_id:
        raise ValueError("user_id is required")
    session = await data_reader().get_session(user_id, session_id)
    if session is None:
        raise SessionNotFound()
    meta = (session.metadata or {}).get("valuz") or {}
    if (
        meta.get("task_id")
        or meta.get("worktree")
        or worktree
        or str(session.status) in {"terminated", "cancelled", "archived"}
    ):
        raise SessionNotRunnable("Target must be a runnable chat without task/worktree binding")
    if not meta.get("project_id"):
        raise SessionNotRunnable("Target has no project binding")
    if project_id is not None and meta.get("project_id") != project_id:
        raise SessionNotRunnable("Target session belongs to a different project")
    if agent_slug is not None and meta.get("agent_slug") != agent_slug:
        raise SessionNotRunnable("Target session belongs to a different agent")
    return session
