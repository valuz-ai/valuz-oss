"""``submit_skill`` + ``prepare_skill_edit`` — the skill-creator's host tools.

``submit_skill`` is how the agent hands a staged draft to the user. It used
to be a no-op whose ``tool_use`` event the frontend paired with a staging
scan; the card's state lived only in the page. It now PROPOSES a
``skill.submit`` operation (``modules/skills/operations.py``): the draft's
file list, its tree hash and how it collides with the library are recorded,
the user's confirm/cancel is a durable decision, and the tool result carries
the operation envelope the client renders and re-syncs after a refresh.

``prepare_skill_edit`` is the "improve an existing skill" entry point: it
copies the library copy into the session's staging directory with the
provenance marker the confirm step needs to tell "same source" from
"diverged", and returns where to edit. The agent must call it — not copy the
library directory by hand — because the marker is what makes the later save
a new VERSION of that skill rather than a collision.

Why this lives in valuz, not the kernel
---------------------------------------
The skill-staging trust model — "agent proposes, user disposes" — and
the per-entry-point side effects (chat / project / skills_library) are
host concerns. The kernel intentionally stays generic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.core.tools import ExecContext, ToolDef, ToolResult

# Side-effect import — surfaces ``src.core...`` on sys.path. Without this,
# the kernel package fails to resolve when this module is imported during
# app startup (before any other valuz module that drags it in).
import valuz_agent.boot.kernel  # noqa: F401

logger = logging.getLogger(__name__)


SUBMIT_SKILL_TOOL_NAME = "submit_skill"
PREPARE_SKILL_EDIT_TOOL_NAME = "prepare_skill_edit"

SUBMIT_SKILL_DESCRIPTION = (
    "Submit the skill you just authored (or modified) for the user's "
    "review. Call this exactly once when SKILL.md and any required "
    "assets are written to the staging directory and the work is "
    "complete. The user will review the staged content and decide "
    "whether to save it to their library; a save records a new version "
    "of the skill. Do not edit the staged files after submitting — the "
    "confirmation is bound to the submitted content and would be "
    "rejected as stale; submit again instead."
)

SUBMIT_SKILL_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "slug": {
            "type": "string",
            "description": (
                "Skill slug as written under the staging directory "
                "(matches the SKILL.md frontmatter `name` field)."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "One-line description of what was created or changed, "
                "shown to the user on the review card."
            ),
        },
        "change_kind": {
            "type": "string",
            "enum": ["create", "update"],
            "description": (
                "`create` for a brand-new skill; `update` when an "
                "existing library skill was prepared with "
                "prepare_skill_edit and modified in staging."
            ),
        },
        "files_touched": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Relative paths under the staged slug directory that "
                "this submission introduces or modifies. At minimum "
                "include `SKILL.md`."
            ),
        },
    },
    "required": ["slug", "summary", "change_kind", "files_touched"],
}

PREPARE_SKILL_EDIT_DESCRIPTION = (
    "Start improving an existing skill from the user's library: copies its "
    "current version into ./.skill-staging/<slug>/ in your working directory "
    "and marks the draft as derived from it, so the user's save becomes the "
    "skill's next version instead of a name collision. Call this BEFORE "
    "editing when the user wants to modify a skill that already exists "
    "(check with list_skills first). Also call it again to keep iterating "
    "after the user has saved — the staged copy is removed on save. Read-only "
    "and official skills cannot be edited in place; create a new skill under "
    "a different slug instead."
)

PREPARE_SKILL_EDIT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "slug": {
            "type": "string",
            "description": "Slug of the library skill to improve (as listed by list_skills).",
        },
    },
    "required": ["slug"],
}


def _operation(row: Any) -> dict[str, Any]:
    """The operation envelope the client's card reads (same shape the
    playbook tools emit, so ``parseOperationToolOutput`` needs no new case)."""
    return {
        "id": row.id,
        "operation_type": row.operation_type,
        "operation_version": row.operation_version,
        "project_id": row.project_id,
        "actor_kind": row.actor_kind,
        "actor_id": row.actor_id,
        "origin_session_id": row.origin_session_id,
        "origin_tool_call_id": row.origin_tool_call_id,
        "origin_playbook_run_id": row.origin_playbook_run_id,
        "origin_automation_run_id": row.origin_automation_run_id,
        "target_refs": row.target_refs,
        "state": row.state,
        "risk_level": row.risk_level,
        "confirmation_policy": row.confirmation_policy,
        "proposal_hash": row.proposal_hash,
        "preview": row.preview,
        "input_payload": row.input_payload,
        "expected_revisions": row.expected_revisions,
        "canonical_result_refs": row.canonical_result_refs,
        "result_payload": row.result_payload,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _session_id_of(context: ExecContext) -> str:
    return (getattr(context, "session_id", "") or "").strip()


_NO_SESSION = ToolResult(
    content=json.dumps(
        {
            "ok": False,
            "action": "submit",
            "error_code": "no_session",
            "message": (
                "Error: session id is empty in ExecContext — cannot resolve "
                "the staging location. Ask the user to retry the session."
            ),
        },
        ensure_ascii=False,
    ),
    is_error=True,
)


def _submit_failed(error_code: str, message: str) -> ToolResult:
    """A failed submission still speaks the envelope.

    The card reads its state from the operation record and falls back to a
    staging scan when the tool result carries none — which is how a HISTORIC
    card (from a session that predates the record) still renders. A bare
    error string is indistinguishable from that, so a failure fell into the
    same branch, and since a save empties staging the scan concluded
    "waiting for the AI to write files": a failure displayed as progress,
    identically before and after a reload. ``ok: false`` is what lets the
    client tell the two apart.
    """
    return ToolResult(
        content=json.dumps(
            {
                "ok": False,
                "action": "submit",
                "error_code": error_code,
                "message": message,
            },
            ensure_ascii=False,
        ),
        is_error=True,
    )


def _not_staged(slug: str, expected_dir: Path) -> ToolResult:
    """The agent wrote somewhere else: teach it the exact expected path so
    its next turn can move the files and retry."""
    project_root = str(expected_dir.parent.parent)
    logger.warning("submit_skill rejected: slug=%s missing SKILL.md at %s", slug, expected_dir)
    return _submit_failed(
        "skill_not_staged",
        (
            f"Error: did not find SKILL.md at the expected staging "
            f"path:\n\n  {expected_dir}/SKILL.md\n\n"
            f"Move every file for slug '{slug}' into "
            f"``./.skill-staging/{slug}/`` (relative to your "
            f"current working directory `{project_root}`), then "
            f"call ``submit_skill`` again. Do not write skill files "
            f"to ``/tmp``, ``~/.agents/skills/``, or any other "
            f"location — staging files MUST live under "
            f"``.skill-staging/`` of the cwd so the host's "
            f"submission flow can find them."
        ),
    )


async def _submit_skill_handler(args: dict[str, object], context: ExecContext) -> ToolResult:
    """Validate the staged slug, then record the submission as an operation.

    The staging directory is resolved through the host's single
    authoritative resolver — the SAME one ``scan_staging`` and the
    confirm step use — keyed off the session id (always set on the
    toolkit MCP path; ``ExecContext.workspace`` is not). A missing
    ``SKILL.md`` returns ``is_error=True`` with the exact expected path so
    the agent's next turn can ``mv`` the files into place and call again.
    """
    user_id = context.user_id
    slug = str(args.get("slug") or "").strip()
    summary = str(args.get("summary") or "")
    change_kind = str(args.get("change_kind") or "create")
    raw_files = args.get("files_touched") or []
    files_touched = [str(f) for f in raw_files] if isinstance(raw_files, list) else []

    session_id = _session_id_of(context)
    if not session_id:
        return _NO_SESSION
    if not slug:
        return _submit_failed("slug_required", "Error: `slug` is required.")

    from valuz_agent.integrations.skills_filesystem import _detect_manifest
    from valuz_agent.modules.skills import staging

    # Cheap validation before any DB work: the most common failure is the
    # agent writing to an invented location, and that answer needs no
    # transaction.
    expected_dir = await staging.staging_dir_for_session(user_id, session_id) / slug
    if _detect_manifest(expected_dir) is None:
        return _not_staged(slug, expected_dir)

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.skills.operations import propose_skill_submission

    try:
        async with async_unit_of_work() as db:
            row = await propose_skill_submission(
                db,
                user_id,
                session_id,
                slug,
                summary=summary,
                change_kind=change_kind,
                files_touched=files_touched,
            )
            envelope = _operation(row)
    except LookupError as exc:
        return _not_staged(slug, Path(str(exc)))
    except Exception as exc:  # noqa: BLE001 — the card must see WHY, not a blank
        logger.exception("submit_skill: could not record the submission for %s", slug)
        return _submit_failed(
            "submit_failed",
            f"Could not record the submission for '{slug}': {exc}",
        )

    preview = envelope.get("preview") or {}
    conflict = str(preview.get("conflict_kind") or "none")
    next_version = preview.get("next_version")
    logger.info(
        "submit_skill: slug=%s change_kind=%s files=%d conflict=%s operation=%s",
        slug,
        change_kind,
        len(files_touched),
        conflict,
        envelope.get("id"),
    )
    if conflict == "unprepared_collision":
        message = (
            f"Submitted '{slug}' for the user's review — note that a library skill "
            f"named '{slug}' already exists and this draft was not prepared from it "
            f"with prepare_skill_edit. The card will ask the user whether to save it "
            f"as that skill's next version or under a new name. Stop here and wait "
            f"for the user; do not edit the staged files (that would invalidate the "
            f"submission)."
        )
    else:
        message = (
            f"Submitted '{slug}' for the user's review (would become v{next_version}). "
            f"They will be shown a card in the chat with options to save to the "
            f"library or dismiss. Stop here — do not edit the staged files or "
            f"continue unless the user asks for changes; if they do, make the "
            f"changes and call submit_skill again."
        )
    return ToolResult(
        content=json.dumps(
            {"ok": True, "action": "submit", "message": message, "operation": envelope},
            ensure_ascii=False,
        )
    )


async def _prepare_skill_edit_handler(args: dict[str, object], context: ExecContext) -> ToolResult:
    user_id = context.user_id
    slug = str(args.get("slug") or "").strip()
    session_id = _session_id_of(context)
    if not session_id:
        return _NO_SESSION
    if not slug:
        return ToolResult(content="Error: `slug` is required.", is_error=True)

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.integrations.skills_filesystem import _default_user_skill_root
    from valuz_agent.modules.artifacts.service import get_head_revision
    from valuz_agent.modules.skills import staging
    from valuz_agent.modules.skills.datastore import SkillDatastore

    library_dir = _default_user_skill_root(user_id) / slug
    async with async_unit_of_work(commit=False) as db:
        ds = SkillDatastore(db)
        row = await ds.get_by_source_path(user_id, str(library_dir))
        if row is None:
            other = await ds.get_by_slug(user_id, slug)
            if other is not None:
                return ToolResult(
                    content=(
                        f"Error: '{slug}' exists but not in the user's editable library "
                        f"(scope={other.scope}). It cannot be edited in place — create "
                        f"a new skill under a different slug instead."
                    ),
                    is_error=True,
                )
            return ToolResult(
                content=(
                    f"Error: no library skill named '{slug}'. Call list_skills to see "
                    f"what exists, or create it as a new skill."
                ),
                is_error=True,
            )
        if (
            bool(getattr(row, "readonly", False))
            or bool(getattr(row, "is_locked", False))
            or bool(getattr(row, "protected", False))
        ):
            return ToolResult(
                content=(
                    f"Error: '{slug}' is read-only and cannot be edited in place. "
                    f"Create a new skill under a different slug instead."
                ),
                is_error=True,
            )
        version: int | None = None
        artifact_id = getattr(row, "artifact_id", None)
        if artifact_id:
            head = await get_head_revision(db, user_id, artifact_id)
            version = head.version_no if head is not None else None
        skill_id = row.id

    try:
        dest = await staging.prepare_optimize(user_id, session_id, library_dir, skill_id)
    except (FileNotFoundError, ValueError) as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    files, file_count, _total = staging.list_staged_files(dest)
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "action": "prepare_edit",
                "slug": slug,
                "staging_path": str(dest),
                "relative_path": f"./.skill-staging/{slug}/",
                "current_version": version,
                "next_version": (version or 0) + 1,
                "file_count": file_count,
                "files": [f.path for f in files if f.type == "file"],
                "message": (
                    f"Copied '{slug}' into ./.skill-staging/{slug}/ (current v{version or '?'}). "
                    f"Edit the files there — do not set `version:` in SKILL.md, the host "
                    f"assigns it on save — then call submit_skill with change_kind='update'."
                ),
            },
            ensure_ascii=False,
        )
    )


SUBMIT_SKILL_TOOL_DEF = ToolDef(
    name=SUBMIT_SKILL_TOOL_NAME,
    description=SUBMIT_SKILL_DESCRIPTION,
    parameters=SUBMIT_SKILL_PARAMETERS,
    handler=_submit_skill_handler,
    read_only=False,
)

PREPARE_SKILL_EDIT_TOOL_DEF = ToolDef(
    name=PREPARE_SKILL_EDIT_TOOL_NAME,
    description=PREPARE_SKILL_EDIT_DESCRIPTION,
    parameters=PREPARE_SKILL_EDIT_PARAMETERS,
    handler=_prepare_skill_edit_handler,
    read_only=False,
)


# Pure declaration (no handler) — the shape persisted on every kernel
# ``agents`` row so the runtime advertises the tool to the model.
# ``build_toolkit_for_config`` walks the global registry to attach the
# real handler when a session is built.
SUBMIT_SKILL_TOOL_DECLARATION = ToolDef(
    name=SUBMIT_SKILL_TOOL_NAME,
    description=SUBMIT_SKILL_DESCRIPTION,
    parameters=SUBMIT_SKILL_PARAMETERS,
    handler=None,
)

PREPARE_SKILL_EDIT_TOOL_DECLARATION = ToolDef(
    name=PREPARE_SKILL_EDIT_TOOL_NAME,
    description=PREPARE_SKILL_EDIT_DESCRIPTION,
    parameters=PREPARE_SKILL_EDIT_PARAMETERS,
    handler=None,
)


def build_submit_skill_tool_defs() -> tuple[ToolDef, ...]:
    """Return the skill-creator tool defs (live handlers) for the host
    toolkit MCP server."""
    return (SUBMIT_SKILL_TOOL_DEF, PREPARE_SKILL_EDIT_TOOL_DEF)


__all__ = [
    "PREPARE_SKILL_EDIT_TOOL_DECLARATION",
    "PREPARE_SKILL_EDIT_TOOL_DEF",
    "PREPARE_SKILL_EDIT_TOOL_NAME",
    "SUBMIT_SKILL_TOOL_DECLARATION",
    "SUBMIT_SKILL_TOOL_DEF",
    "SUBMIT_SKILL_TOOL_NAME",
    "build_submit_skill_tool_defs",
]
