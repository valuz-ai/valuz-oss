"""Translate valuz business state into V5 kernel session creation parameters.

When the host receives ``POST /v1/sessions``, it does not call the kernel
directly with raw user input — the kernel's ``CreateSessionRequest`` wants
absolute paths and ``McpServerConfig`` objects that valuz must produce
from its own catalog tables (skills, MCP providers, providers). This module
owns that translation.

Outputs are pure data — the resolver does no writes. The session router
takes the result and hands it to the kernel via ``StorePort.save_session``.

Currently covered:
- ``skills``: project-enabled skill paths plus session-attached extras,
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

from app.schemas import (
    McpHttpServerConfigSchema as McpHttpServerConfig,
)
from app.schemas import (
    McpServerConfigSchema as McpServerConfig,
)

# Side-effect import — surfaces ``src.core...`` on sys.path.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.mcp_resolver import resolve_mcp_servers
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.skills.contracts import (
    ProjectRef,
    RuntimeContext,
    SkillManifest,
)
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.ports.runtime_resource import (
    RuntimeResourceContractError,
    ensure_managed_root_containment,
    validate_skill_reference,
)


class _SkillSource(Protocol):
    """Structural type matching every skill source's ``list_skills`` API.

    Used to type the optional ``extra_skill_sources`` (e.g. ``OfficialSkillSource``)
    without importing each implementation here.

    ``compute_content_hash`` is part of the contract, not an implementation
    detail: it gates a full read of every file in every package, which every
    real source implements and which this resolver never wants.
    """

    def list_skills(
        self, ctx: RuntimeContext, *, compute_content_hash: bool = True
    ) -> list[SkillManifest]: ...


logger = logging.getLogger(__name__)

# Bundled builtin skills — ``valuz-project-docs`` (teaches KB ``doc_search`` /
# ``list_doc_scope``), ``citation``, ``skill-creator`` and ``browser`` (teaches the
# ``chrome-devtools`` CLI, paired with the ``browser_start``/``browser_stop``
# toolkit tools).
#
# A session skill path must resolve somewhere the process that MATERIALIZES it
# can see. A remote kernel runs inside a sandbox that mounts only the owner's
# data subtrees, so a host-side package path resolves to nothing there and
# materialization fails with "Skill source path not found". Two locations
# satisfy the requirement, and ``official_skill_dir`` is the single source of
# truth for choosing between them: a declared system root (an image-wide
# location the deployment has put in EVERY image, sandbox included), else the
# per-user official root the sandbox mounts. Both ``always_on_skill_paths`` and
# ``sessions.capabilities`` go through it so the injected path strings match
# exactly — dedup depends on that.


def official_skill_dir(slug: str, user_id: str) -> Path:
    """Absolute path to an official-scope skill directory for a user.

    Prefers the copy that ships with the install (``system_skill_roots``) and
    falls back to the per-user official root, which is where an on-demand
    template skill or an externally installed official skill lives. The
    returned path is valid in-process and inside a sandbox alike: a shipped
    package sits at the same absolute path in every image, and the per-user
    root is mounted into the sandbox.

    The path may not exist — every caller already treats a missing directory as
    "this skill is not installed" and skips it.
    """
    from valuz_agent.infra.fs_registry import fs_registry

    shipped = fs_registry.find_system_skill(slug)
    if shipped is not None:
        return shipped
    return fs_registry.official_skill_root(user_id=user_id) / slug


def project_docs_skill_dir(user_id: str) -> Path:
    """Absolute path to the ``valuz-project-docs`` skill (see ``official_skill_dir``)."""
    return official_skill_dir("valuz-project-docs", user_id)


def browser_skill_dir(user_id: str) -> Path:
    """Absolute path to the ``browser`` skill (see ``official_skill_dir``)."""
    return official_skill_dir("browser", user_id)


def citation_skill_dir(user_id: str) -> Path:
    """Absolute path to the always-on citation protocol skill."""
    return official_skill_dir("citation", user_id)


@dataclass(frozen=True)
class ResolvedCapabilities:
    """Inputs the kernel needs to create a session for a valuz project."""

    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    skill_resolution_warnings: tuple[str, ...] = field(default_factory=tuple)


async def resolve_session_capabilities(
    *,
    projects: ProjectDatastore,
    skills: SkillDatastore,
    project_id: str,
    extra_skill_ids: list[str] | None = None,
    skill_source: FilesystemSkillSource | None = None,
    extra_skill_sources: list[_SkillSource] | None = None,
    official_entitled: bool = False,
    enabled_mcp_provider_slugs: list[str] | None = None,
    connectors: ConnectorDatastore | None = None,
    docs: DocumentDatastore | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    all_available_skill_paths: list[str] | None = None,
) -> ResolvedCapabilities:
    if user_id is None:
        raise ValueError("user_id is required")

    """Compute kernel-shaped capabilities for a session in ``project_id``.

    The MCP arguments are optional so the resolver stays usable in code paths
    that don't (yet) expose data-source selection. When all three are
    supplied alongside ``enabled_mcp_provider_slugs`` the resolver materialises
    the corresponding ``McpServerConfig`` list.
    """

    project = await projects.get_by_id(user_id, project_id)
    if project is None:
        raise KeyError(project_id)

    skill_paths: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    all_available = all_available_skill_paths is not None
    for path in all_available_skill_paths or []:
        absolute = str(Path(path).expanduser().resolve(strict=False))
        if absolute in seen:
            continue
        if not Path(absolute).is_dir():
            warnings.append(f"all-available skill path does not exist: {absolute!r}")
            continue
        seen.add(absolute)
        skill_paths.append(absolute)

    # 1) Project-enabled skills — read from the filesystem-based
    #    ``project-config.json`` which is the canonical source of truth
    #    for which skills are enabled for a project.  The DB-backed
    #    ``ProjectSkillConfigRow`` table is not currently populated by the
    #    UI's ``set_skill_enabled`` flow; it writes to JSON instead.
    enabled_paths = [] if all_available else skills.enabled_skill_paths(project)
    for path in enabled_paths:
        absolute = _resolve_to_absolute(path, project.root_path)
        if absolute is None:
            warnings.append(f"project-enabled skill path is not resolvable: {path!r}")
            continue
        if absolute in seen:
            continue
        if not Path(absolute).is_dir():
            fallback = _try_find_skill_by_slug(absolute, user_id=user_id)
            if fallback:
                absolute = fallback
            else:
                warnings.append(f"project-enabled skill path does not exist: {absolute!r}")
                continue
        seen.add(absolute)
        skill_paths.append(absolute)

    # 1b) For non-project (chat) projects, every user-library skill is
    #     implicitly enabled.
    #
    #     Both scans below pass ``compute_content_hash=False``. The resolver
    #     reads only ``path`` / ``scope`` / ``origin_label`` off each manifest —
    #     it has never used ``content_hash``, which only the indexer needs for
    #     change detection. Computing it reads EVERY file of EVERY package:
    #     measured on a managed deployment 2026-08-07, 20 packages / 260 files
    #     / 3.9 MiB on a network mount, **21–28 seconds**, paid at session
    #     creation. ``FilesystemSkillSource.list_skills`` already documents the
    #     flag as "display/catalog listing passes False"; this is one of those
    #     callers and had simply never said so. The skills panel UI advertises them as enabled
    #     for chat (datastore.list_project_skills sets ``enabled=True`` for
    #     project.kind == "chat") and there is no per-project toggle to
    #     opt out, so the resolver must mirror that for the runtime.
    if (
        not all_available
        and project.kind != "project"
        and (skill_source is not None or extra_skill_sources)
    ):
        ctx = RuntimeContext(
            user_id=user_id,
            project=ProjectRef(
                id=project.id,
                slug=project.id,
                kind=project.kind,
                root_path=project.root_path,
            ),
        )
        if skill_source is not None:
            for manifest in skill_source.list_skills(ctx, compute_content_hash=False):
                if manifest.scope != "user":
                    continue
                absolute = _resolve_to_absolute(manifest.path, project.root_path)
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
            for manifest in source.list_skills(ctx, compute_content_hash=False):
                if manifest.scope != "official":
                    continue
                is_bundled = manifest.origin_label == "Built-in"
                if not is_bundled and not official_entitled:
                    continue
                absolute = _resolve_to_absolute(manifest.path, project.root_path)
                if absolute is None or absolute in seen:
                    continue
                if not Path(absolute).is_dir():
                    continue
                seen.add(absolute)
                skill_paths.append(absolute)

    # 2) Session-level extras — SkillView ids attached just for this session
    #    on top of whatever the project already enables. SkillView.id comes from
    #    the manifest (e.g. "official:skill-creator"), while valuz_skill_index.id
    #    is only the DB row primary key; prefer slug lookup when the id is scoped.
    for skill_id in extra_skill_ids or []:
        row = await skills.get_by_id(user_id, skill_id)
        if row is None and ":" in skill_id and hasattr(skills, "get_by_slug"):
            row = await skills.get_by_slug(user_id, skill_id.split(":", 1)[1])
        if row is None:
            warnings.append(f"extra skill id not found: {skill_id!r}")
            continue
        absolute = _resolve_to_absolute(row.source_path, project.root_path)
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
    #      at ``/_internal/mcp/docs/{session_id}/mcp``) implements those
    #      tools scoped to the session's project.
    #
    #      Why unconditional: the skill + MCP form a stable,
    #      prompt-cache-friendly capability layer that mirrors the
    #      ``valuz_automations`` automation pattern. Whether the project
    #      actually has KB bindings (or per-turn attachments) is
    #      announced inside ``UserMessage.additional_context`` — that's
    #      the channel for dynamic state. Putting that state in the
    #      skill set or system prompt would invalidate Anthropic's
    #      prompt cache on every binding / attachment change; making
    #      docs MCP conditional on project.kind == "project" had the
    #      same effect across the chat / project boundary.
    #
    #      The pair MUST travel together: a skill without the MCP
    #      server would teach the agent about non-existent tools; an
    #      MCP server without the skill would leave the agent unaware
    #      that doc search is available. For chat sessions the MCP
    #      tools return empty results (no KB bindings → empty scope)
    #      which is a normal answer the agent already handles.
    for absolute in always_on_skill_paths(user_id=user_id):
        if absolute not in seen:
            seen.add(absolute)
            skill_paths.append(absolute)
    logger.info(
        "Auto-injecting always-on baseline skills for project %s (kind=%s)",
        project_id,
        project.kind,
    )

    # 3) MCP servers — only when the caller wires the catalog in. Anything
    #    missing (no credentials, unknown slug, disabled provider) is logged
    #    inside ``mcp_resolver`` and silently skipped here.
    mcp_configs_list: list[McpServerConfig] = []
    if connectors is not None:
        mcp_configs_list.extend(
            await resolve_mcp_servers(
                enabled_slugs=enabled_mcp_provider_slugs or [],
                connectors=connectors,
                user_id=user_id,
            )
        )

    # 3.5–3.7) In-process always-on HTTP MCP servers (docs / schedules /
    #      connectors). Factored into ``always_on_http_mcp_servers`` so the
    #      task-dispatch path (``agent_resolver.build_member_session``) can
    #      inject the same set — task lead/member sessions don't flow through
    #      this resolver but must still carry these built-in tools.
    if session_id:
        mcp_configs_list.extend(await always_on_http_mcp_servers(session_id, owner_user_id=user_id))
    else:
        logger.warning(
            "session_id not provided — skipping always-on HTTP MCP injection "
            "(docs/schedules/connectors tools will be unavailable in this session)"
        )

    logger.info(
        "Resolved capabilities for project %s: %d skills, %d MCP servers, %d warnings",
        project_id,
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


def always_on_skill_paths(*, user_id: str) -> list[str]:
    """Bundled skills every session carries: docs + citation + skill-creator (+ browser).

    These are the skill half of the always-on baseline (the MCP half lives in
    ``always_on_http_mcp_servers``). ``valuz-project-docs`` teaches the
    ``doc_search`` / ``list_doc_scope`` tools that pair with the ``valuz_docs``
    MCP; ``skill-creator`` (+ its ``submit_skill`` in-process tool) lets any
    session author skills; ``browser`` teaches the ``chrome-devtools`` CLI that
    pairs with the ``browser_start``/``browser_stop`` toolkit tools (injected
    only when the browser engine is available). Returned as
    absolute dirs the kernel materialises into the session cwd. All are injected
    by every session-build path (``resolve_session_capabilities`` for
    chat/project, ``build_member_session`` for task lead/member) so the baseline
    is identical everywhere. A missing dir is skipped + logged so a partial
    install can't break session creation.
    """
    from valuz_agent.modules.browser import service as browser_service

    candidates = [
        project_docs_skill_dir(user_id),
        citation_skill_dir(user_id),
        official_skill_dir("skill-creator", user_id),
    ]
    # The browser skill teaches the ``chrome-devtools`` CLI, which only works
    # when the engine (Node + chrome-devtools-mcp) is available; don't inject a
    # dead skill otherwise. See docs/design/browser-feature.md §8.
    if browser_service.node_available():
        candidates.append(browser_skill_dir(user_id))
    paths: list[str] = []
    for d in candidates:
        if d.is_dir():
            paths.append(str(d.resolve(strict=False)))
        else:
            logger.warning("always-on skill dir missing (skipped): %s", d)
    return paths


# Internal MCP token: a per-owner signed token (same signer as the data service)
# proves the caller's owner to the host built-in MCP endpoints — replacing the old
# shared, single-owner ``internal_mcp_token``. Owner-scoped + long-lived; rotated
# whenever the session's capabilities are re-resolved (create / resume). Verified
# host-side by ``_PerOwnerDataServiceVerifier`` (see integrations/_mcp_asgi).
_MCP_TOKEN_TTL_S = 30 * 86400

# Cache the per-owner token so it is STABLE within a process: a fresh mint each
# call (new iat) would make every capability re-resolve look "changed" and defeat
# the restamp idempotency (prompt-cache warmth in ``refresh_always_on_mcp_for_session``).
# Rotates on process restart / session resume (which re-bakes headers) — fine for a
# long-lived internal token.
_mcp_token_cache: dict[str, str] = {}


def _mint_internal_mcp_token(owner_user_id: str) -> str:
    cached = _mcp_token_cache.get(owner_user_id)
    if cached is not None:
        return cached
    from valuz_agent.boot.kernel import mint_data_service_token
    from valuz_agent.infra.data_service_secret import get_or_create_ds_secret

    secret = get_or_create_ds_secret(owner_user_id)
    token = mint_data_service_token(secret, user_id=owner_user_id, ttl_s=_MCP_TOKEN_TTL_S)
    _mcp_token_cache[owner_user_id] = token
    return token


# Tool-call timeout (ceiling) for the first-party harness MCP servers. Their
# tools can block longer than a runtime client's default cap (codex aborts at
# 120s): the ``harness`` toolkit's ``await_members`` parks up to one window unit,
# and generate_ui can stream a large page for minutes on a slow provider.
# Derive the ceiling from the await window + a margin so a healthy long wait is
# never mis-reported as a transport failure, while a genuinely hung tool still
# fails in minutes (not the arbitrary 1h it used to be). Kept in sync with
# ``coordination._MAX_AWAIT_WINDOW_S`` (600) — 600 + 120 margin = 720.
#
# ``VALUZ_INTERNAL_MCP_TOOL_TIMEOUT_SEC`` overrides it (default unchanged) for
# deployments whose provider gateway is genuinely slow — a finance workbench
# page on a congested channel can legitimately take longer than the default.
def _internal_mcp_tool_timeout_sec() -> float:
    import os

    raw = os.environ.get("VALUZ_INTERNAL_MCP_TOOL_TIMEOUT_SEC")
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 720.0


_INTERNAL_MCP_TOOL_TIMEOUT_SEC = _internal_mcp_tool_timeout_sec()


async def always_on_http_mcp_servers(
    session_id: str, *, owner_user_id: str, toolkit: str = "base"
) -> list[McpHttpServerConfig]:
    """Built-in HTTP MCP servers every session carries: docs, schedules,
    connectors, and the harness toolkit.

    These are always-on for every kind of session (chat / project / task
    dispatch). They are appended after external catalog providers so their
    reserved ``valuz_*`` / ``harness`` names never collide. The shared secret
    travels in the ``X-Valuz-Internal`` header so a misrouted request can't
    reach them; the ``X-Valuz-Session-Id`` header scopes each call to the
    calling session.

    ``toolkit`` selects the harness tool surface: ``base`` (orchestration
    launchers + memory + submit_skill — every ordinary session) or ``lead``
    (the dispatch set — task-lead sessions). The server name stays
    ``harness`` either way so the model-visible tool names
    (``mcp__harness__*``) are stable across kinds.

    Stable tool list across all sessions of a kind keeps the Anthropic
    prompt cache warm. See ADR-009 + ``resolve_session_capabilities`` §2.5
    for the rationale.
    """
    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.integrations.automations_mcp_server import automations_mcp_url
    from valuz_agent.integrations.connectors_mcp_server import connectors_mcp_url
    from valuz_agent.integrations.docs_mcp_server import docs_mcp_url
    from valuz_agent.integrations.toolkit_mcp_server import toolkit_mcp_url
    from valuz_agent.ports.sandbox_credential import get_sandbox_credential_verifier

    internal_credential = await get_sandbox_credential_verifier().credential_for(owner_user_id)
    headers = {
        "X-Valuz-Internal": internal_credential,
        "X-Valuz-Session-Id": session_id,
    }
    base = _settings.backend_base_url
    return [
        McpHttpServerConfig(
            name="valuz_docs",
            url=docs_mcp_url(base_url=base),
            transport="http",
            headers=dict(headers),
            tool_timeout_sec=_INTERNAL_MCP_TOOL_TIMEOUT_SEC,
        ),
        McpHttpServerConfig(
            name="valuz_automations",
            url=automations_mcp_url(base_url=base),
            transport="http",
            headers=dict(headers),
            tool_timeout_sec=_INTERNAL_MCP_TOOL_TIMEOUT_SEC,
        ),
        McpHttpServerConfig(
            name="valuz_connectors",
            url=connectors_mcp_url(base_url=base),
            transport="http",
            headers=dict(headers),
            tool_timeout_sec=_INTERNAL_MCP_TOOL_TIMEOUT_SEC,
        ),
        McpHttpServerConfig(
            name="harness",
            url=toolkit_mcp_url(base_url=base, toolset=toolkit),
            transport="http",
            headers=dict(headers),
            tool_timeout_sec=_INTERNAL_MCP_TOOL_TIMEOUT_SEC,
        ),
        *_edition_always_on_servers(base, headers),
    ]


def _edition_always_on_servers(base: str, headers: dict[str, str]) -> list[McpHttpServerConfig]:
    """Edition-registered always-on servers (ports/mcp_always_on).

    Same internal credential headers and timeout as the built-ins; reserved
    built-in names are skipped so an edition can never shadow them.
    """
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.mcp_always_on import RESERVED_ALWAYS_ON_NAMES

    servers: list[McpHttpServerConfig] = []
    for spec in list(ext.always_on_mcp_specs):
        if spec.name in RESERVED_ALWAYS_ON_NAMES:
            continue
        servers.append(
            McpHttpServerConfig(
                name=spec.name,
                url=f"{base}{spec.path}/mcp",
                transport="http",
                headers=dict(headers),
                tool_timeout_sec=_INTERNAL_MCP_TOOL_TIMEOUT_SEC,
            )
        )
    return servers


def harness_toolkit_for_run_kind(run_kind: str | None) -> str:
    """Map a session's ``metadata.valuz.run_kind`` to its harness toolset."""
    return "lead" if run_kind == "lead" else "base"


def _resolve_to_absolute(path: str | None, project_root: str | None) -> str | None:
    """Return an absolute filesystem path for a skill source dir.

    Accepts the same forms ``SkillDatastore.set_skill_enabled`` accepts:
    absolute paths pass through; relative paths are joined to the
    project root when one exists. Paths whose parent does not exist
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
    skill_entries: object,
    project_root: str | None,
    user_id: str | None = None,
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
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.skills.datastore import SkillDatastore

    if user_id is None:
        raise ValueError("user_id is required")

    entries = list(skill_entries or [])  # type: ignore[arg-type]
    if not entries:
        return []

    # DB access goes through ``SkillDatastore`` on an async session. Both
    # callers (``agent_resolver.build_member_session`` task path,
    # ``sessions.service.create_session`` chat/project path) are async and
    # ``await`` this.
    by_slug: dict[str, str] = {}
    async with async_unit_of_work(commit=False) as db:
        for row in await SkillDatastore(db).list_skills(user_id):
            if row.slug and row.source_path:
                by_slug.setdefault(row.slug, row.source_path)

    resolved: list[str] = []
    managed_root = fs_registry.user_skill_root(user_id)

    def _is_managed_or_project(path: str) -> bool:
        roots: list[Path] = [managed_root]
        if project_root:
            roots.append(Path(project_root).expanduser())
        for root in roots:
            try:
                ensure_managed_root_containment(root, path)
                return True
            except RuntimeResourceContractError:
                continue
        return False

    def _is_system_skill(path: str, slug: str) -> bool:
        system_path = fs_registry.find_system_skill(slug)
        if system_path is None:
            return False
        try:
            return Path(system_path).resolve(strict=False) == Path(path).resolve(strict=False)
        except OSError:
            return False

    def _safe_skill_tree(path: str) -> bool:
        """Reject symlink/reparse and special-file entries before execution."""
        try:
            root = Path(path)
            for candidate in (root, *root.rglob("*")):
                info = candidate.lstat()
                if candidate.is_symlink() or not (candidate.is_dir() or candidate.is_file()):
                    return False
                if candidate.is_file() and info.st_nlink > 1:
                    return False
        except OSError:
            return False
        return True

    def _eligible(path: str, slug: str) -> bool:
        if not _safe_skill_tree(path):
            return False
        if _is_managed_or_project(path) or _is_system_skill(path, slug):
            return True
        from valuz_agent.ports.extensions import ext

        result = ext.external_skill_discovery_policy.decide(
            user_id=user_id,
            source_path=path,
            slug=slug,
        )
        if not result.execution_eligible:
            logger.warning(
                "resolve_skill_slugs: external Skill is not claimed, skipping: %s",
                path,
            )
        return result.execution_eligible

    for entry in entries:
        s = entry if isinstance(entry, str) else getattr(entry, "name", str(entry))
        if os.path.isabs(s):  # already an absolute path
            if os.path.isdir(s) and _eligible(s, Path(s).name):
                try:
                    resolved.append(str(Path(s).resolve(strict=True)))
                except OSError:
                    logger.warning("resolve_skill_slugs: skill path cannot be resolved: %s", s)
            else:
                logger.warning("resolve_skill_slugs: skill path missing, skipping: %s", s)
            continue
        try:
            validate_skill_reference(s)
        except RuntimeResourceContractError:
            logger.warning("resolve_skill_slugs: unsafe skill reference, skipping: %s", s)
            continue
        absolute = _resolve_to_absolute(by_slug.get(s), project_root)
        if absolute and os.path.isdir(absolute) and _eligible(absolute, s):
            resolved.append(absolute)
        else:
            logger.warning("resolve_skill_slugs: unresolved skill slug, skipping: %s", s)
    return resolved


def _try_find_skill_by_slug(absolute_path: str, *, user_id: str) -> str | None:
    """Fallback: try to find a skill by its slug name in the canonical dir."""
    from valuz_agent.infra.fs_registry import fs_registry

    slug = Path(absolute_path).name
    canonical = fs_registry.user_skill_root(user_id=user_id) / slug
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
