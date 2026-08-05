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
from pathlib import Path

from app.schemas import UpdateSessionRequest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.schemas
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.db import async_unit_of_work

logger = logging.getLogger(__name__)


async def refresh_citation_policy_for_session(
    session_id: str,
    user_id: str,
    *,
    citation_enabled_override: bool | None = None,
    verification_enabled_override: bool | None = None,
    task_coverage_enabled_override: bool | None = None,
) -> bool:
    """Apply citation, verification and task-coverage preferences to a session.

    Existing conversations converge lazily before every turn. Internal
    document-summary runs may override citation/verification values so summaries
    keep inspectable citation indices without paying for claim-quality verification.
    """

    from valuz_agent.adapters.capability_resolver import citation_skill_dir
    from valuz_agent.adapters.system_prompt_builder import (
        CITATION_POLICY_REVISION,
        ensure_citation_system_policy,
        remove_citation_system_policy,
    )
    from valuz_agent.modules.settings.preferences import (
        get_conversation_citations_enabled,
        get_conversation_task_coverage_enabled,
        get_conversation_verification_enabled,
    )

    session = await kernel_client.get_session(user_id, session_id)
    if session is None or session.status in ("terminated",):
        return False

    skill_dir = citation_skill_dir(session.user_id)
    skill_path = str(skill_dir.resolve(strict=False))
    current_skills = list(session.skills or ())
    if (
        citation_enabled_override is None
        or verification_enabled_override is None
        or task_coverage_enabled_override is None
    ):
        async with async_unit_of_work(commit=False) as db:
            citation_enabled = (
                await get_conversation_citations_enabled(db, user_id=user_id)
                if citation_enabled_override is None
                else citation_enabled_override
            )
            verification_enabled = (
                await get_conversation_verification_enabled(db, user_id=user_id)
                if verification_enabled_override is None
                else verification_enabled_override
            )
            task_coverage_enabled = (
                await get_conversation_task_coverage_enabled(db, user_id=user_id)
                if task_coverage_enabled_override is None
                else task_coverage_enabled_override
            )
    else:
        citation_enabled = citation_enabled_override
        verification_enabled = verification_enabled_override
        task_coverage_enabled = task_coverage_enabled_override
    verification_enabled = bool(verification_enabled)
    evidence_binding_enabled = bool(citation_enabled or verification_enabled)

    if evidence_binding_enabled:
        new_skills = (
            current_skills if skill_path in current_skills else [*current_skills, skill_path]
        )
        new_instructions = ensure_citation_system_policy(session.instructions or "")
    else:
        new_skills = [path for path in current_skills if Path(path).name != "citation"]
        new_instructions = remove_citation_system_policy(session.instructions or "")
    metadata = dict(session.metadata or {})
    valuz = dict(metadata.get("valuz") or {})
    old_revision = valuz.get("citation_policy_revision")
    old_citation_enabled = valuz.get("citation_enabled")
    old_verification_enabled = valuz.get("citation_verification_enabled")
    old_task_coverage_enabled = valuz.get("task_coverage_enabled")
    if evidence_binding_enabled:
        valuz["citation_policy_revision"] = CITATION_POLICY_REVISION
    else:
        valuz.pop("citation_policy_revision", None)
    valuz["citation_enabled"] = bool(citation_enabled)
    valuz["citation_verification_enabled"] = verification_enabled
    valuz["task_coverage_enabled"] = bool(task_coverage_enabled)
    old_quality_policy = valuz.get("citation_quality_policy")
    old_task_coverage_policy = valuz.get("task_coverage_policy")
    from valuz_agent.ports.extensions import ext

    # Resolve the layered pack once, but expose separate snapshots to the two
    # independent post-run consumers. Enabling Task Coverage must not enable
    # Citation Audit or install its Evidence binding protocol.
    policy_metadata = {**metadata, "valuz": valuz}
    quality_snapshot = None
    if verification_enabled or task_coverage_enabled:
        try:
            quality_snapshot = await ext.citation_quality_policies.resolve(
                user_id,
                session_metadata=policy_metadata,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "citation quality policy resolution failed for session %s",
                session_id,
            )

    if verification_enabled:
        if quality_snapshot is None:
            if isinstance(old_quality_policy, dict):
                quality_policy = {
                    **old_quality_policy,
                    "config": {"unavailable": True},
                }
            else:
                quality_policy = None
        else:
            quality_policy = quality_snapshot.session_metadata()
    else:
        quality_policy = None

    task_coverage_policy = None
    if task_coverage_enabled and quality_snapshot is not None:
        candidate = quality_snapshot.config.get("task_coverage")
        if isinstance(candidate, dict):
            task_coverage_policy = dict(candidate)
            if quality_snapshot.layers:
                task_coverage_policy["layers"] = [dict(item) for item in quality_snapshot.layers]
    if quality_policy is None:
        valuz.pop("citation_quality_policy", None)
    else:
        valuz["citation_quality_policy"] = quality_policy
    if task_coverage_policy is None:
        valuz.pop("task_coverage_policy", None)
    else:
        valuz["task_coverage_policy"] = task_coverage_policy
    metadata["valuz"] = valuz

    if (
        new_skills == current_skills
        and new_instructions == (session.instructions or "")
        and old_revision == (CITATION_POLICY_REVISION if evidence_binding_enabled else None)
        and old_quality_policy == quality_policy
        and old_task_coverage_policy == task_coverage_policy
        and old_citation_enabled == bool(citation_enabled)
        and old_verification_enabled == verification_enabled
        and old_task_coverage_enabled == bool(task_coverage_enabled)
    ):
        return False

    if evidence_binding_enabled and not skill_dir.is_dir():
        # Keep the machine policy even when a damaged/partial installation is
        # missing the skill.  The kernel guard sees the missing skill and marks
        # source-dependent answers degraded (fail closed).
        logger.error("citation built-in skill is missing: %s", skill_dir)
        new_skills = current_skills

    await kernel_client.update_session(
        user_id,
        session_id,
        UpdateSessionRequest(
            skills=list(new_skills),
            instructions=new_instructions,
            metadata=metadata,
        ),
    )
    logger.info(
        "Refreshed citation policy on session %s "
        "(enabled=%s verification=%s task_coverage=%s skill=%s revision=%s quality=%s coverage=%s)",
        session_id,
        citation_enabled,
        verification_enabled,
        task_coverage_enabled,
        evidence_binding_enabled and skill_path not in current_skills and skill_dir.is_dir(),
        CITATION_POLICY_REVISION if evidence_binding_enabled else "disabled",
        quality_policy.get("revision") if quality_policy else "none",
        task_coverage_policy.get("revision") if task_coverage_policy else "none",
    )
    return True


async def refresh_docs_capabilities_for_session(session_id: str, user_id: str) -> bool:
    """Ensure the valuz-project-docs skill + ``valuz_docs`` MCP are
    present on an existing session row.

    Why this exists
    ---------------
    ADR-006 freezes ``session.model`` at create-time but skills + MCP are
    *mutable* (kernel exposes ``PATCH {KERNEL_API_PREFIX}/v1/sessions/{id}``
    for both — ADR-013; default ``/kernel`` for this host).
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

    from valuz_agent.adapters.capability_resolver import project_docs_skill_dir
    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.integrations.docs_mcp_server import docs_mcp_url
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    session = await kernel_client.get_session(user_id, session_id)
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
            return await ProjectDatastore(db).get_by_id(session.user_id, project_id)

    project = await _load_project()
    if project is None:
        return False
    _docs_skill_dir = project_docs_skill_dir(session.user_id)
    if not _docs_skill_dir.is_dir():
        return False

    docs_skill_path = str(_docs_skill_dir.resolve(strict=False))
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
        user_id,
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


async def _refresh_external_connector_entries(user_id: str, entries: list) -> list:
    """Re-resolve user-attached connector entries with CURRENT credentials.

    Each entry's ``name`` is the connector slug (``mcp_resolver`` names configs
    that way), so resolving the same slugs again yields configs with fresh
    headers. Entries that no longer resolve (connector deleted / disabled /
    credentials gone) keep their existing snapshot — same failure surface as
    before this refresh existed. Best-effort: any resolver error keeps the
    originals so the turn proceeds on the old token.
    """
    slugs = [n for n in (getattr(m, "name", None) for m in entries) if n]
    if not slugs:
        return entries
    try:
        from valuz_agent.adapters.mcp_resolver import resolve_mcp_servers
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.connectors.datastore import ConnectorDatastore

        async with async_unit_of_work() as db:
            fresh = await resolve_mcp_servers(
                enabled_slugs=slugs,
                connectors=ConnectorDatastore(db),
                user_id=user_id,
            )
    except Exception:  # noqa: BLE001
        logger.exception("re-stamp: external connector re-resolve failed")
        return entries
    by_name: dict = {}
    for cfg in fresh:
        by_name.setdefault(cfg.name, cfg)
    return [by_name.get(getattr(m, "name", None), m) for m in entries]


async def refresh_always_on_mcp_for_session(session_id: str, user_id: str) -> bool:
    """Re-stamp the always-on in-process MCP servers (docs / automations /
    connectors) on an existing session row with the CURRENT process values.

    Why this exists
    ---------------
    The always-on ``McpHttpServerConfig`` headers carry ``X-Valuz-Internal``
    (``settings.internal_mcp_token``) + ``backend_base_url`` + ``session_id``,
    baked at session create-time (``capability_resolver.always_on_http_mcp_servers``).
    Historically ``internal_mcp_token`` was a per-process RANDOM secret, so a
    session created before a backend restart carried a *stale* token; on resume
    the in-process MCP gate 403'd every request and Claude Code parked the server
    in ``needsAuth`` — hiding the real tools (``automation`` / ``doc_search`` /
    ``create_mcp``, and for a lead the whole orchestration set) and exposing only
    synthetic OAuth stubs. The token is now DERIVED from the stable local owner
    id (``config.internal_mcp_token``) so it no longer rotates across restarts —
    but the ``backend_base_url`` / ``session_id`` can still drift (port change,
    legacy rows), and an ``internal_mcp_token_override`` change still needs
    convergence, so this re-stamp stays as cheap, idempotent self-healing.

    Re-stamping the always-on trio on every turn rewrites the persisted headers
    with the live values, preserving any user-attached external MCP entries
    untouched.

    Returns ``True`` when the session row actually changed (i.e. something was
    stale), ``False`` when the always-on set already matched (the common case,
    so the prompt cache stays warm).
    """
    from valuz_agent.adapters.capability_resolver import (
        always_on_http_mcp_servers,
        harness_toolkit_for_run_kind,
    )

    session = await kernel_client.get_session(user_id, session_id)
    if session is None or session.status in ("terminated",):
        return False

    run_kind = ((session.metadata or {}).get("valuz", {}) or {}).get("run_kind")
    research_context = ((session.metadata or {}).get("valuz", {}) or {}).get("document_research")
    locked_document_research = (
        isinstance(research_context, dict)
        and research_context.get("purpose") == "document-research"
        and research_context.get("source_scope") == "locked"
    )
    fresh = await always_on_http_mcp_servers(
        session_id, owner_user_id=user_id, toolkit=harness_toolkit_for_run_kind(run_kind)
    )
    if locked_document_research:
        # The document-research contract is server-enforced: this child
        # session can only call the owner-scoped docs MCP. Do not reintroduce
        # connectors, automations, harness tools or external MCPs while
        # restamping credentials.
        fresh = [item for item in fresh if item.name == "valuz_docs"]
    fresh_names = {m.name for m in fresh}
    current = list(session.mcp_servers or ())
    # Drop any existing always-on entry (stale token/url), keep everything
    # else (external catalog connectors the user attached), then re-append the
    # freshly-stamped trio. Order mirrors capability_resolver (external first,
    # always-on last) so an unchanged token yields an identical tuple → no save.
    preserved = (
        []
        if locked_document_research
        else [m for m in current if getattr(m, "name", None) not in fresh_names]
    )
    # External catalog connectors carry credentials baked at resolve time —
    # an OAuth bearer header with ~1h expiry for Reportify-backed connectors.
    # Re-resolve them here too, or an EXISTING conversation keeps the stale
    # token forever: a re-auth (or the resolver's own expiry refresh) would
    # otherwise only reach brand-new sessions, 401-ing every call in old ones
    # while the connectors page truthfully shows "connected".
    preserved = await _refresh_external_connector_entries(user_id, preserved)
    new_mcp = (*preserved, *fresh)

    if new_mcp == tuple(current):
        return False

    await kernel_client.update_session(
        user_id, session_id, UpdateSessionRequest(mcp_servers=list(new_mcp))
    )
    logger.info("Re-stamped always-on MCP token on session %s", session_id)
    return True


async def refresh_docs_capabilities_for_project(project_id: str, user_id: str) -> int:
    """Refresh docs capabilities for every active session in ``project_id``.

    Used as the ``project.bindings.changed`` event handler so binding a
    document on a project propagates to all open sessions immediately
    (not just to whatever new session the user creates afterwards).

    Returns the number of sessions whose row actually changed.
    """
    from valuz_agent.modules.sessions import project_index

    try:
        ids = await project_index.list_session_ids(project_id, limit=500, user_id=user_id)
        sessions = await kernel_client.list_sessions(user_id, ids=ids, limit=500)
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
            if await refresh_docs_capabilities_for_session(s.id, user_id):
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
