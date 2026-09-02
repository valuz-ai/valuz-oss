"""``skill.submit`` — the skill-creator submission as a durable operation.

Before this, ``submit_skill`` wrote nothing and the confirmation card
inferred its state from a staging-directory scan, which both confirm and
dismiss delete — so after a page refresh a saved skill and a discarded one
looked identical ("waiting for files"). Now the tool PROPOSES an operation
record (``modules/operations``): what the user is shown, what they decided,
and what happened are all persisted, and a refreshed card asks the server.

The proposal captures the staged tree's hash. Confirmation recomputes it and
refuses on mismatch (``OPERATION_STALE``): what the user approved is what
gets saved, never a draft the agent kept editing after submitting.

The proposal also classifies the collision with the library (§5.2 of the
design): a draft that was not prepared from the existing skill it collides
with cannot be confirmed without the user saying which of two things they
mean — save it as the next version, or save it under another slug.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.frontmatter import split_frontmatter
from valuz_agent.integrations.skills_filesystem import (
    FilesystemSkillSource,
    _default_user_skill_root,
    _detect_manifest,
)
from valuz_agent.modules.operations.models import OperationRecordRow
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import OperationProposal
from valuz_agent.modules.operations.service import proposal_hash
from valuz_agent.modules.skills import staging, versioning
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.service import SkillLibraryService

logger = logging.getLogger(__name__)

SKILL_SUBMIT_OPERATION = "skill.submit"
SKILL_SUBMIT_VERSION = 1

#: How the staged slug relates to the user's library (design §5.2).
CONFLICT_NONE = "none"
CONFLICT_SAME_SOURCE = "same_source"
CONFLICT_DIVERGED = "diverged"
CONFLICT_UNPREPARED_COLLISION = "unprepared_collision"

DECISION_NEW_VERSION = "new_version"
DECISION_RENAME = "rename"

#: Operation states after which a re-submit of the same bytes must mint a
#: fresh record instead of returning the finished one.
_TERMINAL_STATES = frozenset({"succeeded", "cancelled", "failed", "stale", "expired", "superseded"})

_NAME_LINE_RE = re.compile(r"^name\s*:.*$", flags=re.MULTILINE)


@dataclass(frozen=True)
class StagedSubmission:
    slug: str
    staging_dir: Path
    tree_hash: str
    files: list[dict[str, Any]]
    file_count: int
    total_bytes: int
    conflict_kind: str
    library_dir: Path
    source_skill_id: str | None = None
    existing_skill_id: str | None = None
    existing_artifact_id: str | None = None
    next_version: int = 1
    manifest_name: str = ""
    manifest_description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def skill_service_for(db: AsyncSession) -> SkillLibraryService:
    """A library service bound to THIS session — the operation's transaction
    is the one every write has to land in (see ``defer_commits``)."""
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.integrations.skills_official import OfficialSkillSource
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService

    return SkillLibraryService(
        datastore=SkillDatastore(db),
        skill_source=FilesystemSkillSource(),
        project_service=ProjectService(datastore=ProjectDatastore(db), event_bus=event_bus),
        extra_sources=[OfficialSkillSource()],
    )


async def inspect_staged_submission(
    db: AsyncSession, user_id: str, session_id: str, slug: str
) -> StagedSubmission:
    """Everything the proposal needs to know about one staged slug.

    Raises ``LookupError`` when the slug is not staged (no ``SKILL.md`` at
    the session's staging location) — the tool turns that into the "move
    your files here" message the agent already knows.
    """
    staging_dir = await staging.staging_dir_for_session(user_id, session_id) / slug
    if _detect_manifest(staging_dir) is None:
        raise LookupError(str(staging_dir))

    files, file_count, total_bytes = staging.list_staged_files(staging_dir)
    tree_hash = staging.hash_skill_directory(staging_dir)
    meta = staging.read_staging_meta(staging_dir)
    name, description, _version = staging._read_manifest_meta(staging_dir)

    library_dir = _default_user_skill_root(user_id) / slug
    if not library_dir.exists():
        conflict = CONFLICT_NONE
    elif meta is not None and meta.source_content_hash:
        conflict = (
            CONFLICT_SAME_SOURCE
            if staging.hash_skill_directory(library_dir) == meta.source_content_hash
            else CONFLICT_DIVERGED
        )
    else:
        conflict = CONFLICT_UNPREPARED_COLLISION

    row = await SkillDatastore(db).get_by_skill_dir(user_id, library_dir)
    artifact_id = getattr(row, "artifact_id", None) if row is not None else None
    # ``slug`` so an index row with no ``artifact_id`` still finds its lineage
    # — the save does (``deliver_artifact`` looks the artifact up by archive
    # name), and a card that promised a version the save would not use is the
    # bug this closes.
    next_version = await versioning.next_version_no(
        db, user_id, artifact_id, slug=slug, installed_dir=library_dir
    )

    return StagedSubmission(
        slug=slug,
        staging_dir=staging_dir,
        tree_hash=tree_hash,
        files=[{"path": f.path, "type": f.type, "size": f.size} for f in files],
        file_count=file_count,
        total_bytes=total_bytes,
        conflict_kind=conflict,
        library_dir=library_dir,
        source_skill_id=meta.source_skill_id if meta else None,
        existing_skill_id=row.id if row is not None else None,
        existing_artifact_id=artifact_id,
        next_version=next_version,
        manifest_name=name,
        manifest_description=description,
    )


def build_submission_proposal(
    sub: StagedSubmission,
    *,
    session_id: str,
    summary: str,
    change_kind: str,
    files_touched: list[str],
    idempotency_suffix: str = "",
) -> OperationProposal:
    payload = {
        "session_id": session_id,
        "slug": sub.slug,
        "summary": summary,
        "change_kind": change_kind,
        "files_touched": list(files_touched),
        "staging_tree_hash": sub.tree_hash,
        "conflict_kind": sub.conflict_kind,
    }
    preview = {
        "kind": "skill",
        "slug": sub.slug,
        "name": sub.manifest_name,
        "description": sub.manifest_description,
        "summary": summary,
        "change_kind": change_kind,
        "files": sub.files,
        "file_count": sub.file_count,
        "total_bytes": sub.total_bytes,
        "staging_path": str(sub.staging_dir),
        "conflict_kind": sub.conflict_kind,
        "source_skill_id": sub.source_skill_id,
        "existing_skill_id": sub.existing_skill_id,
        "next_version": sub.next_version,
    }
    target_refs: list[dict[str, Any]] = [{"type": "skill", "slug": sub.slug}]
    if sub.existing_skill_id:
        target_refs[0]["id"] = sub.existing_skill_id
    proposal = OperationProposal(
        operation_type=SKILL_SUBMIT_OPERATION,
        operation_version=SKILL_SUBMIT_VERSION,
        project_id=None,
        actor_kind="agent",
        actor_id=session_id,
        origin_session_id=session_id,
        target_refs=target_refs,
        input_payload=payload,
        preview=preview,
        expected_revisions={"staging_tree_hash": sub.tree_hash},
        risk_level="material",
        confirmation_policy="confirm",
        # Placeholder — replaced below once the proposal it must follow exists.
        idempotency_key="pending",
    )
    return _with_derived_key(proposal, session_id, idempotency_suffix)


def _with_derived_key(
    proposal: OperationProposal, session_id: str, suffix: str
) -> OperationProposal:
    """Give the proposal an idempotency key derived from the proposal itself.

    ``OperationService.propose`` refuses a second call that reuses a key under
    a DIFFERENT request, and the request it compares is ``proposal_hash`` —
    the whole proposal, ``input_payload`` and ``preview`` included. A key over
    only ``(slug, staged bytes)`` therefore collides on exactly the flow this
    design is built around:

        submit (change_kind=create, conflict=unprepared_collision)
        → prepare_skill_edit re-seeds the SAME bytes from the library
        → submit (change_kind=update, conflict=same_source)

    Same bytes, so the same key; different ``change_kind`` and summary, so a
    different hash — ``operation_idempotency_conflict``, on the corrected
    submission. Deriving the key FROM the hash makes "same request ⇒ same key"
    an identity, so this operation type cannot collide.

    ``proposal_hash`` does not read ``idempotency_key``, so there is no
    circularity — the placeholder above never reaches the digest. The suffix
    is what lets a re-submission after a terminal record mint a fresh one.
    """
    digest = proposal_hash(proposal)
    if suffix:
        digest = hashlib.sha256(f"{digest}{suffix}".encode()).hexdigest()
    return proposal.model_copy(
        update={"idempotency_key": f"skill.submit:{session_id[:36]}:{digest[:24]}"}
    )


async def propose_skill_submission(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    slug: str,
    *,
    summary: str,
    change_kind: str,
    files_touched: list[str],
) -> OperationRecordRow:
    """Record the submission as an operation awaiting the user's confirmation.

    Idempotent on ``(session, slug, staged bytes)``: a retried tool call
    returns the same pending record. A record that already reached a
    terminal state (the user cancelled, or it was applied) is not reused —
    the same draft submitted again after a dismissal is a new proposal.
    """
    from valuz_agent.facade.projects import ProjectLibrary
    from valuz_agent.modules.operations.service import OperationService

    sub = await inspect_staged_submission(db, user_id, session_id, slug)
    service = OperationService(db, ProjectLibrary())
    proposal = build_submission_proposal(
        sub,
        session_id=session_id,
        summary=summary,
        change_kind=change_kind,
        files_touched=files_touched,
    )
    row = await service.propose(user_id, proposal)
    if row.state in _TERMINAL_STATES:
        proposal = build_submission_proposal(
            sub,
            session_id=session_id,
            summary=summary,
            change_kind=change_kind,
            files_touched=files_touched,
            idempotency_suffix=f":after-{row.id}",
        )
        row = await service.propose(user_id, proposal)
    return row


# ── handler ──────────────────────────────────────────────────────────


def _set_manifest_name(manifest_path: Path, name: str) -> None:
    raw = manifest_path.read_text(encoding="utf-8")
    block, body = split_frontmatter(raw)
    line = f"name: {name}"
    if block is None:
        rewritten = f"---\n{line}\n---\n\n{body}"
    elif _NAME_LINE_RE.search(block):
        rewritten = f"---\n{_NAME_LINE_RE.sub(line, block, count=1)}\n---\n{body}"
    else:
        rewritten = f"---\n{line}\n{block}\n---\n{body}"
    manifest_path.write_text(rewritten, encoding="utf-8")


def _rename_staged_slug(staging_base: Path, library_root: Path, slug: str, new_slug: str) -> Path:
    chosen = (new_slug or "").strip()
    if not staging.SLUG_RE.match(chosen):
        raise ValueError(f"invalid new_slug: {chosen!r}")
    if chosen == slug:
        raise ValueError("new_slug must differ from the colliding slug")
    if (library_root / chosen).exists():
        raise ValueError(f"a library skill named {chosen!r} already exists; pick another slug")
    dest = staging_base / chosen
    if dest.exists():
        raise ValueError(f"staging already holds a draft named {chosen!r}")
    shutil.move(str(staging_base / slug), str(dest))
    manifest = _detect_manifest(dest)
    if manifest is not None:
        _set_manifest_name(manifest, chosen)
    return dest


async def _skill_submit_handler(
    context: OperationContext, payload: dict[str, Any]
) -> OperationResult:
    """Apply a confirmed submission through the library's save pipeline.

    Runs inside the operation's savepoint with commits deferred, so a
    failure anywhere leaves both the record's domain writes and the version
    history untouched. Filesystem writes (the library directory) happen
    after the version is recorded — see ``confirm_submission``.
    """
    user_id = context.user_id
    session_id = str(payload.get("session_id") or "")
    slug = str(payload.get("slug") or "")
    expected_hash = str(payload.get("staging_tree_hash") or "")
    conflict_kind = str(payload.get("conflict_kind") or CONFLICT_NONE)
    if not session_id or not slug:
        raise ValueError("skill.submit payload is missing session_id or slug")

    staging_base = await staging.staging_dir_for_session(user_id, session_id)
    staging_dir = staging_base / slug
    if _detect_manifest(staging_dir) is None:
        raise LookupError(
            f"skill_staging_missing: {staging_dir} no longer holds SKILL.md — "
            "the draft was removed since it was submitted; ask the agent to regenerate it"
        )
    if staging.hash_skill_directory(staging_dir) != expected_hash:
        raise ValueError(
            "stale: the staged files changed after they were submitted — what you "
            "reviewed is not what would be saved. Ask the agent to submit again."
        )

    library_root = _default_user_skill_root(user_id)
    final_slug = slug
    mode = str(context.decision.get("mode") or "")
    if conflict_kind == CONFLICT_UNPREPARED_COLLISION:
        if mode == DECISION_RENAME:
            final_slug = _rename_staged_slug(
                staging_base, library_root, slug, str(context.decision.get("new_slug") or "")
            ).name
        elif mode != DECISION_NEW_VERSION:
            raise ValueError(
                f"skill_slug_collision: the library already has a skill named {slug!r} and "
                "this draft was not prepared from it. Confirm with decision.mode="
                "'new_version' to save it as that skill's next version, or "
                "decision.mode='rename' with decision.new_slug to save it as a new skill."
            )

    svc = skill_service_for(context.db)
    skill, creation_context, bound_project_id = await svc.confirm_submission(
        user_id,
        session_id,
        final_slug,
        summary=str(payload.get("summary") or "") or None,
        change_kind=str(payload.get("change_kind") or "create"),
        files_touched=list(payload.get("files_touched") or []),
    )
    head = (
        await versioning.get_head_revision(context.db, user_id, skill.artifact_id)
        if skill.artifact_id
        else None
    )
    refs: list[dict[str, Any]] = [{"type": "skill", "id": skill.id, "slug": final_slug}]
    if skill.artifact_id:
        refs.append({"type": "artifact", "id": skill.artifact_id})
    return OperationResult(
        canonical_result_refs=refs,
        result_payload={
            "skill_id": skill.id,
            "slug": final_slug,
            "renamed_from": slug if final_slug != slug else None,
            "artifact_id": skill.artifact_id,
            "revision_id": head.id if head is not None else None,
            "version_no": head.version_no if head is not None else None,
            "creation_context": creation_context,
            "bound_to_project_id": bound_project_id,
            "decision_mode": mode or None,
        },
    )


async def _skill_submit_cancel(
    context: OperationContext, payload: dict[str, Any]
) -> OperationResult:
    """The user discarded the draft: drop the staged slug. Filesystem only,
    best-effort (the operation is already cancelled)."""
    session_id = str(payload.get("session_id") or "")
    slug = str(payload.get("slug") or "")
    if session_id and slug:
        await staging.remove_slug(context.user_id, session_id, slug)
    return OperationResult(canonical_result_refs=[], result_payload={"removed": True})


def register_skill_operations() -> None:
    operation_registry.register(
        OperationRegistration(
            operation_type=SKILL_SUBMIT_OPERATION,
            version=SKILL_SUBMIT_VERSION,
            handler=_skill_submit_handler,
            cancel_handler=_skill_submit_cancel,
        )
    )


register_skill_operations()


__all__ = [
    "CONFLICT_DIVERGED",
    "CONFLICT_NONE",
    "CONFLICT_SAME_SOURCE",
    "CONFLICT_UNPREPARED_COLLISION",
    "DECISION_NEW_VERSION",
    "DECISION_RENAME",
    "SKILL_SUBMIT_OPERATION",
    "StagedSubmission",
    "build_submission_proposal",
    "inspect_staged_submission",
    "propose_skill_submission",
    "register_skill_operations",
    "skill_service_for",
]
