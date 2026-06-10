"""Project↔session index — module-level service facade.

The host's own mapping of kernel sessions to projects (see
``models.ProjectSessionRow``). Functions here open their own unit of work so
sibling modules (tasks, projects, runs, automations) can call them without
threading a DB session through — cross-module collaboration stays at the
service layer per the module-boundary contract.

Every kernel ``save_session`` **creation** site must be paired with a
``record(...)`` call; updates to existing sessions don't touch the index.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.sessions.models import ProjectSessionRow

__all__ = [
    "count_for_project",
    "list_recent",
    "list_session_ids",
    "project_of",
    "record",
    "remove",
    "remove_for_project",
]


async def record(
    project_id: str,
    session_id: str,
    *,
    kind: str = "chat",
    origin: str = "user",
) -> None:
    """Register a freshly created kernel session under its project.

    Idempotent on ``session_id`` (re-recording an id updates the row) so
    boot-time reconciliation and retries can't violate the unique index.
    """
    async with async_unit_of_work() as db:
        existing = (
            (await db.execute(select(ProjectSessionRow).filter_by(session_id=session_id)))
            .scalars()
            .first()
        )
        if existing is not None:
            existing.project_id = project_id
            existing.kind = kind
            existing.origin = origin
            return
        db.add(
            ProjectSessionRow(
                project_id=project_id,
                session_id=session_id,
                kind=kind,
                origin=origin,
            )
        )


async def list_session_ids(
    project_id: str | None = None,
    *,
    user_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[str]:
    """Session ids, newest first. ``user_only`` keeps conversation kinds
    (``chat``) and drops task-internal runs (lead / subtask)."""
    async with async_unit_of_work(commit=False) as db:
        stmt = select(ProjectSessionRow.session_id)
        if project_id is not None:
            stmt = stmt.where(ProjectSessionRow.project_id == project_id)
        if user_only:
            stmt = stmt.where(ProjectSessionRow.kind == "chat")
        stmt = stmt.order_by(ProjectSessionRow.created_at.desc()).offset(offset).limit(limit)
        return list((await db.execute(stmt)).scalars().all())


async def project_of(session_id: str) -> str | None:
    async with async_unit_of_work(commit=False) as db:
        stmt = select(ProjectSessionRow.project_id).filter_by(session_id=session_id)
        return (await db.execute(stmt)).scalars().first()


async def count_for_project(project_id: str) -> int:
    async with async_unit_of_work(commit=False) as db:
        stmt = select(func.count(ProjectSessionRow.id)).where(
            ProjectSessionRow.project_id == project_id
        )
        return int((await db.execute(stmt)).scalar() or 0)


async def remove(session_id: str) -> None:
    async with async_unit_of_work() as db:
        await db.execute(
            delete(ProjectSessionRow).where(ProjectSessionRow.session_id == session_id)
        )


async def remove_for_project(project_id: str) -> list[str]:
    """Drop every index row for ``project_id``; returns the removed session
    ids so the caller can cascade the kernel-side deletes."""
    async with async_unit_of_work() as db:
        stmt = select(ProjectSessionRow.session_id).where(
            ProjectSessionRow.project_id == project_id
        )
        ids = list((await db.execute(stmt)).scalars().all())
        await db.execute(
            delete(ProjectSessionRow).where(ProjectSessionRow.project_id == project_id)
        )
        return ids


async def list_recent(limit: int = 200) -> list[ProjectSessionRow]:
    """Most recent index rows across all projects — the runs-overview feed."""
    async with async_unit_of_work(commit=False) as db:
        stmt = select(ProjectSessionRow).order_by(ProjectSessionRow.created_at.desc()).limit(limit)
        return list((await db.execute(stmt)).scalars().all())
