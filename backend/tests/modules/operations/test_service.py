"""Durable proposal, confirmation, idempotency and stale-state behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.database import Base
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import OperationProposal
from valuz_agent.modules.operations.service import OperationService
from valuz_agent.modules.playbooks import operations as _playbook_operations  # noqa: F401
from valuz_agent.modules.playbooks.models import PlaybookDefinitionRow

USER = "owner-1"


class Projects:
    async def get(self, user_id: str, project_id: str) -> ProjectRef | None:
        if user_id == USER and project_id == "p1":
            return ProjectRef(id="p1", name="Research", kind="project")
        return None


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


def create_proposal() -> OperationProposal:
    return OperationProposal(
        operation_type="playbook.create",
        project_id="p1",
        actor_kind="agent",
        actor_id="session-1",
        origin_session_id="session-1",
        input_payload={
            "name": "Earnings review",
            "content": "Review earnings and update the research context.",
            "project_id": "p1",
        },
        preview={
            "kind": "playbook",
            "change": "create",
            "name": "Earnings review",
        },
        confirmation_policy="confirm",
        idempotency_key="agent:session-1:create:one",
    )


async def test_confirm_executes_once_and_returns_canonical_result(
    db: AsyncSession,
) -> None:
    service = OperationService(db, Projects())  # type: ignore[arg-type]
    proposed = await service.propose(USER, create_proposal())
    assert proposed.state == "awaiting_confirmation"

    confirmed = await service.confirm(
        USER,
        proposed.id,
        expected_proposal_hash=proposed.proposal_hash,
    )
    assert confirmed.state == "succeeded"
    definition_id = confirmed.result_payload["definition_id"]
    assert confirmed.canonical_result_refs[0] == {
        "type": "playbook_definition",
        "id": definition_id,
    }

    repeated = await service.confirm(
        USER,
        proposed.id,
        expected_proposal_hash=proposed.proposal_hash,
    )
    assert repeated.result_payload["definition_id"] == definition_id
    definitions = list((await db.scalars(select(PlaybookDefinitionRow))).all())
    assert len(definitions) == 1


async def test_proposal_idempotency_and_hash_conflict(db: AsyncSession) -> None:
    service = OperationService(db, Projects())  # type: ignore[arg-type]
    first = await service.propose(USER, create_proposal())
    repeated = await service.propose(USER, create_proposal())
    assert repeated.id == first.id

    changed = create_proposal().model_copy(deep=True)
    changed.input_payload["content"] = "Different content"
    with pytest.raises(ValueError, match="operation_idempotency_conflict"):
        await service.propose(USER, changed)

    changed_preview = create_proposal().model_copy(deep=True)
    changed_preview.preview["name"] = "A different confirmation preview"
    with pytest.raises(ValueError, match="operation_idempotency_conflict"):
        await service.propose(USER, changed_preview)


async def test_cancel_is_durable_and_prevents_execution(db: AsyncSession) -> None:
    service = OperationService(db, Projects())  # type: ignore[arg-type]
    proposed = await service.propose(USER, create_proposal())
    cancelled = await service.cancel(
        USER,
        proposed.id,
        expected_proposal_hash=proposed.proposal_hash,
    )
    assert cancelled.state == "cancelled"
    with pytest.raises(ValueError, match="operation_not_confirmable"):
        await service.confirm(
            USER,
            proposed.id,
            expected_proposal_hash=proposed.proposal_hash,
        )


async def _unexpected_failure(
    _context: OperationContext,
    _payload: dict[str, object],
) -> OperationResult:
    raise RuntimeError("simulated handler crash")


async def test_unexpected_handler_failure_is_persisted(db: AsyncSession) -> None:
    operation_registry.register(
        OperationRegistration(
            operation_type="test.unexpected_failure",
            version=1,
            handler=_unexpected_failure,
        )
    )
    service = OperationService(db, Projects())  # type: ignore[arg-type]
    proposed = await service.propose(
        USER,
        OperationProposal(
            operation_type="test.unexpected_failure",
            actor_kind="agent",
            confirmation_policy="confirm",
            idempotency_key="test:unexpected-failure",
        ),
    )
    failed = await service.confirm(
        USER,
        proposed.id,
        expected_proposal_hash=proposed.proposal_hash,
    )
    assert failed.state == "failed"
    assert failed.error_code == "OPERATION_INTERNAL_ERROR"
    assert failed.error_message == "simulated handler crash"
