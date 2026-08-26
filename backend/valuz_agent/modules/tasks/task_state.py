"""Task-level status state machine (VALUZ-CHATPLAN).

This module is the single source of truth for what a ``valuz_task.status``
value can be and which transitions between them are legal. It is **pure**
(no DB, no orchestrator imports) so the rules can be unit-tested in
isolation and reused from datastore / orchestrator / route layers.

State machine (see docs/exec-plans/active/chat-plan-then-execute.md §16.8.3
and docs/decisions/agent-project-task-architecture-2026-05.md §16.8):

                ┌─── abandon_task ───┐
                │                     ▼
[*] -draft_task-> draft -commit_task-> active     abandoned
                  │                     │
                  │                     ├─> paused -> active
                  └ revise_plan         │
                  (repeatable)          ├─> stopped -> active
                                        ├─> completed
                                        └─> blocked -> active

Terminal states: ``abandoned`` (hard). ``stopped`` and ``completed`` are
both "soft terminal" — the lead is torn down and mailboxes are shut, but
the row + plan + run_dir + event history all survive, so ``resume_task``
can rebuild a fresh lead via the same ``_recover_one_task`` path that
handles startup recovery. This makes "user inject 停止" reversible (the
common case) and lets a user **reopen a completed task** to supplement or
adjust subtasks from a second chat-plan (VALUZ-AGENT-SLUG / chat-plan
"区分场景": supplement → reopen; new direction → follow-up task). Only
``abandoned`` stays hard-terminal — a discarded draft has no plan to
revive; the user must draft afresh.

``failed`` is not in this enum — task-level failure is folded into
``blocked`` (recoverable; lead errored but plan intact) or ``stopped``
(user-driven termination, now revivable). Subtask-level ``failed`` is
separate (plan node state machine, see ``plan.py``).

Legacy compatibility: tasks created via the original kickoff path skip
``draft`` and go straight to ``active`` (no ``draft_task`` event,
``committed_at`` left NULL). The state machine validator allows
``None -> active`` for that legacy entry path.
"""

from __future__ import annotations

from typing import Literal, get_args

# All recognized status values for ``valuz_task.status``.
TaskStatus = Literal[
    "draft",  # in-chat planning, no lead session yet (VALUZ-CHATPLAN)
    "active",  # lead session running (the normal executing state)
    "paused",  # user-intervened pause; resumable via :intervene action=resume
    "stopped",  # user-intervened stop OR cascade-stop terminal
    "completed",  # finish_task called, plan.all_done()
    "blocked",  # auto-finalize couldn't close (e.g. unresolved nodes + no error)
    "abandoned",  # draft was discarded (terminal)
]

TASK_STATUSES: tuple[str, ...] = get_args(TaskStatus)

# Hard-terminal states — no transitions out.
# ``stopped`` is intentionally NOT here even though it's a closed
# user-driven end-state: keeping it revivable lets a chat "继续刚才那个
# 任务" feel work without forcing a clone. The lead is rebuilt by
# ``_recover_one_task`` (same path as startup recovery).
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "abandoned"})

# Allowed status transitions. Key = current status, value = set of legal
# next statuses. ``None`` key represents "new row" (initial state on
# insert). ``draft`` and ``active`` are both legal initial states —
# draft for the chat-plan-then-execute path, active for the legacy
# direct-kickoff path.
ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"draft", "active"}),
    "draft": frozenset({"active", "abandoned"}),
    "active": frozenset({"paused", "stopped", "completed", "blocked"}),
    "paused": frozenset({"active", "stopped"}),
    "blocked": frozenset({"active", "stopped"}),
    # stopped → active: user-driven termination is reversible. resume_task
    # rebuilds the lead via the recovery path; the lead's run row gets
    # flipped from "completed" back to "active" by resume_task itself so
    # the recovery view is consistent.
    "stopped": frozenset({"active"}),
    # completed → active: a completed task can be REOPENED to supplement or
    # adjust subtasks from a second chat-plan (resume_task rebuilds the lead
    # via the recovery path, same as stopped). "区分场景": supplement →
    # reopen here; a genuinely new direction → a fresh follow-up task.
    "completed": frozenset({"active"}),
    "abandoned": frozenset(),
}


class TaskStateError(ValueError):
    """Raised when a requested status transition is not allowed."""


def is_valid_status(status: str) -> bool:
    """Return True iff ``status`` is one of the seven recognized values."""
    return status in TASK_STATUSES


def assert_transition(from_status: str | None, to_status: str) -> None:
    """Raise ``TaskStateError`` if ``from_status → to_status`` is illegal.

    ``from_status=None`` denotes "row about to be inserted"; only ``draft``
    or ``active`` are allowed as initial values.
    """
    if not is_valid_status(to_status):
        raise TaskStateError(f"unknown target status {to_status!r}")
    if from_status is not None and not is_valid_status(from_status):
        raise TaskStateError(f"unknown source status {from_status!r}")
    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise TaskStateError(
            f"illegal transition {from_status!r} → {to_status!r}"
            f" (allowed from {from_status!r}: {sorted(allowed) or 'none — terminal'})"
        )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "TASK_STATUSES",
    "TERMINAL_STATUSES",
    "TaskStateError",
    "TaskStatus",
    "assert_transition",
    "is_valid_status",
]


# Member final statuses that are NOT reviewable as deliverables: the member
# died (terminated/error) or was cancelled by the user/stop_member
# (cancelled/interrupted). Consumers must NOT flip the plan node to
# ``in_review`` for these — it is already parked in ``rework``, and presenting
# a dead run as a pending deliverable confuses the lead.
NON_REVIEWABLE_DONE: frozenset[str] = frozenset(
    {"terminated", "error", "cancelled", "interrupted"}
)


# A ``valuz_task_session`` row's lifecycle. The one task vocabulary that was
# still enforced only by a comment, while plan nodes, task status, member
# dispositions and inbox kinds all had a Literal.
#   active    — run in flight
#   paused    — parked by a task pause/stop; resumable
#   completed — finished normally, or approved by review_subtask
#   rejected  — user-cancelled (stop_member / an interrupted member turn)
#   archived  — the run errored terminally
RunStatus = Literal["active", "paused", "completed", "rejected", "archived"]

# What a member session is: the lead's own run, or one dispatched subtask.
# Spelled three ways before (``kind`` on the row, ``role`` on the actor loop)
# for one set of two values.
RunKind = Literal["lead", "subtask"]
