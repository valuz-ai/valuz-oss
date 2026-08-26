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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import valuz_agent.boot.kernel  # noqa: F401 — ensures sys.path has kernel root

from src.core import AgentConfig

from valuz_agent.modules.agents.datastore import (
    AgentDatastore,
    ProjectMemberDatastore,
)
from valuz_agent.modules.agents.builtin import (
    SYSTEM_MANAGED_FIELDS,
    VALURION_DEFAULT_EFFORT,
    VALURION_DESCRIPTION,
    VALURION_NAME,
    VALURION_SLUG,
)
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.connectors.service import ConnectorService
from valuz_agent.ports.model_defaults import ModelDefaults
from valuz_agent.ports.runtime_resource import ManagedMutationResult

logger = logging.getLogger(__name__)


async def _factory_model_defaults(user_id: str | None) -> ModelDefaults:
    """Factory runtime/model defaults for creates that omitted them
    (``ext.model_defaults``: Settings env / distribution / cloud-delivered)."""
    from valuz_agent.ports.extensions import ext

    return await ext.model_defaults.get(user_id)


def _prepare_conversation_tools(agent: AgentConfig) -> AgentConfig:
    """Clear an agent's inline tool declarations — agents carry none.

    Every tool surface (task orchestration, memory, submit_skill, browser, …)
    rides the session's ``harness`` MCP entry, served by the host toolkit MCP
    server and scoped per session to its base/lead toolset. So the correct
    ``AgentConfig.tools`` is always empty; this strips whatever a legacy
    snapshot still holds, so stale declarations can never reach a runtime
    alongside the MCP-served set.

    (This function used to ADD the launcher tools and strip only the lead-only
    ones — from ``declarations.ensure_orchestration_tools_on_agent`` /
    ``strip_dispatch_tools``, both since deleted. Applied at agent create/edit
    time so the conversation-session path never mutates or re-saves the agent,
    which once triggered an agent save on every "send".)
    """
    return replace(agent, tools=())


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


class AgentManagedFieldError(Exception):
    """Raised when a caller tries to mutate system-managed Agent state."""

    def __init__(self, slug: str, fields: set[str]) -> None:
        self.slug = slug
        self.fields = tuple(sorted(fields))
        super().__init__(f"agent '{slug}' has system-managed field(s): {', '.join(self.fields)}")


class InvalidAgentSlugError(Exception):
    """Raised when a caller supplies a slug that is not a valid ASCII handle.

    Derived slugs are valid by construction; this only ever fires on a slug the
    caller typed. See ``modules/agents/slug.py`` for why the handle is ASCII —
    a non-ASCII one cannot be sent as an HTTP header value at all.
    """

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(
            f"agent slug '{slug}' is invalid: use ASCII letters, digits and single "
            "dashes (no leading/trailing dash)"
        )


async def _after_agent_saved_hook(
    db: AsyncSession, user_id: str, row: AgentRow, origin: str
) -> None:
    from valuz_agent.ports.extensions import ext

    await ext.agent_lifecycle.after_agent_saved(
        db=db,
        user_id=user_id,
        agent=row,
        origin=origin,  # type: ignore[arg-type]
    )


async def _before_agent_delete_hook(db: AsyncSession, user_id: str, row: AgentRow) -> None:
    from valuz_agent.ports.extensions import ext

    await ext.agent_lifecycle.before_agent_delete(db=db, user_id=user_id, agent=row)


async def _before_managed_agent_mutation(
    user_id: str,
    command: dict[str, Any],
    *,
    expected_etag: str | None = None,
    idempotency_key: str | None = None,
) -> ManagedMutationResult:
    """Ask the bound authority before changing the local executable row."""

    from valuz_agent.ports.extensions import ext

    return await ext.managed_agent_mutation.mutate(
        user_id,
        command,
        expected_etag=expected_etag,
        idempotency_key=idempotency_key,
    )


class AgentService:
    def __init__(
        self,
        db: AsyncSession,
        connector_service: ConnectorService | None = None,
    ) -> None:
        self._db = db
        self._agents = AgentDatastore(db)
        self._members = ProjectMemberDatastore(db)
        # Connector→MCP cohesion lives in ConnectorService (this service never
        # reaches into the secret store itself). Injectable for tests/overlays;
        # defaults to the module's own factory so every call site — including
        # the session-creation paths that build AgentService ad hoc — resolves
        # ``connector_types`` into live MCP servers instead of silently
        # dropping them.
        self._connectors = connector_service or ConnectorService.with_defaults(db)

    # ------------------------------------------------------------------
    # Connector → MCP resolution
    # ------------------------------------------------------------------

    async def _resolve_mcp_servers(
        self, connector_bindings: list[dict[str, str]] | None, user_id: str
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
        for cfg in await self._connectors.resolve_mcp_servers(slugs, user_id=user_id):
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

    async def build_agent_config(
        self, row: AgentRow, agent_id: str | None = None, user_id: str | None = None
    ) -> AgentConfig:
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
        owner_user_id = user_id or row.user_id
        metadata: dict[str, Any] = {}
        connector_bindings = [{"type": s} for s in (row.connector_types or [])] or None
        if connector_bindings:
            metadata["connector_bindings"] = connector_bindings
        if row.provider_id:
            metadata["provider_id"] = row.provider_id
        metadata["agent_slug"] = row.slug
        metadata["agent_kind"] = row.kind
        metadata["resource_policy"] = row.resource_policy
        metadata["inherit_global_instructions"] = row.inherit_global_instructions
        agent = AgentConfig(
            id=kernel_agent_id,
            name=row.name,
            model=row.model or "claude-sonnet-4-6",
            runtime_provider=row.runtime,
            instructions=row.instructions,
            skills=tuple(row.skills or []),
            mcp_servers=await self._resolve_mcp_servers(connector_bindings, user_id=owner_user_id),
            permission_mode=row.permission_mode or "full_access",
            effort=row.effort or None,
            metadata=metadata,
        )
        return _prepare_conversation_tools(agent)

    # ------------------------------------------------------------------
    # Agent reads (MVP agents are read-only)
    # ------------------------------------------------------------------

    async def list_agents(self, user_id: str, source: str | None = None) -> list[AgentRow]:
        # Migrations can only discover owners that already have persisted
        # resources.  Ensure the owner-scoped system Agent on the first Agent
        # library read as a compatibility path for empty legacy accounts.
        await self.ensure_builtin_agent(user_id)
        return await self._agents.list_agents(user_id, source=source)

    async def get_agent(self, user_id: str, slug: str) -> AgentRow:
        row = await self._agents.get_agent(user_id, slug)
        if row is None and slug == VALURION_SLUG:
            row = await self.ensure_builtin_agent(user_id)
        if row is None:
            raise AgentNotFoundError(slug)
        return row

    async def ensure_builtin_agent(self, user_id: str) -> AgentRow:
        """Create or repair the owner's canonical Valurion row.

        The unique ``(user_id, slug)`` constraint is the concurrency arbiter.
        Only system-managed fields are repaired; runtime/model/provider/effort
        preferences survive an idempotent ensure.
        """
        existing = await self._agents.get_agent(user_id, VALURION_SLUG)
        if existing is None:
            factory = await _factory_model_defaults(user_id)
            row = AgentRow(
                user_id=user_id,
                runtime=factory.default_runtime,
                model=factory.default_model,
                provider_id=factory.default_provider_id,
                effort=VALURION_DEFAULT_EFFORT,
                **SYSTEM_MANAGED_FIELDS,
            )
            authority = await _before_managed_agent_mutation(
                user_id,
                {
                    "operation": "upsert",
                    "slug": VALURION_SLUG,
                    "source": "system",
                    **SYSTEM_MANAGED_FIELDS,
                },
            )
            try:
                created = await self._agents.create(user_id, row)
            except IntegrityError:
                await self._db.rollback()
                created = await self._agents.get_agent(user_id, VALURION_SLUG)
                if created is None:
                    raise
            else:
                if authority.cloud_committed:
                    existing = created
                    return existing
                await _after_agent_saved_hook(self._db, user_id, created, "created")
            existing = created

        drift = {
            field: value
            for field, value in SYSTEM_MANAGED_FIELDS.items()
            if getattr(existing, field) != value
        }
        if drift:
            authority = await _before_managed_agent_mutation(
                user_id,
                {"operation": "upsert", "slug": VALURION_SLUG, "patch": drift},
            )
            repaired = await self._agents.update_fields(user_id, VALURION_SLUG, drift)
            if repaired is None:
                raise AgentNotFoundError(VALURION_SLUG)
            existing = repaired
            if not authority.cloud_committed:
                await _after_agent_saved_hook(self._db, user_id, existing, "updated")
        return existing

    async def create_agent(self, user_id: str, payload: dict[str, Any]) -> AgentRow:
        """Create a user-defined agent (source='custom').

        ``slug`` is backend-derived from ``name`` when the caller omits it
        (the UI no longer computes slugs client-side) — CJK-preserving,
        spaces→``-``, case kept. A caller-supplied slug is honored as-is.
        Either way it's made globally unique by suffixing on collision.
        """
        from valuz_agent.modules.agents.slug import (
            derive_slug,
            ensure_unique_slug,
            is_valid_slug,
        )

        slug = (payload.get("slug") or "").strip()
        if not slug:
            existing = {a.slug for a in await self._agents.list_agents(user_id)}
            slug = ensure_unique_slug(derive_slug(payload["name"]), existing)
        elif not is_valid_slug(slug):
            raise InvalidAgentSlugError(slug)
        if slug == VALURION_SLUG:
            raise MemberAlreadyExistsError(f"agent slug '{slug}' is reserved")
        if await self._agents.get_agent(user_id, slug) is not None:
            raise MemberAlreadyExistsError(f"agent '{slug}' already exists")
        factory = await _factory_model_defaults(user_id)
        row = AgentRow(
            slug=slug,
            name=payload["name"],
            description=payload.get("description", ""),
            instructions=payload.get("instructions", ""),
            runtime=payload.get("runtime") or factory.default_runtime,
            model=payload.get("model") or factory.default_model,
            skills=payload.get("skills", []),
            connector_types=payload.get("connector_types", []),
            knowledge_scope=payload.get("knowledge_scope", []),
            provider_id=payload.get("provider_id") or None,
            effort=payload.get("effort") or None,
            kind="standard",
            resource_policy="explicit",
            inherit_global_instructions=payload.get("inherit_global_instructions", True),
            permission_mode=payload.get("permission_mode") or "full_access",
            avatar=payload.get("avatar") or None,
            # Preserve the established ``custom`` provenance for direct
            # creates/imports. Copy explicitly requests the newer ``user``
            # provenance below; neither value grants system identity.
            source=payload.get("_source") or "custom",
        )
        # Live-reference: sessions snapshot the row at creation time, so a
        # fresh agent needs no extra materialization step.
        #
        # Underscore-prefixed keys are private to this call and must not ride
        # along: the mutation crosses a process boundary into a target whose
        # schema rejects fields it does not declare. ``_source`` is exactly
        # that case — it travels as the declared ``source``, read back off the
        # row so the two can never disagree. (The Valurion path above already
        # passes ``source`` explicitly for the same reason.)
        authority = await _before_managed_agent_mutation(
            user_id,
            {
                "operation": "upsert",
                "slug": slug,
                **{key: value for key, value in payload.items() if not key.startswith("_")},
                "source": row.source,
            },
            idempotency_key=payload.get("idempotency_key"),
        )
        if authority.resource_id:
            row.id = authority.resource_id
        created = await self._agents.create(user_id, row)
        canonical = authority.normalized.get("patch")
        if authority.cloud_committed and isinstance(canonical, dict):
            canonical_fields = {
                key: value
                for key, value in canonical.items()
                if key in {
                    "name",
                    "description",
                    "instructions",
                    "runtime",
                    "model",
                    "skills",
                    "connector_types",
                    "knowledge_scope",
                    "provider_id",
                    "effort",
                    "resource_policy",
                    "inherit_global_instructions",
                    "permission_mode",
                    "avatar",
                }
            }
            if canonical_fields:
                created = (
                    await self._agents.update_fields(user_id, slug, canonical_fields)
                    or created
                )
        # A cloud-first port already committed the mutation. Calling the old
        # after-save hook in that case would create a reverse upload/dual
        # writer. OSS local-pass-through keeps the legacy hook unchanged.
        if not authority.cloud_committed:
            await _after_agent_saved_hook(self._db, user_id, created, "created")
        return created

    async def update_agent(self, user_id: str, slug: str, patch: dict[str, Any]) -> AgentRow:
        """Patch an agent's editable fields. Official agents are editable too —
        the `readonly` flag is preserved on the row for provenance but no longer
        gates updates. Deletion is still restricted by `deletable` in
        `delete_agent` below."""
        # Fetch existing row to surface 404 before mutation.
        existing = await self._agents.get_agent(user_id, slug)
        if existing is None:
            raise AgentNotFoundError(slug)

        standard_allowed = {
            "name",
            "description",
            "instructions",
            "runtime",
            "model",
            "skills",
            "connector_types",
            "knowledge_scope",
            "inherit_global_instructions",
            "permission_mode",
            "provider_id",
            "effort",
            "avatar",
        }
        system_allowed = {"runtime", "model", "provider_id", "effort"}
        allowed = system_allowed if existing.kind == "system" else standard_allowed
        attempted = {key for key in patch if key not in allowed}
        if existing.kind == "system" and attempted:
            raise AgentManagedFieldError(slug, attempted)
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        # provider_id is nullable and clearable: when explicitly present in the
        # patch (even as None/""), apply it — None unbinds the default provider.
        if "provider_id" in patch and "provider_id" in allowed:
            fields["provider_id"] = patch["provider_id"] or None
        # effort is nullable and clearable the same way — None means "no
        # override" (the runtime falls through to its SDK default).
        if "effort" in patch and "effort" in allowed:
            fields["effort"] = patch["effort"] or None
        # avatar is nullable and clearable — None / "" unsets the avatar.
        if "avatar" in patch and "avatar" in allowed:
            fields["avatar"] = patch["avatar"] or None
        authority = await _before_managed_agent_mutation(
            user_id,
            {"operation": "upsert", "slug": slug, "resource_id": existing.id, "patch": fields},
            expected_etag=None,
        )
        normalized = authority.normalized.get("patch")
        if isinstance(normalized, dict):
            fields.update({key: value for key, value in normalized.items() if key in allowed})
        row = await self._agents.update_fields(user_id, slug, fields)
        if row is None:
            raise AgentNotFoundError(slug)
        # Live-reference semantics need no kernel cascade anymore: sessions
        # snapshot the row's fields at creation, so every NEW session (in any
        # project the agent is deployed to) picks the edit up automatically.
        if not authority.cloud_committed:
            await _after_agent_saved_hook(self._db, user_id, row, "updated")
        return row

    async def delete_agent(self, user_id: str, slug: str, *, cascade: bool = False) -> None:
        # Official and custom agents are equally deletable now — the only block
        # is the live派驻 guard below. seed_official_agents is insert-if-absent,
        # so deleted defaults simply won't come back unless the user wipes DB.
        existing = await self._agents.get_agent(user_id, slug)
        if existing is None:
            raise AgentNotFoundError(slug)
        # Protected base agents (default-assistant) opt out of deletion.
        if not existing.deletable:
            raise AgentNotDeletableError(slug)
        # v2 派驻 guard: an agent referenced by project members can't be deleted
        # outright — that would orphan those members. Two modes:
        #   cascade=False (default) — block and tell the caller to 解除派驻 first
        #     (keeps the API safe for non-interactive callers).
        #   cascade=True — the confirmed-delete path: 解除 every 派驻 first, then
        #     delete, so the user doesn't have to hunt down each project by hand.
        deployments = await self._members.list_by_source_agent_slug(user_id, existing.slug)
        if deployments and not cascade:
            raise AgentStillDeployedError(slug, len(deployments))
        authority = await _before_managed_agent_mutation(
            user_id,
            {"operation": "delete", "slug": slug, "resource_id": existing.id},
            expected_etag=None,
        )
        if deployments:
            for m in deployments:
                await self._members.delete(user_id, m.project_id, m.agent_slug)
        if not authority.cloud_committed:
            await _before_agent_delete_hook(self._db, user_id, existing)
        if not await self._agents.delete(user_id, slug):
            raise AgentNotFoundError(slug)
        await self._cleanup_marketplace_install(user_id, slug)

    async def copy_agent(
        self,
        user_id: str,
        slug: str,
        *,
        name: str | None = None,
        new_slug: str | None = None,
    ) -> AgentRow:
        """Copy one Agent without copying identity, ownership, or secrets.

        new_slug is the caller's chosen handle for the copy; omitted, the
        slug is derived from the new name as before. It goes through the same
        validation as any caller-supplied slug on create.
        """
        source = await self.get_agent(user_id, slug)
        is_valurion = source.kind == "system" and source.slug == VALURION_SLUG
        if is_valurion:
            payload: dict[str, Any] = {
                "name": name or f"{VALURION_NAME} Copy",
                "description": VALURION_DESCRIPTION,
                "instructions": "",
                "runtime": source.runtime,
                "model": source.model,
                "effort": source.effort,
                "provider_id": None,
                "skills": [],
                "connector_types": [],
                "knowledge_scope": [],
                "inherit_global_instructions": True,
                "permission_mode": source.permission_mode,
                "avatar": source.avatar,
                "_source": "user",
            }
        else:
            payload = {
                "name": name or f"{source.name} Copy",
                "description": source.description,
                "instructions": source.instructions,
                "runtime": source.runtime,
                "model": source.model,
                "provider_id": source.provider_id,
                "effort": source.effort,
                "skills": list(source.skills or []),
                "connector_types": list(source.connector_types or []),
                "knowledge_scope": list(source.knowledge_scope or []),
                "inherit_global_instructions": source.inherit_global_instructions,
                "permission_mode": source.permission_mode,
                "avatar": source.avatar,
                "_source": "user",
            }
        if new_slug:
            payload["slug"] = new_slug
        return await self.create_agent(user_id, payload)

    async def resolve_effective_resources(
        self,
        user_id: str,
        slug: str,
    ) -> Any:
        """Resolve Valurion's current read-only resource view."""
        row = await self.get_agent(user_id, slug)
        if row.resource_policy != "all_available":
            raise ValueError(f"agent '{row.slug}' uses explicit resources, not all_available")
        from valuz_agent.modules.agents.effective_resources import (
            EffectiveResourceResolver,
            current_execution_supports_stdio,
        )

        return await EffectiveResourceResolver.from_session(self._db).resolve(
            user_id,
            runtime=row.runtime,
            supports_stdio=current_execution_supports_stdio(),
        )

    async def _cleanup_marketplace_install(self, user_id: str, slug: str) -> None:
        """Best-effort marketplace provenance cleanup for a deleted agent —
        see the identical hook in ``modules/skills/service.py`` for the
        rationale. Never blocks the delete itself (a narrow-schema test
        engine without the ``marketplace_install`` table, or any storage
        hiccup, is swallowed)."""
        try:
            from valuz_agent.modules.marketplace.install_store import MarketplaceInstallStore

            await MarketplaceInstallStore(self._db).remove_by_ref(user_id, slug)
        except Exception:  # noqa: BLE001 — best-effort; missing provenance is harmless
            logger.warning("marketplace install cleanup failed for agent %s", slug, exc_info=True)

    # ------------------------------------------------------------------
    # Member list
    # ------------------------------------------------------------------

    async def list_deployments(self, user_id: str, slug: str) -> list[dict[str, Any]]:
        """List every派驻 of an agent — the projects it's deployed into.

        Powers the agent detail page's「派驻于 N 个项目」panel + the delete-guard
        UX. Returns ``[{project_id, agent_slug}]`` (the project-local handle);
        the frontend resolves project display names from its own store. Empty
        when the agent has never been deployed (no shared kernel config yet).
        """
        row = await self.get_agent(user_id, slug)
        members = await self._members.list_by_source_agent_slug(user_id, row.slug)
        return [{"project_id": m.project_id, "agent_slug": m.agent_slug} for m in members]

    async def list_members(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        """Return members with their resolved kernel agent summary.

        Each item: {member: ProjectMemberRow, agent: AgentConfig | None}
        Kernel load failures are surfaced as agent=None so the list still
        returns even when a kernel row is missing.
        """
        members = await self._members.list_by_project(user_id, project_id)
        result: list[dict[str, Any]] = []
        for m in members:
            try:
                agent = None
                if m.source_agent_slug:
                    src_row = await self._agents.get_agent(user_id, m.source_agent_slug)
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
        user_id: str,
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
        from valuz_agent.modules.agents.slug import (
            derive_slug,
            ensure_unique_slug,
            is_valid_slug,
        )

        source_agent = await self.get_agent(user_id, source_agent_slug)

        # Project-local handle: derive from the source agent's display name,
        # unique within THIS project (CJK-preserving). The handle is a
        # per-project path component; the underlying agent is shared.
        agent_slug = (agent_slug or "").strip()
        if not agent_slug:
            taken = {m.agent_slug for m in await self._members.list_by_project(user_id, project_id)}
            agent_slug = ensure_unique_slug(derive_slug(source_agent.name), taken)
        elif not is_valid_slug(agent_slug):
            raise InvalidAgentSlugError(agent_slug)

        if await self._members.get(user_id, project_id, agent_slug) is not None:
            raise MemberAlreadyExistsError(
                f"agent '{agent_slug}' already exists in project '{project_id}'"
            )

        # v2 dedup: ONE派驻 per agent per project (live reference — deploying
        # the same agent twice into one project is meaningless). Keyed on the
        # source library slug. Skipped for the automation runner (``dedupe``).
        if dedupe:
            existing_members = await self._members.list_by_project(user_id, project_id)
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
        await self._members.create(user_id, member)

        agent = await self.build_agent_config(source_agent)
        return {"member": member, "agent": agent}

    # ------------------------------------------------------------------
    # Create blank agent (no source agent)
    # ------------------------------------------------------------------

    async def create_blank_agent(
        self,
        user_id: str,
        project_id: str,
        agent_slug: str | None,
        name: str,
        instructions: str,
        description: str = "",
        runtime: str | None = None,
        model: str | None = None,
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
            user_id,
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
            },
        )
        return await self.deploy_agent(
            user_id,
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

    async def delete_member(self, user_id: str, project_id: str, agent_slug: str) -> None:
        """解除派驻: delete ONLY the membership row.

        v2 live-reference: the kernel ``AgentConfig`` is SHARED across projects,
        so undeploying must NOT delete it (other projects may still派驻 it). The
        agent itself lives on in the library;真删 happens via ``delete_agent``.
        """
        member = await self._members.get(user_id, project_id, agent_slug)
        if member is None:
            raise MemberNotFoundError(agent_slug)

        await self._members.delete(user_id, project_id, agent_slug)

        # Undeploying the project's default lead leaves the pointer dangling.
        # Readers fall through it, so this is hygiene rather than correctness —
        # but leaving it set makes the project page advertise a lead that is no
        # longer on the team. Best-effort on purpose: the membership row is
        # already gone, so failing here would report a failed undeploy for an
        # operation that actually succeeded.
        from valuz_agent.modules.projects.service import clear_default_lead_if

        try:
            await clear_default_lead_if(user_id, project_id, agent_slug)
        except Exception:  # noqa: BLE001 — cleanup must not fail the undeploy
            logger.warning(
                "failed to clear default lead after undeploying %s from %s",
                agent_slug,
                project_id,
                exc_info=True,
            )
