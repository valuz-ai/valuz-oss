"""Host resource library — the stable in-process API for reading and writing the
host's resources (agents / skills / connectors / knowledge bases).

This is a **host-provided facade**: OSS owns the per-kind services (which are
heterogeneous in method names, payload shapes and return types); this layer
absorbs that heterogeneity behind one uniform interface so callers — OSS's own
features and commercial overlays alike — never touch the per-module services
directly. It is the read/write counterpart of ``ports.resource_list_hook`` (the
list-post-processing hook overlays *implement*); this one overlays *call*.

Part of the OSS↔overlay contract (importable from ``valuz_agent.facade``). The
uniform unit is a ``ResourceSnapshot`` — a self-contained, portable copy of a
resource (everything needed to recreate it elsewhere), with machine-local fields
(server ids, timestamps, local paths) deliberately excluded from ``data``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

ResourceKind = Literal["agent", "skill", "connector", "kb", "project", "automation", "playbook"]


@dataclass
class ResourceRef:
    """Lightweight pointer to a resource (what ``list`` returns)."""

    kind: ResourceKind
    # portable identity: slug for agent/skill/connector, name for kb,
    # id for project/automation/playbook
    key: str
    name: str


@dataclass
class ResourceSnapshot:
    """A portable, self-contained copy of a resource (export/import unit).

    ``data`` carries everything needed to recreate the resource, with
    machine-local fields (ids, timestamps, local filesystem paths) excluded.
    ``files`` carries a skill's file tree (path → text content); ``None`` for
    kinds without files.
    """

    kind: ResourceKind
    key: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] | None = None


@asynccontextmanager
async def _use(dep_factory: Any, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
    """Drive a FastAPI-style async-generator dependency manually.

    Calls ``dep_factory(*args, **kwargs)``, advances the generator once to get the yielded
    value, then closes the generator in the finally block (triggering any
    teardown / commit logic in the dependency).

    Usage::

        async with _use(get_skill_service) as svc:
            result = await svc.list_catalog(user_id, "chat-default")
    """
    gen = dep_factory(*args, **kwargs)
    value = await gen.__anext__()
    try:
        yield value
    finally:
        await gen.aclose()


class ResourceLibrary:
    """Uniform read/write over the host's resource kinds.

    Stateless: each method opens its own session/service internally.
    Construct directly (``ResourceLibrary()``) or via the
    ``get_resource_library`` dependency.
    """

    # ── list ──────────────────────────────────────────────────────────

    async def list(self, user_id: str, kind: ResourceKind) -> list[ResourceRef]:
        """All of the user's resources of ``kind`` as lightweight refs."""
        if kind == "agent":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.agents.service import AgentService

            async with async_unit_of_work() as db:
                # AgentService annotates db as sync Session but uses it async
                # (so does the OSS agents route) — OSS annotation bug, harmless here.
                rows = await AgentService(db).list_agents(user_id)  # type: ignore[arg-type]
            return [ResourceRef(kind="agent", key=r.slug, name=r.name) for r in rows]

        if kind == "skill":
            from valuz_agent.api.deps import get_skill_service_for_user

            async with _use(get_skill_service_for_user, user_id) as svc:
                cat = await svc.list_catalog(user_id, "chat-default")
            return [ResourceRef(kind="skill", key=s.slug, name=s.name) for s in cat.skills]

        if kind == "connector":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.connectors.service import ConnectorService

            async with async_unit_of_work() as db:
                views = await ConnectorService.with_defaults(db).list_connectors(user_id)
            return [ResourceRef(kind="connector", key=v.slug, name=v.display_name) for v in views]

        if kind == "kb":
            from valuz_agent.api.deps import get_document_service

            async with _use(get_document_service) as svc:
                items = await svc.list_kbs(user_id)
            return [ResourceRef(kind="kb", key=item.name, name=item.name) for item in items]

        if kind == "project":
            # Only ``project``-kind rows are exportable (chat projects skipped —
            # see ``ProjectPackService.export_project`` / ``ProjectNotExportable``).
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.projects.datastore import ProjectDatastore

            async with async_unit_of_work(commit=False) as db:
                project_rows = await ProjectDatastore(db).list_projects(user_id)
            return [
                ResourceRef(kind="project", key=row.id, name=row.name)
                for row in project_rows
                if row.kind == "project"
            ]

        if kind == "automation":
            from valuz_agent.api.deps import get_automation_service

            async with _use(get_automation_service) as svc:
                items = await svc.list_all_automations(user_id)
            return [
                ResourceRef(kind="automation", key=item.automation_id, name=item.name)
                for item in items
            ]

        if kind == "playbook":
            # Definitions are the resource; versions and runs are not. A
            # definition may or may not sit in a Project — ``list_definitions``
            # with no ``project_id`` returns every definition the user owns.
            from valuz_agent.facade.projects import ProjectLibrary
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.playbooks.service import PlaybookService

            async with async_unit_of_work(commit=False) as db:
                definitions = await PlaybookService(db, ProjectLibrary()).list_definitions(user_id)
            return [ResourceRef(kind="playbook", key=row.id, name=row.name) for row in definitions]

        raise NotImplementedError(f"list({kind}) not implemented")

    # ── get ───────────────────────────────────────────────────────────

    async def get(self, user_id: str, kind: ResourceKind, key: str) -> ResourceSnapshot | None:
        """Export one resource as a portable snapshot, or ``None`` if absent."""
        if kind == "agent":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.agents.service import AgentNotFoundError, AgentService

            async with async_unit_of_work() as db:
                try:
                    row = await AgentService(db).get_agent(user_id, key)  # type: ignore[arg-type]
                except AgentNotFoundError:
                    return None
            return ResourceSnapshot(
                kind="agent",
                key=row.slug,
                name=row.name,
                data={
                    "slug": row.slug,
                    "name": row.name,
                    "description": row.description,
                    "instructions": row.instructions,
                    "runtime": row.runtime,
                    "model": row.model,
                    "skills": row.skills,
                    "connector_types": row.connector_types,
                    "knowledge_scope": row.knowledge_scope,
                    "provider_id": row.provider_id,
                    "effort": row.effort,
                    "kind": row.kind,
                    "resource_policy": row.resource_policy,
                    "inherit_global_instructions": row.inherit_global_instructions,
                    "permission_mode": row.permission_mode,
                    "avatar": row.avatar,
                },
            )

        if kind == "skill":
            from valuz_agent.api.deps import get_skill_service_for_user

            async with _use(get_skill_service_for_user, user_id) as svc:
                # Resolve slug → skill id via catalog
                cat = await svc.list_catalog(user_id, "chat-default")
                matched = next((s for s in cat.skills if s.slug == key), None)
                if matched is None:
                    return None
                detail = await svc.get_skill_detail(user_id, matched.id)
                file_nodes = await svc.list_skill_files(user_id, matched.id)
                files: dict[str, str] = {}
                for node in file_nodes:
                    try:
                        fc = await svc.read_skill_file(user_id, matched.id, node.path)
                        files[node.path] = fc.content
                    except Exception:
                        logger.debug(
                            "ResourceLibrary.get skill=%s: could not read file %s",
                            key,
                            node.path,
                            exc_info=True,
                        )
            return ResourceSnapshot(
                kind="skill",
                key=key,
                name=detail.name,
                data={
                    "name": detail.name,
                    "description": detail.description,
                    "instructions_markdown": detail.instructions_markdown,
                    "target_scope": "user",
                },
                files=files or None,
            )

        if kind == "connector":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.connectors.service import ConnectorService

            async with async_unit_of_work() as db:
                svc = ConnectorService.with_defaults(db)
                views = await svc.list_connectors(user_id)
                matched = next((v for v in views if v.slug == key), None)
                if matched is None:
                    return None
                v = await svc.get_connector(user_id, matched.id)
                if v is None:
                    return None
            return ResourceSnapshot(
                kind="connector",
                key=v.slug,
                name=v.display_name,
                data={
                    "display_name": v.display_name,
                    "description": v.description,
                    "transport": v.transport,
                    "url": v.url,
                    "auth_type": v.auth_type,
                    "connector_type": v.connector_type,
                    "command": v.command,
                    "args": v.args,
                    "working_dir": v.working_dir,
                },
            )

        if kind == "kb":
            from valuz_agent.api.deps import get_document_service

            async with _use(get_document_service) as svc:
                items = await svc.list_kbs(user_id)
                matched_item = next((item for item in items if item.name == key), None)
                if matched_item is None:
                    return None
                detail = await svc.get_kb(user_id, matched_item.id)
            return ResourceSnapshot(
                kind="kb",
                key=detail.name,
                name=detail.name,
                data={
                    "name": detail.name,
                    "parser_routing": detail.parser_routing,
                    "auto_discover": getattr(detail, "auto_discover", False),
                },
            )

        if kind == "project":
            # Export bytes are the unified ``.valuzpack`` archive
            # (project target) produced by ``ProjectPackService.export_project``.
            # We base64 the bytes into the text-only ``files`` dict so the
            # snapshot stays JSON-portable for the overlay's sync path
            # (cloud side detects base64 in ``_files`` content and stores
            # the decoded bytes in object storage).
            import base64

            from valuz_agent.api.deps import get_project_pack_service, get_project_service
            from valuz_agent.modules.project_packs.errors import (
                ProjectNotExportable,
                ProjectPackNotFound,
            )

            async with _use(get_project_service) as project_svc:
                try:
                    detail = await project_svc.get_project(user_id, key)
                except KeyError:
                    return None
            async with _use(get_project_pack_service) as pack_svc:
                try:
                    pack_bytes = await pack_svc.export_project(user_id, key)
                except (ProjectPackNotFound, ProjectNotExportable):
                    return None
            return ResourceSnapshot(
                kind="project",
                key=key,
                name=detail.name,
                data={
                    "name": detail.name,
                    "kind": detail.kind,
                    "icon": detail.icon,
                    "instructions_md": detail.instructions_md,
                    "bundle_size": len(pack_bytes),
                },
                files={"bundle.valuzpack": base64.b64encode(pack_bytes).decode("ascii")},
            )

        if kind == "automation":
            from valuz_agent.api.deps import get_automation_service
            from valuz_agent.modules.automations.errors import AutomationNotFound

            async with _use(get_automation_service) as svc:
                try:
                    detail = await svc.get_automation_detail(key, user_id=user_id)
                except AutomationNotFound:
                    return None
            # ``trigger`` is a Pydantic discriminated union — dump to plain
            # dict so the snapshot round-trips through JSON storage.
            trigger_data: dict[str, Any] = (
                detail.trigger.model_dump() if detail.trigger is not None else {}
            )
            return ResourceSnapshot(
                kind="automation",
                key=detail.automation_id,
                name=detail.name,
                data={
                    "name": detail.name,
                    "agent_kind": detail.agent_kind,
                    "agent_slug": detail.agent_slug,
                    "agent_name": detail.agent_name,
                    "project_id_ref": detail.project_id,
                    "project_name_ref": detail.project_name,
                    "project_kind": detail.project_kind,
                    "action_kind": detail.action_kind,
                    "prompt_template": detail.prompt_template,
                    "trigger": trigger_data,
                    "status": detail.status,
                },
            )

        if kind == "playbook":
            from valuz_agent.facade.projects import ProjectLibrary
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.playbooks.service import PlaybookService

            projects = ProjectLibrary()
            async with async_unit_of_work(commit=False) as db:
                svc = PlaybookService(db, projects)
                try:
                    definition = await svc.get_definition(user_id, key)
                    # Only the current version travels: the immutable version
                    # chain is local history, not part of the portable copy.
                    version = await svc.get_version(user_id, key, definition.current_version)
                except LookupError:
                    return None

            # Resolved outside the unit of work — ``ProjectLibrary`` opens its
            # own session, and a missing/foreign project must not fail the export.
            project_name: str | None = None
            if definition.project_id:
                project = await projects.get(user_id, definition.project_id)
                project_name = project.name if project else None

            return ResourceSnapshot(
                kind="playbook",
                key=definition.id,
                name=definition.name,
                data={
                    "name": definition.name,
                    "status": definition.status,
                    "origin": definition.origin,
                    # A Project id is meaningful only on the machine that
                    # owns it — carried as a reference for display, never as
                    # a binding the importer has to satisfy.
                    "project_id_ref": definition.project_id,
                    "project_name_ref": project_name,
                    # ``content`` is the sole authoritative executable body;
                    # the deprecated ``goal`` mirror stays local (see
                    # ``PlaybookVersionRow``) so the migration envelope is not
                    # frozen into the portable copy.
                    "content": version.content,
                    "reference_metadata": version.reference_metadata,
                    "default_executor": version.default_executor,
                    "applicability": version.applicability,
                    "inputs": version.inputs,
                    "stages": version.stages,
                    "context_reads": version.context_reads,
                    "context_writes": version.context_writes,
                    "required_skills": version.required_skills,
                    "allowed_skills": version.allowed_skills,
                    "conditions": version.conditions,
                    "approvals": version.approvals,
                    "outputs": version.outputs,
                    "failure_policy": version.failure_policy,
                },
            )

        raise NotImplementedError(f"get({kind}) not implemented")

    # ── save ──────────────────────────────────────────────────────────

    async def save(self, user_id: str, snapshot: ResourceSnapshot) -> ResourceRef:
        """Create the snapshot's resource locally, or update it if the key exists."""
        if snapshot.kind == "agent":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.agents.service import AgentService, MemberAlreadyExistsError

            async with async_unit_of_work() as db:
                svc = AgentService(db)  # type: ignore[arg-type]
                try:
                    row = await svc.create_agent(user_id, snapshot.data)
                except MemberAlreadyExistsError:
                    row = await svc.update_agent(user_id, snapshot.key, snapshot.data)
            return ResourceRef(kind="agent", key=row.slug, name=row.name)

        if snapshot.kind == "skill":
            from valuz_agent.api.deps import get_skill_service_for_user
            from valuz_agent.modules.skills.models import (
                SkillCreateRequest,
                SkillFileAction,
                SkillUpdateRequest,
            )

            data = snapshot.data
            async with _use(get_skill_service_for_user, user_id) as svc:
                # Try create; on slug-conflict fall back to update
                try:
                    view = await svc.create_skill(
                        user_id,
                        SkillCreateRequest(
                            name=data["name"],
                            target_scope=data.get("target_scope", "user"),
                            description=data.get("description") or "",
                            instructions_markdown=data.get("instructions_markdown"),
                        ),
                    )
                except Exception as exc:
                    # A skill with this name / slug already exists — resolve
                    # slug→id and update in place.
                    logger.debug(
                        "ResourceLibrary.save skill: create failed (%s), attempting update",
                        exc,
                    )
                    cat = await svc.list_catalog(user_id, "chat-default")
                    slug = data["name"].lower().replace(" ", "-")
                    matched = next(
                        (s for s in cat.skills if s.slug == snapshot.key or s.slug == slug), None
                    )
                    if matched is None:
                        raise
                    view = await svc.update_skill(
                        user_id,
                        matched.id,
                        SkillUpdateRequest(
                            name=data.get("name"),
                            description=data.get("description"),
                            instructions_markdown=data.get("instructions_markdown"),
                        ),
                    )

                # Restore files best-effort
                for path, content in (snapshot.files or {}).items():
                    try:
                        await svc.write_skill_file(
                            user_id,
                            view.id,
                            SkillFileAction(path=path, action="create", content=content),
                        )
                    except Exception:
                        logger.warning(
                            "ResourceLibrary.save skill=%s: could not write file %s",
                            view.slug,
                            path,
                            exc_info=True,
                        )
            return ResourceRef(kind="skill", key=view.slug, name=view.name)

        if snapshot.kind == "connector":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.connectors.service import ConnectorService

            data = snapshot.data
            async with async_unit_of_work() as db:
                conn_svc = ConnectorService.with_defaults(db)
                # Check if slug already exists
                views = await conn_svc.list_connectors(user_id)
                existing = next((v for v in views if v.slug == snapshot.key), None)
                if existing is None:
                    view = await conn_svc.create_connector(
                        user_id,
                        slug=snapshot.key,
                        display_name=data["display_name"],
                        transport=data.get("transport", "http"),
                        description=data.get("description"),
                        url=data.get("url"),
                        # A cloud-library download is a user-owned local copy,
                        # even when its source was originally system-managed.
                        # Keeping it custom makes disconnect/delete remove only
                        # this projection while the cloud source stays intact.
                        connector_type="custom",
                        command=data.get("command"),
                        args=data.get("args"),
                        working_dir=data.get("working_dir"),
                    )
                else:
                    updated = await conn_svc.update_connector(
                        user_id,
                        existing.id,
                        display_name=data.get("display_name"),
                        description=data.get("description"),
                        url=data.get("url"),
                        command=data.get("command"),
                        args=data.get("args"),
                        working_dir=data.get("working_dir"),
                    )
                    view = updated if updated is not None else existing
            return ResourceRef(kind="connector", key=view.slug, name=view.display_name)

        if snapshot.kind == "kb":
            import os

            from valuz_agent.api.deps import get_document_service
            from valuz_agent.infra.fs_registry import fs_registry
            from valuz_agent.modules.docs.errors import KbRootDuplicated

            data = snapshot.data
            root = str(fs_registry.kb_root(user_id) / data["name"])
            os.makedirs(root, exist_ok=True)
            async with _use(get_document_service) as svc:
                try:
                    kb = await svc.create_kb(
                        user_id,
                        name=data["name"],
                        root_path=root,
                        parser_routing=data.get("parser_routing", "local_only"),
                        auto_discover=bool(data.get("auto_discover", False)),
                    )
                except KbRootDuplicated:
                    kbs = await svc.list_kbs(user_id)
                    matched_item = next((item for item in kbs if item.name == data["name"]), None)
                    if matched_item is None:
                        raise
                    kb = await svc.get_kb(user_id, matched_item.id)
            return ResourceRef(kind="kb", key=kb.name, name=kb.name)

        if snapshot.kind == "project":
            # Project import is intentionally not exposed through ``save``:
            # the unified-pack import flow requires a 2-stage preview /
            # confirm exchange (the user picks a target folder, resolves
            # connector + skill conflicts, optionally a different name).
            # Callers should drive the OSS ``ProjectPackService.preview_import``
            # / ``confirm_import`` path directly, or invoke the
            # ``POST /api/v1/projects/import/{preview_id}/confirm`` route.
            raise NotImplementedError(
                "save(project) requires a 2-stage preview/confirm flow with a "
                "user-picked root_path; use ProjectPackService.preview_import + "
                "confirm_import (or the /api/v1/projects/import/* routes) instead."
            )

        if snapshot.kind == "automation":
            from valuz_agent.api.deps import get_automation_service
            from valuz_agent.modules.automations.errors import AutomationNotFound
            from valuz_agent.modules.automations.schemas import (
                AutomationCreatePayload,
                AutomationUpdatePayload,
                CronTrigger,
                IntervalTrigger,
                ManualTrigger,
            )

            data = snapshot.data
            raw_trigger = data.get("trigger") or {}
            trigger_kind = raw_trigger.get("kind", "manual")
            if trigger_kind == "cron":
                trigger: Any = CronTrigger(
                    cron_expr=raw_trigger.get("cron_expr", ""),
                    timezone=raw_trigger.get("timezone"),
                )
            elif trigger_kind == "interval":
                trigger = IntervalTrigger(seconds=int(raw_trigger.get("seconds", 0)))
            else:
                trigger = ManualTrigger()

            async with _use(get_automation_service) as svc:
                # Try update if the key (automation_id) still resolves
                # locally — same key + same row → in-place refresh.
                # Otherwise create a fresh automation referencing the
                # snapshot's local entities (project_id_ref / agent_slug).
                try:
                    await svc.get_automation_detail(snapshot.key, user_id=user_id)
                    exists = True
                except AutomationNotFound:
                    exists = False

                if exists:
                    detail = await svc.update(
                        snapshot.key,
                        AutomationUpdatePayload(
                            name=data.get("name"),
                            prompt_template=data.get("prompt_template"),
                            trigger=trigger,
                            agent_slug=data.get("agent_slug"),
                            action_kind=data.get("action_kind"),
                        ),
                        user_id=user_id,
                    )
                else:
                    detail = await svc.create(
                        AutomationCreatePayload(
                            name=data.get("name", "Imported automation"),
                            project_kind=data.get("project_kind", "project"),
                            project_id=data.get("project_id_ref"),
                            agent_kind=data.get("agent_kind", "project_member"),
                            agent_slug=data.get("agent_slug", ""),
                            prompt_template=data.get("prompt_template", ""),
                            trigger=trigger,
                            action_kind=data.get("action_kind", "chat"),
                        ),
                        user_id=user_id,
                    )
            return ResourceRef(kind="automation", key=detail.automation_id, name=detail.name)

        raise NotImplementedError(f"save({snapshot.kind}) not implemented")

    # ── credentials ───────────────────────────────────────────────────

    async def get_connector_access_token(self, user_id: str, slug: str) -> str | None:
        """Live OAuth access token of the user's connector ``slug``, or ``None``.

        Read-only overlay seam: editions/overlays whose data plane is
        authenticated by a connector's OAuth identity (e.g. the built-in
        research connectors) fetch the bearer here instead of reaching into
        ``modules.connectors`` internals. A lapsed token is refreshed before
        returning — the same self-heal the MCP resolver applies — so callers
        can treat the result as ready to use. Returns ``None`` when the
        connector is absent, disabled, not OAuth, or holds no token.
        """
        import json

        from valuz_agent.adapters.mcp_resolver import _ensure_fresh_oauth_token
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.connectors.datastore import ConnectorDatastore

        async with async_unit_of_work() as db:
            ds = ConnectorDatastore(db)
            row = await ds.get_by_slug(user_id, slug)
            if row is None or not row.enabled or row.auth_type != "oauth":
                return None
            token_json = row.oauth_token_json
            if not token_json:
                return None
            # Refresh-if-expired shares the resolver's lock + update path.
            token_json = await _ensure_fresh_oauth_token(row, ds, token_json)
            try:
                data = json.loads(token_json)
            except (json.JSONDecodeError, TypeError):
                return None
            token = data.get("access_token", "")
            return token or None


async def get_resource_library() -> AsyncGenerator[ResourceLibrary, None]:
    """FastAPI dependency yielding a request-scoped ``ResourceLibrary``."""
    yield ResourceLibrary()


__all__ = [
    "ResourceKind",
    "ResourceLibrary",
    "ResourceRef",
    "ResourceSnapshot",
    "get_resource_library",
]
