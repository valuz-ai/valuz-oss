"""Automation ORM models.

Two tables:

- ``valuz_automation``      — one row per user-defined automation (Trigger × Action).
- ``valuz_automation_run``  — one row per fire (cron tick / interval tick / manual /
                              recovered-skip / future webhook).

See [ADR-021](../../../../docs/decisions/ADR-021-automation-trigger-agent.md):
Trigger × Agent. Execution identity (model / provider / runtime / instructions /
skills) is resolved at fire time via the bound agent's ``AgentConfig`` — we
deliberately don't replicate those onto this row, so changing the agent
upstream propagates to the next fire automatically.

CheckConstraints enforce the discriminated-trigger invariant at the DB layer
(cron rows must carry ``cron_expr``; interval rows must carry a
``>= 30`` ``interval_seconds``). Pydantic validates again at the API edge,
but the DB guard is the last-line defence against direct-insert bugs.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

_JSON_VARIANT = JSON().with_variant(JSONB(), "postgresql")


class AutomationRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_automation"
    __table_args__ = (
        CheckConstraint(
            "(trigger_kind = 'cron' AND cron_expr IS NOT NULL) OR trigger_kind != 'cron'",
            name="ck_automation_cron_expr_when_cron",
        ),
        CheckConstraint(
            "(trigger_kind = 'interval' AND interval_seconds IS NOT NULL "
            "AND interval_seconds >= 30) OR trigger_kind != 'interval'",
            name="ck_automation_interval_seconds_floor",
        ),
        CheckConstraint(
            "agent_kind IN ('project_member', 'library_agent')",
            name="ck_automation_agent_kind",
        ),
        CheckConstraint(
            "trigger_kind IN ('cron', 'interval', 'manual', 'event')",
            name="ck_automation_trigger_kind",
        ),
        # An event row that names no source is waiting on nothing. The source
        # itself is validated against the registry at the API edge — the DB can
        # only insist that the column is populated.
        CheckConstraint(
            "(trigger_kind = 'event' AND event_source IS NOT NULL) "
            "OR trigger_kind != 'event'",
            name="ck_automation_event_source_when_event",
        ),
        CheckConstraint(
            "action_kind IN ('chat', 'task')",
            name="ck_automation_action_kind",
        ),
    )

    name: Mapped[str] = mapped_column(String(256))

    # ── Action (执行什么) ─────────────────────────────────────────────
    # ``project_member`` rows reference (project_id, agent_slug) in
    # ``valuz_project_member``; ``library_agent`` rows reference
    # AgentRow.slug. In storage these distinctions matter mainly for
    # display / ownership semantics — runner resolves either kind through
    # the same project_member lookup (library agents are instantiated
    # into the bound chat project at create time; see ADR-021 §4).
    agent_kind: Mapped[str] = mapped_column(String(32))
    agent_slug: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    prompt_template: Mapped[str] = mapped_column(Text)
    # Execution mode at fire time:
    # ``chat`` — single agent run (``create_session + send_message_sync``).
    #   The classic schedule semantic — a fresh session per fire, the agent
    #   produces text, done.
    # ``task`` — kick off a full project task with the bound agent as Lead
    #   (``task_orchestrator.kickoff``). The prompt becomes the task goal;
    #   the lead plans + dispatches sub-members per the project task
    #   protocol. Only valid for projects — chat projects don't
    #   have the multi-member context the task protocol needs.
    action_kind: Mapped[str] = mapped_column(String(16), default="chat")
    # Worktree isolation (design §5) — valid for BOTH action kinds, gated on
    # the bound project being a git repo. ``chat`` fires each run in its own
    # git worktree of the project repo; ``task`` runs the whole task (lead +
    # every member) in ONE worktree. Clean worktrees auto-remove when the run
    # / task finishes. Ignored for non-git (chat-sentinel) projects.
    worktree: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Trigger (何时触发) ────────────────────────────────────────────
    trigger_kind: Mapped[str] = mapped_column(String(32))
    cron_expr: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # IANA tz name. Cron-only — interval / manual ignore it. NULL = follow
    # the user-level default (ADR-010 semantics, scoped down to cron).
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Event subscription (谁来叫醒它) ───────────────────────────────
    # Additive, and deliberately NOT mutually exclusive with the clock: a cron
    # row may also subscribe, and ``trigger_kind='event'`` just means "no clock
    # at all". Making events a fourth value of the discriminator *and* nothing
    # else would have forced anyone who wants both to keep two automations for
    # one job — and then nobody can tell which of the two is the real one.
    #
    # ``event_source`` is a registry key (ports/automation_event_source.py);
    # OSS ships no source, so these stay NULL until an overlay registers one.
    # ``event_refs`` is an opaque list the source hands back to itself on
    # delivery — a watch id, a symbol, whatever it needs. OSS stores and
    # forwards it, never interprets it.
    event_source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_refs: Mapped[list[str] | None] = mapped_column(_JSON_VARIANT, nullable=True)

    # ── Provenance ────────────────────────────────────────────────────
    # Kernel ``tool_use`` id of the ``automation create`` call that proposed
    # this row, stamped when the user confirms the proposal card. NULL for
    # rows created from the UI (no proposing tool call). Indexed so a session
    # reload can map historical proposing tool-calls → their created rows and
    # show "already added" instead of a fresh Confirm button.
    origin_tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # ── Schedule state ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(32), default="enabled")
    # Cron / interval write a concrete next-fire instant; manual leaves it
    # NULL and only fires via run-now (and, later, webhook).
    next_run_at: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[int | None] = mapped_column(BigInteger)

    # Optional exact Playbook contract. Simple automations may continue to use
    # prompt_template only; when set, every run fixes this Definition version.
    playbook_definition_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    playbook_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AutomationRunRow(Base, PrimaryKeyMixin, UserMixin):
    __tablename__ = "valuz_automation_run"
    __table_args__ = (
        UniqueConstraint(
            "automation_id", "event_id", name="uq_automation_run_event"
        ),
    )

    automation_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36))
    # ``cron`` / ``interval`` (scheduled) · ``manual`` (human "Run now")
    # · ``agent`` (agent fired it via the ``automation`` MCP tool) ·
    # ``event`` (an upstream source delivered something — see
    # ports/automation_event_source.py) · ``recovered_skip`` / ``system``
    # (bookkeeping).
    trigger_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    triggered_at: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    # Optional i18n key for a friendly failure message (e.g. billing
    # rejection); the client prefers it over error_code / error_message.
    error_message_key: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(36))
    created_files: Mapped[str | None] = mapped_column(Text)
    # The session that asked for this run, when an AGENT invoked the automation
    # via the MCP tool (trigger_type="agent"). Lets a task spawned by this run
    # chain its provenance back to the originating task (transitive
    # task→automation→task nesting). NULL for cron/interval/manual runs.
    invoked_by_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Per-run extra input appended to the rendered prompt for THIS run only
    # (not stored on the automation). Set when an agent fires the ``run`` action
    # with an ``input`` argument — e.g. a triage agent passing a discovered task
    # id into a manual automation's instruction. NULL for plain runs.
    extra_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the Automation pins a Playbook, every fire creates one immutable
    # PlaybookRun and stores the canonical back-link here.  NULL for simple
    # prompt-only automations and for queued rows that have not started yet.
    playbook_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Stable id of the upstream occurrence that started this run
    # (``InboundEvent.event_id``). NULL for every clock / manual / agent run.
    #
    # This is the idempotency key, and it lives on the run rather than in a
    # side table because "did this automation already run for this event?" and
    # "what happened when it did" are the same question. A webhook retry, a
    # reconciliation replay, and two subscriptions matching the same upstream
    # occurrence all carry the same id and collide on the unique index below,
    # so at most one run exists per (automation, event).
    event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
