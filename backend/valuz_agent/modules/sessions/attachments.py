"""Per-turn attachment lifecycle helpers.

Attachments are per-turn: a turn ships with exactly the pending set, then
those rows get stamped ``consumed_at`` so the next turn starts empty. These
three helpers (load pending / pick agent-facing paths / mark consumed) are
shared by the session run path and the task orchestrator.
"""

from __future__ import annotations

from valuz_agent.infra.db import async_unit_of_work


async def _load_pending_attachments(session_id: str):  # type: ignore[no-untyped-def]
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
        return await SessionDatastore(db).list_attachments(session_id)


def _attachment_paths(rows) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    """Pick the agent-facing filepath for each attachment row.

    Prefers ``parsed_path`` (LightLocalParser markdown extract) over
    the raw ``stored_path`` so the agent can ``Read`` text it can
    reason about, falling back to the original on parser miss/failure
    (raw PDFs / binaries).
    """
    return tuple(
        (row.parsed_path if row.parse_status == "ready" and row.parsed_path else row.stored_path)
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
