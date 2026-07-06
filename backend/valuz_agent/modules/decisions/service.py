"""Enrichment helpers for the Decision Inbox (ADR-022 + question-attention).

Pure functions that take a kernel ``Session`` + a raw kernel
``requires_action`` event payload and produce a fully-enriched
``DecisionEntry``. Every session kind is eligible — ``is_task_driven``
selects the enrichment branch, it is no longer an admission gate:

- **Task sessions** (run_kind ∈ {lead, subtask}) join the task chain:
  ``session.metadata["valuz"]`` → run_kind / task_id / agent_slug;
  ``valuz_task_session`` → subtask_key; ``valuz_task`` → task_title +
  project_id + plan; ``valuz_project`` → project_title + emoji;
  ``TaskPlan.get(subtask_key)`` → subtask_label. Returns ``None`` only
  when the task row is unreadable (broadcast/DB race — the aggregator
  retries, then the reconcile sweep recovers).
- **Conversation sessions** (everything else) need no DB join: context is
  the session title (``metadata["valuz"]["name"]``) + optional project.
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

    Selects the enrichment branch in :func:`enrich_pending` (task chain
    vs conversation context). No longer an inbox admission gate —
    question-attention surfaces every session kind.
    """
    return _valuz_metadata(session).get("run_kind") in TASK_RUN_KINDS


async def enrich_pending(
    session: Session,
    *,
    pending_id: str,
    question_payload: dict[str, Any],
    raised_at: int | None = None,
    user_id: str | None = None,
) -> DecisionEntry | None:
    """Build a ``DecisionEntry`` from a kernel session + raw question payload.

    Dispatcher — every session kind is eligible (question-attention). Task
    sessions join the task chain and may return ``None`` on a broadcast/DB
    race (task row unreadable — the aggregator retries, the reconcile sweep
    recovers). Conversation sessions enrich from session metadata alone and
    never return ``None``.
    """
    if user_id is None:
        raise ValueError("user_id is required")
    if is_task_driven(session):
        return await _enrich_task_pending(
            session,
            pending_id=pending_id,
            question_payload=question_payload,
            raised_at=raised_at,
            user_id=user_id,
        )
    return await _enrich_session_pending(
        session,
        pending_id=pending_id,
        question_payload=question_payload,
        raised_at=raised_at,
    )


async def _enrich_session_pending(
    session: Session,
    *,
    pending_id: str,
    question_payload: dict[str, Any],
    raised_at: int | None,
) -> DecisionEntry:
    """Conversation-session branch: no task chain to join.

    Context = session title (``metadata["valuz"]["name"]``) + optional
    project. ``source_kind`` is ``project_chat`` when the session carries a
    ``valuz.project_id``, else ``chat``. Project join is best-effort — a
    deleted project degrades the label, never the entry.
    """
    v = _valuz_metadata(session)
    raw_project_id = v.get("project_id")
    project_id = str(raw_project_id) if raw_project_id else None
    raw_title = v.get("name")
    session_title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else None
    agent_slug = (
        v.get("agent_slug")
        or getattr(getattr(session, "agent_config", None), "name", None)
        or "assistant"
    )
    owner = getattr(session, "user_id", "") or ""

    project_title: str | None = None
    project_emoji: str | None = None
    if project_id:
        async with async_unit_of_work(commit=False) as db:
            ws = await ProjectDatastore(db).get_by_id(owner, project_id)
            if ws is not None:
                project_title = ws.name
                project_emoji = ws.icon

    return DecisionEntry(
        pending_id=pending_id,
        owner_user_id=owner,
        session_id=getattr(session, "id", ""),
        source_kind="project_chat" if project_id else "chat",
        task_id=None,
        project_id=project_id,
        session_title=session_title,
        agent_slug=str(agent_slug),
        project_title=project_title,
        project_emoji=project_emoji,
        question_payload=question_payload,
        raised_at=raised_at or now_ms(),
    )


async def _enrich_task_pending(
    session: Session,
    *,
    pending_id: str,
    question_payload: dict[str, Any],
    raised_at: int | None,
    user_id: str,
) -> DecisionEntry | None:
    """Task-session branch (run_kind ∈ {lead, subtask}) — the original
    ADR-022 enrichment. Returns ``None`` when ``valuz.task_id`` is absent
    (defensive; warned) or the task row is unreadable (race)."""
    v = _valuz_metadata(session)
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
        task = await TaskDatastore(db).get_task(user_id, task_id)
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
            ws = await ProjectDatastore(db).get_by_id(task.user_id, project_id)
            if ws is not None:
                project_title = ws.name
                project_emoji = ws.icon

    return DecisionEntry(
        pending_id=pending_id,
        owner_user_id=session.user_id,  # owner from the session, never client-supplied
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
