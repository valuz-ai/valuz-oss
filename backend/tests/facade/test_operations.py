"""Edition consumers use only the public operation contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.facade.operations import (
    OperationContext,
    OperationLibrary,
    OperationProposal,
    OperationRegistration,
    OperationResult,
    register_operation,
)
from valuz_agent.infra.database import Base
from valuz_agent.modules.operations.models import ConfirmationDecisionRow, OperationRecordRow


class EffectRow(Base):
    __tablename__ = "test_operation_facade_effect"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


class Projects:
    """These source-free test commands never resolve a Project."""


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [OperationRecordRow.__table__, ConfirmationDecisionRow.__table__, EffectRow.__table__]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def proposal(kind: str) -> OperationProposal:
    return OperationProposal(
        operation_type=kind,
        actor_kind="agent",
        origin_session_id="session-1",
        origin_tool_call_id="tool-1",
        origin_playbook_run_id="run-1",
        expected_revisions={"object-1": {"version": 3}},
        target_refs=[{"type": "test.object", "id": "object-1"}],
        input_payload={"value": "approved"},
        idempotency_key=kind,
    )


async def test_trusted_context_detached_views_and_idempotent_result(db: AsyncSession) -> None:
    calls = []

    async def handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        identity = context.operation
        assert identity is not None
        assert context.db is db and context.user_id == "owner"
        assert identity.proposal_hash == pending.proposal_hash
        assert identity.id == pending.id != context.decision["operation_id"]
        assert identity.project_id is None and identity.actor_kind == "agent"
        assert identity.origin_session_id == "session-1"
        assert identity.origin_tool_call_id == "tool-1"
        assert identity.origin_playbook_run_id == "run-1"
        assert identity.expected_revisions == {"object-1": {"version": 3}}
        calls.append(identity.id)
        db.add(EffectRow(id=identity.id, value=payload["value"]))
        identity.expected_revisions["object-1"]["version"] = 99
        identity.target_refs[0]["id"] = "mutated"
        payload["value"] = "mutated"
        return OperationResult([{"type": "test.effect", "id": identity.id}], {"id": identity.id})

    register_operation(
        OperationRegistration("test.facade.context", 1, handler, input_schema=Payload)
    )
    library = OperationLibrary(db, Projects())  # type: ignore[arg-type]
    pending = await library.propose("owner", proposal("test.facade.context"))
    pending.input_payload["value"] = "client change"
    assert (await library.get("owner", pending.id)).input_payload == {"value": "approved"}
    with pytest.raises(LookupError):
        await library.get("other-owner", pending.id)
    result = await library.confirm(
        "owner",
        pending.id,
        expected_proposal_hash=pending.proposal_hash,
        decision={"operation_id": "untrusted"},
    )
    assert result.state == "succeeded"
    assert result.latest_decision is not None and result.latest_decision.decision == "approve"
    assert result.input_payload == {"value": "approved"}
    assert result.expected_revisions == {"object-1": {"version": 3}}
    assert result.target_refs[0]["id"] == "object-1"
    repeated = await library.confirm(
        "owner", pending.id, expected_proposal_hash=pending.proposal_hash
    )
    assert repeated.canonical_result_refs == result.canonical_result_refs
    assert calls == [pending.id]
    assert await db.scalar(select(func.count()).select_from(ConfirmationDecisionRow)) == 1
    result.result_payload["id"] = "client change"
    assert (await library.get("owner", pending.id)).result_payload == {"id": pending.id}


async def test_failed_effect_rolls_back_but_decision_and_retry_survive(db: AsyncSession) -> None:
    fail = True

    async def handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        assert context.operation is not None
        context.db.add(EffectRow(id=context.operation.id, value=payload["value"]))
        await context.db.flush()
        if fail:
            raise ValueError("simulated_write_failure")
        return OperationResult([], {"id": context.operation.id})

    register_operation(OperationRegistration("test.facade.rollback", 1, handler))
    library = OperationLibrary(db, Projects())  # type: ignore[arg-type]
    pending = await library.propose("owner", proposal("test.facade.rollback"))
    failed = await library.confirm(
        "owner", pending.id, expected_proposal_hash=pending.proposal_hash
    )
    assert failed.state == "failed" and failed.latest_decision is not None
    assert await db.scalar(select(func.count()).select_from(EffectRow)) == 0
    await db.commit()
    assert (await library.get("owner", pending.id)).state == "failed"
    fail = False
    retried = await library.confirm(
        "owner", pending.id, expected_proposal_hash=pending.proposal_hash
    )
    assert retried.state == "succeeded" and retried.id == pending.id
    assert await db.scalar(select(func.count()).select_from(EffectRow)) == 1
    assert await db.scalar(select(func.count()).select_from(ConfirmationDecisionRow)) == 2


async def test_registered_schema_checks_proposal_and_confirm(db: AsyncSession) -> None:
    calls = []

    async def handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        calls.append(payload)
        return OperationResult([], {})

    kind = "test.facade.schema"
    register_operation(OperationRegistration(kind, 1, handler))
    library = OperationLibrary(db, Projects())  # type: ignore[arg-type]
    body = proposal(kind).model_copy(update={"input_payload": {"unexpected": "value"}})
    pending = await library.propose("owner", body)
    register_operation(OperationRegistration(kind, 1, handler, input_schema=Payload))
    assert (await library.propose("owner", body)).id == pending.id
    with pytest.raises(ValidationError):
        await library.propose("owner", body.model_copy(update={"idempotency_key": "invalid-new"}))
    assert await db.scalar(select(func.count()).select_from(OperationRecordRow)) == 1
    failed = await library.confirm(
        "owner", pending.id, expected_proposal_hash=pending.proposal_hash
    )
    assert failed.state == "failed" and calls == []


async def test_cancel_request_changes_and_owner_scoped_status(db: AsyncSession) -> None:
    cancelled = []

    async def handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        assert context.operation is not None
        cancelled.append(context.operation.id)
        return OperationResult([], {})

    kind = "test.facade.cancel"
    register_operation(OperationRegistration(kind, 1, handler, cancel_handler=handler))
    library = OperationLibrary(db, Projects())  # type: ignore[arg-type]
    pending = await library.propose("owner", proposal(kind))
    changes = await library.request_changes(
        "owner",
        pending.id,
        expected_proposal_hash=pending.proposal_hash,
        comment="Clarify scope",
    )
    assert changes.latest_decision is not None
    assert changes.latest_decision.comment == "Clarify scope"
    result = await library.cancel("owner", pending.id, expected_proposal_hash=pending.proposal_hash)
    assert result.state == "cancelled" and cancelled == [pending.id]
    assert await library.status("other-owner", [pending.id]) == []
    assert (await library.status("owner", [pending.id]))[0].state == "cancelled"
    with pytest.raises(ValueError, match="owner_required"):
        await library.status("", [])
    with pytest.raises(ValueError, match="limit_exceeded"):
        await library.status("owner", [pending.id] * 101)


async def test_facade_does_not_commit_callers_transaction(db: AsyncSession) -> None:
    async def handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        return OperationResult([], {})

    kind = "test.facade.outer_transaction"
    register_operation(OperationRegistration(kind, 1, handler))
    library = OperationLibrary(db, Projects())  # type: ignore[arg-type]
    pending = await library.propose("owner", proposal(kind))
    await library.confirm("owner", pending.id, expected_proposal_hash=pending.proposal_hash)
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(OperationRecordRow)) == 0
    assert await db.scalar(select(func.count()).select_from(ConfirmationDecisionRow)) == 0
