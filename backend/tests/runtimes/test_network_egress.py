from __future__ import annotations

from src.runtimes.network_egress import (
    claude_api_key_credential_gate,
    is_loopback_url,
    merge_loopback_no_proxy,
)

import kernel  # noqa: F401


def test_loopback_detection_is_protocol_and_address_strict() -> None:
    assert is_loopback_url("http://127.0.0.1:9000/v1")
    assert is_loopback_url("https://[::1]:9000/v1")
    assert is_loopback_url("http://gateway.localhost/v1")
    assert not is_loopback_url("https://api.example/v1")
    assert not is_loopback_url("file:///tmp/socket")


def test_loopback_merge_is_a_noop_for_remote_targets() -> None:
    env = {"NO_PROXY": "localhost"}
    merge_loopback_no_proxy(env, "https://api.example/v1")
    assert env == {"NO_PROXY": "localhost"}


def test_claude_api_key_gate_matches_the_verified_permission_boundary() -> None:
    assert claude_api_key_credential_gate(
        permission_mode="default", session_mode="default"
    ).eligible
    assert claude_api_key_credential_gate(
        permission_mode="full_access", session_mode="goal"
    ).eligible
    assert (
        claude_api_key_credential_gate(permission_mode="auto_review", session_mode="default").reason
        == "claude_api_key_auto_review_scrub_incompatible"
    )
    assert (
        claude_api_key_credential_gate(permission_mode="default", session_mode="plan").reason
        == "claude_api_key_plan_scrub_incompatible"
    )
