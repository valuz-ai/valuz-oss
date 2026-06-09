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

from sqlalchemy import BigInteger, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin, PrimaryKeyMixin, TimestampMixin


class AutomationRow(Base, PrimaryKeyMixin, TimestampMixin, OwnedMixin):
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
            "trigger_kind IN ('cron', 'interval', 'manual')",
            name="ck_automation_trigger_kind",
        ),
        CheckConstraint(
            "action_kind IN ('chat', 'task')",
            name="ck_automation_action_kind",
        ),
    )

    name: Mapped[str] = mapped_column(String(256))

    # ── Action (执行什么) ─────────────────────────────────────────────
    # ``project_member`` rows reference (workspace_id, agent_slug) in
    # ``valuz_project_member``; ``library_agent`` rows reference
    # AgentRow.slug. In storage these distinctions matter mainly for
    # display / ownership semantics — runner resolves either kind through
    # the same project_member lookup (library agents are instantiated
    # into the bound chat workspace at create time; see ADR-021 §4).
    agent_kind: Mapped[str] = mapped_column(String(32))
    agent_slug: Mapped[str] = mapped_column(String(128))
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    prompt_template: Mapped[str] = mapped_column(Text)
    # Execution mode at fire time:
    # ``chat`` — single agent run (``create_session + send_message_sync``).
    #   The classic schedule semantic — a fresh session per fire, the agent
    #   produces text, done.
    # ``task`` — kick off a full project task with the bound agent as Lead
    #   (``task_orchestrator.kickoff``). The prompt becomes the task goal;
    #   the lead plans + dispatches sub-members per the project task
    #   protocol. Only valid for project workspaces — chat workspaces don't
    #   have the multi-member context the task protocol needs.
    action_kind: Mapped[str] = mapped_column(String(16), default="chat")

    # ── Trigger (何时触发) ────────────────────────────────────────────
    trigger_kind: Mapped[str] = mapped_column(String(32))
    cron_expr: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # IANA tz name. Cron-only — interval / manual ignore it. NULL = follow
    # the user-level default (ADR-010 semantics, scoped down to cron).
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Schedule state ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(32), default="enabled")
    # Cron / interval write a concrete next-fire instant; manual leaves it
    # NULL and only fires via run-now (and, later, webhook).
    next_run_at: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[int | None] = mapped_column(BigInteger)


class AutomationRunRow(Base, PrimaryKeyMixin, OwnedMixin):
    __tablename__ = "valuz_automation_run"

    automation_id: Mapped[str] = mapped_column(String(36), index=True)
    workspace_id: Mapped[str] = mapped_column(String(36))
    # ``cron`` / ``interval`` / ``manual`` / ``recovered_skip`` today;
    # ``webhook`` enum value reserved for the follow-up ADR.
    trigger_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    triggered_at: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(36))
    created_files: Mapped[str | None] = mapped_column(Text)
