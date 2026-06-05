"""Pydantic invariants on ``SessionActionRequest`` for the v2 approval verbs.

These tests pin the wire-shape gate that runs before any kernel code:
defense-in-depth so the orchestrator gets a clean shape regardless of
what the frontend sends. They don't spin up the FastAPI app —
``SessionActionRequest`` is just a Pydantic model and validation runs
at construction.

Coverage:
  - decision enum: only the 5 user-submittable verbs are accepted;
    kernel-only verbs (``auto_approved`` / ``expired`` / ``interrupted``)
    are rejected as input.
  - payload binding pairs:
      ``answer`` ↔ ``answers``
      ``approve_with_changes`` ↔ ``modified_input``
  - Plain verbs (``approve`` / ``approve_for_session`` / ``reject``)
    accept no payload field.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from valuz_agent.api.routes.sessions import SessionActionRequest


def test_should_accept_bare_approve():
    body = SessionActionRequest(pending_id="p-1", decision="approve")
    assert body.decision == "approve"
    assert body.answers is None
    assert body.modified_input is None


def test_should_accept_bare_reject_with_optional_message():
    body = SessionActionRequest(pending_id="p-1", decision="reject", message="user said no")
    assert body.decision == "reject"
    assert body.message == "user said no"


def test_should_accept_approve_for_session_without_extra_payload():
    # ``approve_for_session`` carries no client-side payload — the kernel
    # reuses the staged pending's ``session_rule_preview``. Pydantic must
    # accept the bare verb (no answers, no modified_input).
    body = SessionActionRequest(pending_id="p-1", decision="approve_for_session")
    assert body.decision == "approve_for_session"
    assert body.answers is None
    assert body.modified_input is None


def test_should_accept_answer_when_answers_is_present():
    body = SessionActionRequest(
        pending_id="p-1",
        decision="answer",
        answers={"Pick one": "Yes"},
    )
    assert body.answers == {"Pick one": "Yes"}


def test_should_reject_answer_when_answers_is_missing():
    with pytest.raises(ValidationError) as excinfo:
        SessionActionRequest(pending_id="p-1", decision="answer")
    assert "answers" in str(excinfo.value)


def test_should_reject_answers_on_non_answer_decision():
    with pytest.raises(ValidationError) as excinfo:
        SessionActionRequest(pending_id="p-1", decision="approve", answers={"q": "y"})
    assert "answers" in str(excinfo.value)


def test_should_accept_approve_with_changes_when_modified_input_is_present():
    body = SessionActionRequest(
        pending_id="p-1",
        decision="approve_with_changes",
        modified_input={"command": "ls --color=auto"},
    )
    assert body.modified_input == {"command": "ls --color=auto"}


def test_should_reject_approve_with_changes_when_modified_input_is_missing():
    with pytest.raises(ValidationError) as excinfo:
        SessionActionRequest(pending_id="p-1", decision="approve_with_changes")
    assert "modified_input" in str(excinfo.value)


def test_should_reject_modified_input_on_non_approve_with_changes_decision():
    with pytest.raises(ValidationError) as excinfo:
        SessionActionRequest(
            pending_id="p-1",
            decision="approve",
            modified_input={"command": "x"},
        )
    assert "modified_input" in str(excinfo.value)


@pytest.mark.parametrize(
    "kernel_only_verb",
    ["auto_approved", "expired", "interrupted"],
)
def test_should_reject_kernel_only_decision_verbs(kernel_only_verb: str):
    # Kernel-synthesized verbs must not be accepted on the wire — the
    # client should never claim to be auto-approving (kernel cache does
    # that) or sealing an expired pending (startup scan does that).
    with pytest.raises(ValidationError):
        SessionActionRequest(pending_id="p-1", decision=kernel_only_verb)  # type: ignore[arg-type]


def test_should_reject_unknown_decision_verb():
    with pytest.raises(ValidationError):
        SessionActionRequest(pending_id="p-1", decision="approve_forever")  # type: ignore[arg-type]
