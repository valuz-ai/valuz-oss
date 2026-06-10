"""AgentService — business logic for Agents and Project Members.

Slice 1 scope (lead-dispatch-mvp §S1/§S3):
  - Agent CRUD (list / get — MVP read-only)
  - Project member CRUD (list / create / patch / delete)
  - deploy_agent: creates a kernel AgentConfig from a source agent
  - create_blank_agent: creates a kernel AgentConfig without a source agent
  - delete_member: removes the membership row (shared kernel config lives on)

Connector binding:
  - connector_bindings (``[{type: <slug>}]``) are stored in AgentConfig
    metadata for later inspection AND resolved into live ``McpServerConfig``
    rows so the bound MCP servers are available when the agent runs.
  - Resolution is delegated to ``ConnectorService.resolve_mcp_servers`` (the
    connector module owns credential/header injection) — this service never
    touches the secret store directly.
"""

# ruff: noqa: I001 — kernel_bootstrap side-effect import must precede ``from src.core``
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

import valuz_agent.boot.kernel  # noqa: F401 — ensures sys.path has kernel root

from src.core import AgentConfig

from valuz_agent.modules.agents.datastore import (
    AgentDatastore,
    ProjectMemberDatastore,
)
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.connectors.service import ConnectorService

logger = logging.getLogger(__name__)


def _prepare_conversation_tools(agent: AgentConfig) -> AgentConfig:
    """Make an agent's tool set conversation-ready (M10 附录 E).

    Surfaces the launcher/observability tools (``create_task`` / ``list_tasks``
    / ``get_task``) and strips any lead-only dispatch tools — the latter belong
    on the per-task lead clone, never the base agent. Applied at agent
    create/edit time so the conversation-session path never has to mutate or
    re-save the agent (which previously triggered an agent save on every
    "send" — see the conversation bug fix).

    Also declares the always-on **in-process** baseline tools — memory
    (``memory_get`` / ``memory_write``) and ``submit_skill`` — so every
    member/lead agent surfaces them, exactly like conversation sessions. These
    bind via the persisted ``AgentConfig.tools`` (the kernel reads tools off the
    agent, not the session), so they can only live here; the handlers are
    attached from the kernel tool registry at runtime — we add ``handler=None``
    declarations. The skill/MCP half of the baseline (valuz-project-docs,
    skill-creator skill, schedules/docs MCP) is injected per-session by the
    session-build paths instead.
    """
    from valuz_agent.modules.tasks.dispatch_mcp import (
        ensure_orchestration_tools_on_agent,
        strip_dispatch_tools,
    )

    agent = strip_dispatch_tools(ensure_orchestration_tools_on_agent(agent))
    return _ensure_global_tools_declared(agent)


def _global_tool_declarations() -> tuple[Any, ...]:
    """The always-on in-process tool declarations every agent must carry:
    memory (get/write) + submit_skill. Imported lazily to avoid import cycles."""
    from valuz_agent.modules.memory.tools import MEMORY_TOOL_DECLARATIONS
    from valuz_agent.integrations.tools_skill_creator import SUBMIT_SKILL_TOOL_DECLARATION

    return tuple(MEMORY_TOOL_DECLARATIONS) + (SUBMIT_SKILL_TOOL_DECLARATION,)


def _ensure_global_tools_declared(agent: AgentConfig) -> AgentConfig:
    """Append any missing always-on in-process tool declarations (idempotent)."""
    have = {getattr(t, "name", None) for t in (agent.tools or ())}
    missing = tuple(d for d in _global_tool_declarations() if d.name not in have)
    if not missing:
        return agent
    return replace(agent, tools=tuple(agent.tools or ()) + missing)


class MemberNotFoundError(Exception):
    pass


class AgentNotFoundError(Exception):
    pass


class MemberAlreadyExistsError(Exception):
    pass


class AgentStillDeployedError(Exception):
    """Raised when deleting an agent that is still派驻'd into one or more projects.

    v2 delete guard: prevents orphaning a task holder. Carries the project
    count so the UI can prompt "解除派驻 first".
    """

    def __init__(self, slug: str, deployment_count: int) -> None:
        self.slug = slug
        self.deployment_count = deployment_count
        super().__init__(
            f"agent '{slug}' is still deployed to {deployment_count} project(s); "
            "remove those派驻 first"
        )


class AgentNotDeletableError(Exception):
    """Raised when deleting an agent flagged ``deletable=False`` (e.g. the
    always-present 默认助手 / default-assistant base agent)."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"agent '{slug}' is protected and cannot be deleted")


class AgentService:
    def __init__(
        self,
        db: Session,
        connector_service: ConnectorService | None = None,
    ) -> None:
        self._db = db
        self._agents = AgentDatastore(db)
        self._members = ProjectMemberDatastore(db)
        # Injected so this service never reaches into the secret store
        # itself — connector→MCP cohesion lives in ConnectorService.
        self._connectors = connector_service

    # ------------------------------------------------------------------
    # Connector → MCP resolution
    # ------------------------------------------------------------------

    async def _resolve_mcp_servers(
        self, connector_bindings: list[dict[str, str]] | None
    ) -> tuple[Any, ...]:
        """Resolve connector bindings into kernel ``McpServerConfig`` rows.

        Each binding's ``type`` is a connector slug (e.g. ``valuz-search``).
        Delegates to ``ConnectorService.resolve_mcp_servers`` so credential
        handling stays inside the connector module. When no connector service
        was injected, bindings are stored as metadata only (no live servers).
        """
        if not connector_bindings or self._connectors is None:
            return ()
        slugs = [b["type"] for b in connector_bindings if b.get("type")]
        if not slugs:
            return ()
        # The connector module hands back wire schemas; the agent snapshot is
        # a domain object (tool/agent-prep cluster), so convert here.
        from src.core import (
            McpHttpServerConfig,
            McpStdioServerConfig,
        )

        out = []
        for cfg in await self._connectors.resolve_mcp_servers(slugs):
            if getattr(cfg, "transport", None) == "stdio" or hasattr(cfg, "command"):
                out.append(
                    McpStdioServerConfig(
                        name=cfg.name,
                        command=cfg.command,
                        args=tuple(cfg.args),
                        env=dict(cfg.env),
                        env_vars=tuple(cfg.env_vars),
                    )
                )
            else:
                out.append(
                    McpHttpServerConfig(
                        name=cfg.name,
                        url=cfg.url,
                        transport=cfg.transport,
                        headers=dict(cfg.headers),
                    )
                )
        return tuple(out)

    # ------------------------------------------------------------------
    # Shared kernel AgentConfig (v2 live-reference)
    # ------------------------------------------------------------------

    async def build_agent_config(self, row: AgentRow, agent_id: str | None = None) -> AgentConfig:
        """Build an in-memory kernel ``AgentConfig`` from an AgentRow's fields.

        This is the single AgentRow→AgentConfig constructor: session-creation
        paths embed the result as the session's ``agent_config`` snapshot
        (live-reference semantics: every NEW session picks up the row's
        latest fields; existing sessions keep the snapshot they were created
        with). Connectors are resolved from the row's ``connector_types``;
        provider pin + bindings ride ``metadata`` so downstream adapters
        (mcp_resolver / provider_resolver) see an identical shape.
        """
        kernel_agent_id = agent_id or f"agent:{row.slug}"[:36]
        metadata: dict[str, Any] = {}
        connector_bindings = [{"type": s} for s in (row.connector_types or [])] or None
        if connector_bindings:
            metadata["connector_bindings"] = connector_bindings
        if row.provider_id:
            metadata["provider_id"] = row.provider_id
        agent = AgentConfig(
            id=kernel_agent_id,
            name=row.name,
            model=row.model or "claude-sonnet-4-6",
            runtime_provider=row.runtime,
            instructions=row.instructions,
            skills=tuple(row.skills or []),
            mcp_servers=await self._resolve_mcp_servers(connector_bindings),
            permission_mode="full_access",
            effort=row.effort or None,
            metadata=metadata,
        )
        return _prepare_conversation_tools(agent)

    # ------------------------------------------------------------------
    # Agent reads (MVP agents are read-only)
    # ------------------------------------------------------------------

    async def list_agents(self, source: str | None = None) -> list[AgentRow]:
        return await self._agents.list_agents(source=source)

    async def get_agent(self, slug: str) -> AgentRow:
        row = await self._agents.get_agent(slug)
        if row is None:
            raise AgentNotFoundError(slug)
        return row

    async def create_agent(self, payload: dict[str, Any]) -> AgentRow:
        """Create a user-defined agent (source='custom').

        ``slug`` is backend-derived from ``name`` when the caller omits it
        (the UI no longer computes slugs client-side) — CJK-preserving,
        spaces→``-``, case kept. A caller-supplied slug is honored as-is.
        Either way it's made globally unique by suffixing on collision.
        """
        from valuz_agent.modules.agents.slug import derive_slug, ensure_unique_slug

        slug = (payload.get("slug") or "").strip()
        if not slug:
            existing = {a.slug for a in await self._agents.list_agents()}
            slug = ensure_unique_slug(derive_slug(payload["name"]), existing)
        if await self._agents.get_agent(slug) is not None:
            raise MemberAlreadyExistsError(f"agent '{slug}' already exists")
        row = AgentRow(
            slug=slug,
            name=payload["name"],
            description=payload.get("description", ""),
            instructions=payload.get("instructions", ""),
            runtime=payload.get("runtime", "claude_agent"),
            model=payload.get("model", "claude-sonnet-4-6"),
            skills=payload.get("skills", []),
            connector_types=payload.get("connector_types", []),
            provider_id=payload.get("provider_id") or None,
            effort=payload.get("effort") or None,
            avatar=payload.get("avatar") or None,
            source="custom",
        )
        # Live-reference: sessions snapshot the row at creation time, so a
        # fresh agent needs no extra materialization step.
        return await self._agents.create(row)

    async def update_agent(self, slug: str, patch: dict[str, Any]) -> AgentRow:
        """Patch an agent's editable fields. Official agents are editable too —
        the `readonly` flag is preserved on the row for provenance but no longer
        gates updates. Deletion is still restricted by `deletable` in
        `delete_agent` below."""
        # Fetch existing row to surface 404 before mutation.
        existing = await self._agents.get_agent(slug)
        if existing is None:
            raise AgentNotFoundError(slug)

        allowed = {
            "name",
            "description",
            "instructions",
            "runtime",
            "model",
            "skills",
            "connector_types",
        }
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        # provider_id is nullable and clearable: when explicitly present in the
        # patch (even as None/""), apply it — None unbinds the default provider.
        if "provider_id" in patch:
            fields["provider_id"] = patch["provider_id"] or None
        # effort is nullable and clearable the same way — None means "no
        # override" (the runtime falls through to its SDK default).
        if "effort" in patch:
            fields["effort"] = patch["effort"] or None
        # avatar is nullable and clearable — None / "" unsets the avatar.
        if "avatar" in patch:
            fields["avatar"] = patch["avatar"] or None
        row = await self._agents.update_fields(slug, fields)
        if row is None:
            raise AgentNotFoundError(slug)
        # Live-reference semantics need no kernel cascade anymore: sessions
        # snapshot the row's fields at creation, so every NEW session (in any
        # project the agent is deployed to) picks the edit up automatically.
        return row

    async def delete_agent(self, slug: str) -> None:
        # Official and custom agents are equally deletable now — the only block
        # is the live派驻 guard below. seed_official_agents is insert-if-absent,
        # so deleted defaults simply won't come back unless the user wipes DB.
        existing = await self._agents.get_agent(slug)
        if existing is None:
            raise AgentNotFoundError(slug)
        # Protected base agents (default-assistant) opt out of deletion.
        if not existing.deletable:
            raise AgentNotDeletableError(slug)
        # v2 派驻 guard: block deleting an agent still referenced by any project
        # member (would orphan a task holder). Caller must解除派驻 first.
        deployments = await self._members.list_by_source_agent_slug(existing.slug)
        if deployments:
            if deployments:
                raise AgentStillDeployedError(slug, len(deployments))
        if not await self._agents.delete(slug):
            raise AgentNotFoundError(slug)

    # ------------------------------------------------------------------
    # Member list
    # ------------------------------------------------------------------

    async def list_deployments(self, slug: str) -> list[dict[str, Any]]:
        """List every派驻 of an agent — the projects it's deployed into.

        Powers the agent detail page's「派驻于 N 个项目」panel + the delete-guard
        UX. Returns ``[{project_id, agent_slug}]`` (the project-local handle);
        the frontend resolves project display names from its own store. Empty
        when the agent has never been deployed (no shared kernel config yet).
        """
        row = await self.get_agent(slug)
        members = await self._members.list_by_source_agent_slug(row.slug)
        return [{"project_id": m.project_id, "agent_slug": m.agent_slug} for m in members]

    async def list_members(self, project_id: str) -> list[dict[str, Any]]:
        """Return members with their resolved kernel agent summary.

        Each item: {member: ProjectMemberRow, agent: AgentConfig | None}
        Kernel load failures are surfaced as agent=None so the list still
        returns even when a kernel row is missing.
        """
        members = await self._members.list_by_project(project_id)
        result: list[dict[str, Any]] = []
        for m in members:
            try:
                agent = None
                if m.source_agent_slug:
                    src_row = await self._agents.get_agent(m.source_agent_slug)
                    if src_row is not None:
                        agent = await self.build_agent_config(src_row)
            except Exception:
                logger.warning(
                    "list_members: could not build agent config for member %s/%s (src=%s)",
                    project_id,
                    m.agent_slug,
                )
                agent = None
            result.append({"member": m, "agent": agent})
        return result

    # ------------------------------------------------------------------
    # Instantiate from a source agent
    # ------------------------------------------------------------------

    async def deploy_agent(
        self,
        project_id: str,
        source_agent_slug: str,
        agent_slug: str | None = None,
        dedupe: bool = True,
    ) -> dict[str, Any]:
        """v2 DEPLOY (派驻): live-reference the source library agent.

        NO per-project copy. The member row records ``source_agent_slug``;
        every new session builds its embedded config snapshot from the source
        AgentRow's CURRENT fields, so editing the agent (library or project
        side) propagates to every project automatically.
        Configuration lives on the agent, not the派驻 — to pin a provider on a
        seeded official agent, copy it to your own agent (复制为我的) and set the
        provider there (大脑 tab).

        ``dedupe`` (default True) enforces ONE派驻 per agent per project — the
        project-member UX. The automation runner passes ``dedupe=False`` because
        it intentionally creates a distinct member handle per automation that may
        reference the same source agent in the same project.
        """
        from valuz_agent.modules.agents.slug import derive_slug, ensure_unique_slug

        source_agent = await self.get_agent(source_agent_slug)

        # Project-local handle: derive from the source agent's display name,
        # unique within THIS project (CJK-preserving). The handle is a
        # per-project path component; the underlying agent is shared.
        agent_slug = (agent_slug or "").strip()
        if not agent_slug:
            taken = {m.agent_slug for m in await self._members.list_by_project(project_id)}
            agent_slug = ensure_unique_slug(derive_slug(source_agent.name), taken)

        if await self._members.get(project_id, agent_slug) is not None:
            raise MemberAlreadyExistsError(
                f"agent '{agent_slug}' already exists in project '{project_id}'"
            )

        # v2 dedup: ONE派驻 per agent per project (live reference — deploying
        # the same agent twice into one project is meaningless). Keyed on the
        # source library slug. Skipped for the automation runner (``dedupe``).
        if dedupe:
            existing_members = await self._members.list_by_project(project_id)
            if any(m.source_agent_slug == source_agent.slug for m in existing_members):
                raise MemberAlreadyExistsError(
                    f"agent '{source_agent_slug}' is already deployed to project '{project_id}'"
                )

        member = ProjectMemberRow(
            project_id=project_id,
            agent_slug=agent_slug,
            # Provenance IS the live link: sessions build their snapshot from
            # the source library row at creation time.
            source_agent_slug=source_agent.slug,
        )
        await self._members.create(member)

        agent = await self.build_agent_config(source_agent)
        return {"member": member, "agent": agent}

    # ------------------------------------------------------------------
    # Create blank agent (no source agent)
    # ------------------------------------------------------------------

    async def create_blank_agent(
        self,
        project_id: str,
        agent_slug: str | None,
        name: str,
        instructions: str,
        description: str = "",
        runtime: str = "claude_agent",
        model: str = "claude-sonnet-4-6",
        connector_bindings: list[dict[str, str]] | None = None,
        skills: list[str] | None = None,
        provider_id: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """v2: create a LIBRARY agent (source=custom) from scratch, then派驻 it.

        A "blank agent in a project" is no longer an orphan per-project kernel
        config — it's a first-class library AgentRow (with its own shared kernel
        config built by ``create_agent``) that happens to be created from the
        project entry point and immediately deployed. ``agent_slug`` is the
        project-local member handle; the library slug is derived from ``name``.
        """
        connector_types = [b["type"] for b in (connector_bindings or []) if b.get("type")]
        row = await self.create_agent(
            {
                "name": name,
                "description": description,
                "instructions": instructions,
                "runtime": runtime,
                "model": model,
                "skills": list(skills or []),
                "connector_types": connector_types,
                "provider_id": provider_id,
                "effort": effort,
            }
        )
        return await self.deploy_agent(
            project_id=project_id,
            source_agent_slug=row.slug,
            agent_slug=agent_slug or None,
        )

    # ------------------------------------------------------------------
    # Update member's kernel agent
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Delete member
    # ------------------------------------------------------------------

    async def delete_member(self, project_id: str, agent_slug: str) -> None:
        """解除派驻: delete ONLY the membership row.

        v2 live-reference: the kernel ``AgentConfig`` is SHARED across projects,
        so undeploying must NOT delete it (other projects may still派驻 it). The
        agent itself lives on in the library;真删 happens via ``delete_agent``.
        """
        member = await self._members.get(project_id, agent_slug)
        if member is None:
            raise MemberNotFoundError(agent_slug)

        await self._members.delete(project_id, agent_slug)
