"""Operation REST contract: request-changes feedback, lazy expiry, supersede."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes import operations as routes
from valuz_agent.infra.database import Base
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import OperationProposal
from valuz_agent.modules.operations.service import OperationService

USER = "owner-1"
OP = "test.routes.playbook_like"


async def _handler(_context: OperationContext, _payload: dict[str, Any]) -> OperationResult:
    return OperationResult(canonical_result_refs=[{"type": "thing", "id": "t1"}], result_payload={})


class _Projects:
    pass


async def _propose(db_url: str, key: str, target_id: str, **extra: Any) -> tuple[str, str]:
    """The agent side proposes outside the request, on its own engine so no
    connection is shared across event loops."""
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            row = await OperationService(db, _Projects()).propose(  # type: ignore[arg-type]
                USER,
                OperationProposal(
                    operation_type=OP,
                    actor_kind="agent",
                    target_refs=[{"type": "playbook_definition", "id": target_id}],
                    input_payload={"key": key},
                    confirmation_policy="confirm",
                    idempotency_key=f"{OP}:{key}",
                    **extra,
                ),
            )
            return row.id, row.proposal_hash
    finally:
        await engine.dispose()


@pytest.fixture
def harness(tmp_path):  # type: ignore[no-untyped-def]
    operation_registry.register(
        OperationRegistration(operation_type=OP, version=1, handler=_handler)
    )
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}"
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def service() -> AsyncGenerator[OperationService, None]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions.begin() as db:
            yield OperationService(db, _Projects())  # type: ignore[arg-type]

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[routes.get_operation_service] = service

    def propose(key: str, target_id: str, **extra: Any) -> tuple[str, str]:
        return asyncio.run(_propose(db_url, key, target_id, **extra))

    with TestClient(app) as client:
        yield client, propose


def test_request_changes_is_readable_back_and_leaves_the_proposal_confirmable(harness) -> None:  # type: ignore[no-untyped-def]
    client, propose = harness
    op_id, digest = propose("k1", "def-1")

    bad = client.post(f"/v1/operations/{op_id}/request-changes", json={"proposal_hash": digest})
    assert bad.status_code == 422, "a comment is required"

    revised = client.post(
        f"/v1/operations/{op_id}/request-changes",
        json={"proposal_hash": digest, "comment": "Shorten the prompt."},
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["state"] == "awaiting_confirmation"
    assert body["latest_decision"]["decision"] == "request_changes"
    assert body["latest_decision"]["comment"] == "Shorten the prompt."
    assert body["latest_decision"]["decided_by"] == USER

    fetched = client.get(f"/v1/operations/{op_id}").json()
    assert fetched["latest_decision"]["comment"] == "Shorten the prompt."
    assert fetched["expires_at"] is None and fetched["superseded_by_id"] is None

    batch = client.post("/v1/operations/status/batch", json={"operation_ids": [op_id]}).json()
    assert batch["operations"][op_id]["latest_decision"]["decision"] == "request_changes"

    confirmed = client.post(f"/v1/operations/{op_id}/confirm", json={"proposal_hash": digest})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "succeeded"
    assert confirmed.json()["latest_decision"]["decision"] == "approve"

    late = client.post(
        f"/v1/operations/{op_id}/request-changes",
        json={"proposal_hash": digest, "comment": "again"},
    )
    assert late.status_code == 409
    assert late.json()["detail"] == "operation_not_revisable:succeeded"


def test_expired_proposal_is_reported_lazily_and_confirm_is_refused(harness) -> None:  # type: ignore[no-untyped-def]
    client, propose = harness
    op_id, digest = propose("k2", "def-2", expires_at=now_ms() - 1)

    assert client.get(f"/v1/operations/{op_id}").json()["state"] == "expired"

    refused = client.post(f"/v1/operations/{op_id}/confirm", json={"proposal_hash": digest})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "operation_not_confirmable:expired"

    body = client.get(f"/v1/operations/{op_id}").json()
    assert (body["state"], body["error_code"]) == ("expired", "OPERATION_EXPIRED")


def test_reproposal_supersedes_and_the_view_names_the_successor(harness) -> None:  # type: ignore[no-untyped-def]
    client, propose = harness
    first_id, first_digest = propose("k3", "def-3")
    second_id, _ = propose("k4", "def-3")

    body = client.get(f"/v1/operations/{first_id}").json()
    assert (body["state"], body["superseded_by_id"]) == ("superseded", second_id)
    refused = client.post(
        f"/v1/operations/{first_id}/confirm", json={"proposal_hash": first_digest}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "operation_not_confirmable:superseded"
    assert client.get(f"/v1/operations/{second_id}").json()["state"] == "awaiting_confirmation"
