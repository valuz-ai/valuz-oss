"""Tests for GET /v1/runtimes.

Availability is kernel-sourced (design §3.3): the route reads
``kernel_client.runtime_availability()`` and merges it with the static registry
metadata (display label + supported protocols). These tests mock the kernel
client so they don't depend on real binaries; the binary probe itself is covered
by ``tests/runtimes/test_runtime_availability.py``.

Mounts only the runtimes router on an isolated FastAPI app so tests don't pull in
the rest of the boot pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.routes.runtimes import router

ALL_AVAILABLE = {
    "claude_agent": {"available": True, "unavailable_reason": None},
    "deepagents": {"available": True, "unavailable_reason": None},
    "codex": {"available": True, "unavailable_reason": None},
    "deepseek_harness": {"available": True, "unavailable_reason": None},
}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _mock_availability(mapping: dict) -> object:
    """Patch the module facade the route calls."""
    return patch(
        "valuz_agent.adapters.kernel_client.runtime_availability",
        new=AsyncMock(return_value=mapping),
    )


def test_should_return_four_runtimes(client: TestClient) -> None:
    with _mock_availability(ALL_AVAILABLE):
        resp = client.get("/v1/runtimes")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["runtimes"]]
    assert set(ids) == {"claude_agent", "codex", "deepagents", "deepseek_harness"}


def test_should_carry_display_name_and_supported_protocols(client: TestClient) -> None:
    with _mock_availability(ALL_AVAILABLE):
        resp = client.get("/v1/runtimes")
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}

    # ``supported_protocols`` uses the 4-value user-facing hyphen enum, mirroring
    # ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` — static registry metadata, not
    # affected by availability.
    assert by_id["claude_agent"]["display_name"] == "Claude Code"
    assert by_id["claude_agent"]["supported_protocols"] == ["anthropic"]
    assert by_id["codex"]["display_name"] == "OpenAI Codex"
    assert by_id["codex"]["supported_protocols"] == ["openai-response"]
    assert by_id["deepagents"]["display_name"] == "Deep Agents"
    assert set(by_id["deepagents"]["supported_protocols"]) == {
        "anthropic",
        "openai-completion",
        "gemini",
    }


def test_reflects_kernel_reported_availability(client: TestClient) -> None:
    mapping = {
        "claude_agent": {"available": True, "unavailable_reason": None},
        "deepagents": {"available": True, "unavailable_reason": None},
        "codex": {"available": False, "unavailable_reason": "codex binary not found"},
    }
    with _mock_availability(mapping):
        resp = client.get("/v1/runtimes")
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}

    assert by_id["codex"]["available"] is False
    assert "codex" in by_id["codex"]["unavailable_reason"]
    assert by_id["codex"]["requires_binary"] == "codex"  # static metadata preserved
    assert by_id["claude_agent"]["available"] is True
    assert by_id["claude_agent"]["requires_binary"] is None


def test_codex_available_when_kernel_reports_it(client: TestClient) -> None:
    with _mock_availability(ALL_AVAILABLE):
        resp = client.get("/v1/runtimes")
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}
    assert by_id["codex"]["available"] is True
    assert by_id["codex"]["unavailable_reason"] is None


class _Override:
    def __init__(self, ids: set[str]) -> None:
        self._ids = ids

    def available_runtimes(self) -> set[str]:
        return self._ids


def test_availability_override_wins_over_kernel(client: TestClient) -> None:
    from valuz_agent.ports.extensions import ext

    # Override declares all available → kernel is not consulted.
    with (
        patch.object(
            ext,
            "runtime_availability",
            _Override({"claude_agent", "codex", "deepagents", "deepseek_harness"})
        ),
        patch(
            "valuz_agent.adapters.kernel_client.runtime_availability",
            new=AsyncMock(side_effect=AssertionError("kernel must not be asked when overridden")),
        ),
    ):
        resp = client.get("/v1/runtimes")
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}
    assert all(r["available"] for r in by_id.values())


def test_availability_override_marks_missing_runtime_unavailable(client: TestClient) -> None:
    from valuz_agent.ports.extensions import ext

    with patch.object(ext, "runtime_availability", _Override({"claude_agent", "deepagents"})):
        resp = client.get("/v1/runtimes")
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}
    assert by_id["codex"]["available"] is False
    assert by_id["codex"]["unavailable_reason"]
    assert by_id["claude_agent"]["available"] is True


def test_falls_back_to_local_probe_when_kernel_unreachable(client: TestClient) -> None:
    # Kernel errors → route degrades to the host is_runtime_available probe so
    # the picker still renders instead of 500-ing.
    with (
        patch(
            "valuz_agent.adapters.kernel_client.runtime_availability",
            new=AsyncMock(side_effect=RuntimeError("kernel down")),
        ),
        patch(
            "valuz_agent.api.routes.runtimes.is_runtime_available",
            return_value=(True, None),
        ),
    ):
        resp = client.get("/v1/runtimes")
    assert resp.status_code == 200
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}
    assert all(r["available"] for r in by_id.values())
