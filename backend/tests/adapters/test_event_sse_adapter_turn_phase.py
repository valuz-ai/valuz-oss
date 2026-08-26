"""Translation tests for the ``turn_phase`` observability marker.

Runtimes emit one ``turn_phase`` kernel event per otherwise-invisible turn
boundary (``runtime_init`` / ``thread_init`` / ``dispatch``); the host
translates it into ``session.turn_phase`` so the latency breakdown can be
read straight off the session events API. No dedicated UI — the payload is
the whole product surface, so these tests pin its shape.
"""

from __future__ import annotations

from valuz_agent.adapters.event_sse_adapter import _translate_kernel_event


def test_turn_phase_translates_with_extras_stringified():
    result = _translate_kernel_event(
        "turn_phase",
        {
            "phase": "runtime_init",
            "duration_ms": 231,
            "mcp_tools_ms": 1180,
            "checkpointer_ms": 12,
            "message_id": "msg-1",
        },
    )
    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.turn_phase"
    assert body["phase"] == "runtime_init"
    # Legacy SSE contract is a flat string map — extras ride stringified.
    assert body["duration_ms"] == "231"
    assert body["mcp_tools_ms"] == "1180"
    assert body["checkpointer_ms"] == "12"
    assert body["message_id"] == "msg-1"


def test_turn_phase_thread_init_carries_mode():
    result = _translate_kernel_event(
        "turn_phase",
        {"phase": "thread_init", "mode": "resume", "duration_ms": 402},
    )
    assert result is not None
    _, body = result
    assert body["phase"] == "thread_init"
    assert body["mode"] == "resume"


def test_turn_phase_dispatch_minimal_payload():
    result = _translate_kernel_event("turn_phase", {"phase": "dispatch"})
    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.turn_phase"
    assert body["phase"] == "dispatch"


def test_turn_phase_post_run_verification_carries_lifecycle_and_features():
    result = _translate_kernel_event(
        "turn_phase",
        {
            "phase": "post_run_verification",
            "state": "started",
            "features": ["task_coverage", "citation", "claim_audit"],
            "message_id": "msg-post-run",
        },
    )

    assert result is not None
    legacy_type, body = result
    assert legacy_type == "session.turn_phase"
    assert body == {
        "phase": "post_run_verification",
        "state": "started",
        "features": '["task_coverage", "citation", "claim_audit"]',
        "message_id": "msg-post-run",
    }
