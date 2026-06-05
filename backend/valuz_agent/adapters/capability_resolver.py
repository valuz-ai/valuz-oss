"""Translate valuz business state into V5 kernel session creation parameters.

When the host receives ``POST /v1/sessions``, it does not call the kernel
directly with raw user input — the kernel's ``CreateSessionRequest`` wants
absolute paths and ``McpServerConfig`` objects that valuz must produce
from its own catalog tables (skills, MCP providers, providers). This module
owns that translation.

Outputs are pure data — the resolver does no writes. The session router
takes the result and hands it to the kernel via ``StorePort.save_session``.

Currently covered:
- ``skills``: workspace-enabled skill paths plus session-attached extras,
  resolved to filesystem absolute paths via the skill index.
- ``mcp_servers``: enabled MCP-provider slugs are expanded into kernel
  ``McpServerConfig`` rows by ``adapters.mcp_resolver``. The resolver
  swallows missing-credential cases silently so a session can still be
  created with whatever's connected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from src.core.types import (  # type: ignore[import-not-found]
    McpHttpServerConfig,
    McpServerConfig,
)

# Side-effect import — surfaces ``src.core...`` on sys.path.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.mcp_resolver import resolve_mcp_servers
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import WorkspaceDatastore
from valuz_agent.modules.skills.contracts import (
    RuntimeContext,
    SkillManifest,
    WorkspaceRef,
)
from valuz_agent.modules.skills.datastore import SkillDatastore


class _SkillSource(Protocol):
    """Structural type matching every skill source's ``list_skills`` API.

    Used to type the optional ``extra_skill_sources`` (e.g. ``OfficialSkillSource``)
    without importing each implementation here.
    """

    def list_skills(self, ctx: RuntimeContext) -> list[SkillManifest]: ...


logger = logging.getLogger(__name__)

# Path to the bundled builtin skill that teaches the agent how to search
# the project's knowledge base. Auto-injected into ``session.skills`` when
# the workspace has at least one ``valuz_project_kb_binding`` row.
_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parents[1] / "resources" / "builtin_skills"
_PROJECT_DOCS_SKILL_DIR = _BUILTIN_SKILLS_DIR / "valuz-project-docs"


@dataclass(frozen=True)
class ResolvedCapabilities:
    """Inputs the kernel needs to create a session for a valuz workspace."""

    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    skill_resolution_warnings: tuple[str, ...] = field(default_factory=tuple)


async def resolve_session_capabilities(
    *,
    workspaces: WorkspaceDatastore,
    skills: SkillDatastore,
    workspace_id: str,
    extra_skill_ids: list[str] | None = None,
    skill_source: FilesystemSkillSource | None = None,
    extra_skill_sources: list[_SkillSource] | None = None,
    official_entitled: bool = False,
    secrets: FileSecretStore | None = None,
    enabled_mcp_provider_slugs: list[str] | None = None,
    connectors: ConnectorDatastore | None = None,
    docs: DocumentDatastore | None = None,
    session_id: str | None = None,
) -> ResolvedCapabilities:
    """Compute kernel-shaped capabilities for a session in ``workspace_id``.

    The MCP arguments are optional so the resolver stays usable in code paths
    that don't (yet) expose data-source selection. When all three are
    supplied alongside ``enabled_mcp_provider_slugs`` the resolver materialises
    the corresponding ``McpServerConfig`` list.
    """

    workspace = await workspaces.get_by_id(workspace_id)
    if workspace is None:
        raise KeyError(workspace_id)

    skill_paths: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()

    # 1) Workspace-enabled skills — read from the filesystem-based
    #    ``project-config.json`` which is the canonical source of truth
    #    for which skills are enabled for a workspace.  The DB-backed
    #    ``ProjectSkillConfigRow`` table is not currently populated by the
    #    UI's ``set_skill_enabled`` flow; it writes to JSON instead.
    enabled_paths = skills.enabled_skill_paths(workspace)
    for path in enabled_paths:
        absolute = _resolve_to_absolute(path, workspace.root_path)
        if absolute is None:
            warnings.append(f"workspace-enabled skill path is not resolvable: {path!r}")
            continue
        if absolute in seen:
            continue
        if not Path(absolute).is_dir():
            fallback = _try_find_skill_by_slug(absolute)
            if fallback:
                absolute = fallback
            else:
                warnings.append(f"workspace-enabled skill path does not exist: {absolute!r}")
                continue
        seen.add(absolute)
        skill_paths.append(absolute)

    # 1b) For non-project (chat) workspaces, every user-library skill is
    #     implicitly enabled. The skills panel UI advertises them as enabled
    #     for chat (datastore.list_workspace_skills sets ``enabled=True`` for
    #     workspace.kind == "chat") and there is no per-workspace toggle to
    #     opt out, so the resolver must mirror that for the runtime.
    if workspace.kind != "project" and (skill_source is not None or extra_skill_sources):
        ctx = RuntimeContext(
            workspace=WorkspaceRef(
                id=workspace.id,
                slug=workspace.id,
                kind=workspace.kind,
                root_path=workspace.root_path,
            ),
        )
        if skill_source is not None:
            for manifest in skill_source.list_skills(ctx):
                if manifest.scope != "user":
                    continue
                absolute = _resolve_to_absolute(manifest.path, workspace.root_path)
                if absolute is None or absolute in seen:
                    continue
                if not Path(absolute).is_dir():
                    continue
                seen.add(absolute)
                skill_paths.append(absolute)

        # 1c) Official skills — gated by entitlement, mirroring
        #     SkillLibraryService.list_catalog. Bundled built-ins
        #     (``origin_label == "Built-in"``) are always free; externally
        #     installed official skills require the ``skills:official``
        #     entitlement (passed in as ``official_entitled=True``). Locked
        #     manifests are surfaced in the UI for marketing but never
        #     materialized into the runtime cwd.
        for source in extra_skill_sources or []:
            for manifest in source.list_skills(ctx):
                if manifest.scope != "official":
                    continue
                is_bundled = manifest.origin_label == "Built-in"
                if not is_bundled and not official_entitled:
                    continue
                absolute = _resolve_to_absolute(manifest.path, workspace.root_path)
                if absolute is None or absolute in seen:
                    continue
                if not Path(absolute).is_dir():
                    continue
                seen.add(absolute)
                skill_paths.append(absolute)

    # 2) Session-level extras — opaque skill IDs attached just for this session
    #    on top of whatever the workspace already enables. Look each one up in
    #    the skill index to recover its source_path.
    for skill_id in extra_skill_ids or []:
        row = await skills.get_by_id(skill_id)
        if row is None:
            warnings.append(f"extra skill id not found: {skill_id!r}")
            continue
        absolute = _resolve_to_absolute(row.source_path, workspace.root_path)
        if absolute is None:
            warnings.append(f"extra skill {skill_id!r} has unresolvable source path")
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        skill_paths.append(absolute)

    # 2.5) Builtin valuz-project-docs skill + matching in-process MCP
    #      server — auto-injected together for EVERY session (chat +
    #      project) so the tool list stays stable across the entire
    #      product surface. The skill teaches the agent to use
    #      ``doc_search`` / ``list_doc_scope``; the MCP server (mounted
    #      at ``/internal/mcp/docs/{session_id}/mcp``) implements those
    #      tools scoped to the session's workspace.
    #
    #      Why unconditional: the skill + MCP form a stable,
    #      prompt-cache-friendly capability layer that mirrors the
    #      ``valuz_automations`` automation pattern. Whether the workspace
    #      actually has KB bindings (or per-turn attachments) is
    #      announced inside ``UserMessage.additional_context`` — that's
    #      the channel for dynamic state. Putting that state in the
    #      skill set or system prompt would invalidate Anthropic's
    #      prompt cache on every binding / attachment change; making
    #      docs MCP conditional on workspace.kind == "project" had the
    #      same effect across the chat / project boundary.
    #
    #      The pair MUST travel together: a skill without the MCP
    #      server would teach the agent about non-existent tools; an
    #      MCP server without the skill would leave the agent unaware
    #      that doc search is available. For chat sessions the MCP
    #      tools return empty results (no KB bindings → empty scope)
    #      which is a normal answer the agent already handles.
    for absolute in always_on_skill_paths():
        if absolute not in seen:
            seen.add(absolute)
            skill_paths.append(absolute)
    logger.info(
        "Auto-injecting always-on baseline skills for workspace %s (kind=%s)",
        workspace_id,
        workspace.kind,
    )

    # 3) MCP servers — only when the caller wires the catalog in. Anything
    #    missing (no credentials, unknown slug, disabled provider) is logged
    #    inside ``mcp_resolver`` and silently skipped here.
    mcp_configs_list: list[McpServerConfig] = []
    if secrets is not None:
        mcp_configs_list.extend(
            await resolve_mcp_servers(
                secrets=secrets,
                enabled_slugs=enabled_mcp_provider_slugs or [],
                connectors=connectors,
            )
        )

    # 3.5–3.7) In-process always-on HTTP MCP servers (docs / schedules /
    #      connectors). Factored into ``always_on_http_mcp_servers`` so the
    #      task-dispatch path (``agent_resolver.build_member_session``) can
    #      inject the same set — task lead/member sessions don't flow through
    #      this resolver but must still carry these built-in tools.
    if session_id:
        mcp_configs_list.extend(always_on_http_mcp_servers(session_id))
    else:
        logger.warning(
            "session_id not provided — skipping always-on HTTP MCP injection "
            "(docs/schedules/connectors tools will be unavailable in this session)"
        )

    logger.info(
        "Resolved capabilities for workspace %s: %d skills, %d MCP servers, %d warnings",
        workspace_id,
        len(skill_paths),
        len(mcp_configs_list),
        len(warnings),
    )
    if warnings:
        logger.warning("Skill resolution warnings: %s", warnings)

    return ResolvedCapabilities(
        skills=tuple(skill_paths),
        mcp_servers=tuple(mcp_configs_list),
        skill_resolution_warnings=tuple(warnings),
    )


def always_on_skill_paths() -> list[str]:
    """Bundled skills every session carries: ``valuz-project-docs`` + ``skill-creator``.

    These are the skill half of the always-on baseline (the MCP half lives in
    ``always_on_http_mcp_servers``). ``valuz-project-docs`` teaches the
    ``doc_search`` / ``list_doc_scope`` tools that pair with the ``valuz_docs``
    MCP; ``skill-creator`` (+ its ``submit_skill`` in-process tool) lets any
    session author skills. Returned as absolute dirs the kernel materialises
    into the session cwd. Both are injected by every session-build path
    (``resolve_session_capabilities`` for chat/project, ``build_member_session``
    for task lead/member) so the baseline is identical everywhere. A missing
    dir is skipped + logged so a partial install can't break session creation.
    """
    from valuz_agent.infra.fs_registry import fs_registry

    candidates = [
        _PROJECT_DOCS_SKILL_DIR,
        fs_registry.official_skill_root() / "skill-creator",
    ]
    paths: list[str] = []
    for d in candidates:
        if d.is_dir():
            paths.append(str(d.resolve(strict=False)))
        else:
            logger.warning("always-on skill dir missing (skipped): %s", d)
    return paths


def always_on_http_mcp_servers(session_id: str) -> list[McpHttpServerConfig]:
    """Built-in HTTP MCP servers every session carries: docs, schedules, connectors.

    These are always-on for every kind of session (chat / project / task
    dispatch). They are appended after external catalog providers so their
    reserved ``valuz_*`` names never collide. The shared secret travels in the
    ``X-Valuz-Internal`` header so a misrouted request can't reach them; the
    ``X-Valuz-Session-Id`` header scopes each call to the calling session.

    Stable tool list across all sessions (no kind/attachment gating) keeps the
    Anthropic prompt cache warm. See ADR-009 + ``resolve_session_capabilities``
    §2.5 for the rationale.
    """
    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.integrations.automations_mcp_server import automations_mcp_url
    from valuz_agent.integrations.connectors_mcp_server import connectors_mcp_url
    from valuz_agent.integrations.docs_mcp_server import docs_mcp_url

    headers = {
        "X-Valuz-Internal": _settings.internal_mcp_token,
        "X-Valuz-Session-Id": session_id,
    }
    base = _settings.backend_base_url
    return [
        McpHttpServerConfig(
            name="valuz_docs",
            url=docs_mcp_url(base_url=base),
            transport="http",
            headers=dict(headers),
        ),
        McpHttpServerConfig(
            name="valuz_automations",
            url=automations_mcp_url(base_url=base),
            transport="http",
            headers=dict(headers),
        ),
        McpHttpServerConfig(
            name="valuz_connectors",
            url=connectors_mcp_url(base_url=base),
            transport="http",
            headers=dict(headers),
        ),
    ]


def _resolve_to_absolute(path: str | None, project_root: str | None) -> str | None:
    """Return an absolute filesystem path for a skill source dir.

    Accepts the same forms ``SkillDatastore.set_skill_enabled`` accepts:
    absolute paths pass through; relative paths are joined to the
    workspace root when one exists. Paths whose parent does not exist
    are still returned (the kernel's materializer will raise a clean
    ``SkillSourceMissingError`` later); paths that cannot be normalised
    return ``None`` and bubble up as a warning.
    """
    if not path:
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        if not project_root:
            return None
        candidate = Path(project_root) / candidate
    try:
        return str(candidate.resolve(strict=False))
    except OSError:
        return None


async def resolve_skill_slugs_to_paths(
    skill_entries: object, project_root: str | None
) -> list[str]:
    """Map an agent's ``skills`` entries (slugs and/or absolute paths) to
    absolute skill-directory paths — the single chokepoint for this.

    Agents persist skill SLUGS (e.g. ``"to-prd"``). The kernel materializer
    needs absolute source paths; handing it a bare slug crashes with
    "Skill source path not found or not a directory: <slug>". EVERY
    session-construction path that turns ``agent.skills`` into
    ``Session.skills`` must call this — both the task path
    (``agent_resolver.build_member_session``) and the chat/project path
    (``sessions.service.create_session``). Unresolvable entries are dropped
    with a warning rather than passed through.
    """
    import os

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.skills.datastore import SkillDatastore

    entries = list(skill_entries or [])  # type: ignore[arg-type]
    if not entries:
        return []

    # DB access goes through ``SkillDatastore`` on an async session. Both
    # callers (``agent_resolver.build_member_session`` task path,
    # ``sessions.service.create_session`` chat/project path) are async and
    # ``await`` this.
    by_slug: dict[str, str] = {}
    async with async_unit_of_work(commit=False) as db:
        for row in await SkillDatastore(db).list_skills():
            if row.slug and row.source_path:
                by_slug.setdefault(row.slug, row.source_path)

    resolved: list[str] = []
    for entry in entries:
        s = entry if isinstance(entry, str) else getattr(entry, "name", str(entry))
        if os.path.isabs(s):  # already an absolute path
            if os.path.isdir(s):
                resolved.append(s)
            else:
                logger.warning("resolve_skill_slugs: skill path missing, skipping: %s", s)
            continue
        absolute = _resolve_to_absolute(by_slug.get(s), project_root)
        if absolute and os.path.isdir(absolute):
            resolved.append(absolute)
        else:
            logger.warning("resolve_skill_slugs: unresolved skill slug, skipping: %s", s)
    return resolved


def _try_find_skill_by_slug(absolute_path: str) -> str | None:
    """Fallback: try to find a skill by its slug name in the canonical dir."""
    from valuz_agent.infra.fs_registry import fs_registry

    slug = Path(absolute_path).name
    canonical = fs_registry.user_skill_root() / slug
    if canonical.is_dir():
        logger.info(
            "Skill path %r not found, using canonical fallback: %s",
            absolute_path,
            canonical,
        )
        return str(canonical)
    return None


__all__ = [
    "ResolvedCapabilities",
    "always_on_http_mcp_servers",
    "always_on_skill_paths",
    "resolve_session_capabilities",
]
