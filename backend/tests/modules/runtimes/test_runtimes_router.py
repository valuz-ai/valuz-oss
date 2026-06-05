"""Tests for GET /v1/runtimes.

Mounts only the runtimes router on an isolated FastAPI app so tests
don't pull in the rest of the boot pipeline (DB seeders, skill watcher,
etc). The router has no DB or service-layer dependencies.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.routes.runtimes import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_codex_env_override():
    """Test isolation: never let CODEX_BIN_OVERRIDE leak between tests."""
    import os

    saved = os.environ.pop("CODEX_BIN_OVERRIDE", None)
    yield
    if saved is not None:
        os.environ["CODEX_BIN_OVERRIDE"] = saved


def test_should_return_three_runtimes(client: TestClient) -> None:
    resp = client.get("/v1/runtimes")
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["id"] for r in body["runtimes"]]
    assert set(ids) == {"claude_agent", "codex", "deepagents"}


def test_should_carry_display_name_and_supported_protocols(client: TestClient) -> None:
    resp = client.get("/v1/runtimes")
    body = resp.json()
    by_id = {r["id"]: r for r in body["runtimes"]}

    # Kernel V5+bba3014: ``supported_protocols`` uses the 4-value
    # user-facing hyphen enum (``anthropic | openai-completion |
    # openai-response | gemini``). Each runtime exposes only the
    # protocols its SDK can dispatch — mirrors
    # ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME``.
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


def test_should_mark_pure_python_runtimes_available(client: TestClient) -> None:
    resp = client.get("/v1/runtimes")
    by_id = {r["id"]: r for r in resp.json()["runtimes"]}

    assert by_id["claude_agent"]["available"] is True
    assert by_id["claude_agent"]["unavailable_reason"] is None
    assert by_id["claude_agent"]["requires_binary"] is None

    assert by_id["deepagents"]["available"] is True
    assert by_id["deepagents"]["unavailable_reason"] is None
    assert by_id["deepagents"]["requires_binary"] is None


def test_codex_should_be_unavailable_when_binary_missing(client: TestClient) -> None:
    with patch(
        "valuz_agent.adapters.runtime_registry.shutil.which",
        return_value=None,
    ):
        resp = client.get("/v1/runtimes")

    by_id = {r["id"]: r for r in resp.json()["runtimes"]}
    assert by_id["codex"]["available"] is False
    assert by_id["codex"]["unavailable_reason"] is not None
    assert "codex" in by_id["codex"]["unavailable_reason"]
    assert by_id["codex"]["requires_binary"] == "codex"


def test_codex_should_be_available_when_binary_on_path(client: TestClient) -> None:
    fake_path = "/usr/local/bin/codex"
    with patch(
        "valuz_agent.adapters.runtime_registry.shutil.which",
        return_value=fake_path,
    ):
        resp = client.get("/v1/runtimes")

    by_id = {r["id"]: r for r in resp.json()["runtimes"]}
    assert by_id["codex"]["available"] is True
    assert by_id["codex"]["unavailable_reason"] is None
