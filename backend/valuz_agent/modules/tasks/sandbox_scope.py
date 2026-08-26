"""Session → sandbox-scope resolver for task sessions.

Bound into ``kernel_client.bind_sandbox_scope_resolver`` at boot. A task's lead
and member sessions all execute inside ONE task-scoped sandbox (the members'
manifests hand off to the lead through the shared filesystem), so every EXEC op
on a session indexed by ``valuz_task_session`` must route to
``SandboxScope(kind="task", id=task_id)`` — not to a per-session sandbox.

Only the *creation* call sites pass the scope explicitly (the
``valuz_task_session`` row does not exist yet at kernel-create time); every
later op (run_turn / submit_action / interrupt / live taps) resolves through
this lookup. Non-task sessions return ``None`` → the facade falls back to
``session:{session_id}``. The result is cached facade-side (the mapping is
immutable), so this costs one indexed SELECT per session lifetime.
"""

from __future__ import annotations

from sqlalchemy import select

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.models import TaskSessionRow
from valuz_agent.ports.sandbox_allocator import SandboxScope


async def resolve_sandbox_scope(user_id: str, session_id: str) -> SandboxScope | None:
    """``task:{task_id}`` when ``session_id`` belongs to a task; else ``None``."""
    stmt = select(TaskSessionRow.task_id).where(TaskSessionRow.session_id == session_id)
    if user_id:
        stmt = stmt.where(TaskSessionRow.user_id == user_id)
    stmt = stmt.limit(1)
    async with async_unit_of_work(commit=False) as db:
        task_id = (await db.execute(stmt)).scalar_one_or_none()
    if task_id:
        return SandboxScope(kind="task", id=str(task_id))
    return None


__all__ = ["resolve_sandbox_scope"]
