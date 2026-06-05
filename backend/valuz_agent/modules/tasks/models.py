"""ORM models for the Task, TaskEvent, and TaskSession tables.

Task (valuz_task):
  Durable header for a lead-dispatch task. ``current_holder`` is always
  the lead agent slug (holder-constant model). Status drives the task
  lifecycle (see ``task_state.TASK_STATUSES``):
  ``draft → active → paused/stopped/completed/blocked``,
  plus ``abandoned`` terminal for discarded drafts.
  ``plan`` carries the lead's structured subtask DAG (VALUZ-TASK) — see
  modules/tasks/plan.py.

  Two writer-control fields added by VALUZ-CHATPLAN
  (docs/exec-plans/active/chat-plan-then-execute.md):

  - ``plan_version``  monotonically-incrementing CAS token. Every ``plan``
    mutation must bump this by 1; mutators pass ``expected_version`` to
    detect mid-air conflicts (chat ↔ lead concurrent writes).
  - ``committed_at`` set when a draft transitions to active via
    ``commit_task``. NULL for tasks still in draft AND for legacy
    tasks created via the original kickoff path (where draft never
    existed). UI / observers treat ``NULL`` + ``status=draft`` as
    "drafting", ``NULL`` + ``status=active`` as "legacy committed".

TaskEvent (valuz_task_event):
  Append-only event log scoped to a task. Monotonic ``sequence`` per
  (workspace_id, task_id). Types: kickoff | subtask_spawned |
  subtask_completed | subtask_failed | user_note | goal_revised |
  paused | resumed | stopped | task_completed | task_drafted |
  committed | abandoned | user_inject | user_inject_dropped.

TaskSession (valuz_task_session):
  Index of every kernel session that belongs to a task — the lead's
  session (kind="lead") and every dispatched sub-run (kind="subtask").
  ``result_manifest`` is populated when the session completes. ``subtask_key``
  backlinks a subtask run to its plan node on TaskRow.plan (VALUZ-TASK).

No FK constraints (repo convention — business keys, FKs OFF).
Mirror modules/agents/models.py style.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin


class TaskRow(Base, PrimaryKeyMixin, TimestampMixin):
    """Durable task header — one row per task kickoff."""

    __tablename__ = "valuz_task"

    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    # Relative path within project.cwd: tasks/<id>-<slug>.md
    file_path: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(256))
    goal: Mapped[str] = mapped_column(Text)
    # See ``task_state.TASK_STATUSES``:
    #   draft | active | paused | stopped | completed | blocked | abandoned
    status: Mapped[str] = mapped_column(String(32), default="active")
    # user | schedule | webhook | project_init | agent
    created_by: Mapped[str] = mapped_column(String(32), default="user")
    # Slug of the lead agent — set once at kickoff and never changed
    lead_agent_slug: Mapped[str] = mapped_column(String(128))
    # Active-period plan writer (lead session id, holder-constant model).
    # Draft-period writer is recorded on ``metadata.originating_session_id``;
    # at ``commit_task`` time we flip current_holder onto the new lead. Set
    # only at create / commit (single user-triggered, status-guarded flow);
    # there is no mid-task lead↔member handoff, so plain ORM writes suffice.
    current_holder: Mapped[str] = mapped_column(String(128))
    # Extensible JSON bag for future metadata (e.g. refs, priority,
    # originating_session_id for chat→task tracing).
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    # Structured plan (DAG of subtask nodes) the lead produces before dispatch
    # (VALUZ-TASK). Shape: {"subtasks": [{key,title,goal,agent,depends_on,
    # parallel_group,status,attempts,latest_run_session_id,review_feedback,
    # review_criteria}]}.
    # 1:1 with the task, always read whole, mutated via plan_version CAS
    # (VALUZ-CHATPLAN D7'). See modules/tasks/plan.py.
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # CAS optimistic-lock token for plan mutations. Bumped by 1 on every
    # write (propose_plan / revise_plan / lead-side updates). Callers pass
    # ``expected_version``; mid-air collision → PLAN_VERSION_CONFLICT.
    plan_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Set when a draft is committed via ``commit_task``. NULL for tasks
    # still in draft OR for legacy tasks created via the original kickoff
    # path. See module docstring for interpretation.
    committed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TaskEventRow(Base, PrimaryKeyMixin, TimestampMixin):
    """Append-only event log for one task — timeline backbone."""

    __tablename__ = "valuz_task_event"

    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id", "sequence", name="uq_task_event_ws_task_seq"),
    )

    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    # Monotonic per (workspace_id, task_id); host assigns on append
    sequence: Mapped[int] = mapped_column(Integer)
    # kickoff | subtask_spawned | subtask_completed | subtask_failed |
    # user_note | goal_revised | paused | resumed | stopped | task_completed
    type: Mapped[str] = mapped_column(String(32))
    # user | <agent_slug> | system
    actor: Mapped[str] = mapped_column(String(128))
    # Kernel session id for subtask_* events; NULL for user/system events
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Event-specific payload: goal/refs/summary/artifacts/status etc.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TaskSessionRow(Base, PrimaryKeyMixin, TimestampMixin):
    """Index of kernel sessions that belong to a task (runs)."""

    __tablename__ = "valuz_task_session"

    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    # NULL for independent sessions (not yet used; reserved for §3 isolation)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # References kernel sessions.id — business key, NO FK constraint
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_slug: Mapped[str] = mapped_column(String(128))
    # 0 = lead, 1..N = dispatched subtasks in order
    sequence: Mapped[int] = mapped_column(Integer)
    # lead | subtask
    kind: Mapped[str] = mapped_column(String(16))
    # Backlink to the plan node this run executes (VALUZ-TASK). NULL for the
    # lead run; for subtask runs = the plan node ``key`` (one node → 1..N runs
    # across rework re-dispatches). The plan itself lives on TaskRow.plan.
    subtask_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # active | completed | rejected | archived
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Human label, e.g. "Kickoff" or None
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Scoped brief for subtask (kind=subtask only); NULL for lead row
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # session_id of the lead run that dispatched this subtask
    dispatched_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # isolated | repo-worktree
    workspace_mode: Mapped[str] = mapped_column(String(16), default="isolated")
    # Absolute path to this run's working directory
    run_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated when session completes: {summary, artifacts, status}
    result_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Populated when session ends — Unix epoch ms (UTC), like every host instant.
    ended_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
