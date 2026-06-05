"""Translation tests for the approval contract events.

V5+1aae940 introduces two new kernel-level event types,
``requires_action`` and ``action_resolved``, which the host translates
into ``session.requires_action`` / ``session.action_resolved`` for the
legacy SSE wire contract. These tests pin both directions so that a
future kernel rev or frontend renderer drift surfaces as a fast-failing
test rather than a silent UI freeze when an approval card never renders.
"""

from __future__ import annotations

import json

from valuz_agent.adapters.event_sse_adapter import _translate_kernel_event


def test_should_translate_requires_action_for_shell_command_subject():
    payload = {
        "command": "ls /tmp",
        "cwd": "/Users/test/workspace",
        "network": False,
        "reason": "List temp dir",
    }
    result = _translate_kernel_event(
        "requires_action",
        {
            "pending_id": "pending-abc",
            "subject": "shell_command",
            "runtime_provider": "claude_agent",
            "available_decisions": ["approve", "reject"],
            "payload": payload,
            "expires_at": "2026-05-12T13:14:15Z",
            "message_id": "msg-7",
        },
    )

    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.requires_action"
    # The structured payload + decisions array round-trip through JSON
    # so the legacy ``Record<string,string>`` contract still holds.
    assert json.loads(body["payload"]) == payload
    assert json.loads(body["available_decisions"]) == ["approve", "reject"]
    assert body["pending_id"] == "pending-abc"
    assert body["subject"] == "shell_command"
    assert body["runtime_provider"] == "claude_agent"
    assert body["expires_at"] == "2026-05-12T13:14:15Z"
    assert body["message_id"] == "msg-7"


def test_should_translate_requires_action_for_clarifying_questions():
    questions = [
        {
            "question": "Pick a side",
            "header": "Choose one",
            "multiSelect": False,
            "options": [
                {"label": "Yes", "description": "Confirm"},
                {"label": "No", "description": "Skip"},
            ],
        }
    ]
    result = _translate_kernel_event(
        "requires_action",
        {
            "pending_id": "pending-q1",
            "subject": "clarifying_questions",
            "runtime_provider": "claude_agent",
            "available_decisions": ["answer", "reject"],
            "payload": {"questions": questions},
            "message_id": "msg-9",
        },
    )

    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.requires_action"
    # The ``answer`` decision is what the frontend submits back with
    # ``answers`` populated; assert it's announced as available.
    assert "answer" in json.loads(body["available_decisions"])
    decoded_payload = json.loads(body["payload"])
    assert decoded_payload["questions"][0]["question"] == "Pick a side"


def test_should_translate_action_resolved_with_user_decision():
    result = _translate_kernel_event(
        "action_resolved",
        {
            "pending_id": "pending-abc",
            "decision": "approve",
            "resolved_by": "user",
            "message": "",
            "message_id": "msg-7",
        },
    )

    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.action_resolved"
    assert body["pending_id"] == "pending-abc"
    assert body["decision"] == "approve"
    assert body["resolved_by"] == "user"
    # ``answers`` is always emitted (stringified empty dict) so the
    # frontend can parse it without a presence check.
    assert json.loads(body["answers"]) == {}


def test_should_translate_action_resolved_with_answer_decision_and_answers():
    answers = {"Pick a side": "Yes", "Languages": ["Python", "Rust"]}
    result = _translate_kernel_event(
        "action_resolved",
        {
            "pending_id": "pending-q1",
            "decision": "answer",
            "resolved_by": "user",
            "answers": answers,
            "message_id": "msg-9",
        },
    )

    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.action_resolved"
    assert body["decision"] == "answer"
    assert json.loads(body["answers"]) == answers


def test_should_translate_action_resolved_with_system_expired_seal():
    # Synthetic seal emitted by ``scan_orphan_pendings`` at host startup
    # when a pending was still open from the previous process. Must
    # arrive at the frontend even though it never had a user-side
    # decision — otherwise the renderer waits forever on a dead card.
    result = _translate_kernel_event(
        "action_resolved",
        {
            "pending_id": "stale-1",
            "decision": "expired",
            "resolved_by": "system",
            "message_id": "msg-2",
        },
    )

    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.action_resolved"
    assert body["decision"] == "expired"
    assert body["resolved_by"] == "system"


# ---------------------------------------------------------------------------
# V5+d008b53 — approval contract v2 fields
# ---------------------------------------------------------------------------


def test_should_carry_session_rule_preview_on_requires_action_payload():
    # V5+d008b53: ``approve_for_session`` (v2) parks now carry a structured
    # ``session_rule_preview`` so the frontend can label the "Always for
    # this session" button with what's actually being remembered.
    preview = {
        "kind": "shell_command",
        "display": "Bash(npm test:*)",
        "runtime_kind": "claude_pattern",
        "rule_data": {"pattern": "Bash(npm test:*)"},
    }
    result = _translate_kernel_event(
        "requires_action",
        {
            "pending_id": "pending-rule-1",
            "subject": "shell_command",
            "runtime_provider": "claude_agent",
            "available_decisions": ["approve", "approve_for_session", "reject"],
            "payload": {"command": "npm test", "cwd": "/work/proj"},
            "session_rule_preview": preview,
            "message_id": "msg-11",
        },
    )

    assert result is not None
    _legacy_type, body = result
    # JSON-stringified so the legacy ``Record<string,string>`` contract holds.
    assert json.loads(body["session_rule_preview"]) == preview


def test_should_carry_original_input_on_requires_action_payload():
    # V5+d008b53: ``approve_with_changes`` (A1) needs the original args
    # dict so the frontend's Edit & Approve JSON editor can seed from it
    # before the user mutates.
    original = {"command": "ls /tmp", "cwd": "/home/user", "timeout": 30}
    result = _translate_kernel_event(
        "requires_action",
        {
            "pending_id": "pending-edit-1",
            "subject": "shell_command",
            "runtime_provider": "claude_agent",
            "available_decisions": ["approve", "approve_with_changes", "reject"],
            "payload": original,
            "original_input": original,
            "message_id": "msg-12",
        },
    )

    assert result is not None
    _legacy_type, body = result
    assert json.loads(body["original_input"]) == original


def test_should_emit_empty_strings_for_v2_fields_when_kernel_omits_them():
    # Forward/backward compat: a kernel that doesn't emit
    # ``session_rule_preview`` / ``original_input`` (older revs, or a
    # subject like ``clarifying_questions`` that intentionally skips
    # the rule path) must still produce a frame with stable keys so
    # the frontend parser sees a consistent shape.
    result = _translate_kernel_event(
        "requires_action",
        {
            "pending_id": "pending-clarify-1",
            "subject": "clarifying_questions",
            "runtime_provider": "claude_agent",
            "available_decisions": ["answer", "reject"],
            "payload": {"questions": []},
            "message_id": "msg-13",
        },
    )

    assert result is not None
    _legacy_type, body = result
    assert body["session_rule_preview"] == "{}"
    assert body["original_input"] == "{}"


def test_should_translate_action_resolved_for_approve_for_session_with_rule_id():
    # V5+d008b53: when the user picks "Always for this session", the
    # kernel commits a rule and stamps its UUID on the action_resolved
    # event. The frontend uses this to render the rule badge on the
    # resolved card and to correlate future ``auto_approved`` events.
    result = _translate_kernel_event(
        "action_resolved",
        {
            "pending_id": "pending-rule-1",
            "decision": "approve_for_session",
            "resolved_by": "user",
            "rule_id": "rule-uuid-aaa",
            "message_id": "msg-11",
        },
    )

    assert result is not None
    _legacy_type, body = result
    assert body["decision"] == "approve_for_session"
    assert body["rule_id"] == "rule-uuid-aaa"


def test_should_translate_action_resolved_for_auto_approved_with_back_pointer():
    # V5+d008b53: kernel-synthesized ``auto_approved`` decision fired by
    # the SessionApprovalCache on a cache hit. Carries
    # ``auto_resolved_by_rule_id`` pointing back to the rule that fired,
    # so the frontend can render a small "auto-approved by rule X" strip
    # even though there was no preceding ``requires_action`` card.
    result = _translate_kernel_event(
        "action_resolved",
        {
            "pending_id": "pending-auto-2",
            "decision": "auto_approved",
            "resolved_by": "system",
            "auto_resolved_by_rule_id": "rule-uuid-aaa",
            "message_id": "msg-14",
        },
    )

    assert result is not None
    _legacy_type, body = result
    assert body["decision"] == "auto_approved"
    assert body["resolved_by"] == "system"
    assert body["auto_resolved_by_rule_id"] == "rule-uuid-aaa"


def test_should_translate_action_resolved_for_approve_with_changes():
    # V5+d008b53: A1 verb. The kernel records that the user approved
    # with edits; the runtime received the modified args and ran the
    # tool with them. The frontend renders the resolved strip with a
    # "approved with edits" badge.
    result = _translate_kernel_event(
        "action_resolved",
        {
            "pending_id": "pending-edit-1",
            "decision": "approve_with_changes",
            "resolved_by": "user",
            "message_id": "msg-12",
        },
    )

    assert result is not None
    _legacy_type, body = result
    assert body["decision"] == "approve_with_changes"
    # No rule_id on this verb — comes through empty so the parser
    # doesn't have to guard against a missing key.
    assert body["rule_id"] == ""
    assert body["auto_resolved_by_rule_id"] == ""
