from __future__ import annotations

import asyncio
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.integrations.skills_filesystem import (
    FilesystemSkillSource,
    _default_user_skill_root,
    _detect_manifest,
    _extract_frontmatter,
    _read_text,
)
from valuz_agent.modules.projects.service import (
    WorkspaceService,
)
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.events import (
    SKILL_CHANGED,
    WORKSPACE_SKILLS_CHANGED,
)
from valuz_agent.modules.skills.models import (
    SessionSkillImportConfirmRequest,
    SkillCopyRequest,
    SkillCreateRequest,
    SkillDeleteAffectedProject,
    SkillDeleteMode,
    SkillDeletePreview,
    SkillDetail,
    SkillFileAction,
    SkillFileContent,
    SkillFileNode,
    SkillImportArchiveConfirmRequest,
    SkillImportArchivePreview,
    SkillImportCandidate,
    SkillImportDirectoryPreviewRequest,
    SkillImportPreviewFile,
    SkillImportUrlConfirmRequest,
    SkillOrigin,
    SkillsCatalog,
    SkillUpdateRequest,
    SkillView,
)

# Preview entries are heterogeneous by import kind:
#   archive/directory: (skill_root, managed_temp: bool)  — cleaned via skill_root.parent
#   URL/GitHub:        (skill_root, cleanup_root: Path, created_at: float)
# For URL imports every candidate shares one ``cleanup_root`` (the staging dir),
# ref-counted in ``_import_cleanup_refs`` so confirming one skill never deletes a
# sibling's source; the dir is reclaimed only when the last candidate is gone.
_import_previews: dict[str, tuple[Path, bool] | tuple[Path, Path, float]] = {}
_import_cleanup_refs: dict[str, int] = {}
# Serializes ``startup_scan`` across concurrent callers (e.g. a multi-skill
# import confirming N skills at once). Without it, two scans both see a
# just-copied skill as absent and race to INSERT its index row → UNIQUE
# violation → flush rollback. Module-level because the service is built
# per-request, so an instance lock wouldn't be shared.
_scan_lock = asyncio.Lock()
# Import provenance staged alongside a preview, keyed by the same ``preview_id``.
# Populated for URL/GitHub imports; consumed by ``confirm_url_import`` to persist
# ``valuz_skill_index.origin_json``. Cleaned up with the preview.
_import_origins: dict[str, SkillOrigin] = {}

# Per-skill import caps. A URL can point at an arbitrarily large repository, so
# these bound what a single import may copy into the library — a defence against
# a pathological repo (or a wrong ref/path landing on the whole tree). Exceeding
# any cap aborts the import rather than silently truncating an incomplete skill.
_MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_MAX_IMPORT_TOTAL_BYTES = 25 * 1024 * 1024  # 25 MiB per skill bundle
_MAX_IMPORT_FILE_COUNT = 512  # max files per skill bundle


class SkillLibraryService:
    def __init__(
        self,
        datastore: SkillDatastore,
        skill_source: FilesystemSkillSource,
        workspace_service: WorkspaceService,
        session_datastore: SessionDatastore,
        event_bus: EventBus,
        extra_sources: list | None = None,
        auth_facade: object | None = None,
        remote_registry: object | None = None,
    ) -> None:
        self._ds = datastore
        self._source = skill_source
        self._extra_sources = extra_sources or []
        self._workspaces = workspace_service
        self._sessions = session_datastore
        self._bus = event_bus
        self._auth = auth_facade
        self._remote_registry = remote_registry

    async def list_catalog(
        self, workspace_id: str, *, user_id: str = "local-user", org_id: str | None = None
    ) -> SkillsCatalog:
        workspace = await self._workspaces.get_workspace(workspace_id)
        items = self._ds.list_workspace_skills(workspace, self._source)
        # ``creation_origin`` is host bookkeeping kept only in
        # ``valuz_skill_index`` (never SKILL.md), so it isn't on the
        # filesystem-scanned manifest — overlay it from the DB here. A
        # missing row (skill on disk but not yet indexed) or a NULL value
        # (legacy row seeded before the column landed) coalesces to
        # ``"discovered"`` so the field is always a real enum value.
        origin_by_id = {row.id: row.creation_origin for row in await self._ds.list_skills()}

        def _origin(skill_id: str) -> str:
            return origin_by_id.get(skill_id) or "discovered"

        skills = [
            SkillView(**item.model_dump(), creation_origin=_origin(item.id)) for item in items
        ]

        from valuz_agent.modules.skills.contracts import RuntimeContext, WorkspaceRef

        ctx = RuntimeContext(
            user_id=user_id,
            org_id=org_id,
            workspace=WorkspaceRef(
                id=workspace.id,
                slug=workspace.id,
                kind=workspace.kind,
                root_path=workspace.root_path,
            )
            if hasattr(workspace, "kind")
            else None,
        )
        # Compute the same enabled set ``list_workspace_skills`` uses for the
        # filesystem source so the extra-source branch below can mirror its
        # ``enabled`` semantics. Without this, official / built-in skills
        # (e.g. skill-creator) always rendered ``enabled=False`` in the UI even
        # after the user toggled them on — capability_resolver wrote the path
        # into ``project-config.json`` correctly but the catalog never read it
        # back for extra-sourced skills.
        enabled_paths = self._ds.enabled_skill_paths(workspace)
        has_official_entitlement = await self._check_entitlement("skills:official")
        for source in self._extra_sources:
            for manifest in source.list_skills(ctx):
                view = SkillView(**manifest.model_dump(), creation_origin=_origin(manifest.id))
                # Bundled skills (origin_label="Built-in") ship with the
                # client and are always free; never gate them behind the
                # entitlement. Only externally-installed official skills
                # are subject to the subscription lock.
                is_bundled = view.origin_label == "Built-in"
                if view.scope == "official" and not has_official_entitlement and not is_bundled:
                    view.is_locked = True
                    view.lock_reason = "Connect Reportify to unlock official skills"
                elif view.scope == "official" and (has_official_entitlement or is_bundled):
                    view.is_locked = False
                    view.lock_reason = None
                view.enabled = workspace.kind == "chat" or view.path in enabled_paths
                skills.append(view)

        # Remote registry (SaaS catalog) — commercial version injects via
        # ``remote_registry`` constructor param. OSS: None → skipped.
        if self._remote_registry is not None:
            try:
                for manifest in self._remote_registry.list_remote_skills(ctx):
                    view = SkillView(**manifest.model_dump(), creation_origin="remote")
                    view.enabled = workspace.kind == "chat" or view.path in enabled_paths
                    skills.append(view)
            except Exception:
                pass

        # Deduplicate by slug: official / built-in skills take precedence over
        # user-installed skills with the same slug.
        seen: dict[str, int] = {}
        for idx, s in enumerate(skills):
            slug = s.slug
            if not slug:
                continue
            prev = seen.get(slug)
            if prev is None:
                seen[slug] = idx
                continue
            # Prefer official over user, built-in over everything.
            prev_s = skills[prev]
            prev_rank = (
                0 if prev_s.origin_label == "Built-in" else 1 if prev_s.scope == "official" else 2
            )
            cur_rank = 0 if s.origin_label == "Built-in" else 1 if s.scope == "official" else 2
            if cur_rank < prev_rank:
                skills[prev] = None  # type: ignore[assignment]
                seen[slug] = idx
            else:
                skills[idx] = None  # type: ignore[assignment]
        skills = [s for s in skills if s is not None]

        # Sort: folder birthtime DESC (newest folder first), name ASC as
        # tiebreaker. NULL birthtime sorts last so legacy / unreadable
        # rows don't push fresh skills down. Mirrors the order expected
        # by the desktop skill management page; the page just renders
        # the flat list, no resort on the frontend side.
        def _sort_key(view: SkillView) -> tuple[int, int, str]:
            if view.folder_created_at is None:
                # 1 = "no birthtime" bucket sorts after 0 = "has birthtime".
                # Within the "no birthtime" bucket fall back to name ASC.
                return (1, 0, view.name.lower())
            return (0, -view.folder_created_at, view.name.lower())

        skills.sort(key=_sort_key)

        return SkillsCatalog(workspace_id=workspace_id, skills=skills)

    async def startup_scan(self) -> None:
        # Serialize concurrent scans (see ``_scan_lock``): each acquirer commits
        # its rows before releasing, so the next scan sees them and does an
        # UPDATE instead of a duplicate INSERT.
        async with _scan_lock:
            await self._startup_scan_unlocked()

    async def _startup_scan_unlocked(self) -> None:
        from valuz_agent.modules.skills.contracts import RuntimeContext

        all_manifests: list = []
        ctx = RuntimeContext()
        all_manifests.extend(self._source.list_skills(ctx))
        for source in self._extra_sources:
            all_manifests.extend(source.list_skills(ctx))

        for workspace in await self._workspaces.list_workspaces():
            if workspace.kind == "project" and workspace.root_path:
                from valuz_agent.modules.skills.contracts import WorkspaceRef

                project_ctx = RuntimeContext(
                    workspace=WorkspaceRef(
                        id=workspace.id,
                        slug=workspace.id,
                        kind=workspace.kind,
                        root_path=workspace.root_path,
                    ),
                )
                all_manifests.extend(self._source.list_skills(project_ctx))

        seen_ids: set[str] = set()
        for manifest in all_manifests:
            if manifest.id in seen_ids:
                continue
            seen_ids.add(manifest.id)
            existing = await self._ds.get_by_id(manifest.id)
            from valuz_agent.modules.skills.models import SkillIndexRow

            if existing is None:
                await self._ds.create(
                    SkillIndexRow(
                        id=manifest.id,
                        slug=manifest.slug or manifest.id,
                        name=manifest.name,
                        description=manifest.description,
                        scope=manifest.scope,
                        source=manifest.source,
                        source_path=manifest.path,
                        project_root=manifest.project_root,
                        manifest_filename=None,
                        tags_json=",".join(manifest.tags) if manifest.tags else None,
                        icon=manifest.icon,
                        status="available",
                        readonly=manifest.readonly,
                        deletable=manifest.deletable,
                        is_locked=manifest.is_locked,
                        content_hash=manifest.content_hash,
                        manifest_hash=manifest.manifest_hash,
                        folder_created_at=manifest.folder_created_at,
                        # New rows default to "discovered"; the create /
                        # import flows overwrite this via set_creation_origin
                        # right after they call startup_scan.
                        creation_origin="discovered",
                    )
                )
            else:
                existing.name = manifest.name
                existing.description = manifest.description
                existing.source_path = manifest.path
                existing.status = "available"
                existing.content_hash = manifest.content_hash
                existing.manifest_hash = manifest.manifest_hash
                existing.readonly = manifest.readonly
                existing.deletable = manifest.deletable
                existing.is_locked = manifest.is_locked
                # Birthtime is immutable, so overwriting it every scan is
                # safe (also backfills legacy rows). creation_origin is
                # host bookkeeping owned by the DB — never clobber a real
                # value; only heal a NULL legacy row to "discovered".
                existing.folder_created_at = manifest.folder_created_at
                existing.creation_origin = existing.creation_origin or "discovered"
                await self._ds.update(existing)

        for row in await self._ds.list_skills():
            if row.id not in seen_ids:
                row.status = "unavailable"
                await self._ds.update(row)

    async def set_skill_enabled(
        self,
        workspace_id: str,
        skill_path: str,
        enabled: bool,
    ) -> SkillsCatalog:
        workspace = await self._workspaces.get_workspace(workspace_id)
        self._ds.set_skill_enabled(workspace, skill_path, enabled)
        self._bus.publish(WORKSPACE_SKILLS_CHANGED, workspace_id=workspace_id)
        return await self.list_catalog(workspace_id)

    async def resolve_skill_dirs_for_workspace(self, workspace_id: str) -> list[str]:
        catalog = await self.list_catalog(workspace_id)
        return [skill.path for skill in catalog.skills if skill.enabled]

    # ── Staging (Scenario B + D3 accept) ──────────────────────────────────

    def scan_staging(self, session_id: str):  # type: ignore[no-untyped-def]
        from valuz_agent.modules.skills import staging

        return staging.scan_staging(session_id)

    async def sync_staging(
        self,
        session_id: str,
        items: list,  # type: ignore[type-arg]
        target_scope: str = "user",
        workspace_id: str | None = None,
    ) -> list:  # type: ignore[type-arg]
        from valuz_agent.modules.skills import staging

        target_root = await self._target_root_for_scope(target_scope, workspace_id)
        results = []
        for item in items:
            result = staging.sync_slug(
                session_id=session_id,
                slug=item.slug,
                strategy=item.strategy,
                new_slug=item.new_slug,
                target_root=target_root,
            )
            results.append(result)

        # After files land, refresh the index so the new skills appear in
        # subsequent list_catalog calls without waiting for the next file
        # watcher tick.
        try:
            await self.startup_scan()
        except Exception:  # noqa: BLE001
            pass

        # Staging sync is the skill-creator AI-chat → library landing
        # step — from the user's POV a deliberate creation act. The scan
        # above created the index rows as "discovered"; overwrite that
        # column to "created" (host bookkeeping in valuz_skill_index,
        # never SKILL.md).
        for result in results:
            if not result.written_path or result.skipped:
                continue
            try:
                written = await self._resolve_created_skill(
                    Path(result.written_path), workspace_id=workspace_id
                )
            except KeyError:
                continue
            await self._ds.set_creation_origin(written.id, "created")

        # Notify any subscribers (frontend uses /v1/skills/events/stream).
        self._bus.publish(SKILL_CHANGED, skill_id="*", reason="staging-sync")
        return results

    async def optimize_from_skill(self, session_id: str, source_skill_id: str) -> tuple[str, str]:
        """Copy an existing skill into the session's staging dir for editing.

        Returns (slug, staging_path). Raises KeyError if the skill is unknown.
        """
        from valuz_agent.modules.skills import staging

        # Look up the skill's filesystem path. We accept both
        # `official:slug` ids (filesystem source returns these) and arbitrary
        # SkillIndexRow ids (DB-backed).
        path = await self._resolve_skill_path_by_id(source_skill_id)
        if path is None:
            raise KeyError(f"Skill not found: {source_skill_id!r}")
        dest = staging.prepare_optimize(
            session_id=session_id,
            source_skill_dir=path,
            source_skill_id=source_skill_id,
        )
        return dest.name, str(dest)

    async def _resolve_skill_path_by_id(self, skill_id: str):  # type: ignore[no-untyped-def]
        # Try DB first.
        row = await self._ds.get_by_id(skill_id)
        if row is not None and row.source_path:
            return Path(row.source_path)
        # Fall back to scanning all sources (covers fresh installs / official).
        from valuz_agent.modules.skills.contracts import RuntimeContext

        all_manifests = list(self._source.list_skills(RuntimeContext()))
        for source in self._extra_sources:
            all_manifests.extend(source.list_skills(RuntimeContext()))
        for m in all_manifests:
            if m.id == skill_id:
                return Path(m.path)
        return None

    async def _target_root_for_scope(
        self, target_scope: str, workspace_id: str | None
    ) -> Path | None:
        """Resolve the target skill-library directory for sync / create.

        - "user" (default) → _default_user_skill_root() (controlled by
          VALUZ_USER_SKILLS_DIR env var).
        - "project" → <workspace.root_path>/.claude/skills/. Requires
          workspace_id pointing at a project workspace.
        - "official" / "tenant" → not supported here; raise to surface a clear
          error rather than silently writing to the wrong place.
        """
        if target_scope == "user":
            return _default_user_skill_root()
        if target_scope == "project":
            if not workspace_id:
                raise ValueError("workspace_id required when target_scope='project'")
            workspace = await self._workspaces.get_workspace(workspace_id)
            if workspace.kind != "project" or not workspace.root_path:
                raise ValueError("target workspace is not a project workspace")
            return Path(workspace.root_path) / ".claude" / "skills"
        raise ValueError(f"unsupported target_scope: {target_scope!r}")

    async def create_skill(self, payload: SkillCreateRequest) -> SkillView:
        workspace = await self._resolve_workspace(payload.workspace_id)
        skill_dir = await self._allocate_skill_dir(
            target_scope=payload.target_scope,
            workspace_id=payload.workspace_id,
            name=payload.name,
        )
        self._write_manifest(
            skill_dir=skill_dir,
            name=payload.name,
            description=payload.description,
            instructions_markdown=payload.instructions_markdown,
        )
        if payload.target_scope == "project" and workspace is not None:
            self._ds.set_skill_enabled(workspace, str(skill_dir), True)
        elif payload.add_to_workspace and workspace is not None and workspace.kind == "project":
            self._ds.set_skill_enabled(workspace, str(skill_dir), True)
        result = await self._finalize_origin(skill_dir, "created", payload.workspace_id)
        self._bus.publish(SKILL_CHANGED, skill_id=result.id, reason="created")
        self._bus.publish(
            WORKSPACE_SKILLS_CHANGED, workspace_id=payload.workspace_id or "chat-default"
        )
        return result

    async def update_skill(
        self,
        skill_id: str,
        payload: SkillUpdateRequest,
        workspace_id: str | None = None,
    ) -> SkillView:
        skill = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        manifest_path = _detect_manifest(Path(skill.path))
        if manifest_path is None:
            raise KeyError(skill_id)

        metadata, body = _extract_frontmatter(_read_text(manifest_path))
        next_name = payload.name or str(metadata.get("name") or skill.name)
        next_description = payload.description or str(
            metadata.get("description") or skill.description
        )
        next_body = payload.instructions_markdown or body.strip() or "Skill generated by Valuz."
        # creation_origin lives in the DB (valuz_skill_index), not SKILL.md,
        # so rewriting the manifest here can't relabel a synced skill — the
        # index row is left untouched.
        manifest_path.write_text(
            self._render_manifest(
                name=next_name,
                description=next_description,
                instructions_markdown=next_body,
                tags=metadata.get("tags") if isinstance(metadata.get("tags"), list) else skill.tags,
            ),
            encoding="utf-8",
        )
        result = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        self._bus.publish(SKILL_CHANGED, skill_id=skill_id, reason="updated")
        return result

    async def copy_skill(
        self,
        skill_id: str,
        payload: SkillCopyRequest,
    ) -> SkillView:
        source_skill = await self._resolve_skill(
            skill_id=skill_id, workspace_id=payload.workspace_id
        )
        source_dir = Path(source_skill.path)
        target_dir = await self._allocate_skill_dir(
            target_scope="user",
            workspace_id=payload.workspace_id,
            name=payload.new_name,
        )
        shutil.copytree(source_dir, target_dir)
        manifest_path = _detect_manifest(target_dir)
        if manifest_path is not None:
            metadata, body = _extract_frontmatter(_read_text(manifest_path))
            manifest_path.write_text(
                self._render_manifest(
                    name=payload.new_name,
                    description=str(metadata.get("description") or source_skill.description),
                    instructions_markdown=body.strip() or "Skill copied by Valuz.",
                    tags=(
                        metadata.get("tags")
                        if isinstance(metadata.get("tags"), list)
                        else source_skill.tags
                    ),
                ),
                encoding="utf-8",
            )
        workspace = await self._resolve_workspace(payload.workspace_id)
        if payload.add_to_workspace and workspace is not None and workspace.kind == "project":
            self._ds.set_skill_enabled(workspace, str(target_dir), True)
        # A "duplicate" is the user's deliberate creation act in their
        # library, not an external sync — mark it "created" so the copy
        # lands under the "创建" badge in the .agents group.
        return await self._finalize_origin(target_dir, "created", payload.workspace_id)

    async def delete_skill(
        self,
        skill_id: str,
        workspace_id: str | None = None,
        mode: SkillDeleteMode = "dry_run",
    ) -> SkillDeletePreview | None:
        from valuz_agent.modules.skills.errors import SourceReadonly

        skill = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        if not skill.deletable:
            raise SourceReadonly()
        affected_projects = await self._affected_projects(skill.path)
        preview = SkillDeletePreview(
            affected_projects=affected_projects,
            count=len(affected_projects),
        )
        if mode == "dry_run":
            return preview

        skill_dir = Path(skill.path)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        for workspace in await self._workspaces.list_workspaces():
            if workspace.kind == "project":
                self._ds.remove_skill_path_from_workspace(workspace, skill.path)
        self._bus.publish(SKILL_CHANGED, skill_id=skill_id, reason="deleted")
        self._bus.publish(WORKSPACE_SKILLS_CHANGED, workspace_id=workspace_id or "chat-default")
        return None

    async def import_from_session_confirm(
        self,
        payload: SessionSkillImportConfirmRequest,
    ) -> SkillView:
        assistant_text = self._collect_session_assistant_text(
            await self._sessions.list_events(payload.session_id)
        )
        description = payload.description or "Imported from session output"
        body = assistant_text or description
        return await self.create_skill(
            SkillCreateRequest(
                name=payload.name,
                description=description,
                target_scope=payload.target_scope,
                workspace_id=payload.workspace_id,
                instructions_markdown=body,
                add_to_workspace=payload.add_to_workspace,
            )
        )

    async def import_archive_preview(
        self,
        archive_path: str,
        target_scope: str,
        workspace_id: str | None = None,
    ) -> SkillImportArchivePreview:
        extracted_root = self._extract_archive(Path(archive_path))
        preview_id = f"skill-preview-{uuid4().hex[:8]}"
        skill_root = self._locate_skill_root(
            extracted_root,
            missing_manifest_message="Archive does not contain a valid skill root with SKILL.md",
        )
        _import_previews[preview_id] = (skill_root, True)
        return await self._build_import_preview(
            preview_id=preview_id,
            skill_root=skill_root,
            target_scope=target_scope,
            workspace_id=workspace_id,
        )

    async def import_directory_preview(
        self,
        payload: SkillImportDirectoryPreviewRequest,
    ) -> SkillImportArchivePreview:
        directory_path = Path(payload.directory_path).expanduser()
        if not directory_path.exists():
            raise ValueError("Selected folder does not exist")
        if not directory_path.is_dir():
            raise ValueError("Selected path must be a folder")

        preview_id = f"skill-preview-{uuid4().hex[:8]}"
        skill_root = self._locate_skill_root(
            directory_path,
            missing_manifest_message="Selected folder must contain SKILL.md or skill.md",
        )
        _import_previews[preview_id] = (skill_root, False)
        return await self._build_import_preview(
            preview_id=preview_id,
            skill_root=skill_root,
            target_scope=payload.target_scope,
            workspace_id=payload.workspace_id,
        )

    async def confirm_archive_import(
        self,
        payload: SkillImportArchiveConfirmRequest,
    ) -> SkillView:
        preview = _import_previews.get(payload.preview_id)
        preview_root = preview[0] if preview else None
        if preview_root is None or not preview_root.exists():
            raise KeyError(payload.preview_id)
        target_name = payload.name or preview_root.name
        target_dir = await self._allocate_skill_dir(
            target_scope=payload.target_scope,
            workspace_id=payload.workspace_id,
            name=target_name,
        )
        shutil.copytree(preview_root, target_dir)
        manifest_path = _detect_manifest(target_dir)
        if manifest_path is not None and payload.name:
            # Rename the copied manifest to the user-chosen name. With no
            # name override the archive's SKILL.md is kept verbatim.
            # creation_origin isn't touched here — it's DB bookkeeping
            # applied by _finalize_origin below.
            metadata, body = _extract_frontmatter(_read_text(manifest_path))
            manifest_path.write_text(
                self._render_manifest(
                    name=payload.name,
                    description=str(metadata.get("description") or "Imported local skill"),
                    instructions_markdown=body.strip() or "Imported local skill.",
                    tags=metadata.get("tags") if isinstance(metadata.get("tags"), list) else None,
                ),
                encoding="utf-8",
            )
        workspace = await self._resolve_workspace(payload.workspace_id)
        if payload.target_scope == "project" and workspace is not None:
            self._ds.set_skill_enabled(workspace, str(target_dir), True)
        elif payload.add_to_workspace and workspace is not None and workspace.kind == "project":
            self._ds.set_skill_enabled(workspace, str(target_dir), True)
        self._cleanup_preview(payload.preview_id)
        return await self._finalize_origin(target_dir, "imported", payload.workspace_id)

    # ------------------------------------------------------------------
    # Tags aggregation (T1.2)
    # ------------------------------------------------------------------

    async def list_all_tags(self, workspace_id: str | None = None) -> list[str]:
        workspace_id = workspace_id or "chat-default"
        catalog = await self.list_catalog(workspace_id)
        seen: set[str] = set()
        ordered: list[str] = []
        for skill in catalog.skills:
            for tag in skill.tags:
                if tag not in seen:
                    seen.add(tag)
                    ordered.append(tag)
        return ordered

    # ------------------------------------------------------------------
    # File-level operations (T1.1)
    # ------------------------------------------------------------------

    async def list_skill_files(
        self,
        skill_id: str,
        workspace_id: str | None = None,
    ) -> list[SkillFileNode]:
        skill = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        skill_dir = Path(skill.path)
        if not skill_dir.exists():
            return []
        return self._build_skill_file_tree(skill_dir)

    async def read_skill_file(
        self,
        skill_id: str,
        file_path: str,
        workspace_id: str | None = None,
    ) -> SkillFileContent:
        from valuz_agent.modules.skills.errors import SkillNotFound

        skill = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        skill_dir = Path(skill.path)
        target = (skill_dir / file_path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise ValueError("Path traversal not allowed")
        if not target.exists() or not target.is_file():
            raise SkillNotFound(f"File not found: {file_path}")
        return SkillFileContent(
            path=file_path,
            content=target.read_text(encoding="utf-8"),
        )

    async def write_skill_file(
        self,
        skill_id: str,
        action: SkillFileAction,
        workspace_id: str | None = None,
    ) -> SkillFileContent:
        from valuz_agent.modules.skills.errors import SourceReadonly

        skill = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        if skill.readonly or skill.is_locked:
            raise SourceReadonly()
        skill_dir = Path(skill.path)
        target = (skill_dir / action.path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise ValueError("Path traversal not allowed")

        if action.action == "create":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(action.content or "", encoding="utf-8")
            return SkillFileContent(path=action.path, content=action.content or "")
        elif action.action == "rename":
            if action.new_path is None:
                raise ValueError("new_path required for rename")
            new_target = (skill_dir / action.new_path).resolve()
            if not str(new_target).startswith(str(skill_dir.resolve())):
                raise ValueError("Path traversal not allowed")
            new_target.parent.mkdir(parents=True, exist_ok=True)
            target.rename(new_target)
            content = new_target.read_text(encoding="utf-8") if new_target.is_file() else ""
            return SkillFileContent(path=action.new_path, content=content)
        elif action.action == "delete":
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            return SkillFileContent(path=action.path, content="")
        raise ValueError(f"Unknown action: {action.action}")

    # ------------------------------------------------------------------
    # Skill detail (T1.4)
    # ------------------------------------------------------------------

    async def get_skill_detail(
        self,
        skill_id: str,
        workspace_id: str | None = None,
    ) -> SkillDetail:
        skill = await self._resolve_skill(skill_id=skill_id, workspace_id=workspace_id)
        skill_dir = Path(skill.path)
        manifest_path = _detect_manifest(skill_dir)
        instructions_md: str | None = None
        manifest_filename: str | None = None
        metadata: dict = {}
        if manifest_path is not None:
            manifest_filename = manifest_path.name
            meta, body = _extract_frontmatter(_read_text(manifest_path))
            instructions_md = body.strip() or None
            metadata = {k: v for k, v in meta.items()}

        file_count = (
            sum(1 for _ in skill_dir.rglob("*") if _.is_file()) if skill_dir.exists() else 0
        )

        return SkillDetail(
            **skill.model_dump(),
            instructions_markdown=instructions_md,
            file_count=file_count,
            root_path=str(skill_dir),
            manifest_filename=manifest_filename,
            metadata=metadata,
            origin=await self._load_origin(skill.id),
        )

    async def _load_origin(self, skill_id: str) -> SkillOrigin | None:
        """Read import provenance off the ``valuz_skill_index`` row, if any."""
        row = await self._ds.get_by_id(skill_id)
        if row is None or not row.origin_json:
            return None
        try:
            return SkillOrigin.model_validate_json(row.origin_json)
        except ValueError:
            return None  # tolerate a legacy / malformed blob

    # ------------------------------------------------------------------
    # URL import (T1.3)
    # ------------------------------------------------------------------

    async def import_url_preview(
        self,
        url: str,
        target_scope: str = "user",
        workspace_id: str | None = None,
    ) -> SkillImportArchivePreview:
        import urllib.request

        from valuz_agent.modules.skills.errors import SkillImportFailed

        # Everything for this import is extracted UNDER ``staging_dir`` so the
        # whole tree is reclaimed by a single rmtree once every candidate preview
        # has been confirmed or expired (ref-counted in ``_cleanup_preview``).
        staging_dir = Path(tempfile.mkdtemp(prefix="valuz-skill-url-"))

        try:
            if self._is_github_url(url):
                fetched_root = self._fetch_github_tree(url, staging_dir)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Valuz-Agent/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read()

                downloaded = staging_dir / "download"
                downloaded.write_bytes(content)

                suffix = Path(url.split("?")[0]).suffix.lower()
                if suffix in {".zip", ".tar", ".gz", ".tgz"}:
                    fetched_root = self._extract_archive(downloaded, dest=staging_dir / "extract")
                else:
                    downloaded.rename(staging_dir / "SKILL.md")
                    fetched_root = staging_dir
        except Exception as e:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise SkillImportFailed(f"Failed to fetch URL: {e}") from e

        # A URL may point at a single skill OR a collection/plugin holding many
        # (``skills/<name>/SKILL.md`` …). Enumerate ALL of them so the caller can
        # multi-select — never silently grab the first.
        skill_roots = self._locate_skill_roots(fetched_root)
        if not skill_roots:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise SkillImportFailed("No SKILL.md found in the fetched content")

        return await self._build_multi_preview(
            skill_roots=skill_roots,
            fetched_root=fetched_root,
            target_scope=target_scope,
            workspace_id=workspace_id,
            source_url=url,
            cleanup_root=staging_dir,
        )

    async def _build_multi_preview(
        self,
        *,
        skill_roots: list[Path],
        fetched_root: Path,
        target_scope: str,
        workspace_id: str | None,
        source_url: str,
        cleanup_root: Path,
    ) -> SkillImportArchivePreview:
        """Build a preview that lists every skill found under the source.

        The FIRST skill is the top-level preview (single-skill clients keep
        working); ``skills`` carries all candidates, each with its own
        ``preview_id`` so confirm is called once per chosen skill. Each
        candidate's import provenance is staged in ``_import_origins`` keyed by
        its ``preview_id`` for ``confirm_url_import`` to persist.

        Every candidate is a subdir of the SHARED ``cleanup_root`` (the import's
        staging dir). The root is ref-counted so confirming/cleaning up one
        skill never deletes a sibling's source — the staging dir is reclaimed
        only once the last candidate is consumed or expired.
        """
        import time

        origin_type: Literal["github", "url"] = (
            "github" if self._is_github_url(source_url) else "url"
        )

        primary: SkillImportArchivePreview | None = None
        candidates: list[SkillImportCandidate] = []
        for root in skill_roots:
            preview_id = str(uuid4())
            _import_previews[preview_id] = (root, cleanup_root, time.time())
            self._incref_cleanup_root(cleanup_root)
            preview = await self._build_import_preview(
                preview_id=preview_id,
                skill_root=root,
                target_scope=target_scope,
                workspace_id=workspace_id,
            )
            try:
                relpath = str(root.relative_to(fetched_root))
            except ValueError:
                relpath = root.name
            _import_origins[preview_id] = SkillOrigin(
                type=origin_type,
                source_url=source_url,
                path="" if relpath in (".", "") else relpath,
            )
            candidates.append(
                SkillImportCandidate(
                    preview_id=preview_id,
                    name=preview.name,
                    description=preview.description,
                    file_count=len(preview.file_tree),
                    relpath=relpath,
                )
            )
            if primary is None:
                primary = preview

        assert primary is not None  # skill_roots is non-empty (checked by caller)
        primary.skills = candidates
        return primary

    async def confirm_url_import(
        self,
        payload: SkillImportUrlConfirmRequest,
    ) -> SkillView:
        import time

        from valuz_agent.modules.skills.errors import PreviewExpired

        entry = _import_previews.get(payload.preview_id)
        if entry is None or len(entry) != 3:
            raise PreviewExpired("Import preview not found or expired")
        skill_root, _cleanup_root, created_at = entry
        if time.time() - created_at > 600:
            self._cleanup_preview(payload.preview_id)
            raise PreviewExpired()

        self._enforce_import_caps(skill_root)

        final_name = payload.name or skill_root.name
        target_dir = await self._allocate_skill_dir(
            target_scope=payload.target_scope,
            workspace_id=payload.workspace_id,
            name=final_name,
        )
        shutil.copytree(skill_root, target_dir, dirs_exist_ok=True)

        workspace = await self._resolve_workspace(payload.workspace_id)
        if payload.target_scope == "project" and workspace is not None:
            self._ds.set_skill_enabled(workspace, str(target_dir), True)
        elif payload.add_to_workspace and workspace is not None and workspace.kind == "project":
            self._ds.set_skill_enabled(workspace, str(target_dir), True)
        # Capture provenance before cleanup pops it.
        origin = _import_origins.get(payload.preview_id)
        self._cleanup_preview(payload.preview_id)
        # URL import gets the same "imported" badge as archive / directory
        # imports — host bookkeeping in valuz_skill_index, never SKILL.md.
        skill = await self._finalize_origin(target_dir, "imported", payload.workspace_id)
        if origin is not None:
            await self._ds.set_origin_metadata(skill.id, origin.model_dump_json())
        return skill

    def _enforce_import_caps(self, skill_root: Path) -> None:
        """Reject a staged skill that exceeds the per-import size/count caps.

        Walks the staged tree once and raises ``SkillImportFailed`` on the first
        breach (file too big, too many files, or bundle too large) so a
        pathological repo can't be copied wholesale into the library.
        """
        from valuz_agent.modules.skills.errors import SkillImportFailed

        total = 0
        count = 0
        for path in skill_root.rglob("*"):
            if not path.is_file():
                continue
            count += 1
            if count > _MAX_IMPORT_FILE_COUNT:
                raise SkillImportFailed(
                    f"Import exceeds the {_MAX_IMPORT_FILE_COUNT}-file limit"
                )
            size = path.stat().st_size
            if size > _MAX_IMPORT_FILE_BYTES:
                raise SkillImportFailed(
                    f"File '{path.name}' exceeds the "
                    f"{_MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MiB per-file limit"
                )
            total += size
            if total > _MAX_IMPORT_TOTAL_BYTES:
                raise SkillImportFailed(
                    f"Import bundle exceeds the "
                    f"{_MAX_IMPORT_TOTAL_BYTES // (1024 * 1024)} MiB limit"
                )

    # ------------------------------------------------------------------
    # GitHub URL import helpers
    # ------------------------------------------------------------------

    def _is_github_url(self, url: str) -> bool:
        return "github.com" in url.lower()

    def _fetch_github_tree(self, url: str, staging_dir: Path) -> Path:
        """Fetch a GitHub URL into a local dir and return that RAW tree.

        Always downloads the **zipball via codeload** — a single request that
        does NOT count against the GitHub REST rate limit — then descends to the
        requested subdirectory. The caller runs ``_locate_skill_roots`` over the
        result so a collection/plugin surfaces every skill, not just the first.

        Handles repo-root URLs (``github.com/owner/repo``), ``/tree/<ref>/<dir>``,
        ``/blob/<ref>/<file>``, and raw URLs. The ref may itself contain ``/``
        (e.g. ``release/v2`` or a ``dependabot/...`` branch): GitHub web URLs do
        not delimit where the ref ends and the in-repo path begins, so for
        tree/blob/raw URLs we try each ref/path split point — shortest ref first
        — against codeload (no API call) and accept the first whose ref resolves
        AND whose sub-path exists. The plain ``zip/<ref>`` form resolves a
        branch, tag, OR commit SHA in one request.
        """
        parsed = self._parse_github_url(url)
        if parsed is None:
            raise ValueError(f"Could not parse GitHub URL: {url}")

        owner, repo, segments = parsed

        if segments is None:
            # Bare repo URL — the whole repository is the skill source.
            target = staging_dir / "repo.zip"
            self._download_repo_zipball(owner, repo, target)
            return self._extract_to_subdir(target, "")

        tried: list[str] = []
        for split in range(1, len(segments) + 1):
            ref = "/".join(segments[:split])
            subdir = "/".join(segments[split:])
            tried.append(ref)
            target = staging_dir / f"repo-{split}.zip"
            if not self._try_download_codeload(owner, repo, ref, target):
                continue  # ref does not exist at this split — try a longer ref
            try:
                return self._extract_to_subdir(target, subdir)
            except FileNotFoundError:
                # The ref resolved but this split's sub-path isn't in it — the
                # boundary guess was wrong (e.g. a branch named like the first
                # path segment). Keep trying longer refs.
                continue
        raise ValueError(
            f"Could not resolve a branch/tag/commit in GitHub URL {url} — tried refs: "
            + ", ".join(tried)
        )

    def _extract_to_subdir(self, zip_path: Path, subdir: str) -> Path:
        """Extract a codeload zipball and descend to ``subdir``.

        A GitHub zipball wraps everything in a single ``owner-repo-<sha>/`` dir.
        Raises ``FileNotFoundError`` when ``subdir`` is not present so the caller
        can treat it as a wrong ref/path split and retry a longer ref.

        Extraction lands next to the zip (under the URL import's staging dir) so
        a single cleanup of that staging dir reclaims everything.
        """
        extracted = self._extract_archive(zip_path, dest=zip_path.parent / f"{zip_path.stem}-x")
        tops = [p for p in extracted.iterdir() if p.is_dir()]
        root = tops[0] if len(tops) == 1 else extracted
        if subdir and subdir not in (".", ""):
            sub = root / subdir
            if not sub.is_dir():
                raise FileNotFoundError(subdir)
            return sub
        return root

    def _try_download_codeload(self, owner: str, repo: str, ref: str, target: Path) -> bool:
        """Download the codeload zipball for ``ref`` (branch/tag/commit) into
        ``target``. Returns ``True`` on success, ``False`` if the ref does not
        exist (404). Other HTTP errors propagate.

        Uses the plain ``zip/<ref>`` form, which resolves a branch, tag, or
        commit SHA — including refs that contain ``/`` — in a single request and
        without touching the rate-limited GitHub REST API.
        """
        import urllib.error
        from urllib.parse import quote

        escaped = "/".join(quote(part, safe="") for part in ref.split("/"))
        codeload = f"https://codeload.github.com/{owner}/{repo}/zip/{escaped}"
        try:
            self._download_file(codeload, target)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def _download_repo_zipball(self, owner: str, repo: str, target: Path) -> str:
        """Download a **bare repo's** default-branch zipball; return the ref used.

        Tries the common defaults ``main`` then ``master`` against codeload —
        which, unlike the GitHub REST API, is not rate-limited — and only falls
        back to the API (to learn a non-standard default branch) if neither
        exists. Keeps the common case API-free so bare-repo imports succeed even
        when the REST API is rate-limited.
        """
        for cand in ("main", "master"):
            if self._try_download_codeload(owner, repo, cand, target):
                return cand
        # Default branch is neither main nor master — last resort: ask the API
        # (this path can rate-limit; GITHUB_TOKEN raises the cap when set).
        api_branch = self._github_default_branch(owner, repo)
        if not self._try_download_codeload(owner, repo, api_branch, target):
            raise ValueError(
                f"Could not download default branch '{api_branch}' for {owner}/{repo}"
            )
        return api_branch

    def _github_default_branch(self, owner: str, repo: str) -> str:
        import json
        import os
        import urllib.request

        headers = {
            "User-Agent": "Valuz-Agent/1.0",
            "Accept": "application/vnd.github.v3+json",
        }
        # Unauthenticated api.github.com is capped at 60 req/hour per IP, which a
        # shared/self-hosted host exhausts fast (surfacing as 403 during import).
        # A GITHUB_TOKEN raises the cap to 5000/hour.
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return str(data.get("default_branch") or "main")

    def _parse_github_url(self, url: str) -> tuple[str, str, list[str] | None] | None:
        """Parse a GitHub URL into ``(owner, repo, segments)``.

        ``segments`` is the list of raw (url-decoded) path segments that jointly
        encode ``(ref, in-repo dir)`` after ``/tree/`` or ``/blob/`` — the caller
        resolves where the ref ends, because a ref may contain ``/``. It is
        ``None`` for a bare repo URL (whole repo, default branch). For ``/blob/``
        and raw URLs the trailing filename segment is dropped (its parent dir is
        the skill dir).
        """
        import re
        from urllib.parse import unquote

        def _segments(rest: str, *, drop_last: bool) -> list[str]:
            parts = [unquote(p) for p in rest.split("/") if p]
            if drop_last and parts:
                parts = parts[:-1]
            return parts

        tree_match = re.match(
            r"https?://github\.com/([^/]+)/([^/]+)/tree/(.+)",
            url,
        )
        if tree_match:
            owner, repo, rest = tree_match.groups()
            return owner, repo, _segments(rest, drop_last=False)

        blob_match = re.match(
            r"https?://github\.com/([^/]+)/([^/]+)/blob/(.+)",
            url,
        )
        if blob_match:
            owner, repo, rest = blob_match.groups()
            return owner, repo, _segments(rest, drop_last=True)

        raw_match = re.match(
            r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(.+)",
            url,
        )
        if raw_match:
            owner, repo, rest = raw_match.groups()
            return owner, repo, _segments(rest, drop_last=True)

        # Bare repo URL — ``github.com/owner/repo`` (optionally ``.git`` /
        # trailing slash). No ref or subdirectory → segments is None.
        repo_match = re.match(
            r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            url,
        )
        if repo_match:
            owner, repo = repo_match.groups()
            return owner, repo, None

        return None

    def _download_file(self, url: str, target: Path) -> None:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Valuz-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resp.read())

    # ── Skill submissions (companion to ``submit_skill`` tool) ────────

    async def confirm_submission(
        self,
        session_id: str,
        slug: str,
        *,
        summary: str | None = None,
        change_kind: str = "create",
        files_touched: list[str] | None = None,
    ) -> tuple[SkillView, dict[str, str], str | None]:
        """Promote the staged slug into the user library and apply the
        per-entry-point side effects encoded in the session's
        ``creation_context``.

        Returns ``(skill, creation_context, bound_to_workspace_id)``.
        """
        from valuz_agent.adapters import kernel_store
        from valuz_agent.modules.skills import staging

        kernel_session = await kernel_store.load_session(session_id)
        if kernel_session is None:
            raise KeyError(f"session not found: {session_id!r}")
        valuz_meta = (kernel_session.metadata or {}).get("valuz") or {}
        creation_context_raw = valuz_meta.get("creation_context") or {}
        creation_context: dict[str, str] = {
            str(k): str(v) for k, v in creation_context_raw.items() if v is not None
        }
        # ``creation_context`` is set by the explicit
        # ``/v1/skills/create/start`` launcher, but organic chat sessions
        # that just happen to use the skill-creator skill never go
        # through that endpoint. Infer from the session's workspace
        # instead so the right side-effects fire (project-scoped session
        # → bind to project; chat session → library only).
        if "kind" not in creation_context:
            inferred_workspace_id = str(kernel_session.project_id or "")
            workspace = await self._resolve_workspace(inferred_workspace_id)
            if workspace is not None and workspace.kind == "project":
                creation_context["kind"] = "project"
                creation_context["workspace_id"] = inferred_workspace_id
            else:
                creation_context["kind"] = "chat"

        # Locate the staged slug. The staging dir resolves to
        # ``{workspace_cwd}/.skill-staging/`` — the ``submit_skill`` tool
        # rejects calls where the slug isn't already at that location,
        # so reaching this code path with the slug missing means either
        # the staging was wiped between submission and confirm or the
        # tool's validator was bypassed.
        canonical_dir = staging.staging_dir_for_session(session_id) / slug
        if not canonical_dir.is_dir():
            raise KeyError(
                f"no staging slug {slug!r} for session {session_id!r} at "
                f"{canonical_dir} — the agent's staging files appear to "
                f"have been removed since ``submit_skill`` was called. "
                f"Ask the agent to regenerate the skill."
            )

        # Always promote into the user library — agentskills.io standard
        # location managed by ``fs_registry.user_skill_root()``.
        result = staging.sync_slug(
            session_id=session_id,
            slug=slug,
            strategy="overwrite",
            target_root=_default_user_skill_root(),
        )
        if not result.written_path:
            raise RuntimeError("staging.sync_slug returned no written_path")

        # Refresh the catalog so the new skill is queryable.
        try:
            await self.startup_scan()
        except Exception:  # noqa: BLE001
            pass

        bound_workspace_id: str | None = None
        if creation_context["kind"] == "project":
            bound_workspace_id = creation_context.get("workspace_id")
            if bound_workspace_id:
                workspace = await self._resolve_workspace(bound_workspace_id)
                if workspace is not None and workspace.kind == "project":
                    self._ds.set_skill_enabled(workspace, str(result.written_path), True)

        # Best-effort cleanup of the staging slug after promotion.
        try:
            staging.remove_slug(session_id, slug)
        except Exception:  # noqa: BLE001
            pass

        skill = await self._resolve_created_skill(
            Path(result.written_path), workspace_id=bound_workspace_id
        )
        # The skill-creator AI flow landing a skill is a "created" act.
        # creation_origin is host bookkeeping in valuz_skill_index — the
        # startup_scan above created the row as "discovered"; overwrite it.
        await self._ds.set_creation_origin(skill.id, "created")
        skill.creation_origin = "created"

        # Notify subscribers — frontend reloads the catalog & cards.
        self._bus.publish(
            SKILL_CHANGED,
            skill_id=skill.id,
            reason="submission-confirmed",
            change_kind=change_kind,
            summary=summary or "",
            files_touched=list(files_touched or []),
        )
        if bound_workspace_id:
            self._bus.publish(WORKSPACE_SKILLS_CHANGED, workspace_id=bound_workspace_id)
        else:
            self._bus.publish(WORKSPACE_SKILLS_CHANGED, workspace_id="chat-default")

        return skill, creation_context, bound_workspace_id

    def dismiss_submission(self, session_id: str, slug: str) -> bool:
        """Discard the staged slug — no library write, no DB write.

        Returns ``True`` when something was actually removed; ``False``
        when the staging dir was already empty (idempotent dismiss).
        """
        from valuz_agent.modules.skills import staging

        staging_slug_dir = staging.staging_dir_for_session(session_id) / slug
        existed = staging_slug_dir.is_dir()
        if existed:
            staging.remove_slug(session_id, slug)
        return existed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_workspace(self, workspace_id: str | None):  # type: ignore[no-untyped-def]
        if workspace_id is None:
            return None
        return await self._workspaces.get_workspace(workspace_id)

    async def _check_entitlement(self, entitlement: str) -> bool:
        if self._auth is None:
            return False
        try:
            entitlements = await self._auth.get_entitlements()  # type: ignore[union-attr]
            return entitlement in entitlements
        except Exception:
            return False

    async def _resolve_skill(self, skill_id: str, workspace_id: str | None = None) -> SkillView:
        workspace_id = workspace_id or "chat-default"
        catalog = await self.list_catalog(workspace_id)
        for skill in catalog.skills:
            if skill.id == skill_id:
                return skill
        raise KeyError(skill_id)

    async def _resolve_created_skill(
        self, skill_dir: Path, workspace_id: str | None = None
    ) -> SkillView:
        workspace_id = workspace_id or "chat-default"
        catalog = await self.list_catalog(workspace_id)
        resolved = str(skill_dir.resolve(strict=False))
        for skill in catalog.skills:
            if str(Path(skill.path).resolve(strict=False)) == resolved:
                return skill
        fallback_workspace = next(
            (item.id for item in await self._workspaces.list_workspaces() if item.kind == "chat"),
            "chat-default",
        )
        catalog = await self.list_catalog(fallback_workspace)
        for skill in catalog.skills:
            if str(Path(skill.path).resolve(strict=False)) == resolved:
                return skill
        raise KeyError(str(skill_dir))

    async def _finalize_origin(
        self,
        skill_dir: Path,
        origin: Literal["created", "imported"],
        workspace_id: str | None = None,
    ) -> SkillView:
        """Index a freshly-written skill folder and stamp its creation origin.

        ``creation_origin`` is host-side bookkeeping kept only in the
        ``valuz_skill_index`` DB row — never written into SKILL.md, so a
        user's skill files stay clean and a synced skill can't be
        relabelled by a stray frontmatter key. ``startup_scan`` creates
        the index row (new rows default to ``"discovered"``); this then
        overwrites that single column to mark the skill as
        Valuz-originated (``"created"`` / ``"imported"``) and returns the
        resolved view with the value already patched in so the caller
        doesn't have to re-query.
        """
        try:
            await self.startup_scan()
        except Exception:  # noqa: BLE001
            pass
        skill = await self._resolve_created_skill(skill_dir, workspace_id=workspace_id)
        await self._ds.set_creation_origin(skill.id, origin)
        skill.creation_origin = origin
        return skill

    async def _allocate_skill_dir(
        self,
        target_scope: str,
        workspace_id: str | None,
        name: str,
    ) -> Path:
        slug = self._slugify(name)
        root = await self._scope_root(target_scope=target_scope, workspace_id=workspace_id)
        root.mkdir(parents=True, exist_ok=True)
        candidate = root / slug
        suffix = 1
        while candidate.exists():
            suffix += 1
            candidate = root / f"{slug}-{suffix}"
        return candidate

    async def _scope_root(self, target_scope: str, workspace_id: str | None) -> Path:
        if target_scope == "user":
            return _default_user_skill_root()
        if workspace_id is None:
            raise ValueError("workspace_id is required for project-scoped skills")
        workspace = await self._workspaces.get_workspace(workspace_id)
        if workspace.kind != "project":
            raise ValueError("project-scoped skills require a project workspace")
        return Path(workspace.root_path) / ".claude" / "skills"

    def _write_manifest(
        self,
        skill_dir: Path,
        name: str,
        description: str,
        instructions_markdown: str | None,
    ) -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = skill_dir / "SKILL.md"
        manifest_path.write_text(
            self._render_manifest(
                name=name,
                description=description,
                instructions_markdown=(
                    instructions_markdown or description or "Skill generated by Valuz."
                ),
            ),
            encoding="utf-8",
        )

    def _render_manifest(
        self,
        *,
        name: str,
        description: str,
        instructions_markdown: str,
        tags: list[str] | None = None,
    ) -> str:
        """Render a SKILL.md manifest with frontmatter.

        Origin tracking (created / imported / discovered) is NOT written
        here — it's host bookkeeping kept in the ``valuz_skill_index`` DB
        row so the user's skill files stay clean. See ``_finalize_origin``.
        """
        tag_values = tags or ["research"]
        rendered_tags = ", ".join(f'"{tag}"' for tag in tag_values)
        body = instructions_markdown.strip() or "Skill generated by Valuz."
        if not body.startswith("#"):
            body = f"# {name}\n\n{body}"
        return (
            "---\n"
            f'name: "{name}"\n'
            f'description: "{description}"\n'
            f"tags: [{rendered_tags}]\n"
            "---\n\n"
            f"{body}\n"
        )

    async def _affected_projects(self, skill_path: str) -> list[SkillDeleteAffectedProject]:
        resolved = str(Path(skill_path).expanduser().resolve(strict=False))
        affected: list[SkillDeleteAffectedProject] = []
        for workspace in await self._workspaces.list_workspaces():
            if workspace.kind != "project":
                continue
            if resolved in self._ds.enabled_skill_paths(workspace):
                affected.append(
                    SkillDeleteAffectedProject(
                        workspace_id=workspace.id,
                        name=workspace.name,
                    )
                )
        return affected

    @staticmethod
    def _slugify(name: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
        return cleaned or "skill"

    @staticmethod
    def _collect_session_assistant_text(events: list) -> str:  # type: ignore[type-arg]
        chunks: list[str] = []
        for row in events:
            event_type = getattr(row, "event_type", None)
            if event_type == "message.assistant.delta":
                payload_raw = getattr(row, "payload_json", None)
                if payload_raw:
                    try:
                        payload = json.loads(payload_raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    text = payload.get("text")
                    if text:
                        chunks.append(text)
        return "".join(chunks).strip()

    def _extract_archive(self, archive_path: Path, dest: Path | None = None) -> Path:
        """Extract an archive and return the directory it was extracted into.

        ``dest`` lets the caller place the extraction under a known root (e.g. a
        URL import's shared staging dir) so a single ``rmtree`` of that root
        cleans everything up; without it a fresh temp dir is created.
        """
        staging_dir = dest if dest is not None else Path(
            tempfile.mkdtemp(prefix="valuz-skill-import-")
        )
        staging_dir.mkdir(parents=True, exist_ok=True)
        suffix = archive_path.suffix.lower()
        if suffix == ".zip":
            with zipfile.ZipFile(archive_path) as zipped:
                zipped.extractall(staging_dir)
            return staging_dir
        if suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} or archive_path.name.endswith(
            (".tar.gz", ".tar.bz2", ".tar.xz")
        ):
            with tarfile.open(archive_path) as tarred:
                tarred.extractall(staging_dir)
            return staging_dir
        raise ValueError("Only .zip and .tar archives are supported")

    def _locate_skill_root(
        self,
        extracted_root: Path,
        *,
        missing_manifest_message: str,
    ) -> Path:
        if _detect_manifest(extracted_root) is not None:
            return extracted_root
        directories = [path for path in extracted_root.iterdir() if path.is_dir()]
        for directory in directories:
            if _detect_manifest(directory) is not None:
                return directory
        for directory in directories:
            for candidate in directory.rglob("*"):
                if candidate.is_dir() and _detect_manifest(candidate) is not None:
                    return candidate
        raise ValueError(missing_manifest_message)

    def _locate_skill_roots(self, root: Path) -> list[Path]:
        """Every distinct skill directory under ``root`` — i.e. each directory
        that DIRECTLY contains a SKILL.md.

        Prunes: once a directory is recognised as a skill, its own subtree is
        not re-scanned (a skill's internal folders aren't sub-skills). So a
        collection or Claude plugin laid out as ``skills/<name>/SKILL.md`` yields
        one entry per skill instead of the old behaviour of silently returning
        the first SKILL.md found anywhere in the tree.

        Returns ``[]`` when no SKILL.md exists; ``[root]`` when ``root`` itself
        is a skill.
        """
        if _detect_manifest(root) is not None:
            return [root]

        found: list[Path] = []

        def _walk(directory: Path) -> None:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
            for child in children:
                if _detect_manifest(child) is not None:
                    found.append(child)  # a skill — do not descend into it
                else:
                    _walk(child)

        _walk(root)
        return found

    async def _build_import_preview(
        self,
        *,
        preview_id: str,
        skill_root: Path,
        target_scope: str,
        workspace_id: str | None,
    ) -> SkillImportArchivePreview:
        manifest_path = _detect_manifest(skill_root)
        if manifest_path is None:
            raise ValueError("Selected folder must contain SKILL.md or skill.md")
        metadata, _body = _extract_frontmatter(_read_text(manifest_path))
        name = str(metadata.get("name") or skill_root.name)
        description = str(metadata.get("description") or "Imported local skill")
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        target_root = await self._scope_root(target_scope=target_scope, workspace_id=workspace_id)
        slug = self._slugify(name)
        suggested_name = None
        name_conflict = (target_root / slug).exists()
        if name_conflict:
            suggested_name = f"{name} (1)"
        return SkillImportArchivePreview(
            preview_id=preview_id,
            name=name,
            description=description,
            tags=[str(tag) for tag in tags],
            file_tree=self._build_file_tree(skill_root),
            validation_warnings=[],
            name_conflict=name_conflict,
            suggested_name=suggested_name,
        )

    def _build_file_tree(self, root: Path) -> list[SkillImportPreviewFile]:
        items: list[SkillImportPreviewFile] = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            items.append(
                SkillImportPreviewFile(
                    path=str(relative),
                    type="directory" if path.is_dir() else "file",
                    size=None if path.is_dir() else path.stat().st_size,
                )
            )
        return items

    def _build_skill_file_tree(
        self,
        root: Path,
        anchor: Path | None = None,
    ) -> list[SkillFileNode]:
        # ``anchor`` is the skill root that *every* node's ``path`` is
        # rendered relative to. Recursive calls pass the same anchor so
        # nested file paths stay full (``templates/report.md``) instead
        # of collapsing to basenames (``report.md``) — without this the
        # ``GET /skills/{id}/files/{file_path}`` lookup 404s for any
        # file living inside a subdirectory because the panel sends the
        # basename and the server resolves it against the skill root.
        base = anchor or root
        nodes: list[SkillFileNode] = []
        for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                nodes.append(
                    SkillFileNode(
                        name=entry.name,
                        path=str(entry.relative_to(base)),
                        type="directory",
                        children=self._build_skill_file_tree(entry, base),
                    )
                )
            elif entry.is_file():
                nodes.append(
                    SkillFileNode(
                        name=entry.name,
                        path=str(entry.relative_to(base)),
                        type="file",
                        size=entry.stat().st_size,
                    )
                )
        return nodes

    def _incref_cleanup_root(self, root: Path) -> None:
        key = str(root)
        _import_cleanup_refs[key] = _import_cleanup_refs.get(key, 0) + 1

    def _decref_cleanup_root(self, root: Path) -> None:
        """Drop one reference to a shared staging dir; rmtree it at zero."""
        key = str(root)
        remaining = _import_cleanup_refs.get(key, 1) - 1
        if remaining > 0:
            _import_cleanup_refs[key] = remaining
            return
        _import_cleanup_refs.pop(key, None)
        shutil.rmtree(root, ignore_errors=True)

    def _cleanup_preview(self, preview_id: str) -> None:
        _import_origins.pop(preview_id, None)
        preview = _import_previews.pop(preview_id, None)
        if preview is None:
            return
        # URL/GitHub import: (skill_root, cleanup_root: Path, created_at). All
        # candidates share cleanup_root, so decref and only rmtree at zero —
        # never delete the shared tree out from under a sibling's confirm.
        if len(preview) == 3:
            cleanup_root = preview[1]
            self._decref_cleanup_root(cleanup_root)
            return
        # archive/directory import: (skill_root, managed_temp). A managed temp
        # extraction lives one level above the skill root.
        preview_root, managed_temp = preview
        if managed_temp:
            shutil.rmtree(preview_root.parent, ignore_errors=True)
