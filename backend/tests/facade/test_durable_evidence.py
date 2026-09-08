"""Shared storage contracts with a real SQL transaction, not mocked rows."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.durable_evidence import (
    DurableEvidenceLibrary,
    EntityRef,
    EvidenceSnapshotStorage,
    PendingEvidenceSealInput,
    PendingEvidenceSealStorage,
    ProvenanceInput,
    ProvenanceRecordStorage,
    import_evidence_records,
)
from valuz_agent.facade.evidence import SealedMessageEvidence, canonical_citation_hash
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


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        row.__table__
        for row in (
            EvidenceSnapshotStorage,
            PendingEvidenceSealStorage,
            ProvenanceRecordStorage,
            OperationRecordRow,
            ConfirmationDecisionRow,
        )
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def seal(
    *, source_version: str = "v1", unit: str = "USD", input_version: str | None = None
) -> SealedMessageEvidence:
    payload = {
        "source": {
            "sourceType": "document",
            "providerId": "test",
            "sourceId": "source",
            "documentVersion": source_version,
            "title": "Source",
        },
        "evidence": {"kind": "quote", "text": "Canonical evidence", "unit": unit},
        "locator": {"page": 3},
        "annotations": {},
    }
    if input_version is not None:
        payload["annotations"] = {"methodRef": "sum", "inputVersionRefs": [input_version]}
    return SealedMessageEvidence(
        message_id="message",
        citation_id="citation",
        bundle_version=1,
        citation_hash=canonical_citation_hash(payload),
        **payload,
    )


def pending(
    *, citation_id: str = "citation", expires_at: int | None = None
) -> PendingEvidenceSealInput:
    item = seal()
    return PendingEvidenceSealInput(
        operation_id="op",
        message_id="message",
        citation_id=citation_id,
        payload=item.canonical_payload(),
        citation_hash=item.citation_hash,
        validation_revision="citation-bundle-v1",
        expires_at=expires_at,
    )


async def test_capture_reuses_identity_and_views_are_detached(db: AsyncSession) -> None:
    library = DurableEvidenceLibrary(db)
    first, reused = await library.capture("owner", seal())
    assert not reused and first.source_ref.source_id == "source"
    assert await db.scalar(select(func.count()).select_from(ProvenanceRecordStorage)) == 0
    first.snapshot["text"] = "mutation"
    original = await library.get_snapshot("owner", first.id)
    assert original.snapshot["text"] == "Canonical evidence"
    repeated, reused = await library.capture(
        "owner", replace(seal(), message_id="other-message", citation_id="other-citation")
    )
    assert repeated.id == original.id and repeated.fingerprint == original.fingerprint and reused
    other, reused = await library.capture("other", seal())
    assert other.id != original.id and not reused
    with pytest.raises(LookupError):
        await library.get_snapshot("other", first.id)
    with pytest.raises(ValueError, match="owner_required"):
        await library.capture("", seal())


async def test_first_savepoint_does_not_commit_outer_transaction(db: AsyncSession) -> None:
    library = DurableEvidenceLibrary(db)
    original, _ = await library.capture("owner", seal())
    await db.rollback()
    with pytest.raises(LookupError):
        await library.get_snapshot("owner", original.id)
    await library.seal_pending("owner", pending())
    await db.rollback()
    assert not await library.pending_for_operation("owner", "op")
    connection = await db.connection()
    await connection.run_sync(
        lambda sync: import_evidence_records(
            sync, owner_user_id="owner", kind="snapshot", records=[original.model_dump(mode="json")]
        )
    )
    await db.rollback()
    with pytest.raises(LookupError):
        await library.get_snapshot("owner", original.id)


async def test_capture_preserves_legacy_fingerprint_and_distinguishes_semantics(
    db: AsyncSession,
) -> None:
    import hashlib
    import json

    library = DurableEvidenceLibrary(db)
    first, _ = await library.capture("owner", seal())
    identity = {
        "source_type": "document",
        "provider_id": "test",
        "source_id": "source",
        "source_version": "v1",
        "locator": {"page": 3},
        "evidence_kind": "quote",
        "content_hash": first.content_hash,
        "as_of": None,
        "period": None,
        "unit": "USD",
        "currency": None,
        "scale": None,
        "basis": None,
    }
    assert (
        first.fingerprint
        == "sha256:"
        + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    identities = {first.id}
    for item in (
        seal(source_version="v2"),
        seal(unit="CNY"),
        seal(input_version="a"),
        seal(input_version="b"),
    ):
        result, reused = await library.capture("owner", item)
        assert not reused
        identities.add(result.id)
    assert len(identities) == 5
    changed = seal()
    changed.evidence["text"] = "tampered"
    with pytest.raises(ValueError, match="canonical_seal_changed"):
        await library.capture("owner", changed)


async def test_status_cas_preserves_history_and_hides_inaccessible_payload(
    db: AsyncSession,
) -> None:
    library = DurableEvidenceLibrary(db)
    first, _ = await library.capture("owner", seal())
    await library.set_snapshot_status(
        "owner", first.id, expected_status="ready", status="forbidden"
    )
    with pytest.raises(LookupError):
        await library.get_snapshot("owner", first.id)
    with pytest.raises(LookupError):
        await library.capture("owner", seal())
    with pytest.raises(ValueError, match="conflict"):
        await library.set_snapshot_status(
            "other", first.id, expected_status="forbidden", status="ready"
        )
    await library.set_snapshot_status(
        "owner", first.id, expected_status="forbidden", status="ready"
    )
    assert (await library.get_snapshot("owner", first.id)).fingerprint == first.fingerprint


async def test_provenance_owner_refs_versions_pagination_and_no_mutation(db: AsyncSession) -> None:
    library = DurableEvidenceLibrary(db)
    snapshot, _ = await library.capture("owner", seal())
    values = ProvenanceInput(
        subject_type="test.object",
        subject_id="object",
        subject_version="v1",
        context_refs=[EntityRef(type="test.context", id="context")],
        origin_kind="promotion",
        evidence_snapshot_refs=[snapshot.id],
    )
    first = await library.append_provenance("owner", values)
    next_record = await library.append_provenance(
        "owner", values.model_copy(update={"parent_provenance_refs": [first.id]})
    )
    third = await library.append_provenance(
        "owner", values.model_copy(update={"subject_version": "v2"})
    )
    next_record.context_refs[0].id = "changed"
    page = await library.list_provenance(
        "owner", subject_type="test.object", subject_id="object", limit=1
    )
    assert page.items[0].id == third.id and page.next_before is not None
    page2 = await library.list_provenance(
        "owner", subject_type="test.object", subject_id="object", limit=1, before=page.next_before
    )
    assert page2.items[0].context_refs[0].id == "context"
    specific = await library.list_provenance(
        "owner", subject_type="test.object", subject_id="object", subject_version="v1"
    )
    assert len(specific.items) == 2
    assert not (
        await library.list_provenance("other", subject_type="test.object", subject_id="object")
    ).items
    with pytest.raises(LookupError):
        await library.append_provenance("other", values)
    with pytest.raises(LookupError):
        await library.append_provenance(
            "owner", values.model_copy(update={"parent_provenance_refs": ["missing"]})
        )
    with pytest.raises(ValueError, match="limit"):
        await library.list_provenance(
            "owner", subject_type="test.object", subject_id="object", limit=1000
        )


async def test_seals_idempotence_bounds_expiry_and_claim_conflicts(db: AsyncSession) -> None:
    library = DurableEvidenceLibrary(db)
    first = await library.seal_pending("owner", pending())
    assert (await library.seal_pending("owner", pending())).id == first.id
    with pytest.raises(ValueError, match="seal_conflict"):
        await library.seal_pending("owner", pending().model_copy(update={"claim_id": "different"}))
    with pytest.raises(ValueError, match="conflict"):
        await library.finish_seal(
            "other", first.id, operation_id="op", citation_hash=first.citation_hash
        )
    await library.finish_seal(
        "owner", first.id, operation_id="op", citation_hash=first.citation_hash
    )
    assert not await library.pending_for_operation("owner", "op")
    assert (await library.seal_pending("owner", pending())).status == "consumed"
    await library.seal_pending("owner", pending(citation_id="b"))
    await library.seal_pending("owner", pending(citation_id="c"))
    with pytest.raises(ValueError, match="limit_exceeded"):
        await library.pending_for_operation("owner", "op", limit=1)
    expired = await library.seal_pending("owner", pending(citation_id="d", expires_at=1))
    with pytest.raises(ValueError, match="expired"):
        await library.pending_for_operation("owner", "op")
    with pytest.raises(ValueError, match="conflict"):
        await library.finish_seal(
            "owner", expired.id, operation_id="op", citation_hash=expired.citation_hash
        )


async def test_operation_failure_rolls_back_evidence_lineage_and_seal_consumption(
    db: AsyncSession,
) -> None:
    fail = True
    evidence = DurableEvidenceLibrary(db)
    pending_seal = await evidence.seal_pending("owner", pending())

    async def handler(context: OperationContext, _payload: dict) -> OperationResult:
        snapshot, _ = await evidence.capture(context.user_id, seal())
        assert context.operation is not None
        await evidence.append_provenance(
            context.user_id,
            ProvenanceInput(
                subject_type="test.object",
                subject_id="object",
                subject_version="v1",
                origin_kind="promotion",
                evidence_snapshot_refs=[snapshot.id],
                domain_operation_ref=context.operation.id,
            ),
        )
        await evidence.finish_seal(
            context.user_id,
            pending_seal.id,
            operation_id="op",
            citation_hash=pending_seal.citation_hash,
        )
        if fail:
            raise ValueError("failure_after_all_writes")
        return OperationResult([{"type": "evidence_snapshot", "id": snapshot.id}], {})

    register_operation(OperationRegistration("test.evidence.atomic", 1, handler))
    operations = OperationLibrary(db, object())  # type: ignore[arg-type]
    proposal = await operations.propose(
        "owner",
        OperationProposal(
            operation_type="test.evidence.atomic",
            actor_kind="agent",
            input_payload={},
            idempotency_key="atomic",
        ),
    )
    failed = await operations.confirm(
        "owner", proposal.id, expected_proposal_hash=proposal.proposal_hash
    )
    assert failed.state == "failed"
    assert await db.scalar(select(func.count()).select_from(EvidenceSnapshotStorage)) == 0
    assert await db.scalar(select(func.count()).select_from(ProvenanceRecordStorage)) == 0
    assert len(await evidence.pending_for_operation("owner", "op")) == 1
    await db.commit()
    fail = False
    success = await operations.confirm(
        "owner", proposal.id, expected_proposal_hash=proposal.proposal_hash
    )
    assert success.state == "succeeded" and not await evidence.pending_for_operation("owner", "op")
    repeat = await operations.confirm(
        "owner", proposal.id, expected_proposal_hash=proposal.proposal_hash
    )
    assert repeat.canonical_result_refs == success.canonical_result_refs
    assert await db.scalar(select(func.count()).select_from(EvidenceSnapshotStorage)) == 1
    assert await db.scalar(select(func.count()).select_from(ProvenanceRecordStorage)) == 1


async def test_import_keeps_identity_replay_and_historical_seal_fence(db: AsyncSession) -> None:
    library = DurableEvidenceLibrary(db)
    original, _ = await library.capture("owner", seal())
    pending_seal = await library.seal_pending("owner", pending())
    snapshot = original.model_dump(mode="json")
    connection = await db.connection()
    assert (
        await connection.run_sync(
            lambda sync: import_evidence_records(
                sync, owner_user_id="owner", kind="snapshot", records=[snapshot]
            )
        )
        == 0
    )
    changed = {**snapshot, "source_title": "changed"}
    with pytest.raises(ValueError, match="identity_conflict"):
        await connection.run_sync(
            lambda sync: import_evidence_records(
                sync, owner_user_id="owner", kind="snapshot", records=[changed]
            )
        )
    historic = {**pending_seal.model_dump(mode="json"), "id": "history", "operation_id": "old-op"}
    assert (
        await connection.run_sync(
            lambda sync: import_evidence_records(
                sync, owner_user_id="owner", kind="seal", records=[historic]
            )
        )
        == 1
    )
    assert (
        await connection.run_sync(
            lambda sync: import_evidence_records(
                sync, owner_user_id="owner", kind="seal", records=[historic]
            )
        )
        == 0
    )
    assert not await library.pending_for_operation("owner", "old-op")
    with pytest.raises(ValueError, match="conflict"):
        await library.finish_seal(
            "owner", "history", operation_id="old-op", citation_hash=pending_seal.citation_hash
        )
    fresh = {**snapshot, "id": "new-id", "fingerprint": "new-fingerprint"}
    with pytest.raises(ValueError, match="identity_conflict"):
        await connection.run_sync(
            lambda sync: import_evidence_records(
                sync, owner_user_id="owner", kind="snapshot", records=[fresh, changed]
            )
        )
    with pytest.raises(LookupError):
        await library.get_snapshot("owner", "new-id")
