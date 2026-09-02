"""Generic operation extensions: decisions, deferred commits, cancel hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.db import async_commit_with_retry, commits_deferred, defer_commits
from valuz_agent.modules.operations import models as _models  # noqa: F401
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import OperationProposal
from valuz_agent.modules.operations.service import OperationService

USER = "u-ops"


class _Projects:
    pass


@pytest.fixture
async def factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _proposal(op_type: str, key: str) -> OperationProposal:
    return OperationProposal(
        operation_type=op_type,
        actor_kind="user",
        input_payload={"k": key},
        idempotency_key=key,
    )


async def test_decision_reaches_the_handler_and_commits_are_deferred(factory) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, Any] = {}

    async def _handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        seen["decision"] = dict(context.decision)
        seen["deferred"] = commits_deferred()
        # a domain layer that "commits" inside the handler must not close
        # the operation's savepoint
        await async_commit_with_retry(context.db, where="test")
        seen["still_in_tx"] = context.db.in_transaction()
        return OperationResult(canonical_result_refs=[], result_payload={"ok": True})

    operation_registry.register(
        OperationRegistration(operation_type="test.decide", version=1, handler=_handler)
    )
    async with factory() as db:
        svc = OperationService(db, _Projects())  # type: ignore[arg-type]
        row = await svc.propose(USER, _proposal("test.decide", "k1"))
        await db.commit()
        row = await svc.confirm(
            USER, row.id, expected_proposal_hash=row.proposal_hash, decision={"mode": "x"}
        )
        await db.commit()

    assert row.state == "succeeded", row.error_message
    assert seen == {"decision": {"mode": "x"}, "deferred": True, "still_in_tx": True}
    assert not commits_deferred()


async def test_defer_commits_is_task_local_and_resets() -> None:
    assert not commits_deferred()
    async with defer_commits():
        assert commits_deferred()
        async with defer_commits():
            assert commits_deferred()
        assert commits_deferred()
    assert not commits_deferred()


async def test_cancel_runs_the_cancel_hook_best_effort(factory) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def _handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        return OperationResult(canonical_result_refs=[], result_payload={})

    async def _cancel(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
        calls.append(payload["k"])
        raise RuntimeError("cleanup exploded")

    operation_registry.register(
        OperationRegistration(
            operation_type="test.cancel", version=1, handler=_handler, cancel_handler=_cancel
        )
    )
    async with factory() as db:
        svc = OperationService(db, _Projects())  # type: ignore[arg-type]
        row = await svc.propose(USER, _proposal("test.cancel", "k2"))
        await db.commit()
        row = await svc.cancel(USER, row.id, expected_proposal_hash=row.proposal_hash)
        await db.commit()

    assert row.state == "cancelled"  # the hook's failure did not undo the cancel
    assert calls == ["k2"]
