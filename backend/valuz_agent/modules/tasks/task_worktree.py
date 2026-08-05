"""Task-level worktree support (design §5).

One worktree per task: kickoff creates it (``valuz/task-<id>`` branch) and
stamps the snapshot into ``TaskRow.metadata_["worktree"]``; the lead and every
dispatched member share its cwd — there is deliberately NO per-member
isolation on top (the plan DAG's dependencies are the write-conflict
discipline, exactly as in the main-workspace shared mode). ``finish_task``
removes the worktree iff it holds no work worth keeping (fail-closed).

Coordination files (task narrative, spilled briefs) stay anchored at the MAIN
project cwd so they never register as changes inside the worktree (R5).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def task_worktree_snapshot(task_row: Any) -> dict[str, object] | None:
    """The task's worktree metadata snapshot, or ``None`` (main workspace)."""
    meta = getattr(task_row, "metadata_", None) or {}
    snapshot = meta.get("worktree")
    return snapshot if isinstance(snapshot, dict) else None


async def resolve_task_cwd(task_row: Any, default_cwd: str) -> str:
    """The cwd every session of this task runs in.

    Worktree tasks: the snapshot's projected cwd, with a best-effort
    ``heal_from_snapshot`` first (mirrors the chat-session re-entry guard —
    a worktree removed mid-task is recreated at its deterministic path
    rather than crashing the next dispatch on a missing cwd). Note the
    healed ``base_sha`` is NOT persisted back into the task metadata — the
    stale anchor makes the final clean-check fail closed (worktree kept),
    which is the safe direction for a task someone tampered with.
    """
    snapshot = task_worktree_snapshot(task_row)
    if snapshot is None:
        return default_cwd
    from valuz_agent.modules.worktrees.service import worktree_service

    try:
        await worktree_service.heal_from_snapshot(snapshot)
    except Exception:  # noqa: BLE001 — surface via the turn, not the dispatch plumbing
        logger.warning(
            "task_worktree: heal failed for task %s",
            getattr(task_row, "id", "?"),
            exc_info=True,
        )
    return str(snapshot.get("cwd") or snapshot.get("path") or default_cwd)


def task_worktree_notice(snapshot: dict[str, object] | None) -> str | None:
    """The ``worktree-context`` prompt section for lead/member sessions."""
    if snapshot is None:
        return None
    from valuz_agent.adapters.system_prompt_builder import build_worktree_notice

    base_sha = snapshot.get("base_sha")
    return build_worktree_notice(
        name=str(snapshot.get("name") or ""),
        branch=str(snapshot.get("branch") or ""),
        base_sha=base_sha if isinstance(base_sha, str) else None,
        worktree_path=str(snapshot.get("path") or ""),
        main_workspace=str(snapshot.get("git_root") or ""),
    )


async def cleanup_task_worktree_if_clean(task_row: Any) -> bool:
    """Terminal hook: drop the task's worktree iff clean. Never raises."""
    snapshot = task_worktree_snapshot(task_row)
    if snapshot is None:
        return False
    from valuz_agent.modules.worktrees.service import worktree_service

    removed = await worktree_service.cleanup_if_clean(
        snapshot,
        user_id=str(getattr(task_row, "user_id", "") or ""),
        project_id=str(getattr(task_row, "project_id", "") or ""),
    )
    if removed:
        logger.info(
            "task_worktree: removed clean worktree '%s' for task %s",
            snapshot.get("name"),
            getattr(task_row, "id", "?"),
        )
    return removed
