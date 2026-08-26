"""The per-turn ``<additional-context>`` section listing delivered artifacts.

Why the model is told rather than given a tool
----------------------------------------------
A resident ``list_artifacts`` tool would sit in the ``base`` toolset, which every
session loads, and the stability of that list is a prompt-cache invariant. This
block rides ``UserMessage.additional_context`` instead: rebuilt per turn, part of
the user message rather than the cached prefix, and free of any tool-surface
cost. The full history needs no tool either — snapshots live in the working
directory, so ``ls .artifact/`` reaches whatever the cap left out.

What it must not do
-------------------
Telling the model these exist biases it toward revising them; that is the point
(a revision should continue a deliverable, not start a new one) and also the
risk (the user may have asked for something new). The closing instructions carry
that distinction, and they are the reason this is prose rather than a bare list.

Reading it is not enough to make delivery correct: a model can still ignore all
of it and deliver under a fresh name. Content-hash idempotency and the head
compare-and-set are what actually hold; this only reduces how often they have to.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import REVISION_STATUS_READY

logger = logging.getLogger(__name__)

# Mirrors the bound the knowledge-base scope section uses. The list is per-turn
# and grows with the project, so it needs a ceiling; ten is enough to cover
# "what am I working on" without the block crowding out the user's own message.
MAX_LISTED = 10


def _stamp(epoch_ms: int, tz_name: str | None) -> str:
    try:
        moment = datetime.fromtimestamp(epoch_ms / 1000, ZoneInfo(tz_name or "UTC"))
    except Exception:  # noqa: BLE001 — a bad tz must not cost the whole section
        moment = datetime.fromtimestamp(epoch_ms / 1000, ZoneInfo("UTC"))
    return f"{moment:%Y-%m-%d %H:%M}"


async def build_artifacts_section(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    worktree: str = "",
    tz_name: str | None = None,
) -> str:
    """Render the scope's deliverables, or ``""`` when it has none.

    Only the current version of each is listed. Emitting whole histories would
    grow the block by every revision ever made, and the past versions are one
    ``ls`` away in the same directory the paths already point into.
    """
    if not project_id:
        return ""

    scope = Scope(user_id=user_id, project_id=project_id, worktree=worktree)
    ds = ArtifactDatastore(db)
    rows = await ds.list_scope_heads(scope, limit=MAX_LISTED)
    if not rows:
        return ""
    # A short list is its own count. Only a full page can have more behind it,
    # and this runs on every turn of every session — the common project has
    # fewer deliverables than the cap, so asking would be a query whose answer
    # is already in hand.
    total = len(rows) if len(rows) < MAX_LISTED else await ds.count_scope_artifacts(scope)

    lines = [f"Delivered artifacts in this workspace ({total} total, most recently updated first):"]
    for artifact, head, revision in rows:
        # The id is here because ``deliver_artifacts`` takes it: renaming or
        # moving a deliverable is the one case key matching cannot recognise,
        # and the caller has to name what it is continuing. Without this line
        # that is only possible in a conversation that already delivered the
        # thing once — a rename in a later session could not be expressed at all.
        lines.append(
            f"- {artifact.display_name} — {artifact.kind}, v{head.version_no}, "
            f"id {artifact.id}, updated {_stamp(head.updated_at, tz_name)}"
        )
        if revision.status == REVISION_STATUS_READY and revision.abs_path:
            # Absolute, because that is the file's identity everywhere else: what
            # the model reads inside the sandbox (which mounts the same path),
            # what a valuz-file:// link carries, and what the resolve endpoint
            # exchanges for a local path or a signed URL. A relative path would
            # make the model work out what it is relative to — and a worktree
            # session and the main line have different answers.
            lines.append(f"  current: {revision.abs_path}")
        else:
            # Recorded, but its bytes are gone (a removed worktree, or a legacy
            # row whose file was already missing at migration time). Named
            # without a path so the model does not try to read it.
            lines.append("  current: (unavailable)")

    if total > len(rows):
        lines.append(f"- … and {total - len(rows)} more — run `ls .artifact/` for the full set.")

    lines.append(
        'To revise one: read its "current" file, write the updated version in your '
        "working directory under the SAME file name, then call deliver_artifacts — "
        "it records the next version automatically. If you rename or move one, pass "
        "its id as 'artifactId' in the same call, or it will be recorded as a second "
        "deliverable instead of the next version of this one. Start a new deliverable "
        "only when the user asked for one. Never write into .artifact/ yourself. When "
        "you mention a deliverable in your reply, link it as "
        "[name](valuz-file://<its absolute path>)."
    )
    return "\n".join(lines)
