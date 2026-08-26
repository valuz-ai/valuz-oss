"""Agent Pack service — the single import path for built-in packs (the
recommended teams) and, later, user-supplied packs.

``import_manifest`` is the generalization of the old ``add_template``: it
materializes the pack's bundled/embedded local skills, then idempotently creates
the pack's agents in the user's library (de-dup by fixed slug). SkillHub
dependencies are installed by the Marketplace service before it calls this
service. The concrete
``(runtime, provider_id, model)`` is resolved by the caller (the route, reusing
onboarding's resolver) and applied to every agent — packs stay runtime-neutral
(their per-agent ``runtime`` / ``model_hint`` are display hints), so an agent
always lands on the user's working channel. Each agent's recommended ``effort``
is honored, falling back to the deploy default.

Deploying the resulting agents into a project (where they regroup into a team)
is the existing, separate flow — onboarding layers it on top of this import.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from valuz_agent.integrations.skills_official_bootstrap import materialize_template_skills
from valuz_agent.modules.agent_packs.errors import PackImportFailed, PackNotFound
from valuz_agent.modules.agent_packs.loader import load_builtin_pack, load_builtin_packs
from valuz_agent.modules.agent_packs.manifest import (
    AgentPackManifest,
    PackAgent,
    PackCollection,
    PackConnector,
    PackSkill,
    resolve_text,
)
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.packs_common import (
    PackManifest,
    build_archive,
    embedded_skill_dir,
    extract_archive,
    sanitize_skill_slug,
)

logger = logging.getLogger(__name__)
_TEAM_COORDINATION_MARKER = "## 团队协作（Lead）"

# Staged uploads between preview and confirm: preview_id → (manifest, temp root).
# The temp root holds extracted embedded skills; confirm consumes + cleans it.
_pack_import_stage: dict[str, tuple[PackManifest, Path]] = {}


class AgentPackService:
    """Reads built-in pack manifests; delegates agent creation to the agents
    module's service (cross-module via its public API, never its datastore)."""

    def __init__(self, agent_service: AgentService) -> None:
        self._agents = agent_service

    # -- read -------------------------------------------------------------

    async def list_packs(self, user_id: str) -> list[dict[str, Any]]:
        """Every built-in pack as a localized DTO, annotated with library state
        (``in_library`` per agent, ``added`` per pack)."""
        present = {a.slug for a in await self._agents.list_agents(user_id)}
        return [self._to_dto(m, present) for m in load_builtin_packs()]

    async def get_pack(self, user_id: str, pack_id: str) -> dict[str, Any]:
        manifest = load_builtin_pack(pack_id)
        if manifest is None:
            raise PackNotFound()
        present = {a.slug for a in await self._agents.list_agents(user_id)}
        return self._to_dto(manifest, present)

    # -- write ------------------------------------------------------------

    async def import_pack(
        self,
        user_id: str,
        pack_id: str,
        *,
        runtime: str,
        provider_id: str,
        model: str,
        effort: str | None,
    ) -> dict[str, Any]:
        """Idempotently import a built-in pack's agents into the user's library.
        Raises ``PackNotFound`` for an unknown id."""
        manifest = load_builtin_pack(pack_id)
        if manifest is None:
            raise PackNotFound()
        return await self.import_manifest(
            user_id,
            manifest,
            runtime=runtime,
            provider_id=provider_id,
            model=model,
            effort=effort,
        )

    async def import_manifest(
        self,
        user_id: str,
        manifest: AgentPackManifest | PackManifest,
        *,
        runtime: str,
        provider_id: str,
        model: str,
        effort: str | None,
        embedded_skills_root: Path | None = None,
    ) -> dict[str, Any]:
        """Install the pack's skills, register its connectors, then create its
        agents (de-dup by slug). Returns ``{pack_id, created, skipped, roles}``.

        Accepts both the unified :class:`PackManifest` (user imports / project
        packs) and the legacy v1 :class:`AgentPackManifest` (built-in packs):
        the install path only reads the shared ``agents`` / ``skills`` /
        ``connectors`` / ``collection`` fields, which both models carry.

        ``embedded_skills_root`` is the extracted archive root for a user-imported
        pack — ``embedded`` skills are copied from ``<root>/skills/<slug>/`` into
        the user skill library. Built-in packs pass ``None`` (their skills are
        ``bundled`` and materialize from the app's vendored tree)."""
        # Materialize bundled skills (deferred from boot so an un-imported pack's
        # skills don't clutter the library). Idempotent + off the event loop.
        bundled = [s.slug for s in manifest.skills if s.source == "bundled"]
        if bundled:
            await asyncio.to_thread(materialize_template_skills, bundled, user_id=user_id)

        # Install embedded (user-authored) skills carried inside the archive.
        embedded = [s.slug for s in manifest.skills if s.source == "embedded"]
        if embedded and embedded_skills_root is not None:
            await asyncio.to_thread(
                self._install_embedded_skills,
                embedded_skills_root,
                embedded,
                user_id=user_id,
            )

        # Index the just-installed skills NOW so the pack's agents resolve them
        # immediately. Without this they're only picked up by the next boot scan
        # or the periodic auto-scan (≤30 min), so a session started right after
        # the team is created drops the skill ("Unknown skill" — the skill is on
        # disk but not yet in valuz_skill_index, which resolve_skill_slugs_to_paths
        # reads). Both scopes are covered: ``bundled`` skills land in the official
        # library, ``embedded`` skills in the user library.
        if bundled:
            try:
                from valuz_agent.modules.skills.service import reindex_official_skills
                from valuz_agent.ports.skill_lifecycle import get_skill_lifecycle_hook

                await reindex_official_skills(user_id)
                await get_skill_lifecycle_hook().after_bundled_skills_materialized(
                    user_id=user_id,
                    slugs=tuple(bundled),
                )
            except Exception:  # noqa: BLE001 — best-effort; the boot scan backstops it
                logger.exception(
                    "reindex or lifecycle notification after pack import failed"
                )
        if embedded:
            try:
                from valuz_agent.modules.skills.service import reindex_user_skills

                await reindex_user_skills(user_id)
            except Exception:  # noqa: BLE001 — best-effort; the boot scan backstops it
                logger.exception("reindex_user_skills after pack import failed")

        # Register the pack's connectors as the user's own ConnectorRows so they
        # activate on import (and actually resolve at session time — the runtime
        # reads ConnectorRow, never the catalog). Deferred from the default
        # catalog so an un-imported team's connectors don't clutter the list.
        await self._register_connectors(user_id, manifest)

        existing = {a.slug: a for a in await self._agents.list_agents(user_id)}
        roles: list[Any] = []
        created = 0
        skipped = 0
        for index, agent in enumerate(manifest.agents):
            instructions = self._instructions_for_import(manifest, agent, index)
            if agent.slug in existing:
                row = existing[agent.slug]
                if (
                    index == 0
                    and instructions
                    and _TEAM_COORDINATION_MARKER not in (row.instructions or "")
                ):
                    row = await self._agents.update_agent(
                        user_id,
                        agent.slug,
                        {
                            "instructions": self._with_team_coordination(
                                manifest,
                                row.instructions or instructions,
                            )
                        },
                    )
                roles.append(row)
                skipped += 1
                continue
            row = await self._agents.create_agent(
                user_id,
                {
                    "slug": agent.slug,
                    "name": resolve_text(agent.name),
                    "description": resolve_text(agent.description),
                    "instructions": instructions,
                    "runtime": runtime,
                    "model": model,
                    "provider_id": provider_id,
                    # Honor the pack's recommended effort; fall back to the
                    # deploy default when the pack leaves it unset.
                    "effort": agent.effort or effort,
                    "skills": list(agent.skills),
                    "connector_types": list(agent.connectors),
                    "avatar": agent.avatar,
                },
            )
            roles.append(row)
            created += 1

        pack_id = manifest.collection.id if manifest.collection else "agent-pack"
        logger.info("agent-packs: import %r — created=%d skipped=%d", pack_id, created, skipped)
        return {
            "template_id": pack_id,
            "created": created,
            "skipped": skipped,
            "roles": roles,
        }

    def _instructions_for_import(
        self,
        manifest: AgentPackManifest | PackManifest,
        agent: PackAgent,
        index: int,
    ) -> str:
        base = resolve_text(agent.instructions)
        if index != 0 or len(manifest.agents) < 2:
            return base
        return self._with_team_coordination(manifest, base)

    def _with_team_coordination(
        self,
        manifest: AgentPackManifest | PackManifest,
        base: str,
    ) -> str:
        if _TEAM_COORDINATION_MARKER in base:
            return base

        team_name = resolve_text(manifest.collection.name) if manifest.collection else "Agent Team"
        member_lines = []
        workflow_lines = []
        for idx, member in enumerate(manifest.agents, start=1):
            name = resolve_text(member.name)
            role = resolve_text(member.description) or "完成对应阶段任务"
            label = "Lead" if idx == 1 else "Member"
            member_lines.append(f"- {name}（{label}）：{role}")
            workflow_lines.append(f"{idx}. {name}：{role}")
        workflow_lines.append(
            f"{len(manifest.agents) + 1}. Lead 汇总交付：整合成员产出、处理冲突和缺口，"
            "形成最终答复或项目成果。"
        )

        section = "\n".join(
            [
                "",
                "",
                _TEAM_COORDINATION_MARKER,
                f"你是「{team_name}」的 Lead。这个 Team 安装后会作为多个 Agent "
                "一起派驻到同一个 Project 中工作。你的职责不是独自完成所有任务，"
                "而是组织团队完成工作流。",
                "",
                "### Lead 职责",
                "1. 先澄清用户目标、输入材料、约束条件和交付标准。",
                "2. 根据成员分工拆解任务，并把对应阶段交给合适成员处理。",
                "3. 阅读并整合成员产出；发现冲突、缺口或风险时，要求补充或复核。",
                "4. 最终对用户输出统一结论、过程说明、关键依据和下一步建议。",
                "",
                "### 团队成员分工",
                *member_lines,
                "",
                "### 标准协作流程",
                *workflow_lines,
                "",
                "### 协作边界",
                "如果当前 Project 中某个成员尚未派驻，先说明缺口，再用已有成员完成可覆盖部分。",
                "不要假装已经调用或获得了其他成员的产出；必须基于项目上下文中真实可见的信息整合。",
            ]
        )
        return f"{base}{section}"

    # -- export -----------------------------------------------------------

    async def export_agents(
        self,
        user_id: str,
        agent_slugs: list[str],
        *,
        collection: dict[str, Any] | None = None,
    ) -> bytes:
        """Build a ``.valuzpack`` archive for the given library agents.

        Carries each agent's portable definition (``provider_id`` dropped,
        ``model`` demoted to ``model_hint``), embeds user-authored skills,
        references bundled ones, and includes each connector's definition with
        all secrets stripped. Raises ``AgentNotFoundError`` for an unknown slug.
        """
        agents = [await self._agents.get_agent(user_id, slug) for slug in agent_slugs]
        manifest, skill_dirs = await self._build_export_manifest(user_id, agents, collection)
        return build_archive(manifest, skill_dirs)

    async def _build_export_manifest(
        self,
        user_id: str,
        agents: list[Any],
        collection: dict[str, Any] | None,
    ) -> tuple[PackManifest, dict[str, Path]]:
        from valuz_agent.adapters.capability_resolver import resolve_skill_slugs_to_paths

        pack_agents: list[PackAgent] = []
        skill_slugs: list[str] = []
        connector_slugs: list[str] = []
        for a in agents:
            pack_agents.append(self.pack_agent_from_row(a))
            for s in a.skills or []:
                if s not in skill_slugs:
                    skill_slugs.append(s)
            for c in a.connector_types or []:
                if c not in connector_slugs:
                    connector_slugs.append(c)

        # Skills: embed every skill that's on disk as files, so the pack is
        # self-contained on any install — a recipient may not have a "bundled"
        # skill (team skills only materialize when that team is imported). Only
        # skills we genuinely can't find degrade to a bundled reference.
        skills_idx: list[PackSkill] = []
        skill_dirs: dict[str, Path] = {}
        for slug in skill_slugs:
            # Resolve with the raw slug (that's how it's indexed locally) but
            # emit a sanitized slug everywhere portable — the archive dir, the
            # manifest, and the agent references — so a path-shaped local slug
            # never reaches the recipient.
            paths = await resolve_skill_slugs_to_paths([slug], None, user_id=user_id)
            path = Path(paths[0]).resolve() if paths else None
            clean = sanitize_skill_slug(slug)
            if path is not None and path.is_dir():
                skills_idx.append(PackSkill(slug=clean, source="embedded"))
                skill_dirs[clean] = path
            else:
                # Not on disk — reference by slug; the importer surfaces it as
                # missing if the recipient doesn't have it either.
                logger.warning("agent-packs: skill %s not on disk, exporting as a reference", slug)
                skills_idx.append(PackSkill(slug=clean, source="bundled"))

        # Connectors: full definition, secrets stripped.
        views = {v.slug: v for v in await self._agents._connectors.list_connectors(user_id)}
        conns_idx: list[PackConnector] = []
        for slug in connector_slugs:
            v = views.get(slug)
            if v is None:
                continue  # not registered for this user — can't export a definition
            needs_creds = bool(
                getattr(v, "has_api_key", False)
                or v.auth_type in ("bearer", "oauth")
                or any(getattr(h, "secret", False) for h in (v.headers or []))
                or any(getattr(p, "secret", False) for p in (v.params or []))
            )
            conns_idx.append(
                PackConnector(
                    slug=v.slug,
                    source="custom",
                    display_name=v.display_name,
                    description=v.description,
                    transport=v.transport,
                    auth_type=v.auth_type,
                    requires_credentials=needs_creds,
                    url=v.url,
                    command=v.command,
                    args=list(v.args) if v.args else None,
                )
            )

        # A collection header is always present (one manifest shape). The caller
        # supplies the group name; for a bare single-agent export with none, we
        # fall back to that agent's name — "a group of one". No meaningless id.
        col = collection or {}
        default_name = agents[0].name if agents else "Exported agents"
        collection_obj = PackCollection(
            id=col.get("id"),
            name=col.get("name") or default_name,
            description=col.get("description") or "",
            scenario=col.get("scenario") or "",
            icon=col.get("icon"),
        )
        manifest = PackManifest(
            collection=collection_obj,
            agents=pack_agents,
            skills=skills_idx,
            connectors=conns_idx,
        )
        return manifest, skill_dirs

    def pack_agent_from_row(self, row: Any) -> PackAgent:
        """Build a portable :class:`PackAgent` snapshot from a library
        ``AgentRow``.

        Drops ``provider_id`` (machine-local), demotes ``model``→
        ``model_hint`` (the concrete ``(provider_id, model)`` is re-resolved
        against the recipient's channel at import time), and sanitizes each
        carried skill slug to a single safe archive segment. The single
        source of truth for this projection — ``_build_export_manifest`` and
        ``ProjectPackService`` both call it so the agent-pack and
        project-pack shapes never drift.
        """
        return PackAgent(
            slug=row.slug,
            name=row.name,
            description=row.description or "",
            instructions=row.instructions or "",
            avatar=row.avatar,
            runtime=row.runtime,
            model_hint=row.model or None,
            effort=row.effort,
            skills=[sanitize_skill_slug(s) for s in (row.skills or [])],
            connectors=list(row.connector_types or []),
        )

    # -- import (upload → preview → confirm) ------------------------------

    async def preview_import(self, user_id: str, data: bytes) -> dict[str, Any]:
        """Parse + stage an uploaded ``.valuzpack``; return what it contains and
        how it lands (already-in-library agents, connectors needing setup)."""
        try:
            manifest, root = extract_archive(data)
        except Exception as exc:
            raise PackImportFailed(str(exc)) from exc

        # This endpoint imports an agent collection. A project pack (carries a
        # ``project`` target, no ``collection``) belongs to the project import
        # flow — reject it clearly instead of half-importing its agents.
        if manifest.collection is None:
            shutil.rmtree(root, ignore_errors=True)
            raise PackImportFailed(
                "this is a project pack — import it from the projects page, not here"
            )

        preview_id = f"pack-{uuid4().hex[:8]}"
        _pack_import_stage[preview_id] = (manifest, root)

        present = {a.slug for a in await self._agents.list_agents(user_id)}
        existing_conn = {v.slug for v in await self._agents._connectors.list_connectors(user_id)}
        col = manifest.collection
        return {
            "preview_id": preview_id,
            "collection": {
                "id": col.id or "",
                "name": resolve_text(col.name),
                "description": resolve_text(col.description),
                "scenario": resolve_text(col.scenario),
                "icon": col.icon or "",
            },
            "agents": [
                {
                    "slug": a.slug,
                    "name": resolve_text(a.name),
                    "description": resolve_text(a.description),
                    "in_library": a.slug in present,
                }
                for a in manifest.agents
            ],
            "skills": [{"slug": s.slug, "source": s.source} for s in manifest.skills],
            "connectors": [
                {
                    "slug": c.slug,
                    "display_name": resolve_text(c.display_name) or c.slug,
                    "requires_credentials": c.requires_credentials,
                    "requires_setup": c.requires_setup,
                    "already_present": c.slug in existing_conn,
                }
                for c in manifest.connectors
            ],
        }

    async def confirm_import(
        self,
        user_id: str,
        preview_id: str,
        *,
        runtime: str,
        provider_id: str,
        model: str,
        effort: str | None,
    ) -> dict[str, Any]:
        """Commit a staged import: install skills, register connectors, create
        agents. Returns the import result plus the connectors that still need
        the user's attention (key / local setup)."""
        staged = _pack_import_stage.pop(preview_id, None)
        if staged is None:
            raise PackImportFailed("import preview expired or already used — re-upload the pack")
        manifest, root = staged
        try:
            result = await self.import_manifest(
                user_id,
                manifest,
                runtime=runtime,
                provider_id=provider_id,
                model=model,
                effort=effort,
                embedded_skills_root=root,
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

        result["connectors_to_configure"] = [
            {
                "slug": c.slug,
                "display_name": resolve_text(c.display_name) or c.slug,
                "requires_credentials": c.requires_credentials,
                "requires_setup": c.requires_setup,
            }
            for c in manifest.connectors
            if c.requires_credentials or c.requires_setup
        ]
        return result

    # -- internal ---------------------------------------------------------

    def _install_embedded_skills(self, root: Path, slugs: list[str], *, user_id: str) -> None:
        """Copy embedded skills from an extracted archive into the user skill
        library (``~/.agents/skills/``). Existing slugs are left untouched —
        importing never clobbers a skill the user already authored."""
        from valuz_agent.infra.fs_registry import fs_registry

        dest_root = fs_registry.user_skill_root(user_id=user_id)
        dest_root.mkdir(parents=True, exist_ok=True)
        for slug in slugs:
            src = embedded_skill_dir(root, slug)
            if src is None:
                continue
            dest = dest_root / slug
            if dest.exists():
                continue
            try:
                shutil.copytree(src, dest)
                logger.info("agent-packs: installed embedded skill %s", slug)
            except Exception:  # noqa: BLE001 — one bad skill shouldn't sink the import
                logger.exception("agent-packs: failed to install embedded skill %s", slug)

    async def _register_connectors(
        self, user_id: str, manifest: AgentPackManifest | PackManifest
    ) -> None:
        """Register the pack's connectors as the user's ConnectorRows (idempotent
        by slug). These are deferred from the default catalog, so importing the
        team is what makes them appear and resolve. Best-effort: a bad connector
        is logged and skipped, it doesn't sink the import.

        Credentials are never carried by the pack — a connector that needs a key
        (``requires_credentials``) lands with none and surfaces in the connectors
        page for the user to fill in.
        """
        connectors = manifest.connectors
        connector_svc = self._agents._connectors  # the AgentService's ConnectorService
        if not connectors or connector_svc is None:
            return
        existing = {v.slug for v in await connector_svc.list_connectors(user_id)}
        for conn in connectors:
            if conn.slug in existing:
                continue
            try:
                await connector_svc.create_connector(
                    user_id,
                    slug=conn.slug,
                    display_name=resolve_text(conn.display_name) or conn.slug,
                    transport=conn.transport,  # type: ignore[arg-type]
                    description=resolve_text(conn.description) or None,
                    connector_type="custom",
                    url=conn.url,
                    auth_type=conn.auth_type,  # type: ignore[arg-type]
                    command=conn.command,
                    args=conn.args,
                )
                logger.info("agent-packs: registered connector %s", conn.slug)
            except Exception:  # noqa: BLE001 — one bad connector shouldn't sink the import
                logger.exception("agent-packs: failed to register connector %s", conn.slug)

    def _to_dto(self, manifest: AgentPackManifest, present: set[str]) -> dict[str, Any]:
        """Localize a manifest into the response DTO consumed by the panel.

        Field names mirror the legacy agent-template response (``connector_types``,
        ``scenario``, ``added``) so the existing frontend keeps working.
        """
        collection = manifest.collection
        roles = [
            {
                "slug": agent.slug,
                "name": resolve_text(agent.name),
                "description": resolve_text(agent.description),
                "instructions": resolve_text(agent.instructions),
                "avatar": agent.avatar or "",
                "skills": list(agent.skills),
                "connector_types": list(agent.connectors),
                "runtime": agent.runtime,
                "effort": agent.effort,
                "in_library": agent.slug in present,
            }
            for agent in manifest.agents
        ]
        return {
            # built-in packs always carry a stable id (the :add path id);
            # fall back to the localized name only for collection-id-less packs.
            "id": collection.id or resolve_text(collection.name) or "agent-pack",
            "scenario": resolve_text(collection.scenario),
            "name": resolve_text(collection.name),
            "description": resolve_text(collection.description),
            "icon": collection.icon or "",
            "added": all(agent.slug in present for agent in manifest.agents),
            "roles": roles,
            "skills": [{"slug": s.slug, "source": s.source} for s in manifest.skills],
        }
