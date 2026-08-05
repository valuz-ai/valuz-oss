from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.artifacts.snapshot import ARTIFACT_DIR_NAME
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.skills.datastore import SkillDatastore

logger = logging.getLogger(__name__)

PROJECT_ROOT_MARKER = ".valuz/root"

# Kernel V5+1aae940 collapses ``permission_mode`` to a 3-value enum;
# every legacy value (set on dev DBs by the previous host code) maps to
# ``full_access`` per the migration's data coerce. This helper applies
# the same coercion to in-memory values we read back from the kernel
# before re-saving the row, so a re-mirror after a fresh boot doesn't
# trip the CHECK constraint on its way out.
_VALID_PERMISSION_MODES = ("default", "auto_review", "full_access")


def _coerce_permission_mode(value: str) -> str:
    return value if value in _VALID_PERMISSION_MODES else "full_access"


# Never listed, even with ``include_hidden``. Unlike the names below — which a
# user may reasonably want to see — this one is not user content: it is the
# artifact store, holding the immutable snapshot of every delivered version.
# Showing it invites edits to files whose whole value is that they do not
# change, and buries the working tree under one directory per version.
ALWAYS_EXCLUDED_NAMES = frozenset({ARTIFACT_DIR_NAME})

HIDDEN_NAMES = frozenset(
    {
        ".git",
        ".claude",
        ".valuz",
        "node_modules",
        ".next",
        ".venv",
        "__pycache__",
        ".DS_Store",
        ".env",
    }
)

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg"})
MEDIA_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "ogg", "mp4", "webm", "mov"})
MARKDOWN_EXTENSIONS = frozenset({"md", "markdown", "mdx"})
CODE_EXTENSIONS = frozenset(
    {
        "py",
        "ts",
        "tsx",
        "js",
        "jsx",
        "json",
        "yml",
        "yaml",
        "xml",
        "css",
        "scss",
        "sh",
        "bash",
        "zsh",
        "toml",
        "ini",
        "cfg",
        "sql",
        "go",
        "rs",
        "java",
        "c",
        "cpp",
        "h",
        "rb",
        "php",
        "swift",
        "kt",
        "vue",
        "svelte",
        "astro",
    }
)
PLAIN_EXTENSIONS = frozenset({"txt", "log", "env", "gitignore", "dockerignore", "editorconfig"})
HTML_EXTENSIONS = frozenset({"html", "htm"})
DOCX_EXTENSIONS = frozenset({"docx"})
SPREADSHEET_EXTENSIONS = frozenset({"csv", "xls", "xlsx"})


@dataclass
class ProjectListItem:
    id: str
    name: str
    kind: str
    root_path: str | None
    icon: str | None
    # Resolved working directory the kernel runs sessions in. For project
    # projects this equals ``root_path`` resolved through the project root
    # boundary; for chat projects it's the managed workspace under
    # ``fs_registry.project_root(user_id)`` (local: user_project_root, cloud:
    # user_project_root/{user_id}). Surfaced so the UI can offer "Open in
    # Finder" without a second detail fetch.
    cwd: str | None = None


@dataclass
class ProjectDetail(ProjectListItem):
    instructions_md: str | None = None
    # Member slug that leads a task when the caller names none. ``None`` = not
    # configured (the launcher then falls back to the conversation agent).
    default_lead_agent_slug: str | None = None


@dataclass
class ProjectDeletePreview:
    session_count: int
    doc_binding_count: int
    schedule_count: int
    skill_config_count: int


class ProjectMembers(Protocol):
    """The slice of project membership this module needs — sibling datastores
    are reached through a protocol, never imported directly."""

    async def get(self, user_id: str, project_id: str, agent_slug: str) -> Any: ...

    async def delete_by_project(self, user_id: str, project_id: str) -> int: ...


@dataclass
class FileNode:
    name: str
    type: str  # "file" | "directory"
    size: int | None = None
    modified: str | None = None
    children: list[FileNode] = field(default_factory=list)


def _row_to_list_item(row: ProjectRow, cwd: str | None = None) -> ProjectListItem:
    return ProjectListItem(
        id=row.id,
        name=row.name,
        kind=row.kind,
        root_path=row.root_path,
        icon=row.icon,
        cwd=cwd,
    )


def _row_to_detail(
    row: ProjectRow,
    instructions_md: str | None = None,
    cwd: str | None = None,
) -> ProjectDetail:
    return ProjectDetail(
        id=row.id,
        name=row.name,
        kind=row.kind,
        root_path=row.root_path,
        icon=row.icon,
        instructions_md=instructions_md,
        cwd=cwd,
        default_lead_agent_slug=row.default_lead_agent_slug,
    )


async def project_cwd_by_id(user_id: str, project_id: str) -> str | None:
    """Resolve a project's session cwd by id — module-level so sibling
    modules (memory scope, prompt context, skills staging) can call it
    without wiring a ProjectService. Opens its own unit of work."""
    if not project_id:
        return None
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    async with async_unit_of_work(commit=False) as db:
        row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    if row is None:
        return None
    kind = row.kind if row.kind in ("chat", "project") else "chat"
    if kind == "project":
        return str(_root_path(user_id, row.root_path)) if row.root_path else None
    return str(fs_registry.project_cwd(user_id, row.id, kind, row.root_path))  # type: ignore[arg-type]


async def clear_default_lead_if(user_id: str, project_id: str, agent_slug: str) -> bool:
    """Clear the project's default lead when it points at ``agent_slug``.

    Module-level (like the other cross-module readers here) so the agents module
    can call it after undeploying a member without importing this module's
    datastore. Returns whether anything was cleared.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    async with async_unit_of_work(commit=True) as db:
        ds = ProjectDatastore(db)
        row = await ds.get_by_id(user_id, project_id)
        if row is None or row.default_lead_agent_slug != agent_slug:
            return False
        row.default_lead_agent_slug = None
        await ds.update(row)
        return True


async def project_name_map(user_id: str) -> dict[str, str]:
    """Return project id -> display name without exposing the project datastore."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    async with async_unit_of_work(commit=False) as db:
        rows = await ProjectDatastore(db).list_projects(user_id)
    return {row.id: row.name for row in rows}


async def project_root_paths(user_id: str) -> list[tuple[str, str, str | None]]:
    """Return ``(project_id, kind, root_path)`` triples for every project —
    used by the backup module to resolve which external bound folders fall
    inside the user's backup scope, without exposing the project datastore."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    async with async_unit_of_work(commit=False) as db:
        rows = await ProjectDatastore(db).list_projects(user_id)
    return [(row.id, row.kind, row.root_path) for row in rows]


async def project_brief_by_id(user_id: str, project_id: str) -> tuple[str, str, str | None] | None:
    """Return ``(kind, name, instructions_md)`` for project-scoped collaborators."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    async with async_unit_of_work(commit=False) as db:
        row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    if row is None:
        return None
    return (row.kind, row.name, row.instructions_md)


class ProjectService:
    def __init__(
        self,
        datastore: ProjectDatastore,
        event_bus: EventBus,
        session_datastore: SessionDatastore | None = None,
        document_datastore: DocumentDatastore | None = None,
        automation_datastore: AutomationDatastore | None = None,
        skill_datastore: SkillDatastore | None = None,
        connector_datastore: ConnectorDatastore | None = None,
        member_datastore: ProjectMembers | None = None,
    ) -> None:
        self._ds = datastore
        self._bus = event_bus
        self._sessions = session_datastore
        self._docs = document_datastore
        # Automation count surfaces in the project delete-preview as the
        # ``schedule_count`` field — kept that name on the response model in
        # this slice for frontend compatibility; renamed to
        # ``automation_count`` in S5.
        self._automations = automation_datastore
        self._skills = skill_datastore
        self._connectors = connector_datastore
        self._members = member_datastore

    async def ensure_chat_project(self, user_id: str) -> None:
        existing = await self._ds.get_chat_project(user_id)
        if existing:
            return
        row = ProjectRow(name="Chat", kind="chat", sort_order=0)
        await self._ds.create(user_id, row)

    async def create_chat_project_for_session(self, user_id: str, name: str = "Chat") -> ProjectRow:
        """Materialize a fresh, ephemeral chat project for one chat-kind context.

        Each call creates a NEW ``ProjectRow(kind="chat")`` and mirrors it
        into a dedicated kernel project + agent (1:1 by id). The kernel
        project gets its own cwd under ``fs_registry.project_root(user_id)`` via
        ``fs_registry.project_cwd``, so every chat session runs in an
        isolated directory and can't trip over files written by sibling
        chats.

        Callers:
        - ``SessionService.send_message`` (quick-chat) — default ``name="Chat"``
        - ``AutomationService.create`` (scheduled chat) — passes the
          automation name so the run list grouping reads naturally
          ("Chat: 每日新闻摘要") instead of N anonymous "Chat" groups.

        The singleton chat project seeded by ``ensure_chat_project``
        is left in place — it remains the scope key (``"chat-default"``)
        for chat-skills configuration, which is global across all chat
        sessions, not bound to any single chat project's id.
        """
        row = ProjectRow(name=name, kind="chat", sort_order=100)
        await self._ds.create(user_id, row)
        return row

    async def list_projects(self, user_id: str) -> list[ProjectListItem]:
        rows = await self._ds.list_projects(user_id)
        items: list[ProjectListItem] = []
        for row in rows:
            items.append(_row_to_list_item(row, cwd=await self.resolve_project_cwd(user_id, row)))
        return items

    async def get_project(self, user_id: str, project_id: str) -> ProjectDetail:
        if project_id == "chat-default":
            row = await self._ds.get_chat_project(user_id)
            if not row:
                await self.ensure_chat_project(user_id)
                row = await self._ds.get_chat_project(user_id)
            if row:
                return _row_to_detail(
                    row,
                    instructions_md=row.instructions_md,
                    cwd=await self.resolve_project_cwd(user_id, row),
                )
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        return _row_to_detail(
            row,
            instructions_md=row.instructions_md,
            cwd=await self.resolve_project_cwd(user_id, row),
        )

    async def create_project(
        self,
        user_id: str,
        name: str,
        root_path: str | None = None,
    ) -> ProjectDetail:
        """Create a project.

        A non-empty ``root_path`` is resolved and uniqueness-checked against
        existing projects (legacy behaviour — the project's cwd is that
        caller-supplied local directory).

        An empty/None ``root_path`` allocates and stores an absolute managed cwd
        under ``fs_registry.project_root(user_id)``, mirroring
        ``create_project_from_pack``. This is the cloud/managed path: the
        project works without a caller-supplied local directory, which a
        remote backend could not reach anyway.
        """
        new_id = uuid4().hex
        managed_root = not (root_path and root_path.strip())
        if not managed_root:
            resolved_root = _normalize_explicit_root(root_path or "")
            existing = await self._ds.get_by_root_path(user_id, resolved_root)
            if existing:
                raise ValueError(f"Directory already bound to project '{existing.name}'")
        else:
            resolved_root = _managed_project_root(user_id, new_id)
        _write_relative_file(_root_path(user_id, resolved_root), PROJECT_ROOT_MARKER, b"")
        row = ProjectRow(
            id=new_id,
            name=name,
            kind="project",
            root_path=resolved_root,
            sort_order=10,
        )
        try:
            await self._ds.create(user_id, row)
        except Exception:
            if managed_root:
                try:
                    shutil.rmtree(_root_path(user_id, resolved_root), ignore_errors=True)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "managed project root cleanup skipped for %s",
                        new_id,
                        exc_info=True,
                    )
            raise
        return _row_to_detail(row, cwd=await self.resolve_project_cwd(user_id, row))

    async def get_by_name(self, user_id: str, name: str) -> ProjectRow | None:
        """Exact-name passthrough to the datastore (used by project import's
        name-collision check). Returns ``None`` when no project of that name
        exists for the caller."""
        return await self._ds.get_by_name(user_id, name)

    async def create_project_from_pack(
        self,
        user_id: str,
        name: str,
        kind: str,
        icon: str | None,
        instructions_md: str | None,
        root_path: str | None = None,
    ) -> ProjectRow:
        """Create a project from an imported pack's metadata.

        Mints a fresh id. If ``root_path`` is supplied (user picked a
        folder in the import dialog), it is resolved + uniqueness-checked
        against existing projects and used verbatim — same rule as
        ``create_project``. Otherwise a managed cwd under
        ``fs_registry.project_root(user_id)`` is created (cross-machine portability:
        the source machine's ``root_path`` is never reused). Only
        ``project`` kind is supported — chat projects are not exportable.
        """
        if kind != "project":
            raise ValueError(f"create_project_from_pack does not support kind={kind!r}")
        new_id = uuid4().hex
        managed_root = not (root_path and root_path.strip())
        if not managed_root:
            resolved_root = _normalize_explicit_root(root_path or "")
            existing = await self._ds.get_by_root_path(user_id, resolved_root)
            if existing:
                raise ValueError(f"directory already bound to a project: {resolved_root}")
        else:
            # Imported projects without a user-picked folder get a managed
            # cwd under fs_registry.project_root(user_id) (mirrors chat projects) so
            # they're still cross-machine portable.
            resolved_root = _managed_project_root(user_id, new_id)
        _write_relative_file(_root_path(user_id, resolved_root), PROJECT_ROOT_MARKER, b"")
        row = ProjectRow(
            id=new_id,
            name=name,
            kind="project",
            root_path=resolved_root,
            icon=icon,
            instructions_md=(instructions_md or "").strip() or None,
            sort_order=10,
        )
        try:
            await self._ds.create(user_id, row)
        except Exception:
            if managed_root:
                try:
                    shutil.rmtree(_root_path(user_id, resolved_root), ignore_errors=True)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "managed project root cleanup skipped for %s",
                        new_id,
                        exc_info=True,
                    )
            raise
        return row

    async def rename_project(self, user_id: str, project_id: str, name: str) -> ProjectDetail:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.kind == "chat":
            raise ValueError("Chat project cannot be renamed")
        row.name = name
        await self._ds.update(row)
        return _row_to_detail(row, cwd=await self.resolve_project_cwd(user_id, row))

    async def set_default_lead(
        self, user_id: str, project_id: str, agent_slug: str | None
    ) -> ProjectDetail:
        """Point the project at the member that leads tasks by default.

        ``None``/empty clears it. The slug must name a current member —
        accepting a stranger would produce a launcher failure much later, far
        from the mistake.
        """
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        slug = (agent_slug or "").strip() or None
        if slug is not None:
            if self._members is None:
                raise ValueError("project members are unavailable")
            if await self._members.get(user_id, project_id, slug) is None:
                raise ValueError(f"'{slug}' is not a member of this project")
        row.default_lead_agent_slug = slug
        await self._ds.update(row)
        return _row_to_detail(
            row,
            instructions_md=row.instructions_md,
            cwd=await self.resolve_project_cwd(user_id, row),
        )

    async def update_instructions(
        self, user_id: str, project_id: str, instructions_md: str
    ) -> None:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        row.instructions_md = (instructions_md or "").strip() or None
        await self._ds.update(row)
        # Per ADR-008: the runtime reads ``session.instructions`` (frozen at
        # session creation), not ``agent.instructions``. So edits here only
        # affect *future new sessions* — already-running sessions keep the
        # prompt they were created with. UI surfaces a hint to that effect.

    async def get_connectors(self, user_id: str, project_id: str) -> list[str]:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row or not row.root_path:
            raise KeyError(project_id)
        if not self._connectors:
            return []
        return await self._connectors.get_project_connectors(user_id, project_id)

    async def set_connectors(self, user_id: str, project_id: str, slugs: list[str]) -> None:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row or not row.root_path:
            raise KeyError(project_id)
        if not self._connectors:
            raise RuntimeError("connector_datastore not wired")
        await self._connectors.set_project_connectors(user_id, project_id, slugs)

    async def preview_delete(self, user_id: str, project_id: str) -> ProjectDeletePreview:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.kind == "chat":
            raise ValueError("Chat project cannot be deleted")

        # Session counts come from the host project↔session index.
        try:
            session_count = await project_index.count_for_project(project_id, user_id=user_id)
        except Exception:  # noqa: BLE001
            session_count = 0
        doc_binding_count = (
            await self._docs.count_bindings(user_id, project_id) if self._docs else 0
        )
        schedule_count = (
            await self._automations.count_by_project(user_id, project_id)
            if self._automations
            else 0
        )
        skill_config_count = (
            len(await self._skills.list_project_skills(user_id, project_id)) if self._skills else 0
        )

        return ProjectDeletePreview(
            session_count=session_count,
            doc_binding_count=doc_binding_count,
            schedule_count=schedule_count,
            skill_config_count=skill_config_count,
        )

    async def delete_project(self, user_id: str, project_id: str) -> None:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.kind == "chat":
            raise ValueError("Chat project cannot be deleted")

        # Delete kernel sessions for this project (and their events) — ids
        # come from the host index, which is cleared in the same sweep.
        try:
            for sid in await project_index.remove_for_project(project_id, user_id=user_id):
                await kernel_client.delete_session(user_id, sid)
        except Exception:  # noqa: BLE001
            pass
        if self._docs:
            await self._docs.remove_all_bindings(user_id, project_id)
        if self._automations:
            await self._automations.delete_all_for_project(user_id, project_id)
        if self._skills:
            await self._skills.set_project_skills(user_id, project_id, [])
        if self._members:
            await self._members.delete_by_project(user_id, project_id)
        await self._ds.delete(user_id, project_id)
        # Source-driven forgetting (memory-system-design §11): a deleted project's
        # centralized memory dir is Valuz-owned (never the user's bound repo), so
        # it's safe to remove. Best-effort — never fail the delete on cleanup.
        try:
            from valuz_agent.modules.memory.service import memory_store

            memory_store.drop_project(user_id, project_id)
        except Exception:  # noqa: BLE001
            logger.debug("project memory cleanup skipped for %s", project_id, exc_info=True)

    # ------------------------------------------------------------------
    # Kernel mirror — every valuz project must back a V5 kernel Project +
    # Agent so sessions can be created against it. The id of the mirrored
    # kernel rows equals the project id (1:1) and the agent id is derived
    # deterministically from the project id, so re-running these helpers
    # is idempotent.
    # ------------------------------------------------------------------

    @staticmethod
    def _kernel_agent_id(project_id: str) -> str:
        # Deterministic so re-running ensure flows is idempotent without an
        # extra lookup. UUID-shaped to satisfy the kernel's ``String(36)``.
        # ``agent-`` is 6 chars + project_id (32 hex) = 38; trim to 36.
        return f"agent-{project_id}"[:36]

    async def resolve_project_cwd(self, user_id: str, row: ProjectRow) -> str | None:
        """Absolute cwd a session in this project runs in — required at
        session creation now that the kernel has no project to fall back to."""
        kind = row.kind if row.kind in ("chat", "project") else "chat"
        if kind == "project":
            return str(_root_path(user_id, row.root_path)) if row.root_path else None
        return str(fs_registry.project_cwd(user_id, row.id, kind, row.root_path))  # type: ignore[arg-type]

    async def _worktree_root(self, user_id: str, row: ProjectRow, worktree: str) -> Path | None:
        """Resolve a session's worktree cwd (design D7), or ``None`` if the
        named worktree isn't a live managed worktree of this project's repo.

        Lets the file-tree / artifact-read endpoints scope to the directory a
        worktree session actually runs in instead of the shared project cwd.
        """
        from valuz_agent.modules.worktrees.service import worktree_service

        resolved = await worktree_service.resolve_session_cwd(user_id, row, worktree)
        return Path(resolved) if resolved else None

    async def list_files(
        self,
        user_id: str,
        project_id: str,
        depth: int = 2,
        include_hidden: bool = False,
        worktree: str | None = None,
    ) -> list[dict[str, object]]:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        # A worktree session's file tree reflects the worktree checkout, not the
        # shared project cwd. Resolve it up front; a removed/invalid worktree
        # yields an empty tree (the session self-heals on the next send).
        if worktree:
            wt_root = await self._worktree_root(user_id, row, worktree)
            if wt_root is None or not wt_root.exists():
                return []
            nodes = _walk_dir(wt_root, depth=depth, include_hidden=include_hidden)
            return [_node_to_dict(n) for n in nodes]
        # Projects delegate to the system file system. Chat projects walk their managed cwd under
        # ``fs_registry.project_root(user_id)`` so any files the agent generates
        # during the chat (excel exports, reports, scratch outputs, …)
        # show up in the right-rail "generated files" panel.
        if row.kind == "project":
            if not row.root_path:
                return []
            nodes = _walk_dir(
                _root_path(user_id, row.root_path), depth=depth, include_hidden=include_hidden
            )
            return [_node_to_dict(n) for n in nodes]
        else:
            root = fs_registry.project_cwd(user_id, project_id, "chat")
        if not root.exists():
            return []
        nodes = _walk_dir(root, depth=depth, include_hidden=include_hidden)
        return [_node_to_dict(n) for n in nodes]

    async def write_file(
        self,
        user_id: str,
        project_id: str,
        file_path: str,
        data: bytes,
    ) -> str:
        """Write ``data`` to ``file_path`` (relative) inside the project cwd.

        Returns the resolved relative posix path. Rejects absolute paths,
        parent-traversal, and anything escaping the project root — but,
        unlike the resolve endpoint's ``assert_owned``, does NOT require the target to
        exist (it is a write). Powers ``POST /v1/projects/{id}/files`` so a
        cloud-managed project can receive files without a caller-supplied
        local directory.
        """
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.kind == "project":
            if not row.root_path:
                raise ValueError("Project has no root path")
            return _write_relative_file(_root_path(user_id, row.root_path), file_path, data)
        root = _project_root(user_id, row, project_id)
        rel = Path(file_path)
        if rel.is_absolute() or any(part in {"", "."} for part in rel.parts):
            raise ValueError("Invalid file path")
        if any(part == ".." for part in rel.parts):
            raise ValueError("Invalid file path")
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            raise ValueError("File path escapes project root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target.relative_to(root).as_posix()


def _managed_project_root(user_id: str, project_id: str) -> str:
    return str((fs_registry.project_root(user_id) / project_id).resolve())


def _normalize_explicit_root(root_path: str) -> str:
    value = root_path.strip()
    if not value:
        raise ValueError("Project root path is required")
    path = Path(value).expanduser()
    return str(path.resolve()) if path.is_absolute() else value.strip("/")


def _root_path(user_id: str, root_path: str) -> Path:
    path = Path(root_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (fs_registry.project_root(user_id) / root_path).resolve()


def _write_relative_file(root: Path, file_path: str, data: bytes) -> str:
    target = _resolve_project_write_target(root, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target.relative_to(root).as_posix()


def _resolve_project_write_target(root: Path, file_path: str) -> Path:
    relative = Path(file_path)
    if relative.is_absolute() or any(part in {"", "."} for part in relative.parts):
        raise ValueError("Invalid file path")
    if any(part == ".." for part in relative.parts):
        raise ValueError("Invalid file path")
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError("File path escapes project root")
    return target


def _walk_dir(
    directory: Path,
    depth: int,
    include_hidden: bool,
) -> list[FileNode]:
    if depth < 0 or not directory.is_dir():
        return []
    items: list[FileNode] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []
    for entry in entries:
        if entry.name in ALWAYS_EXCLUDED_NAMES:
            continue
        if not include_hidden and entry.name in HIDDEN_NAMES:
            continue
        if not include_hidden and entry.name.startswith(".") and entry.name != ".":
            continue
        if entry.is_dir():
            children = (
                _walk_dir(entry, depth=depth - 1, include_hidden=include_hidden)
                if depth > 0
                else []
            )
            items.append(FileNode(name=entry.name, type="directory", children=children))
        elif entry.is_file():
            try:
                stat = entry.stat()
                size = stat.st_size
                modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
            except OSError:
                size = None
                modified = None
            items.append(FileNode(name=entry.name, type="file", size=size, modified=modified))
    return items


def _project_root(user_id: str, row: ProjectRow, project_id: str) -> Path:
    if row.kind == "project":
        if not row.root_path:
            raise ValueError("Project has no root path")
        return _root_path(user_id, row.root_path)
    return fs_registry.project_cwd(user_id, project_id, "chat").resolve()


def _extension(name: str) -> str:
    if name.startswith(".") and name.count(".") == 1:
        return name[1:].lower()
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _preview_kind(name: str, mime_type: str | None) -> str:
    ext = _extension(name)
    if ext in MARKDOWN_EXTENSIONS:
        return "markdown"
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in HTML_EXTENSIONS or mime_type == "text/html":
        return "html"
    if ext in DOCX_EXTENSIONS:
        return "docx"
    if ext in SPREADSHEET_EXTENSIONS:
        return "spreadsheet"
    if ext in PLAIN_EXTENSIONS or (mime_type and mime_type.startswith("text/")):
        return "plain"
    if ext in IMAGE_EXTENSIONS or (mime_type and mime_type.startswith("image/")):
        return "image"
    if ext in MEDIA_EXTENSIONS or (
        mime_type and (mime_type.startswith("audio/") or mime_type.startswith("video/"))
    ):
        return "media"
    if ext == "pdf" or mime_type == "application/pdf":
        return "pdf"
    return "unsupported"


def _node_to_dict(node: FileNode) -> dict[str, object]:
    result: dict[str, object] = {
        "name": node.name,
        "type": node.type,
    }
    if node.type == "file":
        result["size"] = node.size
        result["modified"] = node.modified
    if node.children:
        result["children"] = [_node_to_dict(c) for c in node.children]
    return result
