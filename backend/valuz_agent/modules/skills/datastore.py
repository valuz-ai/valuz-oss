from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest, WorkspaceRef
from valuz_agent.modules.skills.models import (
    ProjectSkillConfigRow,
    SkillIndexRow,
)


class SkillDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._config_name = "project-config.json"

    # ------------------------------------------------------------------
    # DB-backed SkillIndexRow CRUD (retained for future startup_scan)
    # ------------------------------------------------------------------

    async def list_skills(
        self,
        query: str | None = None,
        scope: str | None = None,
    ) -> list[SkillIndexRow]:
        stmt = select(SkillIndexRow)
        if scope:
            stmt = stmt.filter_by(scope=scope)
        if query:
            stmt = stmt.filter(SkillIndexRow.name.ilike(f"%{query}%"))
        stmt = stmt.order_by(SkillIndexRow.name)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_id(self, skill_id: str) -> SkillIndexRow | None:
        return await self._db.get(SkillIndexRow, skill_id)

    async def set_creation_origin(self, skill_id: str, origin: str) -> None:
        """Stamp ``creation_origin`` on an existing ``valuz_skill_index`` row.

        ``creation_origin`` is host-only bookkeeping — it never touches
        SKILL.md. The row is expected to exist (``startup_scan`` creates
        it just before this is called from a create / import flow); a
        missing row is a no-op rather than an error, since the next
        ``startup_scan`` recreates it as ``"discovered"`` anyway.
        """
        row = await self._db.get(SkillIndexRow, skill_id)
        if row is None:
            return
        row.creation_origin = origin
        await async_commit_with_retry(self._db, where="SkillDatastore.set_creation_origin")

    async def create(self, row: SkillIndexRow) -> SkillIndexRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="SkillDatastore.create")
        return row

    async def update(self, row: SkillIndexRow) -> SkillIndexRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="SkillDatastore.update")
        return row

    async def delete(self, skill_id: str) -> None:
        await self._db.execute(sa_delete(SkillIndexRow).where(SkillIndexRow.id == skill_id))
        await async_commit_with_retry(self._db, where="SkillDatastore.delete")

    async def list_project_skills(self, workspace_id: str) -> list[ProjectSkillConfigRow]:
        return list(
            (
                await self._db.execute(
                    select(ProjectSkillConfigRow).filter_by(workspace_id=workspace_id)
                )
            )
            .scalars()
            .all()
        )

    async def set_project_skills(
        self, workspace_id: str, rows: list[ProjectSkillConfigRow]
    ) -> None:
        await self._db.execute(
            sa_delete(ProjectSkillConfigRow).where(
                ProjectSkillConfigRow.workspace_id == workspace_id
            )
        )
        self._db.add_all(rows)
        await async_commit_with_retry(self._db, where="SkillDatastore.set_project_skills")

    # ------------------------------------------------------------------
    # Filesystem-based workspace skill config (JSON project-config.json)
    # ------------------------------------------------------------------

    def list_workspace_skills(
        self,
        workspace: _WorkspaceLike,
        source: FilesystemSkillSource,
    ) -> list[SkillManifest]:
        context = RuntimeContext(
            workspace=WorkspaceRef(
                id=workspace.id,
                slug=workspace.id,
                kind=workspace.kind,
                root_path=workspace.root_path,
            ),
        )
        manifests = source.list_skills(context)
        enabled_paths = self.enabled_skill_paths(workspace)

        items: list[SkillManifest] = []
        for manifest in manifests:
            enabled = workspace.kind == "chat" or manifest.path in enabled_paths
            items.append(manifest.model_copy(update={"enabled": enabled}))
        return items

    def enabled_skill_paths(self, workspace: _WorkspaceLike) -> set[str]:
        if workspace.kind != "project":
            return set()

        config = self._project_config_path(workspace)
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
                candidate = Path(workspace.root_path) / value
            resolved.add(str(candidate.resolve(strict=False)))
        return resolved

    def set_skill_enabled(
        self,
        workspace: _WorkspaceLike,
        skill_path: str,
        enabled: bool,
    ) -> set[str]:
        if workspace.kind != "project":
            return set()

        current = self.enabled_skill_paths(workspace)
        resolved_path = str(Path(skill_path).expanduser().resolve(strict=False))
        if enabled:
            current.add(resolved_path)
        else:
            current.discard(resolved_path)
        self._write_enabled_skill_paths(workspace, current)
        return current

    def overwrite_enabled_skill_paths(
        self,
        workspace: _WorkspaceLike,
        skill_paths: list[str],
    ) -> set[str]:
        if workspace.kind != "project":
            return set()

        resolved: set[str] = set()
        for skill_path in skill_paths:
            if not skill_path:
                continue
            candidate = Path(skill_path).expanduser()
            if not candidate.is_absolute():
                candidate = Path(workspace.root_path) / skill_path
            resolved.add(str(candidate.resolve(strict=False)))
        self._write_enabled_skill_paths(workspace, resolved)
        return resolved

    def remove_skill_path_from_workspace(
        self,
        workspace: _WorkspaceLike,
        skill_path: str,
    ) -> None:
        if workspace.kind != "project":
            return
        current = self.enabled_skill_paths(workspace)
        current.discard(str(Path(skill_path).expanduser().resolve(strict=False)))
        self._write_enabled_skill_paths(workspace, current)

    def scan(self, workspace: _WorkspaceLike, source: FilesystemSkillSource) -> int:
        return len(self.list_workspace_skills(workspace, source))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_config_path(self, workspace: _WorkspaceLike) -> Path:
        return Path(workspace.root_path) / ".claude" / self._config_name

    def _read_config(self, workspace: _WorkspaceLike) -> dict:
        config = self._project_config_path(workspace)
        if not config.exists():
            return {}
        return json.loads(config.read_text(encoding="utf-8"))

    def _write_config(self, workspace: _WorkspaceLike, data: dict) -> None:
        config_path = self._project_config_path(workspace)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _write_enabled_skill_paths(
        self, workspace: _WorkspaceLike, enabled_paths: set[str]
    ) -> None:
        data = self._read_config(workspace)
        data["skills_enabled"] = sorted(
            self._normalize_ref(workspace, path) for path in enabled_paths
        )
        self._write_config(workspace, data)

    def get_mcp_servers(self, workspace: _WorkspaceLike) -> list[str]:
        if workspace.kind != "project" or not workspace.root_path:
            return []
        data = self._read_config(workspace)
        value = data.get("mcp_servers", [])
        return value if isinstance(value, list) else []

    def set_mcp_servers(self, workspace: _WorkspaceLike, slugs: list[str]) -> None:
        data = self._read_config(workspace)
        data["mcp_servers"] = slugs
        self._write_config(workspace, data)

    def _normalize_ref(self, workspace: _WorkspaceLike, skill_path: str) -> str:
        candidate = Path(skill_path).expanduser().resolve(strict=False)
        if workspace.kind == "project" and workspace.root_path:
            project_skill_root = (Path(workspace.root_path) / ".claude" / "skills").resolve(
                strict=False
            )
            try:
                relative = candidate.relative_to(project_skill_root)
            except ValueError:
                return str(candidate)
            return str(Path(".claude") / "skills" / relative)
        return str(candidate)


class _WorkspaceLike(Protocol):
    id: str
    kind: str
    root_path: str | None
