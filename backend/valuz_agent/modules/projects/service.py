from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from valuz_agent.adapters import kernel_store, kernel_sync
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.integrations.tools_skill_creator import (
    SUBMIT_SKILL_TOOL_DECLARATION,
    SUBMIT_SKILL_TOOL_NAME,
)
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import WorkspaceDatastore
from valuz_agent.modules.projects.models import WorkspaceContextRow, WorkspaceRow
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.skills.datastore import SkillDatastore


def _ensure_submit_skill_declared(prior_tools: tuple) -> tuple:  # type: ignore[type-arg]
    """Add the ``submit_skill`` declaration if the agent doesn't already
    have one. Idempotent — re-mirrors leave the tuple unchanged."""
    for tool in prior_tools:
        if getattr(tool, "name", None) == SUBMIT_SKILL_TOOL_NAME:
            return prior_tools
    return tuple(prior_tools) + (SUBMIT_SKILL_TOOL_DECLARATION,)


def _ensure_memory_tools_declared(prior_tools: tuple) -> tuple:  # type: ignore[type-arg]
    """Declare memory_get / memory_write on the agent so the runtime surfaces
    them to the model (handlers are attached from the registry at runtime).
    Idempotent — only appends declarations the agent is missing."""
    from valuz_agent.modules.memory.tools import MEMORY_TOOL_DECLARATIONS

    have = {getattr(t, "name", None) for t in prior_tools}
    missing = tuple(d for d in MEMORY_TOOL_DECLARATIONS if d.name not in have)
    return tuple(prior_tools) + missing if missing else tuple(prior_tools)


def _ensure_orchestration_declared(prior_tools: tuple) -> tuple:  # type: ignore[type-arg]
    """Declare the task launcher/observability tools (create_task / list_tasks /
    get_task) on the workspace synthetic agent so a PROJECT conversation can
    spawn + track tasks (VALUZ-TASK / M10 附录 E). Gated to project workspaces at
    call time by ``_check_orchestration_gate`` — harmless on chat-default
    workspaces. Idempotent. (The per-task lead clone strips these — they are
    conversation-only.)"""
    from valuz_agent.modules.tasks.dispatch_mcp import ORCHESTRATION_TOOL_DECLARATIONS

    have = {getattr(t, "name", None) for t in prior_tools}
    missing = tuple(d for d in ORCHESTRATION_TOOL_DECLARATIONS if d.name not in have)
    return tuple(prior_tools) + missing if missing else tuple(prior_tools)


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
class WorkspaceListItem:
    id: str
    name: str
    kind: str
    root_path: str | None
    icon: str | None
    # Resolved working directory the kernel runs sessions in. For project
    # workspaces this equals ``root_path``; for chat workspaces it's the
    # managed dir under ``data_dir/workspaces/{id}/``. Surfaced so the
    # UI can offer "Open in Finder" without a second detail fetch.
    cwd: str | None = None


@dataclass
class WorkspaceDetail(WorkspaceListItem):
    instructions_md: str | None = None
    memory_summary: str | None = None


@dataclass
class WorkspaceDeletePreview:
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


def _row_to_list_item(row: WorkspaceRow, cwd: str | None = None) -> WorkspaceListItem:
    return WorkspaceListItem(
        id=row.id,
        name=row.name,
        kind=row.kind,
        root_path=row.root_path,
        icon=row.icon,
        cwd=cwd,
    )


def _row_to_detail(
    row: WorkspaceRow,
    instructions_md: str | None = None,
    memory_summary: str | None = None,
    cwd: str | None = None,
) -> WorkspaceDetail:
    return WorkspaceDetail(
        id=row.id,
        name=row.name,
        kind=row.kind,
        root_path=row.root_path,
        icon=row.icon,
        instructions_md=instructions_md,
        memory_summary=memory_summary,
        cwd=cwd,
    )


class WorkspaceService:
    def __init__(
        self,
        datastore: WorkspaceDatastore,
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
        # Automation count surfaces in the workspace delete-preview as the
        # ``schedule_count`` field — kept that name on the response model in
        # this slice for frontend compatibility; renamed to
        # ``automation_count`` in S5.
        self._automations = automation_datastore
        self._skills = skill_datastore
        self._connectors = connector_datastore

    async def ensure_chat_workspace(self) -> None:
        existing = await self._ds.get_chat_workspace()
        if existing:
            self._ensure_kernel_mirror(existing, instructions_md=None)
            return
        row = WorkspaceRow(name="Chat", kind="chat", sort_order=0)
        await self._ds.create(row)
        self._ensure_kernel_mirror(row, instructions_md=None)

    async def create_chat_workspace_for_session(self, name: str = "Chat") -> WorkspaceRow:
        """Materialize a fresh, ephemeral chat workspace for one chat-kind context.

        Each call creates a NEW ``WorkspaceRow(kind="chat")`` and mirrors it
        into a dedicated kernel project + agent (1:1 by id). The kernel
        project gets its own cwd at ``data_dir/workspaces/{ws_id}/`` via
        ``fs_registry.workspace_cwd``, so every chat session runs in an
        isolated directory and can't trip over files written by sibling
        chats.

        Callers:
        - ``SessionService.send_message`` (quick-chat) — default ``name="Chat"``
        - ``AutomationService.create`` (scheduled chat) — passes the
          automation name so the run list grouping reads naturally
          ("Chat: 每日新闻摘要") instead of N anonymous "Chat" groups.

        The singleton chat workspace seeded by ``ensure_chat_workspace``
        is left in place — it remains the scope key (``"chat-default"``)
        for chat-skills configuration, which is global across all chat
        sessions, not bound to any single chat workspace's id.
        """
        row = WorkspaceRow(name=name, kind="chat", sort_order=100)
        await self._ds.create(row)
        self._ensure_kernel_mirror(row, instructions_md=None)
        return row

    async def ensure_all_kernel_mirrors(self) -> None:
        """Reconcile every valuz workspace into the kernel project/agent tables.

        Idempotent boot-time safety net for two cases:

        1. Pre-existing workspaces that were created before the kernel-mirror
           code was wired in (e.g. the chat-default row in databases stamped
           by an early build).
        2. Workspaces whose kernel mirror was lost (manual DB editing, kernel
           re-vendor that dropped/recreated tables, etc.).

        Without this, ``orchestrator.run_turn`` raises ``ProjectNotFoundError``
        on the first send into the affected workspace — manifests as quick
        chat / skill-creator chat failing with category "ProjectNotFoundError".
        """
        # Always ensure the chat workspace exists first (creates the row if
        # missing AND mirrors to kernel).
        await self.ensure_chat_workspace()
        # Then walk every other workspace and re-mirror.
        for row in await self._ds.list_workspaces():
            ctx = await self._ds.get_context(row.id)
            self._ensure_kernel_mirror(row, instructions_md=ctx.instructions_md if ctx else None)

    async def list_workspaces(self) -> list[WorkspaceListItem]:
        rows = await self._ds.list_workspaces()
        return [_row_to_list_item(r, cwd=self._resolve_kernel_cwd(r)) for r in rows]

    async def get_workspace(self, workspace_id: str) -> WorkspaceDetail:
        if workspace_id == "chat-default":
            row = await self._ds.get_chat_workspace()
            if not row:
                await self.ensure_chat_workspace()
                row = await self._ds.get_chat_workspace()
            if row:
                ctx = await self._ds.get_context(row.id)
                return _row_to_detail(
                    row,
                    instructions_md=ctx.instructions_md if ctx else None,
                    memory_summary=ctx.memory_summary if ctx else None,
                    cwd=self._resolve_kernel_cwd(row),
                )
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        ctx = await self._ds.get_context(workspace_id)
        return _row_to_detail(
            row,
            instructions_md=ctx.instructions_md if ctx else None,
            memory_summary=ctx.memory_summary if ctx else None,
            cwd=self._resolve_kernel_cwd(row),
        )

    async def create_project(self, name: str, root_path: str) -> WorkspaceDetail:
        abs_path = str(Path(root_path).resolve())
        existing = await self._ds.get_by_root_path(abs_path)
        if existing:
            raise ValueError(f"Directory already bound to project '{existing.name}'")
        row = WorkspaceRow(name=name, kind="project", root_path=abs_path, sort_order=10)
        await self._ds.create(row)
        ctx = WorkspaceContextRow(
            workspace_id=row.id,
            instructions_md=None,
            memory_summary=None,
            memory_version=0,
            updated_at=now_ms(),
        )
        await self._ds.upsert_context(ctx)
        self._ensure_kernel_mirror(row, instructions_md=None)
        return _row_to_detail(row, cwd=self._resolve_kernel_cwd(row))

    async def rename_workspace(self, workspace_id: str, name: str) -> WorkspaceDetail:
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        if row.kind == "chat":
            raise ValueError("Chat workspace cannot be renamed")
        row.name = name
        await self._ds.update(row)
        # Keep the kernel project's display name in lock-step. Pass the row we
        # already loaded so the sync ``kernel_sync`` helper needs no host-DB read.
        self._rename_kernel_mirror(workspace_id, name, row)
        return _row_to_detail(row, cwd=self._resolve_kernel_cwd(row))

    async def update_instructions(self, workspace_id: str, instructions_md: str) -> None:
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        ctx = await self._ds.get_context(workspace_id)
        if ctx is None:
            ctx = WorkspaceContextRow(
                workspace_id=workspace_id,
                memory_version=0,
                updated_at=now_ms(),
            )
        normalized = (instructions_md or "").strip() or None
        ctx.instructions_md = normalized
        ctx.updated_at = now_ms()
        await self._ds.upsert_context(ctx)
        # Per ADR-008: the runtime reads ``session.instructions`` (frozen at
        # session creation), not ``agent.instructions``. So edits here only
        # affect *future new sessions* — already-running sessions keep the
        # prompt they were created with. UI surfaces a hint to that effect.

    async def get_connectors(self, workspace_id: str) -> list[str]:
        row = await self._ds.get_by_id(workspace_id)
        if not row or not row.root_path:
            raise KeyError(workspace_id)
        if not self._connectors:
            return []
        # Pure filesystem read (.claude/project-config.json) — stays sync.
        return self._connectors.get_workspace_connectors(row)

    async def set_connectors(self, workspace_id: str, slugs: list[str]) -> None:
        row = await self._ds.get_by_id(workspace_id)
        if not row or not row.root_path:
            raise KeyError(workspace_id)
        if not self._connectors:
            raise RuntimeError("connector_datastore not wired")
        # Pure filesystem write (.claude/project-config.json) — stays sync.
        self._connectors.set_workspace_connectors(row, slugs)

    async def update_memory(
        self,
        workspace_id: str,
        summary: str | None,
        expected_version: int,
    ) -> None:
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        ctx = await self._ds.get_context(workspace_id)
        if ctx is None:
            ctx = WorkspaceContextRow(
                workspace_id=workspace_id,
                memory_version=0,
                updated_at=now_ms(),
            )
        if ctx.memory_version != expected_version:
            raise ValueError("WORKSPACE_MEMORY_VERSION_CONFLICT")
        ctx.memory_summary = summary
        ctx.memory_version = expected_version + 1
        ctx.updated_at = now_ms()
        await self._ds.upsert_context(ctx)

    async def preview_delete(self, workspace_id: str) -> WorkspaceDeletePreview:
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        if row.kind == "chat":
            raise ValueError("Chat workspace cannot be deleted")

        # Session counts now come from the kernel store.
        try:
            sessions = await kernel_store.list_sessions(project_id=workspace_id, limit=1000)
            session_count = len(sessions)
        except Exception:  # noqa: BLE001
            session_count = 0
        doc_binding_count = await self._docs.count_bindings(workspace_id) if self._docs else 0
        schedule_count = (
            await self._automations.count_by_workspace(workspace_id) if self._automations else 0
        )
        skill_config_count = (
            len(await self._skills.list_project_skills(workspace_id)) if self._skills else 0
        )

        return WorkspaceDeletePreview(
            session_count=session_count,
            doc_binding_count=doc_binding_count,
            schedule_count=schedule_count,
            skill_config_count=skill_config_count,
        )

    async def delete_workspace(self, workspace_id: str) -> None:
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        if row.kind == "chat":
            raise ValueError("Chat workspace cannot be deleted")

        # Delete kernel sessions for this workspace (and their events).
        try:
            sessions = await kernel_store.list_sessions(project_id=workspace_id, limit=1000)
            for s in sessions:
                await kernel_store.delete_session(s.id)
        except Exception:  # noqa: BLE001
            pass
        if self._docs:
            self._docs.remove_all_bindings(workspace_id)
        if self._automations:
            await self._automations.delete_all_for_workspace(workspace_id)
        if self._skills:
            self._skills.set_project_skills(workspace_id, [])
        # Soft-delete the matching kernel Project (and its Agent) so kernel
        # listing endpoints stop showing this workspace. The kernel only soft-
        # deletes by default; existing sessions remain readable for audit.
        self._delete_kernel_mirror(workspace_id)
        self._ds.delete(workspace_id)

    # ------------------------------------------------------------------
    # Kernel mirror — every valuz workspace must back a V5 kernel Project +
    # Agent so sessions can be created against it. The id of the mirrored
    # kernel rows equals the workspace id (1:1) and the agent id is derived
    # deterministically from the workspace id, so re-running these helpers
    # is idempotent.
    # ------------------------------------------------------------------

    @staticmethod
    def _kernel_agent_id(workspace_id: str) -> str:
        # Deterministic so re-running ensure flows is idempotent without an
        # extra lookup. UUID-shaped to satisfy the kernel's ``String(36)``.
        # ``agent-`` is 6 chars + workspace_id (32 hex) = 38; trim to 36.
        return f"agent-{workspace_id}"[:36]

    def _resolve_kernel_cwd(self, row: WorkspaceRow) -> str:
        kind = row.kind if row.kind in ("chat", "project") else "chat"
        return str(fs_registry.workspace_cwd(row.id, kind, row.root_path))  # type: ignore[arg-type]

    def _ensure_kernel_mirror(self, row: WorkspaceRow, *, instructions_md: str | None) -> None:
        """Create or reconcile the kernel Project + Agent for ``row``.

        Idempotent: re-running updates the kernel rows in place.

        Per ADR-008, the per-workspace synthetic agent only carries
        identity/budget fields — instructions / skills / mcp_servers all
        live on the *session*. The ``instructions_md`` argument is no
        longer threaded into the agent here; the session-create path in
        ``SessionService`` reads the latest ``instructions_md`` and writes
        it into ``Session.instructions`` instead.
        """
        del instructions_md  # ADR-008: session is the source of truth
        from src.core.agent_config import (
            AgentConfig as KernelAgentConfig,  # type: ignore[import-not-found]
        )
        from src.core.project import Project as KernelProject  # type: ignore[import-not-found]

        cwd = self._resolve_kernel_cwd(row)
        agent_id = self._kernel_agent_id(row.id)

        existing_agent = kernel_sync.load_agent_sync(agent_id)
        # Ensure ``submit_skill`` is declared on the agent so the runtime
        # advertises it to the model. Idempotent — re-mirroring an agent
        # that already has the declaration leaves the tuple unchanged.
        prior_tools = existing_agent.tools if existing_agent else ()
        merged_tools = _ensure_orchestration_declared(
            _ensure_memory_tools_declared(_ensure_submit_skill_declared(prior_tools))
        )
        agent = KernelAgentConfig(
            id=agent_id,
            name=row.name,
            instructions="",  # ADR-008: session-level field is what the runtime reads
            # Carry forward fields that may have been edited via the kernel
            # API so a cwd refresh doesn't reset the agent's tuning.
            model=existing_agent.model if existing_agent else "claude-sonnet-4-6",
            tools=merged_tools,
            callable_agents=existing_agent.callable_agents if existing_agent else (),
            skills=existing_agent.skills if existing_agent else (),
            mcp_servers=(),  # ADR-008: session-level via capability_resolver
            # ``full_access`` is the agent-level default. The actual approval
            # behaviour for any given turn is decided by ``session.permission_mode``
            # (sunk in V5+1aae940 per ADR-008 successor), which the host stamps
            # at session creation from the user's per-session selection.
            # The synthetic per-workspace agent never surfaces in the UI, so
            # its agent-level default just needs to be a valid value the new
            # 3-value CHECK constraint accepts — pre-upgrade rows of the legacy
            # ``bypass`` value get coerced to ``full_access`` by the kernel's
            # ``807642401b71`` migration.
            permission_mode=_coerce_permission_mode(
                existing_agent.permission_mode if existing_agent else "full_access"
            ),
            max_turns=existing_agent.max_turns if existing_agent else 50,
            max_cost_usd=existing_agent.max_cost_usd if existing_agent else 10.0,
            effort=existing_agent.effort if existing_agent else None,
            thinking=existing_agent.thinking if existing_agent else None,
        )
        kernel_sync.save_agent_sync(agent)

        existing_project = kernel_sync.load_project_sync(row.id)
        project = KernelProject(
            id=row.id,
            name=row.name,
            cwd=cwd,
            status="active",
            metadata=existing_project.metadata if existing_project else {},
        )
        kernel_sync.save_project_sync(project)

    def _rename_workspace_kernel_agent(
        self, workspace_id: str, new_name: str, row: WorkspaceRow | None
    ) -> None:
        """Keep the synthetic agent's ``name`` in lock-step with the workspace.

        Per ADR-008 the agent no longer carries the workspace's prompt;
        the only field this method touches is ``name`` (so kernel listings
        stay readable for ops). If the agent doesn't exist yet we bootstrap it
        via ``_ensure_kernel_mirror`` — ``row`` is the already-fetched workspace
        row threaded down from the async caller (so this stays a pure
        ``kernel_sync`` helper with no host-DB access).
        """
        agent_id = self._kernel_agent_id(workspace_id)
        existing = kernel_sync.load_agent_sync(agent_id)
        if existing is None:
            if row is not None:
                self._ensure_kernel_mirror(row, instructions_md=None)
            return

        from src.core.agent_config import (
            AgentConfig as KernelAgentConfig,  # type: ignore[import-not-found]
        )

        kernel_sync.save_agent_sync(
            KernelAgentConfig(
                id=existing.id,
                name=new_name,
                model=existing.model,
                instructions=existing.instructions,
                tools=_ensure_orchestration_declared(
                    _ensure_memory_tools_declared(_ensure_submit_skill_declared(existing.tools))
                ),
                callable_agents=existing.callable_agents,
                skills=existing.skills,
                mcp_servers=existing.mcp_servers,
                # Re-coerce on every save: a pre-upgrade dev DB whose
                # cached agent row carries a legacy enum value would
                # otherwise re-emit it under the new CHECK constraint.
                permission_mode=_coerce_permission_mode(existing.permission_mode),
                max_turns=existing.max_turns,
                max_cost_usd=existing.max_cost_usd,
                effort=existing.effort,
                thinking=existing.thinking,
            )
        )

    def _rename_kernel_mirror(
        self, workspace_id: str, new_name: str, row: WorkspaceRow | None
    ) -> None:
        existing_project = kernel_sync.load_project_sync(workspace_id)
        if existing_project is None:
            return
        from src.core.project import Project as KernelProject  # type: ignore[import-not-found]

        kernel_sync.save_project_sync(
            KernelProject(
                id=existing_project.id,
                name=new_name,
                cwd=existing_project.cwd,
                status=existing_project.status,
                created_at=existing_project.created_at,
                metadata=existing_project.metadata,
            )
        )
        self._rename_workspace_kernel_agent(workspace_id, new_name, row)

    def _delete_kernel_mirror(self, workspace_id: str) -> None:
        # Kernel does soft-delete (status = "deleted"); the agent stays so
        # historical sessions remain readable.
        kernel_sync.delete_project_sync(workspace_id)

    async def list_files(
        self,
        workspace_id: str,
        depth: int = 2,
        include_hidden: bool = False,
    ) -> list[dict[str, object]]:
        row = await self._ds.get_by_id(workspace_id)
        if not row:
            raise KeyError(workspace_id)
        # Project workspaces walk the user-supplied root_path.
        # Chat workspaces walk their managed cwd under
        # ``data_dir/workspaces/{id}/`` so any files the agent generates
        # during the chat (excel exports, reports, scratch outputs, …)
        # show up in the right-rail "generated files" panel.
        if row.kind == "project":
            if not row.root_path:
                return []
            root = Path(row.root_path)
        else:
            root = fs_registry.workspace_cwd(workspace_id, "chat")
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
