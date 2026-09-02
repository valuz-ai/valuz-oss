from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from sqlalchemy import case, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.skills.contracts import ProjectRef, RuntimeContext, SkillManifest
from valuz_agent.modules.skills.models import (
    ProjectSkillConfigRow,
    SkillIndexRow,
)


class SkillDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._config_name = "project-config.json"

    @property
    def session(self) -> AsyncSession:
        """The bound DB session — a narrow, intentional escape hatch for
        cross-module hooks that need this datastore's session (e.g. the
        marketplace-install-provenance cleanup on skill delete) without
        reaching into a sibling module's own datastore."""
        return self._db

    # ------------------------------------------------------------------
    # DB-backed SkillIndexRow CRUD (retained for future startup_scan)
    # ------------------------------------------------------------------

    async def list_skills(
        self,
        user_id: str,
        query: str | None = None,
        scope: str | None = None,
    ) -> list[SkillIndexRow]:
        stmt = select(SkillIndexRow).where(SkillIndexRow.user_id == user_id)
        if scope:
            stmt = stmt.filter_by(scope=scope)
        if query:
            stmt = stmt.filter(SkillIndexRow.name.ilike(f"%{query}%"))
        stmt = stmt.order_by(SkillIndexRow.name)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_id(self, user_id: str, skill_id: str) -> SkillIndexRow | None:
        return (
            (
                await self._db.execute(
                    select(SkillIndexRow).where(
                        SkillIndexRow.id == skill_id, SkillIndexRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    # Same-slug rows can now coexist across scopes (an ``official`` bundled copy
    # AND a ``user`` copy under ~/.agents/skills). ``get_by_slug`` is the "which
    # copy is effective for this slug" resolver — used by agent runtime skill
    # resolution and read APIs — so it returns the highest-priority row
    # deterministically: official > project > user, ``source_path`` as a stable
    # final tiebreak. Row-precise writes (create/import/delete stamping) must use
    # ``get_by_source_path`` instead, never this.
    _SCOPE_PRIORITY = case(
        (SkillIndexRow.scope == "official", 0),
        (SkillIndexRow.scope == "project", 1),
        else_=2,
    )

    async def get_by_slug(self, user_id: str, slug: str) -> SkillIndexRow | None:
        return (
            (
                await self._db.execute(
                    select(SkillIndexRow)
                    .where(
                        SkillIndexRow.user_id == user_id,
                        SkillIndexRow.slug == slug,
                    )
                    .order_by(self._SCOPE_PRIORITY, SkillIndexRow.source_path)
                )
            )
            .scalars()
            .first()
        )

    async def get_by_source_path(self, user_id: str, source_path: str) -> SkillIndexRow | None:
        """The row for one on-disk skill directory — the business identity.

        This is the exact-row lookup the upsert and the create/import/delete
        stamping paths use, so a same-slug copy in another scope is never
        touched by accident."""
        return (
            (
                await self._db.execute(
                    select(SkillIndexRow).where(
                        SkillIndexRow.user_id == user_id,
                        SkillIndexRow.source_path == source_path,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def set_creation_origin(self, user_id: str, skill_id: str, origin: str) -> None:
        """Stamp ``creation_origin`` on an existing ``valuz_skill_index`` row.

        ``creation_origin`` is host-only bookkeeping — it never touches
        SKILL.md. The row is expected to exist (``startup_scan`` creates
        it just before this is called from a create / import flow); a
        missing row is a no-op rather than an error, since the next
        ``startup_scan`` recreates it as ``"discovered"`` anyway.
        """
        row = await self.get_by_id(user_id, skill_id)
        if row is None:
            return
        row.creation_origin = origin
        await async_commit_with_retry(self._db, where="SkillDatastore.set_creation_origin")

    async def set_creation_origin_by_slug(self, user_id: str, slug: str, origin: str) -> None:
        row = await self.get_by_slug(user_id, slug)
        if row is None:
            return
        row.creation_origin = origin
        await async_commit_with_retry(self._db, where="SkillDatastore.set_creation_origin_by_slug")

    async def set_creation_origin_by_path(
        self, user_id: str, source_path: str, origin: str
    ) -> None:
        """Row-precise variant of ``set_creation_origin_by_slug`` — stamps the
        exact skill folder the create/import flow just wrote, so a same-slug
        copy in another scope is never mislabeled."""
        row = await self.get_by_source_path(user_id, source_path)
        if row is None:
            return
        row.creation_origin = origin
        await async_commit_with_retry(self._db, where="SkillDatastore.set_creation_origin_by_path")

    async def set_artifact_id_by_path(
        self, user_id: str, source_path: str, artifact_id: str
    ) -> None:
        """Bind a skill folder to its version lineage. Host-only bookkeeping
        like ``creation_origin`` — the scan never writes it. A missing row is
        a no-op."""
        row = await self.get_by_source_path(user_id, source_path)
        if row is None:
            return
        row.artifact_id = artifact_id
        await async_commit_with_retry(self._db, where="SkillDatastore.set_artifact_id_by_path")

    async def set_origin_metadata(self, user_id: str, skill_id: str, origin_json: str) -> None:
        """Stamp import provenance (``origin_json``) on an existing row.

        Host-only bookkeeping like ``creation_origin`` — never touches SKILL.md
        and survives ``startup_scan`` rescans (the scan never writes this
        column). A missing row is a no-op.
        """
        row = await self.get_by_id(user_id, skill_id)
        if row is None:
            return
        row.origin_json = origin_json
        await async_commit_with_retry(self._db, where="SkillDatastore.set_origin_metadata")

    async def set_origin_metadata_by_slug(self, user_id: str, slug: str, origin_json: str) -> None:
        row = await self.get_by_slug(user_id, slug)
        if row is None:
            return
        row.origin_json = origin_json
        await async_commit_with_retry(self._db, where="SkillDatastore.set_origin_metadata_by_slug")

    async def set_origin_metadata_by_path(
        self, user_id: str, source_path: str, origin_json: str
    ) -> None:
        """Row-precise variant of ``set_origin_metadata_by_slug`` (see
        ``set_creation_origin_by_path``)."""
        row = await self.get_by_source_path(user_id, source_path)
        if row is None:
            return
        row.origin_json = origin_json
        await async_commit_with_retry(self._db, where="SkillDatastore.set_origin_metadata_by_path")

    async def create(self, user_id: str, row: SkillIndexRow) -> SkillIndexRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="SkillDatastore.create")
        return row

    async def update(self, row: SkillIndexRow) -> SkillIndexRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SkillDatastore.update")
        return row

    async def delete(self, user_id: str, skill_id: str) -> None:
        await self._db.execute(
            sa_delete(SkillIndexRow).where(
                SkillIndexRow.id == skill_id, SkillIndexRow.user_id == user_id
            )
        )
        await async_commit_with_retry(self._db, where="SkillDatastore.delete")

    async def mark_unavailable_by_slug(self, user_id: str, slug: str) -> None:
        row = await self.get_by_slug(user_id, slug)
        if row is None:
            return
        row.status = "unavailable"
        await async_commit_with_retry(self._db, where="SkillDatastore.mark_unavailable_by_slug")

    async def mark_unavailable_by_path(self, user_id: str, source_path: str) -> None:
        """Row-precise variant of ``mark_unavailable_by_slug`` — used by delete
        so removing a user copy never marks a same-slug official row absent."""
        row = await self.get_by_source_path(user_id, source_path)
        if row is None:
            return
        row.status = "unavailable"
        await async_commit_with_retry(self._db, where="SkillDatastore.mark_unavailable_by_path")

    async def list_project_skills(
        self, user_id: str, project_id: str
    ) -> list[ProjectSkillConfigRow]:
        return list(
            (
                await self._db.execute(
                    select(ProjectSkillConfigRow).where(
                        ProjectSkillConfigRow.project_id == project_id,
                        ProjectSkillConfigRow.user_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    async def set_project_skills(
        self, user_id: str, project_id: str, rows: list[ProjectSkillConfigRow]
    ) -> None:
        await self._db.execute(
            sa_delete(ProjectSkillConfigRow).where(
                ProjectSkillConfigRow.project_id == project_id,
                ProjectSkillConfigRow.user_id == user_id,
            )
        )
        for r in rows:
            r.user_id = user_id
        self._db.add_all(rows)
        await async_commit_with_retry(self._db, where="SkillDatastore.set_project_skills")

    # ------------------------------------------------------------------
    # Global library on/off switch (``valuz_skill_index.library_enabled``)
    # ------------------------------------------------------------------

    async def list_library_disabled_ids(self, user_id: str) -> set[str]:
        """Index-row ids currently OFF in the library."""
        rows = (
            await self._db.execute(
                select(SkillIndexRow.id).where(
                    SkillIndexRow.user_id == user_id,
                    SkillIndexRow.library_enabled.is_(False),
                )
            )
        ).scalars()
        return set(rows)

    async def list_library_disabled_slugs(self, user_id: str) -> set[str]:
        """Skill slugs currently OFF in the library."""
        rows = (
            await self._db.execute(
                select(SkillIndexRow.slug).where(
                    SkillIndexRow.user_id == user_id,
                    SkillIndexRow.library_enabled.is_(False),
                )
            )
        ).scalars()
        return set(rows)

    async def list_library_disabled_paths(self, user_id: str) -> set[str]:
        """Skill ``source_path``s currently OFF in the library.

        The path-keyed companion to ``list_library_disabled_slugs``: the catalog
        overlays the library switch per row, and same-slug rows can now coexist
        across scopes with different switch states (an ``official`` copy default
        ON, a ``discovered`` user copy default OFF). Keying the overlay on slug
        would let the disabled user copy wrongly hide the enabled official one;
        keying on ``source_path`` targets each row exactly."""
        rows = (
            await self._db.execute(
                select(SkillIndexRow.source_path).where(
                    SkillIndexRow.user_id == user_id,
                    SkillIndexRow.library_enabled.is_(False),
                )
            )
        ).scalars()
        return set(rows)

    async def set_library_enabled(self, user_id: str, skill_id: str, enabled: bool) -> None:
        """Set the global library switch on one index row (the Skills-page
        representative). No-op if the id is unknown to this owner."""
        row = await self.get_by_id(user_id, skill_id)
        if row is None:
            return
        row.library_enabled = enabled
        await async_commit_with_retry(self._db, where="SkillDatastore.set_library_enabled")

    async def set_library_enabled_by_slug(self, user_id: str, slug: str, enabled: bool) -> None:
        row = await self.get_by_slug(user_id, slug)
        if row is None:
            return
        row.library_enabled = enabled
        await async_commit_with_retry(self._db, where="SkillDatastore.set_library_enabled_by_slug")

    async def set_library_enabled_by_path(
        self, user_id: str, source_path: str, enabled: bool
    ) -> None:
        """Row-precise variant — flips the switch on the exact row the Skills
        page shows (resolved by its ``source_path``), so a toggle never leaks
        onto a same-slug copy in another scope."""
        row = await self.get_by_source_path(user_id, source_path)
        if row is None:
            return
        row.library_enabled = enabled
        await async_commit_with_retry(self._db, where="SkillDatastore.set_library_enabled_by_path")

    # ------------------------------------------------------------------
    # Filesystem-based project skill config (JSON project-config.json)
    # ------------------------------------------------------------------

    def list_project_skill_manifests(
        self,
        project: _ProjectLike,
        source: FilesystemSkillSource,
        *,
        compute_content_hash: bool = True,
    ) -> list[SkillManifest]:
        context = RuntimeContext(
            project=ProjectRef(
                id=project.id,
                slug=project.id,
                kind=project.kind,
                root_path=project.root_path,
            ),
        )
        manifests = source.list_skills(context, compute_content_hash=compute_content_hash)
        enabled_paths = self.enabled_skill_paths(project)

        items: list[SkillManifest] = []
        for manifest in manifests:
            enabled = project.kind == "chat" or manifest.path in enabled_paths
            items.append(manifest.model_copy(update={"enabled": enabled}))
        return items

    def enabled_skill_paths(self, project: _ProjectLike) -> set[str]:
        if project.kind != "project":
            return set()

        config = self._project_config_path(project)
        if not config.exists():
            return set()

        raw = json.loads(config.read_text(encoding="utf-8"))
        values = raw.get("skills_enabled", [])
        if not isinstance(values, list):
            return set()

        resolved: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = Path(project.root_path) / value
            resolved.add(str(candidate.resolve(strict=False)))
        return resolved

    def set_skill_enabled(
        self,
        project: _ProjectLike,
        skill_path: str,
        enabled: bool,
    ) -> set[str]:
        if project.kind != "project":
            return set()

        current = self.enabled_skill_paths(project)
        resolved_path = str(Path(skill_path).expanduser().resolve(strict=False))
        if enabled:
            current.add(resolved_path)
        else:
            current.discard(resolved_path)
        self._write_enabled_skill_paths(project, current)
        return current

    def overwrite_enabled_skill_paths(
        self,
        project: _ProjectLike,
        skill_paths: list[str],
    ) -> set[str]:
        if project.kind != "project":
            return set()

        resolved: set[str] = set()
        for skill_path in skill_paths:
            if not skill_path:
                continue
            candidate = Path(skill_path).expanduser()
            if not candidate.is_absolute():
                candidate = Path(project.root_path) / skill_path
            resolved.add(str(candidate.resolve(strict=False)))
        self._write_enabled_skill_paths(project, resolved)
        return resolved

    def remove_skill_path_from_project(
        self,
        project: _ProjectLike,
        skill_path: str,
    ) -> None:
        if project.kind != "project":
            return
        current = self.enabled_skill_paths(project)
        current.discard(str(Path(skill_path).expanduser().resolve(strict=False)))
        self._write_enabled_skill_paths(project, current)

    def scan(self, project: _ProjectLike, source: FilesystemSkillSource) -> int:
        return len(self.list_project_skill_manifests(project, source))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_config_path(self, project: _ProjectLike) -> Path:
        return Path(project.root_path) / ".claude" / self._config_name

    def _read_config(self, project: _ProjectLike) -> dict:
        config = self._project_config_path(project)
        if not config.exists():
            return {}
        return json.loads(config.read_text(encoding="utf-8"))

    def _write_config(self, project: _ProjectLike, data: dict) -> None:
        config_path = self._project_config_path(project)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _write_enabled_skill_paths(self, project: _ProjectLike, enabled_paths: set[str]) -> None:
        data = self._read_config(project)
        data["skills_enabled"] = sorted(
            self._normalize_ref(project, path) for path in enabled_paths
        )
        self._write_config(project, data)

    def get_mcp_servers(self, project: _ProjectLike) -> list[str]:
        if project.kind != "project" or not project.root_path:
            return []
        data = self._read_config(project)
        value = data.get("mcp_servers", [])
        return value if isinstance(value, list) else []

    def set_mcp_servers(self, project: _ProjectLike, slugs: list[str]) -> None:
        data = self._read_config(project)
        data["mcp_servers"] = slugs
        self._write_config(project, data)

    def _normalize_ref(self, project: _ProjectLike, skill_path: str) -> str:
        candidate = Path(skill_path).expanduser().resolve(strict=False)
        if project.kind == "project" and project.root_path:
            project_skill_root = (Path(project.root_path) / ".claude" / "skills").resolve(
                strict=False
            )
            try:
                relative = candidate.relative_to(project_skill_root)
            except ValueError:
                return str(candidate)
            return str(Path(".claude") / "skills" / relative)
        return str(candidate)


class _ProjectLike(Protocol):
    id: str
    kind: str
    root_path: str | None
