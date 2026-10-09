"""Current confirmed lessons can inform a Skill draft, never enable one by themselves."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.ports.memory import MemoryProtected, MemoryRecord, MemorySnapshot


class LearningRecordRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: str
    record_revision: int = Field(ge=1, strict=True)


class SkillLearningSubmissionContext(BaseModel):
    """Untrusted IDs/version claims; always checked against the live owner authority.

    Contains no caller-supplied owner, approval or source text. A marked draft
    binds these claims into its existing skill.submit proposal and file hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    namespace: Literal["core"] = "core"
    project_id: str | None
    authority_id: str | None
    authority_epoch: int | None = Field(ge=0, strict=True)
    evidence: tuple[LearningRecordRef, ...] = Field(min_length=2, max_length=8)


class SkillLearningPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    catalog_revision: int
    authority_id: str | None
    authority_epoch: int | None
    evidence: tuple[LearningRecordRef, ...]
    draft: str
    replay_cases: tuple[str, ...]
    submission_context: SkillLearningSubmissionContext
    saved: Literal[False] = False
    installed: Literal[False] = False
    required_next_steps: tuple[str, ...] = (
        "Review the exact draft and its sources.",
        "Replay representative cases under the existing permissions.",
        "Write submission_context verbatim to .skill-learning.json beside the staged SKILL.md; "
        "the existing submission approval revalidates these source versions before saving.",
        "Submit the reviewed draft through skill-creator and the existing skill.submit approval.",
    )


class SkillLearningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_ids: tuple[str, ...] = Field(min_length=2, max_length=8)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    purpose: str = Field(min_length=1, max_length=300)
    replay_cases: tuple[str, ...] = Field(min_length=2, max_length=8)


def _eligible(
    snapshot: MemorySnapshot, request: SkillLearningRequest, project_id: str | None
) -> tuple[MemoryRecord, ...]:
    records = _eligible_records(snapshot, request.record_ids, project_id)
    if any(len(case.strip()) < 3 or len(case) > 500 for case in request.replay_cases):
        raise ValueError("Replay cases must describe bounded, representative checks")
    return records


def _eligible_records(
    snapshot: MemorySnapshot, record_ids: tuple[str, ...], project_id: str | None
) -> tuple[MemoryRecord, ...]:
    ids = set(record_ids)
    records = tuple(record for record in snapshot.records if record.id in ids)
    if len(ids) != len(record_ids) or len(records) != len(ids):
        raise MemoryProtected("Every lesson must still exist in the selected owner catalog")
    if any(
        record.namespace != "core"
        or not record.confirmed
        or record.kind not in {"lesson", "decision"}
        or (record.target == "project" and record.project_id != project_id)
        for record in records
    ):
        raise MemoryProtected("Only current confirmed lessons in this scope can inform a Skill")
    sources = {
        (ref.kind, ref.source_id)
        for record in records
        for ref in record.source_refs
        if ref.origin == "owner" and ref.kind in {"session", "task", "manual"}
    }
    if sum(len(record.content) for record in records) > 3200:
        raise ValueError("Select a smaller set of lessons for a bounded Skill draft")
    if any(
        not any(
            ref.origin == "owner" and ref.kind in {"session", "task", "manual"}
            for ref in record.source_refs
        )
        for record in records
    ):
        raise MemoryProtected("Each lesson must have reviewed owner evidence")
    if len(sources) < 2:
        raise MemoryProtected("A repeated method needs at least two independent reviewed sources")
    return records


async def validate_learning_submission(
    user_id: str,
    context: SkillLearningSubmissionContext,
    *,
    project_id: str | None,
    library: MemoryLibrary | None = None,
) -> None:
    """Refresh marked learning sources before proposal and confirmed save.

    No grant or fallback: ordinary unmarked skills never call this function.
    This protects the explicit learning lineage, not semantic classification
    of arbitrary manually rewritten SKILL.md text.
    """
    import asyncio

    from valuz_agent.ports.memory import MemoryUnavailable

    if not user_id or (library is not None and library.user_id != user_id):
        raise MemoryProtected("An explicit matching memory owner is required")
    if context.project_id != project_id:
        raise ValueError("stale: learning source project scope changed; preview again")
    try:
        async with asyncio.timeout(3):
            if not await MemoryLibrary.recall_enabled(user_id):
                raise MemoryProtected("Memory use is disabled; learning submission refused")
            snapshot = await (library or MemoryLibrary(user_id)).snapshot()
    except TimeoutError as exc:
        raise MemoryUnavailable("Learning authority refresh unavailable; no Skill saved") from exc
    except MemoryUnavailable as exc:
        raise MemoryUnavailable("Learning authority refresh unavailable; no Skill saved") from exc
    except MemoryProtected as exc:
        raise MemoryProtected("Learning source access refused; no Skill saved") from exc
    if (snapshot.authority_id, snapshot.authority_epoch) != (
        context.authority_id,
        context.authority_epoch,
    ):
        raise ValueError("stale: learning authority binding changed; preview again")
    try:
        records = _eligible_records(
            snapshot, tuple(ref.record_id for ref in context.evidence), project_id
        )
    except MemoryProtected as exc:
        raise ValueError("stale: learning sources are no longer current confirmed lessons") from exc
    expected = {ref.record_id: ref.record_revision for ref in context.evidence}
    if any(expected[record.id] != record.revision for record in records):
        raise ValueError("stale: learning source revisions changed; preview again")


async def preview_skill_learning(
    user_id: str,
    request: SkillLearningRequest,
    *,
    project_id: str | None,
    library: MemoryLibrary | None = None,
) -> SkillLearningPreview:
    if not user_id or (library is not None and library.user_id != user_id):
        raise MemoryProtected("An explicit matching memory owner is required")
    if not await MemoryLibrary.recall_enabled(user_id):
        raise MemoryProtected("Memory use is disabled")
    snapshot = await (library or MemoryLibrary(user_id)).snapshot()
    records = _eligible(snapshot, request, project_id)
    # This is a proposed document, not instructions injected into the current session.
    body = "\n".join(f"- {record.content}" for record in records)
    cases = "\n".join(f"- {case.strip()}" for case in request.replay_cases)
    draft = (
        f"---\nname: {json.dumps(request.name, ensure_ascii=False)}\n"
        f"description: {json.dumps(request.purpose, ensure_ascii=False)}\n---\n\n"
        f"# Proposed method\n\n{body}\n\n# Replay before enabling\n\n{cases}\n\n"
        "Keep existing tool permissions, approval rules and scope. This draft does not "
        "grant access, create automation, or authorize external actions.\n"
    )
    return SkillLearningPreview(
        catalog_revision=snapshot.revision,
        authority_id=snapshot.authority_id,
        authority_epoch=snapshot.authority_epoch,
        evidence=tuple(
            LearningRecordRef(record_id=record.id, record_revision=record.revision)
            for record in records
        ),
        draft=draft,
        replay_cases=request.replay_cases,
        submission_context=SkillLearningSubmissionContext(
            project_id=project_id,
            authority_id=snapshot.authority_id,
            authority_epoch=snapshot.authority_epoch,
            evidence=tuple(
                LearningRecordRef(record_id=record.id, record_revision=record.revision)
                for record in records
            ),
        ),
    )
