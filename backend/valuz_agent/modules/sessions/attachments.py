"""Per-turn attachment lifecycle helpers.

Attachments are per-turn: a turn ships with exactly the pending set, then
those rows get stamped ``consumed_at`` so the next turn starts empty. These
three helpers (load pending / pick agent-facing paths / mark consumed) are
shared by the session run path and the task orchestrator.
"""

from __future__ import annotations

import os
from pathlib import Path

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.sessions.models import SessionAttachmentRow


def _resolve_file_key_path(user_id: str, ref: str | None) -> str | None:
    """Resolve a stored attachment reference to a local filesystem path.

    Local attachments store a Valuz-owned relative key under ``VALUZ_DATA_DIR``.
    Legacy rows and ``kb_doc`` references may hold an absolute path (the
    KB-owned source / a pre-migration file) and are used as-is so the migration
    needs no backfill.
    """
    if not ref:
        return None
    if os.path.isabs(ref):
        return ref
    rel = Path(ref)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        return None
    target = (fs_registry.data_dir(user_id) / rel).resolve()
    data_root = fs_registry.data_dir(user_id).resolve()
    if data_root != target and data_root not in target.parents:
        return None
    return str(target) if target.is_file() else None


async def _load_pending_attachments(session_id: str, user_id: str) -> list[SessionAttachmentRow]:
    if not user_id:
        raise ValueError("user_id is required")

    """Load the **pending** attachment rows for a session.

    Pending = ``consumed_at IS NULL`` = staged for the next turn.
    Attachments are per-turn: a turn ships with exactly this set, then
    those rows get stamped ``consumed_at`` (see
    ``_mark_attachments_consumed``) so the next turn starts empty. The
    caller captures this list once at the top of the turn and reuses
    it for both ``UserMessage.attachments`` and the
    ``additional-context`` block, so the two never disagree even if a
    new upload lands mid-turn.

    Returns detached ``SessionAttachmentRow`` objects — the session is
    closed before return, so only already-loaded columns are safe to
    read (all of them are, since SQLAlchemy eager-loads scalar
    columns).
    """
    from valuz_agent.modules.sessions.datastore import SessionDatastore

    async with async_unit_of_work() as db:
        return await SessionDatastore(db).list_attachments(user_id, session_id)


def _attachment_specs(rows, user_id: str) -> tuple[tuple[str | None, str | None], ...]:  # type: ignore[no-untyped-def]
    """Map each attachment row to a ``(source_path, parsed_path)`` pair.

    ``source_path`` is always the original file the user attached
    (``stored_path``) so the agent can operate on the real bytes. ``parsed_path``
    is the markdown text extract — surfaced *alongside* the original so the agent
    can ``Read`` reasoning-friendly text — but only when parsing actually
    succeeded (``parse_status == "ready"`` with a path); it is ``None`` while a
    file is still parsing, on parser miss/failure, or for raw PDFs / binaries.

    This replaces the old ``_attachment_paths`` collapse-to-one behavior, which
    dropped the original whenever a parse succeeded — leaving the agent unable to
    act on the source file.
    """
    return tuple(
        (
            _resolve_file_key_path(user_id, row.stored_path),
            _resolve_file_key_path(user_id, row.parsed_path)
            if row.parse_status == "ready" and row.parsed_path
            else None,
        )
        for row in rows
    )


async def _mark_attachments_consumed(attachment_ids: list[str]) -> None:
    """Stamp ``consumed_at`` on this turn's attachment rows.

    Called after ``run_turn`` so a file uploaded for turn N doesn't
    silently re-attach to turns N+1, N+2, …. No-op on an empty list.
    """
    if not attachment_ids:
        return
    from valuz_agent.modules.sessions.datastore import SessionDatastore

    async with async_unit_of_work() as db:
        await SessionDatastore(db).mark_attachments_consumed(attachment_ids)
