"""ProjectPackService — export/import a project as a ``.valuzpack`` archive
(the unified pack format, ``project`` target).

Mirrors ``AgentPackService`` (export / preview / confirm) and reuses its
``pack_agent_from_row`` to build the per-agent portable snapshot, plus
``import_manifest`` to install the project's agents, skills and
connectors on the recipient (de-duped by slug, re-resolved against the
recipient's channel). On top of that this service handles the project
row itself, project members, automations, project-skill paths, and the
on-disk project memory directory.

Cross-machine portability contract:
  - ``id`` / ``root_path`` / ``provider_id`` are dropped (machine-local).
  - ``model`` is demoted to ``model_hint``.
  - connector definitions carry only their non-secret fields.
  - project memory is carried as a ``memory/`` file tree.
  - import regenerates a fresh project id, remaps the memory dir to it,
    reuses (or recreates) the library agents by slug, preserves each
    member's project-local ``agent_slug`` handle so automations resolve,
    and SKIPS a name-collision (never overwrites).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from valuz_agent.adapters.capability_resolver import resolve_skill_slugs_to_paths
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.agent_packs.manifest import (
    PackCollection,
    PackConnector,
    PackSkill,
    resolve_text,
)
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.automations.service import AutomationService
from valuz_agent.modules.packs_common import (
    PackAutomation,
    PackManifest,
    PackMember,
    PackProject,
    PackProjectConnector,
    PackProjectSkillConfig,
    build_archive,
    extract_archive,
    sanitize_skill_slug,
)
from valuz_agent.modules.project_packs.errors import (
    ProjectNotExportable,
    ProjectPackImportFailed,
    ProjectPackNotFound,
)
from valuz_agent.modules.projects.service import ProjectService

logger = logging.getLogger(__name__)

# Staged uploads between preview and confirm: preview_id → (manifest, temp
# root). The temp root holds extracted embedded skills + memory; confirm
# consumes + cleans it. Mirrors AgentPackService._pack_import_stage.
_project_import_stage: dict[str, tuple[PackManifest, Path]] = {}


class ProjectPackService:
    """Reads/writes ``.valuzpack`` project archives. Constructor injects every
    service the export/import paths need so the module boundary stays
    clean (no sibling datastores imported)."""

    def __init__(
        self,
        project_service: ProjectService,
        agent_service: AgentService,
        agent_pack_service: AgentPackService,
        automation_service: AutomationService,
    ) -> None:
        self._projects = project_service
        self._agents = agent_service
        self._agent_packs = agent_pack_service
        self._automations = automation_service

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    async def export_project(self, user_id: str, project_id: str) -> bytes:
        """Build a ``.valuzpack`` archive for the given project.

        Chat projects are not exportable (they're ephemeral local
        scratch). Raises ``ProjectPackNotFound`` if the id is unknown and
        ``ProjectNotExportable`` for chat-kind rows.
        """
        try:
            row = await self._projects.get_project(user_id, project_id)
        except KeyError:
            row = None
        except Exception:  # noqa: BLE001 — treat any lookup failure as "not found"
            row = None
        if row is None:
            raise ProjectPackNotFound()
        if row.kind != "project":
            raise ProjectNotExportable()

        manifest = await self._build_export_manifest(user_id, row)
        # Resolve the on-disk memory dir; pass None when absent / empty so
        # the archive carries no ``memory/`` entries for projects that have
        # never written any memory yet.
        memory_dir = self._resolve_memory_dir(user_id, project_id)

        skill_dirs = await self._resolve_embedded_skill_dirs(user_id, manifest)
        return build_archive(manifest, skill_dirs, memory_dir)

    async def _build_export_manifest(self, user_id: str, project_row: Any) -> PackManifest:
        # --- members + the hoisted agent payload ---
        # Each member is a project-local ``agent_slug`` handle pointing at a
        # library agent; the agent *definition* lives once in the top-level
        # ``agents[]`` payload (de-duped by slug), so two handles onto the same
        # library agent cost one payload entry and two slim members.
        members_data = await self._agents.list_members(user_id, project_row.id)
        pack_members: list[PackMember] = []
        pack_agents: list[Any] = []
        seen_agent_slugs: set[str] = set()
        skill_slugs: list[str] = []
        connector_slugs: list[str] = []
        for entry in members_data:
            member = entry["member"]
            agent_row = None
            if member.source_agent_slug:
                try:
                    agent_row = await self._agents.get_agent(user_id, member.source_agent_slug)
                except Exception:  # noqa: BLE001 — source agent gone
                    logger.warning(
                        "project-pack: source agent %s missing for member %s",
                        member.source_agent_slug,
                        member.agent_slug,
                    )
            if agent_row is None:
                # Without a source snapshot the member isn't portable.
                continue
            pack_agent = self._agent_packs.pack_agent_from_row(agent_row)
            if pack_agent.slug not in seen_agent_slugs:
                seen_agent_slugs.add(pack_agent.slug)
                pack_agents.append(pack_agent)
            pack_members.append(
                PackMember(
                    agent_slug=member.agent_slug,
                    source_agent_slug=member.source_agent_slug or pack_agent.slug,
                )
            )
            for s in agent_row.skills or []:
                clean = sanitize_skill_slug(s)
                if clean not in skill_slugs:
                    skill_slugs.append(clean)
            for c in agent_row.connector_types or []:
                if c not in connector_slugs:
                    connector_slugs.append(c)

        # --- skills index (embed on-disk, reference bundled) ---
        skills_idx, skill_dirs_idx = await self._build_skills_index(user_id, skill_slugs)

        # --- connectors index (secrets stripped) ---
        conns_idx = await self._build_connectors_index(user_id, connector_slugs)

        # --- automations (flat port of valuz_automation columns) ---
        # ``list_automations_in_project`` returns the light
        # ``AutomationItemResponse`` shape which omits ``prompt_template``
        # (only the detail response carries it). Fetch each detail so the
        # prompt travels in the archive — without it every automation
        # would fail ``AutomationPromptEmpty`` on import.
        automations: list[PackAutomation] = []
        for a in await self._automations.list_automations_in_project(
            project_row.id, user_id=user_id
        ):
            detail = await self._automations.get_automation_detail(a.automation_id, user_id=user_id)
            trigger = detail.trigger
            cron_expr: str | None = None
            timezone: str | None = None
            interval_seconds: int | None = None
            trigger_kind = trigger.kind
            if trigger_kind == "cron":
                cron_expr = trigger.cron_expr  # type: ignore[union-attr]
                timezone = trigger.timezone  # type: ignore[union-attr]
            elif trigger_kind == "interval":
                interval_seconds = trigger.seconds  # type: ignore[union-attr]
            automations.append(
                PackAutomation(
                    name=detail.name,
                    agent_kind=detail.agent_kind,
                    agent_slug=detail.agent_slug,
                    prompt_template=detail.prompt_template,
                    action_kind=detail.action_kind,
                    trigger_kind=trigger_kind,
                    cron_expr=cron_expr,
                    timezone=timezone,
                    interval_seconds=interval_seconds,
                    status=detail.status,
                )
            )

        # --- project skill configs (paths) ---
        project_skills: list[PackProjectSkillConfig] = []
        for path in await self._enabled_project_skill_paths(user_id, project_row):
            project_skills.append(PackProjectSkillConfig(skill_path=path))

        # --- project connector configs (slugs) ---
        project_connectors: list[PackProjectConnector] = []
        try:
            for slug in await self._projects.get_connectors(user_id, project_row.id):
                project_connectors.append(PackProjectConnector(slug=slug))
        except (KeyError, RuntimeError):
            # ``get_connectors`` requires root_path; a project without a
            # bound root exports an empty connector list.
            pass

        project = PackProject(
            name=project_row.name,
            kind="project",
            icon=project_row.icon,
            instructions_md=project_row.instructions_md or "",
            members=pack_members,
            automations=automations,
            skills=project_skills,
            connectors=project_connectors,
        )
        return PackManifest(
            project=project,
            agents=pack_agents,
            skills=skills_idx,
            connectors=conns_idx,
        )

    # ------------------------------------------------------------------
    # Import (upload → preview → confirm)
    # ------------------------------------------------------------------

    async def preview_import(self, user_id: str, data: bytes) -> dict[str, Any]:
        """Parse + stage an uploaded ``.valuzpack`` project; return what it
        contains and how it lands. Performs NO DB writes."""
        try:
            manifest, root = extract_archive(data)
        except Exception as exc:
            raise ProjectPackImportFailed(str(exc)) from exc

        # This endpoint imports a project. An agent collection pack (carries a
        # ``collection`` target, no ``project``) belongs to the agents import
        # flow — reject it clearly instead of failing deep inside confirm.
        project = manifest.project
        if project is None:
            shutil.rmtree(root, ignore_errors=True)
            raise ProjectPackImportFailed(
                "this is an agent pack — import it from the agents page, not here"
            )

        preview_id = f"project-{uuid4().hex[:8]}"
        _project_import_stage[preview_id] = (manifest, root)

        present_agents = {a.slug for a in await self._agents.list_agents(user_id)}
        present_conns = (
            {v.slug for v in await self._agents._connectors.list_connectors(user_id)}
            if getattr(self._agents, "_connectors", None)
            else set()
        )

        name_conflict = await self._projects.get_by_name(user_id, project.name) is not None

        # Members are slim handles; resolve each to its agent snapshot in the
        # top-level ``agents[]`` payload (by source slug) for display.
        agent_by_slug = {a.slug: a for a in manifest.agents}
        members_preview = []
        for m in project.members:
            source_slug = m.source_agent_slug or m.agent_slug
            agent = agent_by_slug.get(source_slug)
            members_preview.append(
                {
                    "agent_slug": m.agent_slug,
                    "source_agent_slug": source_slug,
                    "name": resolve_text(agent.name) if agent else source_slug,
                    "description": resolve_text(agent.description) if agent else "",
                    "in_library": source_slug in present_agents,
                }
            )

        automations_preview = [
            {
                "name": a.name,
                "agent_slug": a.agent_slug,
                "trigger_kind": a.trigger_kind,
                "cron_expr": a.cron_expr,
                "interval_seconds": a.interval_seconds,
                "status": a.status,
            }
            for a in project.automations
        ]

        skills_preview = [{"slug": s.slug, "source": s.source} for s in manifest.skills]
        connectors_preview = [
            {
                "slug": c.slug,
                "display_name": resolve_text(c.display_name) or c.slug,
                "requires_credentials": c.requires_credentials,
                "requires_setup": c.requires_setup,
                "already_present": c.slug in present_conns,
            }
            for c in manifest.connectors
        ]

        return {
            "preview_id": preview_id,
            "project": {
                "name": project.name,
                "kind": project.kind,
                "icon": project.icon,
                "instructions_md": resolve_text(project.instructions_md),
            },
            "name_conflict": name_conflict,
            "members": members_preview,
            "automations": automations_preview,
            "project_skills": [s.skill_path for s in project.skills],
            "project_connectors": [c.slug for c in project.connectors],
            "skills": skills_preview,
            "connectors": connectors_preview,
            "has_memory": bool(project.memory),
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
        root_path: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Commit a staged import. ``name`` overrides the name carried by the
        pack, which is how the importer resolves a clash with a project they
        already own. If the resulting name is still taken, SKIP (don't create,
        don't overwrite) and return ``{status: "skipped_name_conflict"}``.
        """
        staged = _project_import_stage.pop(preview_id, None)
        if staged is None:
            raise ProjectPackImportFailed(
                "import preview expired or already used — re-upload the pack"
            )
        manifest, root = staged
        try:
            project = manifest.project
            if project is None:
                raise ProjectPackImportFailed(
                    "this is an agent pack — import it from the agents page, not here"
                )

            # The importer may rename on the way in; everything downstream
            # (the synthesized collection, the created project) uses the
            # effective name.
            effective_name = (name or "").strip() or project.name

            # Re-check name conflict at confirm time (a project created
            # between preview and confirm must not be overwritten).
            if await self._projects.get_by_name(user_id, effective_name) is not None:
                return {
                    "status": "skipped_name_conflict",
                    "project": None,
                    "project_name": effective_name,
                }

            # 1) Install skills + connectors + library agents de-duped by slug.
            #    Reuse the agent-pack install path by presenting the payload as
            #    a collection manifest (the agents already live in ``agents[]``).
            synth = PackManifest(
                collection=PackCollection(name=effective_name),
                agents=list(manifest.agents),
                skills=manifest.skills,
                connectors=manifest.connectors,
            )
            pack_result = await self._agent_packs.import_manifest(
                user_id,
                synth,
                runtime=runtime,
                provider_id=provider_id,
                model=model,
                effort=effort,
                embedded_skills_root=root,
            )

            # 2) Create the project row (fresh id). If the user picked a
            #    folder in the import dialog, it becomes the root_path;
            #    otherwise create_project_from_pack falls back to a managed cwd.
            project_row = await self._projects.create_project_from_pack(
                user_id,
                name=effective_name,
                kind=project.kind,
                icon=project.icon,
                instructions_md=resolve_text(project.instructions_md),
                root_path=root_path,
            )

            # 3) Restore memory dir (best-effort — never fail the import).
            #    Locate it via the manifest's archive-relative ``memory`` pointer;
            #    guard against a traversal in an untrusted manifest.
            try:
                if project.memory:
                    src_memory = (root / project.memory).resolve()
                    if src_memory.is_dir() and src_memory.is_relative_to(root.resolve()):
                        dest_memory = fs_registry.memory_dir(
                            user_id, "project", project_id=project_row.id
                        )
                        shutil.copytree(src_memory, dest_memory, dirs_exist_ok=True)
            except Exception:  # noqa: BLE001
                logger.exception("project-pack: memory restore failed for %s", project_row.id)

            # 4) Recreate members, preserving each member's project-local
            #    ``agent_slug`` handle so automations keep resolving.
            recreated_members: list[dict[str, Any]] = []
            for m in project.members:
                source_slug = m.source_agent_slug or m.agent_slug
                try:
                    await self._agents.deploy_agent(
                        user_id,
                        project_id=project_row.id,
                        source_agent_slug=source_slug,
                        agent_slug=m.agent_slug,
                        dedupe=False,
                    )
                    recreated_members.append(
                        {
                            "agent_slug": m.agent_slug,
                            "source_agent_slug": source_slug,
                        }
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("project-pack: deploy member %s failed", m.agent_slug)

            # 5) Recreate automations.
            recreated_automations: list[dict[str, Any]] = []
            automation_errors: list[dict[str, str]] = []
            from valuz_agent.modules.automations.models import AutomationRow

            for a in project.automations:
                row = AutomationRow(
                    name=a.name,
                    agent_kind=a.agent_kind,
                    agent_slug=a.agent_slug,
                    project_id=project_row.id,
                    prompt_template=a.prompt_template,
                    action_kind=a.action_kind,
                    trigger_kind=a.trigger_kind,
                    cron_expr=a.cron_expr,
                    timezone=a.timezone,
                    interval_seconds=a.interval_seconds,
                    status=a.status,
                )
                try:
                    detail = await self._automations.create_from_row(row, owner_user_id=user_id)
                    recreated_automations.append(
                        {"automation_id": detail.automation_id, "name": detail.name}
                    )
                except Exception as exc:  # noqa: BLE001
                    # One bad automation must not sink the rest, but the error
                    # is surfaced in the response so it isn't a silent drop.
                    logger.exception("project-pack: automation %s recreate failed", a.name)
                    automation_errors.append({"name": a.name, "error": str(exc)})

            # 6) Restore project-skill paths (best-effort; paths may be
            #    source-machine-specific, so skip the ones that don't resolve).
            try:
                await self._restore_project_skills(user_id, project_row.id, manifest)
            except Exception:  # noqa: BLE001
                logger.exception("project-pack: project skills restore failed")

            # 7) Restore project connectors (slugs only; recipient must have
            #    installed the connector for it to activate).
            try:
                slugs = [c.slug for c in project.connectors]
                # Filter to slugs the recipient actually has installed.
                installed = (
                    {v.slug for v in await self._agents._connectors.list_connectors(user_id)}
                    if getattr(self._agents, "_connectors", None)
                    else set()
                )
                present = [s for s in slugs if s in installed]
                if present:
                    await self._projects.set_connectors(user_id, project_row.id, present)
            except Exception:  # noqa: BLE001
                logger.exception("project-pack: project connectors restore failed")

            return {
                "status": "created",
                # Frontend contract: a ProjectListItem-shaped object so the
                # caller can upsertProject + navigate. ``cwd`` equals
                # ``root_path`` for project-kind (see ProjectListItem).
                "project": {
                    "id": project_row.id,
                    "name": project_row.name,
                    "kind": project_row.kind,
                    "root_path": project_row.root_path,
                    "icon": project_row.icon,
                    "cwd": project_row.root_path,
                },
                "project_id": project_row.id,
                "project_name": project_row.name,
                "members_created": len(recreated_members),
                "members_reused": 0,
                "agents_created": pack_result["created"],
                "agents_skipped": pack_result["skipped"],
                "automations_created": len(recreated_automations),
                "automation_errors": automation_errors,
                "members": recreated_members,
                "automations": recreated_automations,
                "connectors_to_configure": [
                    {
                        "slug": c.slug,
                        "display_name": resolve_text(c.display_name) or c.slug,
                        "requires_credentials": c.requires_credentials,
                        "requires_setup": c.requires_setup,
                    }
                    for c in manifest.connectors
                    if c.requires_credentials or c.requires_setup
                ],
            }
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_memory_dir(self, user_id: str, project_id: str) -> Path | None:
        """Return the project's memory dir if it has any files, else None."""
        try:
            d = fs_registry.memory_dir(user_id, "project", project_id=project_id)
        except ValueError:
            return None
        if not d.is_dir():
            return None
        # Treat an empty dir as "no memory" so we don't carry an empty
        # ``memory/`` entry in the archive.
        try:
            if not any(d.rglob("*")):
                return None
            if not any(p.is_file() for p in d.rglob("*")):
                return None
        except OSError:
            return None
        return d

    async def _resolve_embedded_skill_dirs(
        self, user_id: str, manifest: PackManifest
    ) -> dict[str, Path]:
        """For every embedded skill in the manifest, resolve its on-disk
        directory; bundled skills are skipped."""
        out: dict[str, Path] = {}
        embedded = [s.slug for s in manifest.skills if s.source == "embedded"]
        if not embedded:
            return out
        # One slug at a time. The resolver DROPS entries it will not hand over
        # (unresolvable, ineligible, or protected), so a single batch call
        # returns a shorter list and the positional ``zip`` this used to do
        # silently paired slugs with the wrong directories from the first drop
        # onwards. ``_build_skills_index`` already resolves per-slug for the
        # same reason.
        for slug in embedded:
            paths = await resolve_skill_slugs_to_paths([slug], None, user_id=user_id)
            path_str = paths[0] if paths else ""
            if not path_str:
                continue
            p = Path(path_str)
            if p.is_dir():
                out[sanitize_skill_slug(slug)] = p
        return out

    async def _build_skills_index(
        self, user_id: str, skill_slugs: list[str]
    ) -> tuple[list[PackSkill], dict[str, Path]]:
        """Build the shared ``skills[]`` index — embed every on-disk skill,
        reference others as bundled. Mirrors ``AgentPackService`` behavior
        line-for-line."""
        skills_idx: list[PackSkill] = []
        skill_dirs: dict[str, Path] = {}
        for slug in skill_slugs:
            clean = sanitize_skill_slug(slug)
            paths = await resolve_skill_slugs_to_paths([slug], None, user_id=user_id)
            path = Path(paths[0]).resolve() if paths and paths[0] else None
            if path is not None and path.is_dir():
                skills_idx.append(PackSkill(slug=clean, source="embedded"))
                skill_dirs[clean] = path
            else:
                logger.warning(
                    "project-pack: skill %s not on disk, exporting as a reference",
                    slug,
                )
                skills_idx.append(PackSkill(slug=clean, source="bundled"))
        return skills_idx, skill_dirs

    async def _build_connectors_index(
        self, user_id: str, connector_slugs: list[str]
    ) -> list[PackConnector]:
        """Build the shared ``connectors[]`` index, secrets stripped.
        Mirrors ``AgentPackService`` behavior — only url / command / args /
        auth_type / transport, never headers_json / params_json / env_json /
        oauth / api-key."""
        connector_svc = getattr(self._agents, "_connectors", None)
        if connector_svc is None or not connector_slugs:
            return []
        views = {v.slug: v for v in await connector_svc.list_connectors(user_id)}
        out: list[PackConnector] = []
        for slug in connector_slugs:
            v = views.get(slug)
            if v is None:
                continue
            needs_creds = bool(
                getattr(v, "has_api_key", False)
                or v.auth_type in ("bearer", "oauth")
                or any(getattr(h, "secret", False) for h in (v.headers or []))
                or any(getattr(p, "secret", False) for p in (v.params or []))
            )
            out.append(
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
        return out

    async def _enabled_project_skill_paths(self, user_id: str, project_row: Any) -> list[str]:
        """Read the project's enabled skill paths through the project
        service's own channel so this module doesn't import the skills
        datastore directly. Best-effort: returns ``[]`` on any failure."""
        try:
            from valuz_agent.modules.projects.service import ProjectListItem

            project = ProjectListItem(
                id=project_row.id,
                name=project_row.name,
                kind=project_row.kind,
                root_path=project_row.root_path,
                icon=project_row.icon,
            )
        except Exception:  # noqa: BLE001
            return []
        # ProjectService doesn't expose enabled skill paths directly; read
        # via the skill datastore's ProjectLike protocol through the
        # project service's injected skill datastore. The boundary rule
        # forbids importing the skills datastore here, so go through the
        # SkillDatastore API as exposed on ProjectService.
        skills_ds = getattr(self._projects, "_skills", None)
        if skills_ds is None:
            return []
        try:
            return sorted(skills_ds.enabled_skill_paths(project))
        except Exception:  # noqa: BLE001
            return []

    async def _restore_project_skills(
        self, user_id: str, project_id: str, manifest: PackManifest
    ) -> None:
        """Best-effort restore of project-skill paths. Each path is written
        through the project service's skills datastore so the boundary rule
        is respected."""
        skills_ds = getattr(self._projects, "_skills", None)
        if skills_ds is None or manifest.project is None:
            return
        try:
            project_detail = await self._projects.get_project(user_id, project_id)
            if project_detail is None or project_detail.kind != "project":
                return
            for cfg in manifest.project.skills:
                path = cfg.skill_path
                if not path:
                    continue
                try:
                    skills_ds.set_skill_enabled(project_detail, path, True)
                except Exception:  # noqa: BLE001
                    logger.debug("project-pack: skipping project-skill path %s", path)
        except Exception:  # noqa: BLE001
            logger.exception("project-pack: project skills restore failed")
