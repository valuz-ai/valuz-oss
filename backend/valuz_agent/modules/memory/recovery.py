"""Fenced execution of registered memory reviews using the host execution lease."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.infra.execution_lease import ExecutionLease, hold_lease
from valuz_agent.modules.memory.extraction import ExtractionPlan, InvalidReviewError, mutation_args
from valuz_agent.modules.memory.journal import (
    LEASE_SCOPE,
    ReviewFencedError,
    ReviewJob,
    ReviewJournal,
    ReviewLimits,
    SubjectKind,
    review_journal,
)
from valuz_agent.ports.memory import (
    MemoryConflictError,
    MemorySnapshot,
    MemoryUnavailableError,
    SourceRef,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewExecution:
    journal: ReviewJournal
    job: ReviewJob
    lease: ExecutionLease
    limits: ReviewLimits


current_review: contextvars.ContextVar[ReviewExecution | None] = contextvars.ContextVar(
    "memory_review", default=None
)
_active: set[str] = set()
_DEFAULT_LIMITS = ReviewLimits()


async def dispatch(
    job: ReviewJob,
    *,
    journal: ReviewJournal = review_journal,
    limits: ReviewLimits = _DEFAULT_LIMITS,
) -> None:
    if job.id in _active:
        return
    _active.add(job.id)
    try:
        async with hold_lease(scope=LEASE_SCOPE, key=job.id) as lease:
            if lease is None:
                return
            claimed = await journal.claim(job, lease, limits)
            if claimed is None:
                return
            execution = ReviewExecution(journal, claimed, lease, limits)
            token = current_review.set(execution)
            try:
                from valuz_agent.modules.memory.runner import (
                    run_extraction_for_session,
                    run_task_finish_extraction,
                )

                runner = (
                    run_extraction_for_session
                    if job.kind == "session"
                    else run_task_finish_extraction
                )
                await runner(job.subject_id, job.user_id)
            except asyncio.CancelledError:
                raise  # retain the durable plan for the next host
            except (MemoryConflictError, InvalidReviewError) as exc:
                try:
                    await journal.finish(
                        execution.job,
                        lease,
                        reason="review_conflict"
                        if isinstance(exc, MemoryConflictError)
                        else "invalid_review",
                    )
                except ReviewFencedError:
                    pass  # owner cleanup already stopped and purged the candidate
            except ReviewFencedError:
                pass
            except Exception:  # noqa: BLE001
                try:
                    await journal.retry(execution.job, lease, limits, "review_unavailable")
                except ReviewFencedError:
                    pass
                logger.debug("memory review unavailable", exc_info=True)
            finally:
                current_review.reset(token)
    finally:
        _active.discard(job.id)


async def review_now(kind: SubjectKind, subject: str, owner: str) -> None:
    from valuz_agent.infra.time_utils import now_ms

    key = await review_journal.schedule(owner, kind, subject, now_ms())
    job = await review_journal.get(owner, key)
    if job is not None:
        await dispatch(job)


async def stop_review(reason: str) -> None:
    execution = current_review.get()
    if execution is not None:
        await execution.journal.finish(execution.job, execution.lease, reason=reason)


def authority_binding(snapshot: MemorySnapshot) -> tuple[str | None, int | None]:
    authority_id = snapshot.authority_id
    epoch = snapshot.authority_epoch
    if authority_id is None and epoch is None:
        return None, None  # local OSS catalogs; shared backend rejects absence
    if not isinstance(authority_id, str) or not authority_id or type(epoch) is not int or epoch < 0:
        raise MemoryUnavailableError("incomplete memory authority binding")
    return authority_id, epoch


async def execute_plan(
    *,
    library: MemoryLibrary,
    sources: tuple[SourceRef, ...],
    build: Callable[[MemorySnapshot, str], Awaitable[ExtractionPlan]],
    verify: Callable[[], Awaitable[None]],
    provider_id: str,
    cursor: tuple[str, ...],
) -> None:
    execution = current_review.get()
    if execution is None:
        raise RuntimeError("durable review execution required")
    journal, lease, limits = execution.journal, execution.lease, execution.limits
    job = execution.job
    await verify()
    if await library.sources_forgotten(sources):
        await stop_review("source_forgotten")
        return
    if not job.plan_ready:
        snapshot = await library.snapshot()
        binding = authority_binding(snapshot)
        review_id = hashlib.sha256(
            f"{job.id}:{job.generation}:{snapshot.revision}:{binding}".encode()
        ).hexdigest()
        job = await journal.prepare(
            job,
            lease,
            revision=snapshot.revision,
            review_id=review_id,
            sources=sources,
            project_id=job.project_id,
            authority_id=binding[0],
            authority_epoch=binding[1],
        )
        execution.job = job
        from valuz_agent.ports.extensions import ext

        budget = await ext.billing.check_budget(job.user_id, provider_id=provider_id)
        if not budget.allowed:
            await stop_review("budget_denied")
            return
        if not await journal.reserve_model(job, lease, limits):
            await stop_review("daily_call_limit")
            return
        plan = await asyncio.wait_for(build(snapshot, review_id), timeout=limits.model_timeout)
        await verify()
        fresh = await library.snapshot()
        if (
            fresh.revision != snapshot.revision
            or authority_binding(fresh) != binding
            or await library.sources_forgotten(sources)
        ):
            raise MemoryConflictError("memory changed during review")
        job = await journal.save_plan(job, lease, list(plan.operations))
        execution.job = job
    if job.base_revision is None:
        raise InvalidReviewError("missing review revision")
    revision = job.base_revision if not job.receipts else int(job.receipts[-1]["revision"])
    while job.next_op < len(job.planned_ops):
        await verify()
        await journal.check(job, lease)
        if await library.sources_forgotten(job.sources):
            await stop_review("source_forgotten")
            return
        op = job.planned_ops[job.next_op]
        receipt = await library.operation_receipt(str(op["operation_id"]))
        fresh = await library.snapshot()
        # A receipt can explain our crash window, never an unrelated foreground correction.
        expected = receipt.revision if receipt is not None else revision
        if fresh.revision != expected or authority_binding(fresh) != (
            job.authority_id,
            job.authority_epoch,
        ):
            raise MemoryConflictError("memory changed before application")
        result = await library.mutate(
            **mutation_args(
                op, revision, authority_id=job.authority_id, authority_epoch=job.authority_epoch
            )
        )
        if (result.authority_id, result.authority_epoch) != (job.authority_id, job.authority_epoch):
            raise MemoryUnavailableError("mutation receipt authority changed")
        job = await journal.checkpoint(job, lease, result.model_dump(mode="json"))
        execution.job = job
        revision = result.revision
    await journal.finish(job, lease, cursor=cursor)
