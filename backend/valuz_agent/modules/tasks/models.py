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
  Append-only event log scoped to a task; monotonic ``sequence`` per
  (project_id, task_id). The type vocabulary is open (plain string column);
  the frontend's ``TaskEventType`` union lists the known ones.

  ``subtask_message`` is lead → member; ``subtask_reported`` is member →
  lead. Pre-2026-07 rows carry ``subtask_message`` for BOTH directions
  (split by ``payload.direction``) — the log is never rewritten, so readers
  must keep handling that.

TaskSession (valuz_task_session):
  Index of every kernel session that belongs to a task — the lead's
  session (kind="lead") and every dispatched sub-run (kind="subtask").
  ``result_manifest`` is populated when the session completes. ``subtask_key``
  backlinks a subtask run to its plan node on TaskRow.plan (VALUZ-TASK).

TaskLease (valuz_task_lease):
  Which host process currently drives a task, with a TTL it must renew.
  One row per task; see ``modules/tasks/lease.py`` for why it exists (the
  actor loops and the mailbox are process-local, the tasks they drive are not).

No FK constraints (repo convention — business keys, FKs OFF).
Mirror modules/agents/models.py style.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

# The one event type that is a full SNAPSHOT rather than an increment: each
# carries the whole plan, so all but the newest are dead weight on a bulk read
# (see TaskEventDatastore.list_events).
PLAN_SNAPSHOT_EVENT = "task_plan_update"


class TaskRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Durable task header — one row per task kickoff."""

    __tablename__ = "valuz_task"

    __table_args__ = (
        # ``list_all`` (sidebar) and ``list_tasks_page`` (polled activity feed)
        # are WHERE user_id ... ORDER BY updated_at DESC LIMIT n — without the
        # composite they sort every row of the owner, plan JSON included.
        Index("ix_valuz_task_user_updated", "user_id", "updated_at"),
        # ``list_active`` runs every 60s from the health watchdog.
        Index("ix_valuz_task_status", "status"),
    )

    project_id: Mapped[str] = mapped_column(String(36), index=True)
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

    # ── Trigger provenance (who/what spawned this task) ──────────────────────
    # Resolved once at kickoff/draft and immutable thereafter. Surfaced in the
    # task list ("由 … 触发") and enables reverse "spawned tasks" queries via the
    # indexed source ids. ``trigger_session_id`` (the originating session, for
    # the chat link) keeps living on ``metadata.originating_session_id`` — it is
    # load-bearing for the plan-writer gate — so it is not duplicated here.
    #   user      — direct user action (default; no source)
    #   chat      — a project conversation spawned it (source = originating session)
    #   agent     — a task lead/member spawned it (source = trigger_task_id + agent)
    #   automation— a scheduled/agent-run automation fired it (source = automation)
    trigger_type: Mapped[str] = mapped_column(
        String(32), default="user", server_default="user", nullable=False
    )
    # Parent task whose lead/member triggered this one (trigger_type=agent).
    # Indexed so "what did task X spawn?" is a cheap lookup.
    trigger_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # The agent slug that triggered it (trigger_type=agent), for the label.
    trigger_agent_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Automation that fired this task (trigger_type=automation). Indexed so
    # "what did automation X spawn?" is a cheap lookup.
    trigger_automation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class TaskEventRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Append-only event log for one task — timeline backbone."""

    __tablename__ = "valuz_task_event"

    __table_args__ = (
        UniqueConstraint("project_id", "task_id", "sequence", name="uq_task_event_ws_task_seq"),
    )

    # NOT indexed on its own: every query filters project_id together with
    # task_id, which the unique constraint above already covers as a prefix.
    project_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    # Monotonic per (project_id, task_id); host assigns on append
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


class TaskSessionRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Index of kernel sessions that belong to a task (runs)."""

    __tablename__ = "valuz_task_session"

    # NOT indexed: no query filters runs by project_id alone (task_id and
    # session_id are the access paths).
    project_id: Mapped[str] = mapped_column(String(36))
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
    # active | paused | completed | rejected | archived
    #   active    — run in flight
    #   paused    — parked by stop_task (task pause/stop); resumable
    #   completed — finished normally, or approved by review_subtask
    #   rejected  — user-cancelled (stop_member / an interrupted member turn)
    #   archived  — the run errored terminally
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Human label, e.g. "Kickoff" or None
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Scoped brief for subtask (kind=subtask only); NULL for lead row
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # session_id of the lead run that dispatched this subtask
    dispatched_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Display-only; always ``shared`` since v2.1 (members share the project
    # cwd, a task worktree relocates it wholesale). Legacy per-member modes
    # are retired; the default remains only for old rows.
    project_mode: Mapped[str] = mapped_column(String(16), default="isolated")
    # Absolute path to this run's working directory
    run_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated when session completes: {summary, artifacts, status}
    result_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Populated when session ends — Unix epoch ms (UTC), like every host instant.
    ended_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TaskLeaseRow(Base, TimestampMixin, UserMixin):
    """Which process currently drives a task — see ``modules/tasks/lease.py``.

    One row per task (``task_id`` IS the primary key: a task has at most one
    driver). ``PrimaryKeyMixin`` is deliberately not used — a surrogate ``id``
    would let two rows exist for one task, which is the single thing this table
    is here to prevent.
    """

    __tablename__ = "valuz_task_lease"

    __table_args__ = (
        # The watchdog sweeps expired leases; the owner column inherited from
        # ``UserMixin`` is indexed for the per-owner reads.
        Index("ix_valuz_task_lease_expires", "lease_expires_at"),
    )

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # The lead run this lease drives. Informational: the lease is keyed by task,
    # but a stale row naming a different lead is a useful diagnostic.
    lead_session_id: Mapped[str] = mapped_column(String(36))
    # Opaque process identity — host:pid:boot. Compared, never parsed.
    holder_id: Mapped[str] = mapped_column(String(128))
    # Bumped on EVERY acquisition, including re-acquisition by the same process
    # (mirrors ``mailbox_registry.claim``: a later claim invalidates earlier
    # tokens). A holder whose token no longer matches has been fenced and must
    # stop driving.
    fence_token: Mapped[int] = mapped_column(BigInteger, default=1)
    # held | released
    state: Mapped[str] = mapped_column(String(16), default="held")
    heartbeat_at: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_expires_at: Mapped[int] = mapped_column(BigInteger, default=0)
