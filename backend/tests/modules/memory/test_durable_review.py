"""Synthetic durable reviews exercise host DB fencing plus the real memory catalog."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.infra import execution_lease as leases
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.integrations.memory_local import LocalMemoryBackend
from valuz_agent.modules.memory.extraction import (
    ExtractionPlan,
    InvalidReviewError,
    MemoryOp,
    mutation_args,
    plan_ops,
)
from valuz_agent.modules.memory.journal import (
    LEASE_SCOPE,
    ReviewFencedError,
    ReviewJournal,
    ReviewLimits,
    ReviewOwnerRow,
)
from valuz_agent.modules.memory.recovery import ReviewExecution, current_review, execute_plan
from valuz_agent.modules.memory.scheduler import MemoryScheduler
from valuz_agent.modules.memory.service import MemoryStore
from valuz_agent.ports.memory import MemoryConflictError, MemorySnapshot, SourceRef
from valuz_agent.ports.memory_maintenance import MemoryMaintenanceScope

pytestmark = pytest.mark.asyncio
REAL_EXCLUSIVE_PROBE = leases._exclusive_by_construction
SOURCE = SourceRef(kind="message", source_id="synthetic-message", revision="v1", origin="owner")


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryLibrary:
    fs = FsRegistry()
    monkeypatch.setattr(fs, "data_dir", lambda owner: tmp_path / owner)
    return MemoryLibrary("owner", backend=LocalMemoryBackend(MemoryStore(fs=fs)))


async def claim(
    journal: ReviewJournal, *, key: str | None = None, limits: ReviewLimits | None = None
) -> ReviewExecution:
    if key is None:
        key = await journal.schedule("owner", "session", "session", 0)
    job = await journal.get("owner", key)
    assert job is not None
    lease = await leases.acquire_lease(scope=LEASE_SCOPE, key=key)
    assert lease is not None
    limits = limits or ReviewLimits(retry_delay=0)
    job = await journal.claim(job, lease, limits)
    assert job is not None
    return ReviewExecution(journal, job, lease, limits)


async def verify() -> None:
    return None


async def build(snapshot: MemorySnapshot, review_id: str) -> ExtractionPlan:
    return plan_ops(
        [
            MemoryOp("add", "global", content="durable fact", source_ids=("s1",)),
            MemoryOp("add", "global", content="second fact", source_ids=("s1",)),
        ],
        snapshot=snapshot,
        review_id=review_id,
        source_aliases={"s1": SOURCE},
    )


async def apply(execution: ReviewExecution, library: MemoryLibrary, builder: Any = build) -> None:
    token = current_review.set(execution)
    try:
        await execute_plan(
            library=library,
            sources=(SOURCE,),
            build=builder,
            verify=verify,
            provider_id="byok",
            cursor=("observed-message:v1",),
        )
    finally:
        current_review.reset(token)


async def test_restart_replays_partial_effect_without_model_or_duplicate(
    review_db: async_sessionmaker[AsyncSession],
    library: MemoryLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = ReviewJournal()
    first = await claim(journal)
    snapshot = await library.snapshot()
    plan = await build(snapshot, "stable-review")
    first.job = await journal.prepare(
        first.job,
        first.lease,
        revision=snapshot.revision,
        review_id=plan.review_id,
        sources=plan.source_refs,
        project_id=None,
    )
    first.job = await journal.save_plan(first.job, first.lease, list(plan.operations))
    # Crash after the catalog committed, before the journal checkpoint.
    await library.mutate(**mutation_args(plan.operations[0], snapshot.revision))
    await first.lease.release()
    monkeypatch.setattr(leases, "_HOLDER_ID", "new-boot")
    second = await claim(ReviewJournal(), key=first.job.id)

    async def forbidden_model(_snapshot: MemorySnapshot, _review: str) -> ExtractionPlan:
        raise AssertionError("a persisted plan must not call the model again")

    await apply(second, library, forbidden_model)
    catalog = await library.snapshot()
    assert [record.content for record in catalog.records] == ["durable fact", "second fact"]
    assert catalog.revision == 2
    done = await journal.get("owner", first.job.id)
    assert done is not None and done.status == "done" and done.planned_ops == ()
    assert [receipt["status"] for receipt in done.receipts] == ["applied", "applied"]
    assert done.receipts[0]["replayed"] is True
    assert done.cursor == ("observed-message:v1",)


async def test_late_model_after_forget_cannot_save_or_resurrect(
    review_db: async_sessionmaker[AsyncSession], library: MemoryLibrary
) -> None:
    journal = ReviewJournal()
    execution = await claim(journal)
    started, proceed = asyncio.Event(), asyncio.Event()

    async def slow(snapshot: MemorySnapshot, review: str) -> ExtractionPlan:
        started.set()
        await proceed.wait()
        return await build(snapshot, review)

    task = asyncio.create_task(apply(execution, library, slow))
    await started.wait()
    result = await library.forget_source(SOURCE, operation_id="forget-source", base_revision=0)
    cleanup = await journal.purge_after_mutation(
        owner_user_id="owner", revision=result.revision, source_refs=(SOURCE,)
    )
    assert cleanup.complete
    proceed.set()
    with pytest.raises(MemoryConflictError):
        await task
    assert (await library.snapshot()).records == ()
    stopped = await journal.get("owner", execution.job.id)
    assert stopped is not None and stopped.status == "stopped" and stopped.planned_ops == ()


async def test_foreground_correction_during_model_prevents_stale_apply(
    review_db: async_sessionmaker[AsyncSession], library: MemoryLibrary
) -> None:
    journal = ReviewJournal()
    execution = await claim(journal)

    async def concurrent(snapshot: MemorySnapshot, review: str) -> ExtractionPlan:
        await library.mutate(
            action="add",
            target="global",
            operation_id="human",
            base_revision=snapshot.revision,
            content="human confirmed",
            source="user",
        )
        return await build(snapshot, review)

    with pytest.raises(MemoryConflictError):
        await apply(execution, library, concurrent)
    assert [record.content for record in (await library.snapshot()).records] == ["human confirmed"]
    job = await journal.get("owner", execution.job.id)
    assert job is not None and not job.plan_ready


async def test_two_host_holders_cannot_claim_unexpired_shared_lease(
    review_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    execution = await claim(ReviewJournal())
    monkeypatch.setattr(leases, "_HOLDER_ID", "other-host")
    assert await leases.acquire_lease(scope=LEASE_SCOPE, key=execution.job.id) is None
    await execution.journal.check(execution.job, execution.lease)


async def test_proven_exclusive_new_boot_takes_never_lease_and_fences_old(
    review_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from valuz_agent.infra import db_urls, single_writer

    monkeypatch.setattr(single_writer, "_lock_fd", None)
    monkeypatch.setattr(db_urls, "is_sqlite_runtime", lambda: True)
    monkeypatch.setattr(leases, "_exclusive_by_construction", REAL_EXCLUSIVE_PROBE)
    single_writer.acquire_single_writer_lock(tmp_path / "actual-writer.lock")
    assert REAL_EXCLUSIVE_PROBE()
    first = await claim(ReviewJournal())
    async with review_db() as db:
        row = await db.get(leases.ExecutionLeaseRow, (LEASE_SCOPE, first.job.id))
        assert row is not None and row.lease_expires_at == leases._NEVER_MS
    monkeypatch.setattr(leases, "_HOLDER_ID", "exclusive-new-boot")
    second = await claim(ReviewJournal(), key=first.job.id)
    assert second.lease.fence_token > first.lease.fence_token
    with pytest.raises(ReviewFencedError):
        await first.journal.check(first.job, first.lease)
    with pytest.raises(ReviewFencedError):
        await first.journal.checkpoint(first.job, first.lease, {"revision": 1})
    await second.journal.check(second.job, second.lease)
    single_writer.release_single_writer_lock()


async def test_purge_owner_and_plan_body_isolation(
    review_db: async_sessionmaker[AsyncSession], library: MemoryLibrary
) -> None:
    journal = ReviewJournal()
    execution = await claim(journal)
    plan = await build(await library.snapshot(), "r")
    execution.job = await journal.prepare(
        execution.job,
        execution.lease,
        revision=0,
        review_id="r",
        sources=(SOURCE,),
        project_id=None,
    )
    execution.job = await journal.save_plan(execution.job, execution.lease, list(plan.operations))
    other = await journal.schedule("foreign-owner", "session", "session", 0)
    cleanup = await journal.purge_after_mutation(
        owner_user_id="owner", revision=1, scope=MemoryMaintenanceScope("global")
    )
    assert cleanup.complete and cleanup.purged_plans == 1
    assert await journal.clear_before("owner") > 0
    assert await journal.clear_before("foreign-owner") == 0
    assert await journal.get("foreign-owner", execution.job.id) is None
    foreign = await journal.get("foreign-owner", other)
    assert foreign is not None and foreign.status == "pending"
    with pytest.raises(ReviewFencedError):
        await journal.save_plan(execution.job, execution.lease, list(plan.operations))


async def test_debounce_survives_scheduler_recreation_and_owner_namespace(
    review_db: async_sessionmaker[AsyncSession],
) -> None:
    first = MemoryScheduler(limits=ReviewLimits(idle_delay=0))
    await first.notify_turn("same-session", "owner")
    await first.notify_turn("same-session", "owner")
    await first.notify_turn("same-session", "foreign-owner")
    second = MemoryScheduler(limits=ReviewLimits(idle_delay=0))
    due = await second.journal.due()
    assert len(due) == 2 and {job.user_id for job in due} == {"owner", "foreign-owner"}
    assert all(job.attempts == 0 for job in due)


async def test_attempt_limit_and_daily_model_limit_are_durable(
    review_db: async_sessionmaker[AsyncSession],
) -> None:
    journal = ReviewJournal()
    limits = ReviewLimits(max_attempts=2, max_daily_calls=1, retry_delay=0)
    first = await claim(journal, limits=limits)
    assert await journal.reserve_model(first.job, first.lease, limits)
    assert not await journal.reserve_model(first.job, first.lease, limits)
    await journal.retry(first.job, first.lease, limits, "unavailable")
    await first.lease.release()
    second = await claim(ReviewJournal(), key=first.job.id, limits=limits)
    await journal.retry(second.job, second.lease, limits, "unavailable")
    stopped = await journal.get("owner", first.job.id)
    assert stopped is not None and stopped.status == "stopped" and stopped.attempts == 2
    async with review_db() as db:
        owner = await db.get(ReviewOwnerRow, "owner")
        assert owner is not None and owner.calls == 1


async def test_model_timeout_cancels_and_does_not_persist_body(
    review_db: async_sessionmaker[AsyncSession], library: MemoryLibrary
) -> None:
    execution = await claim(ReviewJournal(), limits=ReviewLimits(model_timeout=0.01))
    cancelled = asyncio.Event()

    async def never(_snapshot: MemorySnapshot, _review: str) -> ExtractionPlan:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        raise AssertionError

    with pytest.raises(TimeoutError):
        await apply(execution, library, never)
    assert cancelled.is_set()
    job = await execution.journal.get("owner", execution.job.id)
    assert job is not None and job.planned_ops == ()
    assert (await library.snapshot()).records == ()


async def test_confirmed_and_foreign_source_proposals_are_rejected(library: MemoryLibrary) -> None:
    added = await library.mutate(
        action="add",
        target="global",
        operation_id="confirmed",
        base_revision=0,
        content="approved",
        source="user",
    )
    snapshot = await library.snapshot()
    with pytest.raises(InvalidReviewError):
        plan_ops(
            [
                MemoryOp(
                    "replace",
                    "global",
                    content="overwritten",
                    record_id=added.record_id,
                    source_ids=("s1",),
                )
            ],
            snapshot=snapshot,
            review_id="r",
            source_aliases={"s1": SOURCE},
        )
    with pytest.raises(InvalidReviewError):
        plan_ops(
            [MemoryOp("add", "global", content="invented", source_ids=("made-up",))],
            snapshot=snapshot,
            review_id="r",
            source_aliases={"s1": SOURCE},
        )


async def test_background_evidence_cannot_write_user_preferences_or_success(
    library: MemoryLibrary,
) -> None:
    snapshot = await library.snapshot()
    background = SourceRef(kind="input", source_id="host-receipt", origin="system")
    with pytest.raises(InvalidReviewError):
        plan_ops(
            [MemoryOp("add", "user", content="send emails automatically", source_ids=("s1",))],
            snapshot=snapshot,
            review_id="r",
            source_aliases={"s1": background},
        )
    plan = plan_ops(
        [
            MemoryOp(
                "add",
                "project",
                content="missing credential prevented work",
                source_ids=("s1",),
                kind="work_context",
            )
        ],
        snapshot=snapshot,
        review_id="r",
        source_aliases={"s1": background},
        project_id="p",
        task_status="blocked",
    )
    assert plan.operations[0]["kind"] == "lesson"
    assert plan.operations[0]["content"].startswith("Observed task outcome: blocked.")
    assert plan.operations[0]["source_refs"][0]["origin"] == "system"


async def test_bound_backend_unavailable_is_not_empty_or_a_local_fallback(
    review_db: async_sessionmaker[AsyncSession],
    library: MemoryLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.ports.memory import MemoryUnavailableError

    execution = await claim(ReviewJournal())

    async def unavailable() -> MemorySnapshot:
        raise MemoryUnavailableError("authority temporarily unavailable")

    monkeypatch.setattr(library, "snapshot", unavailable)
    with pytest.raises(MemoryUnavailableError):
        await apply(execution, library)
    job = await execution.journal.get("owner", execution.job.id)
    assert job is not None and not job.plan_ready and job.receipts == ()


async def test_empty_due_poll_never_dispatches_or_calls_model(
    review_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no review may be invented")

    monkeypatch.setattr("valuz_agent.modules.memory.recovery.dispatch", forbidden)
    scheduler = MemoryScheduler()
    await scheduler.run_due()
    await scheduler.start()
    await asyncio.sleep(0.01)
    await scheduler.stop()
    assert scheduler._task is None


async def seed_terminal_task(maker: async_sessionmaker[AsyncSession]) -> None:
    from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow

    async with maker() as db:
        connection = await db.connection()
        await connection.run_sync(
            lambda conn: TaskRow.metadata.create_all(
                conn, tables=[TaskRow.__table__, TaskEventRow.__table__]
            )
        )
        db.add(
            TaskRow(
                id="task",
                user_id="owner",
                project_id="project",
                file_path="tasks/task.md",
                title="Synthetic task",
                goal="Synthetic goal",
                status="active",
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={},
            )
        )
        await db.commit()


async def test_terminal_status_and_candidate_rollback_together_without_emit(
    review_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.modules.memory.scheduler import memory_scheduler
    from valuz_agent.modules.tasks import events
    from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow

    await seed_terminal_task(review_db)
    monkeypatch.setattr(memory_scheduler, "registered", True)
    emitted: list[str] = []
    monkeypatch.setattr(events, "publish_task_finalized", lambda *args: emitted.append("published"))
    original = memory_scheduler.journal.schedule

    async def fail_after_insert(*args: Any, **kwargs: Any) -> str:
        await original(*args, **kwargs)
        raise RuntimeError("synthetic journal failure")

    monkeypatch.setattr(memory_scheduler.journal, "schedule", fail_after_insert)
    async with review_db() as db:
        with pytest.raises(RuntimeError, match="synthetic journal failure"):
            await events.finalize_task(
                db,
                user_id="owner",
                project_id="project",
                task_id="task",
                status="stopped",
                event_type="stopped",
                actor="system",
            )
    async with review_db() as db:
        row = await db.get(TaskRow, "task")
        assert row is not None and row.status == "active"
        assert (await db.execute(select(TaskEventRow))).scalars().all() == []
    assert emitted == []
    assert await ReviewJournal().due() == []


async def test_terminal_status_commits_with_candidate_and_new_scheduler_recovers(
    review_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.modules.memory.scheduler import memory_scheduler
    from valuz_agent.modules.tasks import events
    from valuz_agent.modules.tasks.models import TaskRow

    await seed_terminal_task(review_db)
    monkeypatch.setattr(memory_scheduler, "registered", True)
    observations: list[str] = []
    monkeypatch.setattr(
        events, "publish_task_finalized", lambda *args: observations.append("published")
    )
    async with review_db() as db:
        event = await events.finalize_task(
            db,
            user_id="owner",
            project_id="project",
            task_id="task",
            status="stopped",
            event_type="stopped",
            actor="system",
        )
        assert event is not None
    async with review_db() as db:
        task = await db.get(TaskRow, "task")
        assert task is not None and task.status == "stopped"
    due = await MemoryScheduler(journal=ReviewJournal()).journal.due()
    assert len(due) == 1 and due[0].kind == "task" and due[0].subject_id == "task"
    assert observations == ["published"]


async def test_authority_changed_during_model_cannot_save_plan_even_same_revision(
    review_db: async_sessionmaker[AsyncSession],
    library: MemoryLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = await claim(ReviewJournal())
    current = MemorySnapshot(
        owner_user_id="owner", revision=0, authority_id="first", authority_epoch=1
    )

    async def snapshot() -> MemorySnapshot:
        return current

    monkeypatch.setattr(library, "snapshot", snapshot)

    async def switch(snapshot: MemorySnapshot, review_id: str) -> ExtractionPlan:
        nonlocal current
        current = MemorySnapshot(
            owner_user_id="owner", revision=0, authority_id="second", authority_epoch=1
        )
        return await build(snapshot, review_id)

    with pytest.raises(MemoryConflictError):
        await apply(execution, library, switch)
    job = await execution.journal.get("owner", execution.job.id)
    assert (
        job is not None
        and job.authority_id == "first"
        and job.authority_epoch == 1
        and not job.plan_ready
    )


async def test_restart_keeps_original_authority_and_epoch_not_current_binding(
    review_db: async_sessionmaker[AsyncSession],
    library: MemoryLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = ReviewJournal()
    execution = await claim(journal)
    original = MemorySnapshot(
        owner_user_id="owner", revision=0, authority_id="original", authority_epoch=3
    )
    plan = await build(original, "stable")
    execution.job = await journal.prepare(
        execution.job,
        execution.lease,
        revision=0,
        review_id="stable",
        sources=(SOURCE,),
        project_id=None,
        authority_id="original",
        authority_epoch=3,
    )
    execution.job = await journal.save_plan(execution.job, execution.lease, list(plan.operations))
    await execution.lease.release()
    recovered = await claim(ReviewJournal(), key=execution.job.id)

    async def changed() -> MemorySnapshot:
        return MemorySnapshot(
            owner_user_id="owner", revision=0, authority_id="original", authority_epoch=4
        )

    monkeypatch.setattr(library, "snapshot", changed)

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("authority switch must not be silently rebound")

    monkeypatch.setattr(library, "mutate", forbidden)
    with pytest.raises(MemoryConflictError):
        await apply(recovered, library, forbidden)
    assert recovered.job.authority_id == "original" and recovered.job.authority_epoch == 3


async def test_two_real_worker_processes_only_one_durable_claim(
    review_db: async_sessionmaker[AsyncSession],
) -> None:
    import sys

    journal = ReviewJournal()
    key = await journal.schedule("owner", "session", "process-race", 0)
    # Both subprocesses share only this test database, not a host/user runtime.
    url = str(review_db.kw["bind"].url)
    code = """
import asyncio, sys
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from valuz_agent.infra import execution_lease as leases
from valuz_agent.modules.memory import journal
engine = create_async_engine(sys.argv[1], poolclass=NullPool)
maker = async_sessionmaker(engine, expire_on_commit=False)
@asynccontextmanager
async def uow(*, commit=True):
    async with maker() as db:
        yield db
        if commit:
            await db.commit()
leases.async_unit_of_work = uow
journal.async_unit_of_work = uow
async def main():
    lease = await leases.acquire_lease(scope=journal.LEASE_SCOPE, key=sys.argv[2])
    if lease is None:
        print('0')
    else:
        job = await journal.ReviewJournal().get('owner', sys.argv[2])
        claimed = await journal.ReviewJournal().claim(job, lease, journal.ReviewLimits())
        print('1' if claimed else '0')
    await engine.dispose()
asyncio.run(main())
"""
    first, second = await asyncio.gather(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            url,
            key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
        asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            url,
            key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
    )
    results = await asyncio.gather(first.communicate(), second.communicate())
    assert first.returncode == second.returncode == 0, [stderr.decode() for _, stderr in results]
    assert sorted(stdout.decode().strip() for stdout, _ in results) == ["0", "1"]
    job = await journal.get("owner", key)
    assert job is not None and job.attempts == 1 and job.status == "claimed"


async def test_auto_mutations_forward_exact_saved_authority_binding(
    review_db: async_sessionmaker[AsyncSession],
    library: MemoryLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.ports.memory import MemoryMutationResult

    execution = await claim(ReviewJournal())
    revision = 0
    commands: list[dict[str, Any]] = []

    async def snapshot() -> MemorySnapshot:
        return MemorySnapshot(
            owner_user_id="owner",
            revision=revision,
            authority_id="cloud-authority",
            authority_epoch=7,
        )

    async def mutate(**kwargs: Any) -> MemoryMutationResult:
        nonlocal revision
        commands.append(kwargs)
        revision += 1
        return MemoryMutationResult(
            status="applied", revision=revision, authority_id="cloud-authority", authority_epoch=7
        )

    monkeypatch.setattr(library, "snapshot", snapshot)
    monkeypatch.setattr(library, "mutate", mutate)
    await apply(execution, library)
    assert len(commands) == 2
    assert all(
        command["source"] == "auto"
        and command["authority_id"] == "cloud-authority"
        and command["authority_epoch"] == 7
        for command in commands
    )
    assert [command["base_revision"] for command in commands] == [0, 1]
    job = await execution.journal.get("owner", execution.job.id)
    assert (
        job is not None
        and job.authority_id == "cloud-authority"
        and job.authority_epoch == 7
        and job.status == "done"
    )


async def test_real_journal_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from valuz_agent.modules.memory.journal import ReviewJobRow, ReviewOwnerRow

    path = Path(__file__).parents[3] / "alembic/host/versions/0055_memory_review_journal.py"
    spec = importlib.util.spec_from_file_location("journal_migration_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}", poolclass=NullPool
    )

    def migrate(connection: Connection) -> None:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        for model in (ReviewJobRow, ReviewOwnerRow):
            columns = {
                column["name"] for column in inspect(connection).get_columns(model.__tablename__)
            }
            assert columns == set(model.__table__.columns.keys())
        assert {
            "ix_valuz_memory_review_job_user_id",
            "ix_valuz_memory_review_job_due_at",
            "ix_valuz_memory_review_job_status",
        } <= {
            index["name"] for index in inspect(connection).get_indexes(ReviewJobRow.__tablename__)
        }
        module.downgrade()
        assert not inspect(connection).has_table(ReviewJobRow.__tablename__)
        assert not inspect(connection).has_table(ReviewOwnerRow.__tablename__)

    async with engine.begin() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()
