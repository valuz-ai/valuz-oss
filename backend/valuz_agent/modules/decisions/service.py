"""Enrichment helpers for the Decision Inbox (ADR-022).

Pure functions that take a kernel ``Session`` + a raw kernel
``requires_action`` event payload and produce a fully-enriched
``DecisionEntry``. Returns ``None`` when the session isn't task-driven
or the metadata join fails — the aggregator silently drops these.

Joins:

- ``session.metadata["valuz"]`` → run_kind / task_id / agent_slug
- ``valuz_task_session`` (``TaskSessionDatastore.get_run``) → subtask_key
- ``valuz_task`` (``TaskDatastore.get_task``) → task_title + project_id + plan
- ``valuz_project`` (``ProjectDatastore.get_by_id``) → project_title + emoji
- ``TaskPlan.get(subtask_key)`` → subtask_label
"""

# ruff: noqa: I001 — kernel_bootstrap MUST import before src.core (sys.path setup)
from __future__ import annotations

import logging
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from app.schemas import SessionData as Session

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.decisions.schemas import DecisionEntry
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.plan import TaskPlan

logger = logging.getLogger(__name__)


# ``run_kind`` values that count as "task-driven" — i.e. the user is NOT
# already sitting on the question's source page. Plain conversation
# sessions (run_kind absent or any other value) are excluded; their
# AskUserQuestion renders inline on the page the user is already viewing.
TASK_RUN_KINDS = frozenset({"lead", "subtask"})


def _valuz_metadata(session: Session) -> dict[str, Any]:
    """Pull ``session.metadata['valuz']`` as a dict, defensively.

    Kernel ``metadata`` is typed ``dict[str, Any] | None`` and the valuz
    sub-tree may also be missing for non-task-managed sessions.
    """
    meta = getattr(session, "metadata", None) or {}
    sub = meta.get("valuz") if isinstance(meta, dict) else None
    return sub if isinstance(sub, dict) else {}


def is_task_driven(session: Session) -> bool:
    """True iff this session is a ``lead`` or ``subtask`` run.

    The aggregator filters every incoming ``requires_action`` against
    this before doing any enrichment — non-task-driven sessions are
    never surfaced in the inbox.
    """
    return _valuz_metadata(session).get("run_kind") in TASK_RUN_KINDS


async def enrich_pending(
    session: Session,
    *,
    pending_id: str,
    question_payload: dict[str, Any],
    raised_at: int | None = None,
) -> DecisionEntry | None:
    """Build a ``DecisionEntry`` from a kernel session + raw question payload.

    Returns ``None`` when:
    - The session isn't task-driven (``run_kind`` ∉ {lead, subtask})
    - The ``valuz.task_id`` is missing (defensive — every lead/subtask
      session should carry it; logged at warning level if absent)
    - The task row was deleted (race between event broadcast and our
      lookup — silently drop)

    Joins are best-effort: a deleted project or missing plan node
    degrades to ``None`` on the enriched field, NOT a failed entry.
    """
    v = _valuz_metadata(session)
    if v.get("run_kind") not in TASK_RUN_KINDS:
        return None

    task_id = v.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        logger.warning(
            "decisions: task-driven session %s has no valuz.task_id; skipping",
            getattr(session, "id", "?"),
        )
        return None

    agent_slug = v.get("agent_slug") or "?"
    session_id = getattr(session, "id", "")

    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
        if task is None:
            # Race: event broadcast outpaced the DB delete, or task was
            # just abandoned. Either way, no useful entry to render.
            return None

        # subtask_key is stored on TaskSessionRow (the per-run record),
        # NOT on the kernel session metadata. Lead sessions return
        # ``kind="lead"`` here so ``subtask_key`` is None — which is
        # what we want.
        run = await TaskSessionDatastore(db).get_run(session_id)
        subtask_key = run.subtask_key if run else None

        # Subtask label lives on TaskRow.plan (the JSON-serialized
        # TaskPlan blob). Parse it and look up the node; ``None`` if
        # the key has been deleted from the plan since dispatch.
        subtask_label: str | None = None
        if subtask_key and task.plan:
            try:
                plan = TaskPlan.from_dict(task.plan)
                node = plan.get(subtask_key)
                subtask_label = node.title if node else None
            except Exception:  # noqa: BLE001 — plan parse errors degrade silently
                logger.warning(
                    "decisions: failed to parse plan for task %s; subtask_label=None",
                    task_id,
                    exc_info=True,
                )

        project_title: str | None = None
        project_emoji: str | None = None
        project_id = task.project_id
        if project_id:
            ws = await ProjectDatastore(db).get_by_id(project_id)
            if ws is not None:
                project_title = ws.name
                project_emoji = ws.icon

    return DecisionEntry(
        pending_id=pending_id,
        session_id=session_id,
        task_id=task_id,
        project_id=project_id or None,
        subtask_key=subtask_key,
        agent_slug=agent_slug,
        project_title=project_title,
        project_emoji=project_emoji,
        task_title=task.title,
        subtask_label=subtask_label,
        question_payload=question_payload,
        raised_at=raised_at or now_ms(),
    )


__all__ = [
    "enrich_pending",
    "is_task_driven",
    "TASK_RUN_KINDS",
]
