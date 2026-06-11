"""ProviderPolicyPort — default allows; a bound deny-policy yields 403.

Mounts only the providers router on an isolated app, overriding the service
and current-user deps so the policy gate is exercised without DB/network.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_current_user_id, get_provider_service
from valuz_agent.api.routes.providers import router
from valuz_agent.ports.provider_policy import (
    AllowAllProviderPolicy,
    PolicyDecision,
    ProviderWriteContext,
    get_provider_policy,
    set_provider_policy,
)


@pytest.mark.asyncio
async def test_default_policy_allows() -> None:
    decision = await get_provider_policy().authorize_write(
        ProviderWriteContext(user_id="u1", action="create")
    )
    assert decision.allowed is True


class _DenyPolicy:
    async def authorize_write(self, ctx: ProviderWriteContext) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason="locked by org")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    # The deny path short-circuits before the service is touched, but FastAPI
    # still resolves the dependency — stub it so no DB is needed.
    app.dependency_overrides[get_provider_service] = lambda: object()
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    return TestClient(app)


def test_create_blocked_when_policy_denies(client: TestClient) -> None:
    set_provider_policy(_DenyPolicy())
    try:
        resp = client.post(
            "/v1/providers",
            json={"name": "My Key", "provider_kind": "anthropic", "api_key": "sk-x"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "provider.custom_models_locked"
    finally:
        set_provider_policy(AllowAllProviderPolicy())


def test_update_blocked_when_policy_denies(client: TestClient) -> None:
    set_provider_policy(_DenyPolicy())
    try:
        resp = client.patch("/v1/providers/some-id", json={"name": "x"})
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "provider.custom_models_locked"
    finally:
        set_provider_policy(AllowAllProviderPolicy())
