"""VALUZ-CHATPLAN S1 — task status state machine (pure)."""

from __future__ import annotations

import pytest

from valuz_agent.modules.tasks.task_state import (
    ALLOWED_TRANSITIONS,
    LIVE_STATUSES,
    RECOVERABLE_STATUSES,
    TASK_STATUSES,
    TERMINAL_STATUSES,
    TaskStateError,
    assert_transition,
    is_live,
    is_recoverable,
    is_terminal,
    is_valid_status,
)

# ── status set membership ─────────────────────────────────────────────────


def test_seven_recognized_statuses() -> None:
    assert set(TASK_STATUSES) == {
        "draft",
        "active",
        "paused",
        "stopped",
        "completed",
        "blocked",
        "abandoned",
    }


def test_terminal_statuses_are_terminal_only() -> None:
    # ``abandoned`` has no transitions out (hard terminal). ``completed`` is
    # display-terminal but soft-revivable (completed → active reopen), so it
    # is NOT frozenset() — see test_completed_soft_terminal_can_reopen.
    assert ALLOWED_TRANSITIONS["abandoned"] == frozenset()
    assert ALLOWED_TRANSITIONS["completed"] == frozenset({"active"})


def test_terminal_set_matches_state_machine() -> None:
    # ``blocked`` is recoverable (back to active or stopped) — must NOT be terminal.
    # ``stopped`` is a soft terminal: revivable via resume_task (state machine
    # allows stopped → active). Only completed/abandoned are HARD terminals.
    assert TERMINAL_STATUSES == {"completed", "abandoned"}
    assert "blocked" not in TERMINAL_STATUSES
    assert "stopped" not in TERMINAL_STATUSES


def test_live_set_excludes_terminals_and_blocked() -> None:
    # blocked is "alive but stuck" — UX still lets the user resume / stop it,
    # but it isn't "in motion" for sweeper purposes. Keep blocked out of LIVE.
    assert LIVE_STATUSES == {"draft", "active", "paused"}
    assert TERMINAL_STATUSES.isdisjoint(LIVE_STATUSES)


def test_recoverable_set_is_active_and_paused() -> None:
    # startup recovery / inject only acts on these.
    assert RECOVERABLE_STATUSES == {"active", "paused"}


# ── predicate functions ──────────────────────────────────────────────────


def test_is_valid_status_accepts_all_known() -> None:
    for s in TASK_STATUSES:
        assert is_valid_status(s)


def test_is_valid_status_rejects_unknown() -> None:
    assert not is_valid_status("failed")  # subtask-level only
    assert not is_valid_status("")
    assert not is_valid_status("ACTIVE")  # case-sensitive
    assert not is_valid_status("running")  # kernel session vocab, not task


def test_is_terminal_predicate() -> None:
    assert is_terminal("completed")
    assert is_terminal("abandoned")
    assert not is_terminal("active")
    assert not is_terminal("draft")
    assert not is_terminal("blocked")  # recoverable
    assert not is_terminal("stopped")  # soft terminal: revivable via resume_task


def test_is_live_predicate() -> None:
    assert is_live("draft")
    assert is_live("active")
    assert is_live("paused")
    assert not is_live("completed")
    assert not is_live("blocked")  # excluded by design (see test above)


def test_is_recoverable_predicate() -> None:
    assert is_recoverable("active")
    assert is_recoverable("paused")
    assert not is_recoverable("draft")  # no lead session to recover
    assert not is_recoverable("abandoned")
    assert not is_recoverable("completed")


# ── happy-path transitions ────────────────────────────────────────────────


def test_initial_insert_draft_or_active() -> None:
    # Two valid entry points: chat-plan-then-execute starts at draft;
    # legacy direct kickoff starts at active.
    assert_transition(None, "draft")
    assert_transition(None, "active")


def test_chat_plan_then_execute_flow() -> None:
    # draft -> active (commit) -> completed
    assert_transition(None, "draft")
    assert_transition("draft", "active")
    assert_transition("active", "completed")


def test_legacy_kickoff_flow() -> None:
    # active -> paused -> active -> completed
    assert_transition(None, "active")
    assert_transition("active", "paused")
    assert_transition("paused", "active")
    assert_transition("active", "completed")


def test_draft_abandon_flow() -> None:
    assert_transition(None, "draft")
    assert_transition("draft", "abandoned")


def test_blocked_recovery_paths() -> None:
    # blocked can go back to active (e.g. user resolves) or stopped (user gives up)
    assert_transition("active", "blocked")
    assert_transition("blocked", "active")
    assert_transition("blocked", "stopped")


# ── illegal transitions ──────────────────────────────────────────────────


def test_abandoned_rejects_all_transitions() -> None:
    """``abandoned`` is the only HARD-terminal state — a discarded draft has
    no plan to revive, so every transition out is illegal. ``stopped`` and
    ``completed`` are soft terminals (each has a → active escape; see
    test_stopped_soft_terminal_can_revive / test_completed_soft_terminal_can_reopen)."""
    for target in TASK_STATUSES:
        with pytest.raises(TaskStateError, match="illegal transition"):
            assert_transition("abandoned", target)


def test_stopped_soft_terminal_can_revive() -> None:
    """stopped → active is the only escape; everything else is illegal."""
    assert_transition("stopped", "active")  # allowed
    for target in TASK_STATUSES:
        if target == "active":
            continue
        with pytest.raises(TaskStateError, match="illegal transition"):
            assert_transition("stopped", target)


def test_completed_soft_terminal_can_reopen() -> None:
    """completed → active is the only escape — a finished task can be REOPENED
    to supplement/adjust subtasks (区分场景). Everything else is illegal."""
    assert_transition("completed", "active")  # allowed (reopen)
    for target in TASK_STATUSES:
        if target == "active":
            continue
        with pytest.raises(TaskStateError, match="illegal transition"):
            assert_transition("completed", target)


def test_draft_cannot_jump_to_paused() -> None:
    # Must commit (-> active) before pausing.
    with pytest.raises(TaskStateError, match="draft.*paused"):
        assert_transition("draft", "paused")


def test_draft_cannot_jump_to_completed() -> None:
    with pytest.raises(TaskStateError):
        assert_transition("draft", "completed")


def test_active_cannot_become_draft() -> None:
    # Once committed, a task cannot go back to draft state.
    with pytest.raises(TaskStateError, match="active.*draft"):
        assert_transition("active", "draft")


def test_active_cannot_become_abandoned() -> None:
    # abandoned is for discarded drafts only — active tasks use stopped instead.
    with pytest.raises(TaskStateError, match="active.*abandoned"):
        assert_transition("active", "abandoned")


def test_paused_cannot_jump_to_completed() -> None:
    # Must resume (-> active) before completing.
    with pytest.raises(TaskStateError):
        assert_transition("paused", "completed")


def test_initial_to_completed_rejected() -> None:
    # A new task cannot be inserted as already-completed.
    with pytest.raises(TaskStateError, match="None.*completed"):
        assert_transition(None, "completed")


# ── input validation ─────────────────────────────────────────────────────


def test_unknown_target_status_rejected() -> None:
    with pytest.raises(TaskStateError, match="unknown target"):
        assert_transition("active", "failed")  # subtask vocab


def test_unknown_source_status_rejected() -> None:
    with pytest.raises(TaskStateError, match="unknown source"):
        assert_transition("running", "active")  # kernel session vocab


def test_empty_string_status_rejected() -> None:
    with pytest.raises(TaskStateError):
        assert_transition("active", "")


# ── invariants over the whole graph ──────────────────────────────────────


def test_every_status_appears_in_transition_map() -> None:
    # Every known status must have an entry in ALLOWED_TRANSITIONS (even if
    # the value is an empty frozenset for terminals).
    for s in TASK_STATUSES:
        assert s in ALLOWED_TRANSITIONS


def test_no_status_transitions_to_itself() -> None:
    for s, targets in ALLOWED_TRANSITIONS.items():
        if s is None:
            continue
        assert s not in targets, f"{s!r} should not transition to itself"


def test_all_transition_targets_are_known_statuses() -> None:
    for src, targets in ALLOWED_TRANSITIONS.items():
        for t in targets:
            assert is_valid_status(t), f"transition {src!r} → {t!r}: target unknown"
