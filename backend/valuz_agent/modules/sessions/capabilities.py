"""Docs-capability maintenance for existing session rows.

ADR-006 freezes ``session.model`` at create time, but skills + MCP stay
mutable. These helpers (re)install the ``valuz-project-docs`` skill +
``valuz_docs`` MCP on a session — or across every active session in a
project when a KB binding changes. Deliberately **sync**: invoked from sync
service code (``send_message``) and from the synchronous in-process eventbus
(``project.bindings.changed``); the async host store is driven via the
former ``kernel_sync`` thread bridge (now fully async).
"""

from __future__ import annotations

import logging

from app.schemas import UpdateSessionRequest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.schemas
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.db import async_unit_of_work

logger = logging.getLogger(__name__)


async def refresh_docs_capabilities_for_session(session_id: str) -> bool:
    """Ensure the valuz-project-docs skill + ``valuz_docs`` MCP are
    present on an existing session row.

    Why this exists
    ---------------
    ADR-006 freezes ``session.model`` at create-time but skills + MCP are
    *mutable* (kernel exposes ``PATCH /api/v1/sessions/{id}`` for both).
    The docs skill + MCP are auto-injected at creation for every
    session (chat + project) unconditionally — but pre-upgrade sessions,
    or sessions whose skills were edited externally, may be missing the
    pair. This helper restores it without touching any other entry the
    user attached to the session.

    Note: stripping is no longer performed. The docs skill + MCP are
    part of the stable capability layer regardless of KB bindings;
    whether the project has docs to search is announced per-turn via
    ``UserMessage.additional_context``. This keeps Anthropic prompt
    cache hits high across binding changes.

    Returns ``True`` when the session row was changed. Returns
    ``False`` when no change was needed (already present) or the
    session can't be loaded / isn't a project session.

    Safe to call repeatedly — idempotent on the docs pair.
    """
    from app.schemas import (
        McpHttpServerConfigSchema as _McpHttpServerConfig,
    )

    from valuz_agent.adapters.capability_resolver import _PROJECT_DOCS_SKILL_DIR
    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.integrations.docs_mcp_server import docs_mcp_url
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    session = await kernel_client.get_session(session_id)
    if session is None:
        return False
    # Sessions that have already finished don't run new turns; capability
    # changes have no effect, skip.
    if session.status in ("terminated",):
        return False

    project_id = str(((session.metadata or {}).get("valuz", {}) or {}).get("project_id") or "")
    if not project_id:
        return False

    # Every session (chat + project) carries the docs capability —
    # see ``capability_resolver`` (2.5). The MCP server's tools return
    # empty results when the project has no KB bindings, so chat
    # sessions trivially short-circuit at the tool layer.
    #
    async def _load_project():  # type: ignore[no-untyped-def]
        async with async_unit_of_work(commit=False) as db:
            return await ProjectDatastore(db).get_by_id(project_id)

    project = await _load_project()
    if project is None:
        return False
    if not _PROJECT_DOCS_SKILL_DIR.is_dir():
        return False

    docs_skill_path = str(_PROJECT_DOCS_SKILL_DIR.resolve(strict=False))
    current_skills = list(session.skills or ())
    current_mcp = list(session.mcp_servers or ())

    has_docs_skill = docs_skill_path in current_skills
    has_docs_mcp = any(getattr(m, "name", None) == "valuz_docs" for m in current_mcp)

    if has_docs_skill and has_docs_mcp:
        return False

    new_skills = current_skills if has_docs_skill else [*current_skills, docs_skill_path]
    new_mcp = list(current_mcp)
    if not has_docs_mcp:
        new_mcp.append(
            _McpHttpServerConfig(
                name="valuz_docs",
                url=docs_mcp_url(base_url=_settings.backend_base_url),
                transport="http",
                headers={
                    "X-Valuz-Internal": _settings.internal_mcp_token,
                    "X-Valuz-Session-Id": session_id,
                },
            )
        )
    await kernel_client.update_session(
        session_id,
        UpdateSessionRequest(skills=list(new_skills), mcp_servers=list(new_mcp)),
    )
    logger.info(
        "Refreshed docs capabilities on session %s (skill=%s mcp=%s)",
        session_id,
        not has_docs_skill,
        not has_docs_mcp,
    )
    return True


async def refresh_always_on_mcp_for_session(session_id: str) -> bool:
    """Re-stamp the always-on in-process MCP servers (docs / automations /
    connectors) on an existing session row with the CURRENT process values.

    Why this exists
    ---------------
    ``settings.internal_mcp_token`` is generated **per process** and is baked
    into every always-on ``McpHttpServerConfig`` header at session create-time
    (``capability_resolver.always_on_http_mcp_servers``). It is not stable
    across restarts. A session created before a backend restart therefore
    carries a *stale* ``X-Valuz-Internal``; when the turn resumes, the
    in-process MCP gate 403s every request and Claude Code parks the server in
    ``needsAuth`` — hiding the real tools (``automation`` / ``doc_search`` /
    ``create_mcp``) and exposing only its synthetic OAuth ``authenticate`` /
    ``complete_authentication`` stubs. The agent then "needs OAuth" for a
    server that never used OAuth.

    Re-stamping the always-on trio on every turn keeps the token in-memory
    while letting this derived state self-heal: the persisted headers are
    rewritten with the live token + ``backend_base_url`` + ``session_id``,
    preserving any user-attached external MCP entries untouched.

    Returns ``True`` when the session row actually changed (i.e. something was
    stale), ``False`` when the always-on set already matched (the common case,
    so the prompt cache stays warm).
    """
    from valuz_agent.adapters.capability_resolver import (
        always_on_http_mcp_servers,
        harness_toolkit_for_run_kind,
    )

    session = await kernel_client.get_session(session_id)
    if session is None or session.status in ("terminated",):
        return False

    run_kind = ((session.metadata or {}).get("valuz", {}) or {}).get("run_kind")
    fresh = always_on_http_mcp_servers(
        session_id, toolkit=harness_toolkit_for_run_kind(run_kind)
    )
    fresh_names = {m.name for m in fresh}
    current = list(session.mcp_servers or ())
    # Drop any existing always-on entry (stale token/url), keep everything
    # else (external catalog connectors the user attached), then re-append the
    # freshly-stamped trio. Order mirrors capability_resolver (external first,
    # always-on last) so an unchanged token yields an identical tuple → no save.
    preserved = [m for m in current if getattr(m, "name", None) not in fresh_names]
    new_mcp = (*preserved, *fresh)

    if new_mcp == tuple(current):
        return False

    await kernel_client.update_session(
        session_id, UpdateSessionRequest(mcp_servers=list(new_mcp))
    )
    logger.info("Re-stamped always-on MCP token on session %s", session_id)
    return True


async def refresh_docs_capabilities_for_project(project_id: str) -> int:
    """Refresh docs capabilities for every active session in ``project_id``.

    Used as the ``project.bindings.changed`` event handler so binding a
    document on a project propagates to all open sessions immediately
    (not just to whatever new session the user creates afterwards).

    Returns the number of sessions whose row actually changed.
    """
    from valuz_agent.modules.sessions import project_index

    try:
        ids = await project_index.list_session_ids(project_id, limit=500)
        sessions = await kernel_client.list_sessions(ids=ids, limit=500)
    except Exception:  # noqa: BLE001 — never raise into eventbus handlers
        logger.exception(
            "refresh_docs_capabilities_for_project: failed to list sessions for %s",
            project_id,
        )
        return 0
    changed = 0
    for s in sessions:
        # Skip terminated sessions — they won't run again, no point.
        if s.status == "terminated":
            continue
        try:
            if await refresh_docs_capabilities_for_session(s.id):
                changed += 1
        except Exception:  # noqa: BLE001 — one bad session can't sink the batch
            logger.exception(
                "refresh_docs_capabilities_for_session: failed on session %s",
                s.id,
            )
    if changed:
        logger.info(
            "project.bindings.changed: refreshed docs caps on %d session(s) for project %s",
            changed,
            project_id,
        )
    return changed
