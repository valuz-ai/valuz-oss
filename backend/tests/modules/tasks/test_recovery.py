"""VALUZ-RESUME S1 — reconciliation core (pure)."""

from __future__ import annotations

from valuz_agent.modules.tasks.recovery import (
    RESUME_RETRY_CAP,
    classify_member,
    reconcile,
)

# ── classify_member ──────────────────────────────────────────────────────


def test_classify_created_session_is_resumable() -> None:
    assert classify_member("created", None) == "resume"


def test_classify_missing_session_is_resumable() -> None:
    assert classify_member(None, None) == "resume"


def test_classify_running_session_is_in_flight() -> None:
    assert classify_member("running", None) == "in_flight"


def test_classify_end_turn_is_completed() -> None:
    assert classify_member("idle", {"type": "end_turn"}) == "completed"


def test_classify_host_restart_is_resumable() -> None:
    assert classify_member("idle", {"type": "error", "category": "host_restart"}) == "resume"


def test_classify_real_error_is_failed() -> None:
    assert classify_member("idle", {"type": "error", "category": "execution_error"}) == "failed"


def test_classify_idle_without_stop_reason_is_resumable() -> None:
    assert classify_member("idle", None) == "resume"


def test_classify_accepts_error_object_stop_reason() -> None:
    class _Err:
        type = "error"
        category = "host_restart"

    assert classify_member("idle", _Err()) == "resume"


# ── reconcile ─────────────────────────────────────────────────────────────


def test_reconcile_completed_marks_in_review_and_notifies_lead() -> None:
    r = reconcile("idle", {"type": "end_turn"}, node_attempts=1)
    assert r.disposition == "completed"
    assert r.run_status == "completed"
    assert r.node_status == "in_review"
    assert r.deliver_member_done is True
    assert r.resume is False


def test_reconcile_failed_marks_rework_no_resume() -> None:
    r = reconcile("idle", {"type": "error", "category": "boom"}, node_attempts=0)
    assert r.disposition == "failed"
    assert r.run_status == "archived"
    assert r.node_status == "rework"
    assert r.resume is False


def test_reconcile_resumable_under_cap_respawns() -> None:
    r = reconcile("created", None, node_attempts=RESUME_RETRY_CAP - 1)
    assert r.disposition == "resume"
    assert r.run_status == "active"
    assert r.node_status == "in_progress"
    assert r.resume is True


def test_reconcile_resume_at_cap_becomes_rework() -> None:
    r = reconcile("created", None, node_attempts=RESUME_RETRY_CAP)
    assert r.disposition == "failed"
    assert r.node_status == "rework"
    assert r.resume is False
    assert "retry cap" in r.reason


def test_reconcile_in_flight_leaves_everything() -> None:
    r = reconcile("running", None, node_attempts=0)
    assert r.disposition == "in_flight"
    assert r.run_status is None and r.node_status is None
    assert r.resume is False and r.deliver_member_done is False
