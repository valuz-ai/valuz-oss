"""History imports preserve identity without inheriting execution authority."""

from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.durable_operations import (
    ConfirmationDecisionStorage,
    OperationRecordStorage,
    import_operation_records,
)
from valuz_agent.facade.operations import (
    OperationLibrary,
    OperationRegistration,
    OperationResult,
    register_operation,
)
from valuz_agent.infra.database import Base

from .test_operations import Projects, proposal
from .test_operations import db as db


def operation(**changes: Any) -> dict[str, Any]:
    result = {column.name: None for column in OperationRecordStorage.__table__.columns}
    result.update(
        id="original",
        user_id="owner",
        created_at=1,
        updated_at=2,
        operation_type="test.history",
        operation_version=1,
        actor_kind="agent",
        target_refs=[],
        input_payload={},
        preview={},
        expected_revisions={},
        risk_level="material",
        confirmation_policy="confirm",
        state="awaiting_confirmation",
        proposal_hash="a" * 64,
        idempotency_key="original",
        canonical_result_refs=[],
        result_payload={},
        historical_only=False,
        expires_at=1,
    )
    return result | changes


def decision(**changes: Any) -> dict[str, Any]:
    return (
        dict(
            id="decision",
            user_id="owner",
            created_at=3,
            updated_at=3,
            operation_id="original",
            proposal_hash="a" * 64,
            decision="approve",
            decided_by="original-reviewer",
            comment="Reviewed",
        )
        | changes
    )


def test_import_atomic_identity_replay_and_outer_rollback() -> None:
    engine = create_engine("sqlite://")
    tables = [OperationRecordStorage.__table__, ConfirmationDecisionStorage.__table__]
    Base.metadata.create_all(engine, tables=tables)
    with engine.connect() as connection:
        transaction = connection.begin()
        assert (
            import_operation_records(
                connection, owner_user_id="owner", kind="operation", records=[operation()]
            )
            == 1
        )
        assert (
            import_operation_records(
                connection, owner_user_id="owner", kind="decision", records=[decision()]
            )
            == 1
        )
        assert (
            import_operation_records(
                connection, owner_user_id="owner", kind="operation", records=[operation()]
            )
            == 0
        )
        row = connection.execute(select(OperationRecordStorage.__table__)).mappings().one()
        assert dict(row) == operation(historical_only=True)
        with pytest.raises(ValueError, match="identity_conflict"):
            import_operation_records(
                connection,
                owner_user_id="owner",
                kind="operation",
                records=[
                    operation(id="second", idempotency_key="second"),
                    operation(input_payload={"changed": True}),
                ],
            )
        assert connection.scalar(select(func.count()).select_from(OperationRecordStorage)) == 1
        transaction.rollback()
        assert connection.scalar(select(func.count()).select_from(OperationRecordStorage)) == 0
        assert connection.scalar(select(func.count()).select_from(ConfirmationDecisionStorage)) == 0
    engine.dispose()


def test_live_authority_owner_and_decision_parent_are_not_overwritten() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[OperationRecordStorage.__table__, ConfirmationDecisionStorage.__table__]
    )
    with engine.begin() as connection:
        connection.execute(OperationRecordStorage.__table__.insert().values(**operation()))
        assert (
            import_operation_records(
                connection, owner_user_id="owner", kind="operation", records=[operation()]
            )
            == 0
        )
        with pytest.raises(ValueError, match="live_decision_conflict"):
            import_operation_records(
                connection, owner_user_id="owner", kind="decision", records=[decision()]
            )
        with pytest.raises(ValueError, match="parent_mismatch"):
            import_operation_records(
                connection,
                owner_user_id="owner",
                kind="decision",
                records=[decision(proposal_hash="b" * 64)],
            )
        with pytest.raises(ValueError, match="identity_conflict"):
            import_operation_records(
                connection,
                owner_user_id="other",
                kind="operation",
                records=[operation(user_id="other")],
            )
        with pytest.raises(ValueError, match="owner_required"):
            import_operation_records(connection, owner_user_id="", kind="operation", records=[])
        assert not connection.scalar(select(OperationRecordStorage.historical_only))
    engine.dispose()


@pytest.mark.parametrize(
    "state",
    [
        "awaiting_confirmation",
        "executing",
        "failed",
        "succeeded",
        "cancelled",
        "expired",
        "stale",
        "superseded",
    ],
)
async def test_all_archived_states_are_readonly_even_after_expiry(
    db: AsyncSession, state: str
) -> None:  # noqa: F811
    calls = []

    async def handler(context: Any, payload: Any) -> OperationResult:
        calls.append(payload)
        return OperationResult([], {})

    kind = f"test.history.{state}"
    register_operation(OperationRegistration(kind, 1, handler, cancel_handler=handler))
    original = operation(
        operation_type=kind, state=state, target_refs=[{"type": "test.object", "id": "one"}]
    )
    connection = await db.connection()
    await connection.run_sync(
        lambda sync: import_operation_records(
            sync, owner_user_id="owner", kind="operation", records=[original]
        )
    )
    library = OperationLibrary(db, Projects())
    before = deepcopy(original) | {"historical_only": True}
    view = await library.get("owner", "original")
    assert view.historical_only and view.state == state and view.updated_at == 2
    assert (await library.status("owner", ["original"]))[0].state == state
    for action in ("confirm", "cancel", "request_changes"):
        kwargs = {"expected_proposal_hash": original["proposal_hash"]}
        if action == "request_changes":
            kwargs["comment"] = "rewrite"
        with pytest.raises(ValueError, match="operation_historical_only"):
            await getattr(library, action)("owner", "original", **kwargs)
    # A fresh proposal for the same target cannot supersede imported history.
    await library.propose(
        "owner", proposal(kind).model_copy(update={"target_refs": original["target_refs"]})
    )
    row = (
        (
            await db.execute(
                select(OperationRecordStorage.__table__).where(
                    OperationRecordStorage.id == "original"
                )
            )
        )
        .mappings()
        .one()
    )
    assert dict(row) == before and calls == []
    assert await db.scalar(select(func.count()).select_from(ConfirmationDecisionStorage)) == 0


async def test_imported_exact_proposal_replay_stays_historical(db: AsyncSession) -> None:  # noqa: F811
    async def handler(context: Any, payload: Any) -> OperationResult:
        return OperationResult([], {})

    register_operation(OperationRegistration("test.history.replay", 1, handler))
    library = OperationLibrary(db, Projects())
    body = proposal("test.history.replay")
    pending = await library.propose("owner", body)
    record = dict((await db.execute(select(OperationRecordStorage.__table__))).mappings().one())
    await db.rollback()
    connection = await db.connection()
    await connection.run_sync(
        lambda sync: import_operation_records(
            sync, owner_user_id="owner", kind="operation", records=[record]
        )
    )
    replay = await library.propose("owner", body)
    assert replay.id == pending.id and replay.historical_only
    with pytest.raises(ValueError, match="historical_only"):
        await library.confirm("owner", replay.id, expected_proposal_hash=replay.proposal_hash)
