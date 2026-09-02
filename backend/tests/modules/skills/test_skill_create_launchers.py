"""Contract tests for the skill-creator launch endpoints.

Per 09-assistant every conversation binds an agent. The AI-create launchers
previously minted sessions through the agentless raw-model path, which left
the conversation composer with no agent selected and — since a live session
locks its binding — no way to pick one: a dead conversation. These tests pin
the contract that both launchers bind the seeded default assistant when it
exists (and fall back to the legacy agentless path on a fresh install where
onboarding hasn't seeded it yet).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import valuz_agent.api.routes.skills as skills_routes
from valuz_agent.api.app import create_app
from valuz_agent.api.deps import get_session_service
from valuz_agent.modules.agents.seed import DEFAULT_ASSISTANT_SLUG


class _CapturingSessionService:
    """Stands in for SessionService; records create_session kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_session(self, project_id: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"project_id": project_id, **kwargs})
        return SimpleNamespace(id="sess-1", project_id="ws-resolved")


@pytest.fixture
def client_and_svc(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _CapturingSessionService]:
    app = create_app()
    svc = _CapturingSessionService()

    async def _override():  # type: ignore[no-untyped-def]
        yield svc

    async def _default_present(user_id: str) -> str | None:  # noqa: ARG001
        return DEFAULT_ASSISTANT_SLUG

    app.dependency_overrides[get_session_service] = _override
    # The launcher looks the default assistant up in the DB; these tests run
    # without lifespan/DB, so pin the lookup to "present".
    monkeypatch.setattr(skills_routes, "_default_assistant_slug_if_present", _default_present)
    # No lifespan: these routes touch nothing beyond the overridden service.
    return TestClient(app), svc


def test_start_create_binds_default_assistant(client_and_svc) -> None:
    client, svc = client_and_svc
    resp = client.post(
        "/v1/skills/create/start",
        json={"context": {"kind": "skills_library"}},
    )

    assert resp.status_code == 201
    assert resp.json()["session_id"] == "sess-1"
    (call,) = svc.calls
    assert call["agent_slug"] == DEFAULT_ASSISTANT_SLUG
    assert call["project_id"] == "chat-default"
    assert call["trigger_meta"] == {"mode": "skill-creator"}
    assert call["creation_context"] == {"kind": "skills_library"}


def test_start_create_project_kind_binds_default_assistant(client_and_svc) -> None:
    client, svc = client_and_svc
    resp = client.post(
        "/v1/skills/create/start",
        json={"context": {"kind": "project", "project_id": "ws-42"}},
    )

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] == DEFAULT_ASSISTANT_SLUG
    assert call["project_id"] == "ws-42"


def test_start_create_chat_shim_binds_default_assistant(client_and_svc) -> None:
    client, svc = client_and_svc
    resp = client.post("/v1/skills/create/chat/start", json={})

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] == DEFAULT_ASSISTANT_SLUG
    assert call["project_id"] == "chat-default"
    assert call["creation_context"] == {"kind": "chat"}


def test_falls_back_to_agentless_when_default_assistant_missing(
    client_and_svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh install (onboarding hasn't seeded the assistant) → legacy path."""
    client, svc = client_and_svc

    async def _absent(user_id: str) -> str | None:  # noqa: ARG001
        return None

    monkeypatch.setattr(skills_routes, "_default_assistant_slug_if_present", _absent)
    resp = client.post(
        "/v1/skills/create/start",
        json={"context": {"kind": "skills_library"}},
    )

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] is None


def test_explicit_agent_slug_wins_over_default(client_and_svc) -> None:
    """Draft-first frontend passes the composer's pick — it must win."""
    client, svc = client_and_svc
    resp = client.post(
        "/v1/skills/create/start",
        json={"context": {"kind": "skills_library"}, "agent_slug": "researcher"},
    )

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] == "researcher"


# ── agentless launch (composer parity) ────────────────────────────────


def test_explicit_brain_launches_without_binding_an_agent(client_and_svc) -> None:
    """A runtime + model pick with no agent runs on exactly that brain.

    Falling back to the default assistant here would hand the AGENT's
    runtime to a user who just picked one — the inconsistency this endpoint
    had with the conversation composer, which lets a quick chat run
    agentless.
    """
    client, svc = client_and_svc

    resp = client.post(
        "/v1/skills/create/start",
        json={
            "context": {"kind": "skills_library"},
            "provider_id": "prov-1",
            "model_id": "gpt-5",
            "runtime_id": "codex",
            "effort": "high",
        },
    )

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] is None
    assert call["provider_id"] == "prov-1"
    assert call["model_id"] == "gpt-5"
    assert call["runtime_id"] == "codex"
    assert call["effort"] == "high"


def test_a_named_agent_still_wins_over_the_default(client_and_svc) -> None:
    client, svc = client_and_svc

    resp = client.post(
        "/v1/skills/create/start",
        json={"context": {"kind": "skills_library"}, "agent_slug": "my-agent"},
    )

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] == "my-agent"


def test_choosing_nothing_still_falls_back_to_the_default_assistant(client_and_svc) -> None:
    """The fallback is for a launch that expressed no preference at all —
    it must not disappear along with the agent requirement."""
    client, svc = client_and_svc

    resp = client.post(
        "/v1/skills/create/start",
        json={"context": {"kind": "skills_library"}},
    )

    assert resp.status_code == 201
    (call,) = svc.calls
    assert call["agent_slug"] == DEFAULT_ASSISTANT_SLUG
