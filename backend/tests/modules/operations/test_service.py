"""Durable proposal, confirmation, idempotency and stale-state behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.database import Base
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.operations.models import ConfirmationDecisionRow, OperationRecordRow
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


async def _noop(_context: OperationContext, _payload: dict[str, object]) -> OperationResult:
    return OperationResult(canonical_result_refs=[], result_payload={"ok": True})


def _register(operation_type: str, **kwargs: object) -> None:
    operation_registry.register(
        OperationRegistration(  # type: ignore[arg-type]
            operation_type=operation_type, version=1, handler=_noop, **kwargs
        )
    )


def _targeted(operation_type: str, key: str, target_id: str | None) -> OperationProposal:
    return OperationProposal(
        operation_type=operation_type,
        actor_kind="agent",
        target_refs=(
            [{"type": "playbook_definition", "id": target_id, "version": 1}] if target_id else []
        ),
        input_payload={"key": key},
        confirmation_policy="confirm",
        idempotency_key=f"{operation_type}:{key}",
    )


async def test_request_changes_records_feedback_and_keeps_the_proposal_pending(
    db: AsyncSession,
) -> None:
    service = OperationService(db, Projects())  # type: ignore[arg-type]
    proposed = await service.propose(USER, create_proposal())

    with pytest.raises(ValueError, match="operation_proposal_hash_mismatch"):
        await service.request_changes(
            USER, proposed.id, expected_proposal_hash="0" * 64, comment="nope"
        )

    revised = await service.request_changes(
        USER,
        proposed.id,
        expected_proposal_hash=proposed.proposal_hash,
        comment="Add a risk section before I approve.",
    )
    assert revised.state == "awaiting_confirmation"
    latest = await service.latest_decisions(USER, [proposed.id])
    assert latest[proposed.id].decision == "request_changes"
    assert latest[proposed.id].comment == "Add a risk section before I approve."
    assert (await service.get(USER, proposed.id)).state == "awaiting_confirmation"

    # The feedback does not block the user from deciding on the proposal as is.
    confirmed = await service.confirm(
        USER, proposed.id, expected_proposal_hash=proposed.proposal_hash
    )
    assert confirmed.state == "succeeded"
    decisions = list(
        (
            await db.scalars(
                select(ConfirmationDecisionRow)
                .where(ConfirmationDecisionRow.operation_id == proposed.id)
                .order_by(ConfirmationDecisionRow.created_at, ConfirmationDecisionRow.id)
            )
        ).all()
    )
    assert [item.decision for item in decisions] == ["request_changes", "approve"]
    assert (await service.latest_decisions(USER, [proposed.id]))[proposed.id].decision == "approve"

    with pytest.raises(ValueError, match="operation_not_revisable:succeeded"):
        await service.request_changes(
            USER, proposed.id, expected_proposal_hash=proposed.proposal_hash, comment="late"
        )


async def test_expired_proposal_reads_as_expired_and_refuses_confirmation(
    db: AsyncSession,
) -> None:
    calls: list[str] = []

    async def _handler(_context: OperationContext, payload: dict[str, object]) -> OperationResult:
        calls.append(str(payload["key"]))
        return OperationResult(canonical_result_refs=[], result_payload={})

    operation_registry.register(
        OperationRegistration(operation_type="test.expiry", version=1, handler=_handler)
    )
    _register("test.expiry_default_ttl", default_ttl_ms=60_000)
    service = OperationService(db, Projects())  # type: ignore[arg-type]

    proposal = _targeted("test.expiry", "past", None).model_copy(
        update={"expires_at": now_ms() - 1}
    )
    proposed = await service.propose(USER, proposal)
    proposed_id, digest = proposed.id, proposed.proposal_hash
    await db.commit()
    db.expire_all()
    # Nothing wrote the state: the stored row is still pending.
    stored = await db.get(OperationRecordRow, proposed_id)
    assert stored is not None and stored.state == "awaiting_confirmation"
    assert stored.expires_at is not None and stored.expires_at < now_ms()

    fetched = await service.get(USER, proposed_id)
    assert fetched.state == "expired", "lazy: past its lifetime from the first read"
    [listed] = await service.status(USER, [proposed_id])
    assert listed.state == "expired"

    with pytest.raises(ValueError, match="operation_not_confirmable:expired"):
        await service.confirm(USER, proposed_id, expected_proposal_hash=digest)
    assert calls == [], "no handler runs for an expired proposal"
    await db.commit()
    db.expire_all()
    persisted = await db.get(OperationRecordRow, proposed_id)
    assert persisted is not None
    assert (persisted.state, persisted.error_code) == ("expired", "OPERATION_EXPIRED")

    with pytest.raises(ValueError, match="operation_not_cancellable:expired"):
        await service.cancel(USER, proposed_id, expected_proposal_hash=digest)
    with pytest.raises(ValueError, match="operation_not_revisable:expired"):
        await service.request_changes(
            USER, proposed_id, expected_proposal_hash=digest, comment="too late"
        )

    # A registration default applies when the proposer sets no lifetime.
    before = now_ms()
    defaulted = await service.propose(USER, _targeted("test.expiry_default_ttl", "ttl", None))
    assert defaulted.expires_at is not None
    assert before + 60_000 <= defaulted.expires_at <= now_ms() + 60_000
    assert defaulted.state == "awaiting_confirmation"
    never = await service.propose(USER, _targeted("test.expiry", "never", None))
    assert never.expires_at is None


async def test_reproposal_for_the_same_target_supersedes_the_pending_one(
    db: AsyncSession,
) -> None:
    _register("test.supersede")
    service = OperationService(db, Projects())  # type: ignore[arg-type]

    first = await service.propose(USER, _targeted("test.supersede", "a", "def-1"))
    other_target = await service.propose(USER, _targeted("test.supersede", "b", "def-2"))
    done = await service.propose(USER, _targeted("test.supersede", "c", "def-3"))
    await service.confirm(USER, done.id, expected_proposal_hash=done.proposal_hash)
    assert done.state == "succeeded"

    second = await service.propose(USER, _targeted("test.supersede", "d", "def-1"))
    assert second.state == "awaiting_confirmation"
    assert (first.state, first.superseded_by_id) == ("superseded", second.id)
    assert other_target.state == "awaiting_confirmation", "a different target is untouched"

    with pytest.raises(ValueError, match="operation_not_confirmable:superseded"):
        await service.confirm(USER, first.id, expected_proposal_hash=first.proposal_hash)
    with pytest.raises(ValueError, match="operation_not_cancellable:superseded"):
        await service.cancel(USER, first.id, expected_proposal_hash=first.proposal_hash)

    third = await service.propose(USER, _targeted("test.supersede", "e", "def-3"))
    assert done.state == "succeeded", "terminal records are never superseded"
    assert third.state == "awaiting_confirmation"

    # Proposals without an identifiable target (creates) never replace each other.
    create_1 = await service.propose(USER, _targeted("test.supersede", "f", None))
    create_2 = await service.propose(USER, _targeted("test.supersede", "g", None))
    assert (create_1.state, create_2.state) == ("awaiting_confirmation", "awaiting_confirmation")

    # Another owner's pending proposal for the same target is out of scope.
    theirs = await service.propose("owner-2", _targeted("test.supersede", "h", "def-1"))
    assert theirs.state == "awaiting_confirmation"
    assert second.state == "awaiting_confirmation"
