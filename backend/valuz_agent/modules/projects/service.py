from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.skills.datastore import SkillDatastore

# Kernel V5+1aae940 collapses ``permission_mode`` to a 3-value enum;
# every legacy value (set on dev DBs by the previous host code) maps to
# ``full_access`` per the migration's data coerce. This helper applies
# the same coercion to in-memory values we read back from the kernel
# before re-saving the row, so a re-mirror after a fresh boot doesn't
# trip the CHECK constraint on its way out.
_VALID_PERMISSION_MODES = ("default", "auto_review", "full_access")


def _coerce_permission_mode(value: str) -> str:
    return value if value in _VALID_PERMISSION_MODES else "full_access"


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


@dataclass
class ProjectListItem:
    id: str
    name: str
    kind: str
    root_path: str | None
    icon: str | None
    # Resolved working directory the kernel runs sessions in. For project
    # projects this equals ``root_path``; for chat projects it's the
    # managed dir under ``data_dir/projects/{id}/``. Surfaced so the
    # UI can offer "Open in Finder" without a second detail fetch.
    cwd: str | None = None


@dataclass
class ProjectDetail(ProjectListItem):
    instructions_md: str | None = None
    memory_summary: str | None = None


@dataclass
class ProjectDeletePreview:
    session_count: int
    doc_binding_count: int
    schedule_count: int
    skill_config_count: int


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
    memory_summary: str | None = None,
    cwd: str | None = None,
) -> ProjectDetail:
    return ProjectDetail(
        id=row.id,
        name=row.name,
        kind=row.kind,
        root_path=row.root_path,
        icon=row.icon,
        instructions_md=instructions_md,
        memory_summary=memory_summary,
        cwd=cwd,
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
    return str(fs_registry.project_cwd(row.id, kind, row.root_path))  # type: ignore[arg-type]


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

    async def ensure_chat_project(self, user_id: str) -> None:
        existing = await self._ds.get_chat_project(user_id)
        if existing:
            return
        row = ProjectRow(name="Chat", kind="chat", sort_order=0)
        await self._ds.create(user_id, row)

    async def create_chat_project_for_session(self, name: str = "Chat") -> ProjectRow:
        """Materialize a fresh, ephemeral chat project for one chat-kind context.

        Each call creates a NEW ``ProjectRow(kind="chat")`` and mirrors it
        into a dedicated kernel project + agent (1:1 by id). The kernel
        project gets its own cwd at ``data_dir/projects/{ws_id}/`` via
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
        await self._ds.create(require_current_user_id(), row)
        return row

    async def list_projects(self, user_id: str) -> list[ProjectListItem]:
        rows = await self._ds.list_projects(user_id)
        return [_row_to_list_item(r, cwd=self.resolve_project_cwd(r)) for r in rows]

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
                    memory_summary=row.memory_summary,
                    cwd=self.resolve_project_cwd(row),
                )
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        return _row_to_detail(
            row,
            instructions_md=row.instructions_md,
            memory_summary=row.memory_summary,
            cwd=self.resolve_project_cwd(row),
        )

    async def create_project(self, user_id: str, name: str, root_path: str) -> ProjectDetail:
        abs_path = str(Path(root_path).resolve())
        existing = await self._ds.get_by_root_path(user_id, abs_path)
        if existing:
            raise ValueError(f"Directory already bound to project '{existing.name}'")
        row = ProjectRow(name=name, kind="project", root_path=abs_path, sort_order=10)
        await self._ds.create(user_id, row)
        return _row_to_detail(row, cwd=self.resolve_project_cwd(row))

    async def rename_project(self, user_id: str, project_id: str, name: str) -> ProjectDetail:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.kind == "chat":
            raise ValueError("Chat project cannot be renamed")
        row.name = name
        await self._ds.update(row)
        return _row_to_detail(row, cwd=self.resolve_project_cwd(row))

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
        # Pure filesystem read (.claude/project-config.json) — stays sync.
        return self._connectors.get_project_connectors(row)

    async def set_connectors(self, user_id: str, project_id: str, slugs: list[str]) -> None:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row or not row.root_path:
            raise KeyError(project_id)
        if not self._connectors:
            raise RuntimeError("connector_datastore not wired")
        # Pure filesystem write (.claude/project-config.json) — stays sync.
        self._connectors.set_project_connectors(row, slugs)

    async def update_memory(
        self,
        user_id: str,
        project_id: str,
        summary: str | None,
        expected_version: int,
    ) -> None:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.memory_version != expected_version:
            raise ValueError("PROJECT_MEMORY_VERSION_CONFLICT")
        row.memory_summary = summary
        row.memory_version = expected_version + 1
        await self._ds.update(row)

    async def preview_delete(self, user_id: str, project_id: str) -> ProjectDeletePreview:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        if row.kind == "chat":
            raise ValueError("Chat project cannot be deleted")

        # Session counts come from the host project↔session index.
        try:
            session_count = await project_index.count_for_project(project_id)
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
            for sid in await project_index.remove_for_project(project_id):
                await kernel_client.delete_session(require_current_user_id(), sid)
        except Exception:  # noqa: BLE001
            pass
        if self._docs:
            await self._docs.remove_all_bindings(user_id, project_id)
        if self._automations:
            await self._automations.delete_all_for_project(user_id, project_id)
        if self._skills:
            await self._skills.set_project_skills(user_id, project_id, [])
        await self._ds.delete(user_id, project_id)

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

    def resolve_project_cwd(self, row: ProjectRow) -> str:
        """Absolute cwd a session in this project runs in — required at
        session creation now that the kernel has no project to fall back to."""
        kind = row.kind if row.kind in ("chat", "project") else "chat"
        return str(fs_registry.project_cwd(row.id, kind, row.root_path))  # type: ignore[arg-type]

    async def list_files(
        self,
        user_id: str,
        project_id: str,
        depth: int = 2,
        include_hidden: bool = False,
    ) -> list[dict[str, object]]:
        row = await self._ds.get_by_id(user_id, project_id)
        if not row:
            raise KeyError(project_id)
        # Projects walk the user-supplied root_path.
        # Chat projects walk their managed cwd under
        # ``data_dir/projects/{id}/`` so any files the agent generates
        # during the chat (excel exports, reports, scratch outputs, …)
        # show up in the right-rail "generated files" panel.
        if row.kind == "project":
            if not row.root_path:
                return []
            root = Path(row.root_path)
        else:
            root = fs_registry.project_cwd(project_id, "chat")
        if not root.exists():
            return []
        nodes = _walk_dir(root, depth=depth, include_hidden=include_hidden)
        return [_node_to_dict(n) for n in nodes]


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
