"""The two lead playbooks are one body with two headers — keep them that way.

They used to be hand-maintained copies that were ~60% identical, and they
drifted apart in exactly the way duplicated prose does: each kept guidance the
other lacked. The committed copy taught ``dispatch(key)`` when the handler
requires ``subtask_key`` and hard-fails without it; the kickoff copy never
explained ``<user-instruction>`` or ``expected_version`` even though a
kickoff-path lead receives both. Those were live defects — the model acts on
these strings.
"""

from __future__ import annotations

import pytest

from valuz_agent.adapters.agent_resolver import (
    COMMITTED_LEAD_PLAYBOOK,
    DISPATCH_PLAYBOOK,
)

BOTH = pytest.mark.parametrize(
    "playbook",
    [DISPATCH_PLAYBOOK, COMMITTED_LEAD_PLAYBOOK],
    ids=["kickoff", "committed"],
)


@BOTH
@pytest.mark.parametrize(
    "must_contain",
    [
        # The real parameter name — the handler rejects the call without it.
        "dispatch(subtask_key=...)",
        "review_subtask(subtask_key=...",
        # Guidance that lived in only ONE copy before the merge:
        "expected_version",  # was committed-only
        "PLAN_VERSION_CONFLICT",  # was committed-only
        "<user-instruction source=\"chat\">",  # was committed-only
        "in_review/rework/paused",  # was kickoff-only (and missed `paused`)
        "BLOCKS you for minutes",  # was kickoff-only
        "<system-recovery>",
        # The rework branch the tool result actually reports.
        "delivered_to_live_member",
    ],
)
def test_both_playbooks_carry_the_shared_protocol(playbook: str, must_contain: str) -> None:
    assert must_contain in playbook


@BOTH
@pytest.mark.parametrize(
    "must_not_contain",
    [
        # Argument names the handlers reject (the drift that shipped).
        "dispatch(key)",
        "review_subtask(key,",
        # A removal primitive modify_plan does not have — following it leaves
        # the lead with a node it cannot clear and a finish_task it cannot pass.
        "remove them",
    ],
)
def test_neither_playbook_teaches_a_refused_call(playbook: str, must_not_contain: str) -> None:
    assert must_not_contain not in playbook


def test_only_step_one_differs() -> None:
    """The variants exist for ONE reason: plan first vs read the committed
    plan. Everything from step 2 on is the shared body — if this fails, the
    copies are drifting again."""
    tail_marker = "\n2. DISPATCH INDEPENDENT SUBTASKS IN PARALLEL."
    assert DISPATCH_PLAYBOOK[DISPATCH_PLAYBOOK.index(tail_marker) :] == (
        COMMITTED_LEAD_PLAYBOOK[COMMITTED_LEAD_PLAYBOOK.index(tail_marker) :]
    )
    assert "1. PLAN FIRST." in DISPATCH_PLAYBOOK
    assert "1. READ THE PLAN." in COMMITTED_LEAD_PLAYBOOK
    assert "DO NOT call plan_task" in COMMITTED_LEAD_PLAYBOOK


def test_chat_playbook_states_the_routing_once() -> None:
    """It is injected into EVERY project chat session, so its size is a
    per-conversation tax. It used to state the same status routing three
    times (Step 0, a "Reviving a … lead" section, and a "Quick rules of
    thumb" recap that was a strict subset of both) — and the copies
    disagreed about whether a halted task needs resume_task before inject.
    """
    from valuz_agent.adapters.agent_resolver import CHAT_TASK_PLAYBOOK as chat

    assert "Quick rules of thumb" not in chat
    assert "Reviving a paused/blocked/stopped/completed lead" not in chat
    # inject_into_task revives a halted task itself (recovery.inject_or_revive)
    # — prescribing resume-then-inject costs the model an extra call.
    assert "resume_task`` first, then inject" not in chat
    assert "TASK_RESUMED" in chat
    # The one thing inject genuinely cannot do must still be stated.
    assert "abandoned" in chat


@BOTH
def test_a_playbook_says_it_outranks_what_follows(playbook: str) -> None:
    """The lead's role must be stated as OVERRIDING, not merely present.

    A task lead's prompt also carries the user's standing instructions, which
    are written for the agent working alone and routinely describe their own
    way of planning and delegating. Two plausible sets of rules with no stated
    precedence is a coin flip, and it came up wrong on qa: the lead planned
    with the runtime's built-in todo tool and delegated with its built-in
    subagent tool, so the task closed with an empty plan and no members.
    """
    assert "OVERRIDES EVERYTHING BELOW" in playbook
    assert "built-in todo / Task /" in playbook, (
        "name the competing tools — 'follow the playbook' does not tell a model "
        "which of two familiar tools to stop reaching for"
    )


def test_the_lead_role_is_declared_before_the_standing_instructions() -> None:
    """Order is the mechanism; the override sentence only names it.

    Measured on a real qa lead: 49,939 characters of prompt, of which the
    user's own global instructions were 39,710 (79.5%) and carried a
    "## Task Planning" section of their own at 10.2%. The playbook started at
    82.6%. Whatever the playbook says about precedence, it has to be read
    before the thing it overrides — so this pins the ordering itself, which is
    a list literal one edit away from silently reverting.
    """
    import inspect

    from valuz_agent.adapters import agent_resolver

    src = inspect.getsource(agent_resolver)
    body = src[src.index("instructions = assemble_session_instructions") :]
    body = body[: body.index("ensure_citation_system_policy")]

    def at(tag: str) -> int:
        i = body.find(f'"{tag}"')
        assert i != -1, f"{tag} is no longer part of the session instructions"
        return i

    assert at("task-playbook") < at("global-instructions"), (
        "the task playbook must be assembled BEFORE the user's standing "
        "instructions — a lead that meets its role 40k characters in follows "
        "whatever it read first"
    )
    assert at("task-playbook") < at("agent-instructions")
    assert at("member-roster") < at("task-playbook"), (
        "the playbook tells the lead its members are listed above"
    )
