"""Plan → markdown projection (file-as-truth mirror).

Renders the task's plan into its narrative file (``task_row.file_path``) on
every plan change — a human/agent-readable mirror, never a source of truth.
Split out of ``planning.py`` so the plan-domain module stays free of
filesystem rendering concerns.
"""

from __future__ import annotations

import logging
from pathlib import Path

from valuz_agent.modules.tasks.models import TaskRow
from valuz_agent.modules.tasks.plan import TaskPlan

logger = logging.getLogger(__name__)


def render_plan_md(task_row: TaskRow, plan: TaskPlan) -> None:
    """Best-effort mirror of the plan into the task markdown file (file-as-truth).

    Never raises — the DB plan column is the source of truth; the md is a
    human/agent-readable mirror.
    """
    try:
        path = Path(task_row.file_path)
        lines = [f"# {task_row.title}", "", f"> Goal: {task_row.goal}", "", "## Plan", ""]
        for n in plan.nodes:
            deps = f" (after: {', '.join(n.depends_on)})" if n.depends_on else ""
            agent = f" — {n.agent}" if n.agent else ""
            lines.append(f"- [{n.status}] **{n.key}**{agent}: {n.title}{deps}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("plan md render skipped for task %s", task_row.id, exc_info=True)
