"""Which working directory a delivery belongs to.

Artifact identity and artifact storage are both scoped to the cwd the session
actually runs in — the project's own, or a worktree's. Resolving that is the
first thing a delivery does, and it either succeeds for the whole batch or
fails it: every later step (the scope-relative key, the ``.artifact`` directory,
the boundary check) is stated in terms of the answer.

Failure is deliberately loud. A worktree session whose worktree has been removed
must NOT quietly fall back to the project cwd: the same relative path would then
resolve against two different roots, so one artifact's history would end up
split across two directories.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from valuz_agent.modules.artifacts.datastore import Scope
from valuz_agent.modules.artifacts.models import SHARED_CWD

logger = logging.getLogger(__name__)


class ScopeUnavailableError(Exception):
    """No usable delivery scope. Carries text the model is expected to read."""


@dataclass(frozen=True)
class DeliveryScope:
    """Where a delivery lands: its identity scope and the directory itself."""

    scope: Scope
    cwd: Path


def _valuz_meta(session: object) -> dict:  # type: ignore[type-arg]
    meta = getattr(session, "metadata", None) or {}
    if not isinstance(meta, dict):
        return {}
    valuz = meta.get("valuz") or {}
    return valuz if isinstance(valuz, dict) else {}


async def resolve_delivery_scope(user_id: str, session_id: str) -> DeliveryScope:
    """Resolve ``session_id``'s delivery scope, or raise ``ScopeUnavailable``.

    Every session belongs to a project — quick chats get their own
    ``kind="chat"`` project with its own cwd — so a missing project id means
    something is wrong upstream, not that this is an ordinary project-less
    session. It is reported rather than papered over.
    """
    from valuz_agent.adapters.data_reader import data_reader
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    session = await data_reader().get_session(user_id, session_id)
    if session is None:
        raise ScopeUnavailableError("session not found — cannot record deliverables")

    valuz = _valuz_meta(session)
    project_id = str(valuz.get("project_id") or "")
    if not project_id:
        logger.warning("deliver: session %s carries no project id", session_id)
        raise ScopeUnavailableError("this session has no project — cannot record deliverables")

    async with async_unit_of_work(commit=False) as db:
        project_row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    if project_row is None:
        raise ScopeUnavailableError("this session's project no longer exists")

    snapshot = valuz.get("worktree")
    worktree_name = str(snapshot.get("name") or "") if isinstance(snapshot, dict) else ""

    if worktree_name:
        from valuz_agent.modules.worktrees.service import worktree_service

        # Resolved live rather than read from the snapshot: the snapshot records
        # the worktree ROOT at creation time, while a session runs in the
        # project's subdirectory inside it, and the worktree may since have been
        # removed. ``None`` means gone — a hard failure, see the module
        # docstring.
        resolved = await worktree_service.resolve_session_cwd(user_id, project_row, worktree_name)
        if not resolved:
            raise ScopeUnavailableError(
                f"the worktree '{worktree_name}' this session runs in no longer exists"
            )
        return DeliveryScope(
            scope=Scope(user_id=user_id, project_id=project_id, worktree=worktree_name),
            cwd=Path(resolved),
        )

    from valuz_agent.modules.projects.service import project_cwd_by_id

    cwd = await project_cwd_by_id(user_id, project_id)
    if not cwd:
        raise ScopeUnavailableError("cannot resolve this session's working directory")
    return DeliveryScope(
        scope=Scope(user_id=user_id, project_id=project_id, worktree=SHARED_CWD),
        cwd=Path(cwd),
    )
