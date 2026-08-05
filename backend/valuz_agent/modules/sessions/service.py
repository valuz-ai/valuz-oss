"""Session service — drives the V5 kernel for all session state and execution.

All session rows live in the kernel ``sessions`` table. Valuz UX fields are
stored under ``sessions.metadata["valuz"]``:

    {
        "valuz": {
            "name": "...",
            "origin": "user",
            "trigger_meta": {...},
            "last_user_message_text": "...",
            "locked_provider_id": null,
            "extra_skill_ids": []
        }
    }

All execution events live in the kernel ``events`` table.  The SSE adapter
(``adapters.event_sse_adapter``) reads from there directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from app.schemas import (
    CreateSessionRequest,
    EventPayload,
    FinalizeSessionRequest,
    ModelSettingsSchema,
    SubmitActionRequest,
    UpdateSessionRequest,
)
from src.core.agent_config import (
    AgentConfig as KernelAgentConfig,
)

# Kernel wire schemas + the in-process run-driver's domain types
# (resolved via sys.path injection from kernel bootstrap).
import valuz_agent.boot.kernel  # noqa: F401 — side-effect: puts kernel on sys.path
from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.capability_resolver import resolve_session_capabilities
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.adapters.model_resolver import resolve_model
from valuz_agent.adapters.system_prompt_builder import (
    build_project_system_prompt,
    ensure_citation_system_policy,
)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.agents.effective_resources import (
    EffectiveResourceManifest,
    EffectiveResourceResolver,
    current_execution_supports_stdio,
)
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.attachments import (
    _attachment_specs,
    _load_pending_attachments,
    _mark_attachments_consumed,
)
from valuz_agent.modules.sessions.context_builder import (
    _build_additional_context,
    worktree_name_of,
)
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.dto import (
    QueuedInput,
    QueuedInputList,
    SessionDetail,
    SessionEventEnvelope,
    SessionListItem,
    SessionRunResponse,
)
from valuz_agent.modules.sessions.errors import (
    BudgetExceeded,
    QueuedInputNotFound,
    QueueFull,
    SessionConflict,
    SessionNotRunnable,
)
from valuz_agent.modules.sessions.events import (
    SESSION_CREATED,
    SESSION_FINISHED,
    SESSION_MESSAGE_SENT,
    SESSION_STATUS_CHANGED,
)
from valuz_agent.modules.sessions.mappers import (
    _coerce_session_effort,
    _coerce_session_permission_mode,
    _kernel_session_not_found,
    _map_kernel_status,
    _session_to_detail,
    _session_to_list_item,
    _valuz_meta,
)
from valuz_agent.modules.sessions.models import QueuedInputRow
from valuz_agent.modules.sessions.pre_turn import chat_capability_hook
from valuz_agent.modules.sessions.run_orchestrator import (
    _derive_session_name,
    _run_agent_background,
    get_dispatching_queue_id,
    is_draining_queue,
    schedule_drain,
)
from valuz_agent.modules.sessions.schemas import SessionWorktreeSpec
from valuz_agent.modules.skills.datastore import SkillDatastore

if TYPE_CHECKING:
    from src.core.types import Session as KernelSessionT

    from valuz_agent.modules.worktrees.service import ProjectRowLike, WorktreeHandle

logger = logging.getLogger(__name__)

# Soft cap on still-``queued`` follow-up inputs per session (input queue). A
# guard against accidental flooding, not a hard product limit. See
# docs/design/session-input-queue.md §8.5.
QUEUE_SOFT_CAP = 20


def _queued_input_to_dto(row: QueuedInputRow) -> QueuedInput:
    payload = row.input or {}
    attachments = payload.get("attachments") or []
    return QueuedInput(
        id=row.id,
        status=row.status,
        position=row.position,
        text=str(payload.get("text") or ""),
        attachment_count=len(attachments),
        provider_id=row.provider_id,
        model_id=row.model_id,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _session_owner_user_id(session: object, fallback_user_id: str | None = None) -> str | None:
    """Resolve a session owner from persisted session data, not ambient context."""
    owner = getattr(session, "user_id", None) or getattr(session, "owner_user_id", None)
    if owner:
        return str(owner)
    metadata = getattr(session, "metadata", None) or {}
    if isinstance(metadata, dict):
        owner = metadata.get("owner_user_id")
        if owner:
            return str(owner)
    return fallback_user_id


async def _enforce_budget(session: object, user_id: str | None = None) -> None:
    """Channel-aware wallet pre-check before a turn runs.

    Resolves the session's owner and its **locked channel** (``locked_provider_id``)
    and hands both to the billing port. Passing the channel lets a billing
    overlay skip enforcement for channels it does not meter — a user's own
    direct API-key channel or an org BYOK channel never consume platform
    credits, so an empty wallet must not block them. Raises ``BudgetExceeded``
    (carrying the overlay's i18n key) when the port rejects.
    """
    from valuz_agent.ports.extensions import ext

    uid = _session_owner_user_id(session, user_id)
    if uid is None:
        # Explicit-identity contract: budget enforcement without an owner is
        # meaningless — fail loudly rather than bill nobody.
        raise LookupError("owner context not set — cannot check budget")
    locked = _valuz_meta(session).get("locked_provider_id")
    budget = await ext.billing.check_budget(uid, provider_id=str(locked) if locked else None)
    if not budget.allowed:
        raise BudgetExceeded(
            budget.reason or "insufficient credits",
            message_key=budget.message_key,
            message_params=budget.message_params,
        )


# ---------------------------------------------------------------------------
# SessionService
# ---------------------------------------------------------------------------


class SessionService:
    """Business façade over the V5 kernel session machinery.

    Constructor parameters are deliberately minimal — only what is needed to
    resolve capabilities at session-creation time and publish internal events.
    The old ``runtime_ctx`` / ``runtime_port`` parameters are gone; execution
    now runs through ``kernel orchestrator.run_turn``.
    """

    def __init__(
        self,
        event_bus: EventBus,
        project_svc: ProjectService,
        providers: ProviderDatastore,
        skills: SkillDatastore,
        projects: ProjectDatastore,
        # KB integration — optional. When supplied, session creation
        # auto-injects the ``valuz-project-docs`` builtin skill into
        # ``session.skills`` if the project has any KB binding. Tests that
        # don't care about KB can omit it.
        docs: DocumentDatastore | None = None,
        # MCP integration — optional so legacy callers (and tests that don't
        # need data sources) can omit them. When provided the capability
        # resolver injects ``McpServerConfig`` rows into the kernel session
        # at creation time.
        connectors: ConnectorDatastore | None = None,
        # User-library skill source — when supplied, chat (non-project)
        # projects auto-include every discovered user-scoped skill in
        # ``Session.skills``. Tests that don't care about skill discovery
        # can omit it.
        skill_source: FilesystemSkillSource | None = None,
        # Additional skill sources (e.g. ``OfficialSkillSource``) walked
        # alongside ``skill_source`` for chat projects. Each source's
        # manifests are filtered by scope inside the resolver. Optional —
        # tests that only care about user skills can omit it.
        extra_skill_sources: list | None = None,
        # Auth facade used to look up the user's entitlements (e.g.
        # ``skills:official``). When ``None``, official skills are gated to
        # bundled built-ins only.
        auth_facade: object | None = None,
        # Legacy keyword accepted for callers that haven't been updated yet;
        # silently ignored.
        datastore: object | None = None,
        runtime_ctx: object | None = None,
        runtime_port: object | None = None,
    ) -> None:
        self._bus = event_bus
        self._project_svc = project_svc
        self._providers = providers
        self._skills = skills
        self._projects = projects
        self._connectors = connectors
        self._docs = docs
        self._skill_source = skill_source
        self._extra_skill_sources = extra_skill_sources or []
        self._auth = auth_facade

    async def _resolve_all_available_resources(
        self,
        user_id: str,
        runtime: object,
    ) -> EffectiveResourceManifest:
        return await EffectiveResourceResolver(
            skills=self._skills,
            connectors=self._connectors,
            docs=self._docs,
        ).resolve(
            user_id,
            runtime=str(runtime),
            supports_stdio=current_execution_supports_stdio(),
        )

    async def _has_official_entitlement(self) -> bool:
        """Check if the connected account grants ``skills:official``.

        Mirrors ``SkillLibraryService._check_entitlement`` so the chat
        runtime applies the same gating the catalog UI does. Returns
        ``False`` when no auth facade is wired (test harnesses) or when
        the lookup raises.
        """
        if self._auth is None:
            return False
        try:
            entitlements = await self._auth.get_entitlements()  # type: ignore[attr-defined]
            return "skills:official" in entitlements
        except Exception:  # noqa: BLE001
            return False

    async def _auto_default_mcp_slugs(
        self, project_id: str, user_id: str | None = None
    ) -> list[str]:
        if user_id is None:
            raise ValueError("user_id is required")

        if self._connectors is None:
            return []

        project_row = await self._projects.get_by_id(user_id, project_id)
        is_project = project_row is not None and project_row.kind == "project"

        try:
            if is_project:
                return await self._connectors.get_project_connectors(user_id, project_id)
            # Chat project: all enabled connectors that are connected or unknown
            return [
                conn.slug
                for conn in await self._connectors.list_enabled(user_id)
                if conn.status in ("connected", "unknown")
            ]
        except Exception:  # noqa: BLE001
            logger.warning("auto-default connector discovery failed", exc_info=True)
            return []

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    async def get_project_last_pick(
        self, project_id: str, user_id: str | None = None
    ) -> dict[str, str | None] | None:
        """Per-project composer memory, seeded on new-session entry.

        Returns two independent agent memories plus the chat-side
        (runtime, provider, model):

        - ``agent_slug`` + ``runtime_provider`` / ``provider_id`` /
          ``model_id`` — from the most recent **conversation** (``chat``).
          ``user_only`` drops task-internal runs so a task the user just ran
          doesn't masquerade as the last chat.
        - ``task_agent_slug`` — the Lead of the most recent **task**
          (``task_lead``), remembered separately so the composer's Task mode
          defaults back to the last Lead, not the last chat agent (the two
          roles usually differ).

        Skips sessions whose ``locked_provider_id`` is missing — OAuth
        subscription rows and partially-created sessions have an empty slot
        and would yield a useless ``(runtime, None, None)`` triple. Scans a
        small recent window in case the very latest is one of those.

        Returns ``None`` only when the project has neither a usable chat nor
        any task (caller falls back to the global Settings → Default tuple).
        """
        if user_id is None:
            raise ValueError("user_id is required")
        uid = user_id
        # Chat memory: runtime/provider/model + the conversation's agent.
        chat_ids = await project_index.list_session_ids(
            project_id, user_only=True, limit=10, user_id=uid
        )
        chat_sessions = await data_reader().list_sessions(uid, ids=chat_ids, limit=10)
        chat_pick: dict[str, str | None] | None = None
        for s in chat_sessions:
            meta = _valuz_meta(s)
            provider_id = meta.get("locked_provider_id") or None
            if not provider_id:
                continue
            chat_pick = {
                "runtime_provider": getattr(s, "runtime_provider", None) or None,
                "provider_id": str(provider_id),
                "model_id": s.model or None,
                "agent_slug": str(a) if (a := meta.get("agent_slug")) else None,
            }
            break

        # Task memory: the Lead agent of the most recent task in this project.
        lead_ids = await project_index.list_session_ids(
            project_id, kind="task_lead", limit=10, user_id=uid
        )
        lead_sessions = await data_reader().list_sessions(uid, ids=lead_ids, limit=10)
        task_agent_slug: str | None = None
        for s in lead_sessions:
            if a := _valuz_meta(s).get("agent_slug"):
                task_agent_slug = str(a)
                break

        if chat_pick is None and task_agent_slug is None:
            return None
        pick: dict[str, str | None] = chat_pick or {
            "runtime_provider": None,
            "provider_id": None,
            "model_id": None,
            "agent_slug": None,
        }
        pick["task_agent_slug"] = task_agent_slug
        return pick

    async def list_sessions(
        self,
        project_id: str | None = None,
        query: str | None = None,
        user_id: str | None = None,
    ) -> list[SessionListItem]:
        if user_id is None:
            raise ValueError("user_id is required")
        # Task-internal sessions (lead / dispatched sub-runs, user_id: str | None = None) belong to
        # tasks and are reachable from the task detail page; the sidebar
        # 对话 rail only wants user-initiated chats. The host-side
        # project↔session index filters by kind *before* the LIMIT, so we
        # get exactly N chats — no over-fetching, no chat/task ratio
        # assumptions.
        ids = await project_index.list_session_ids(
            project_id, user_only=True, limit=200, user_id=user_id
        )
        sessions = await data_reader().list_sessions(user_id, ids=ids, limit=200)
        order = {sid: i for i, sid in enumerate(ids)}
        sessions.sort(key=lambda s: order.get(s.id, len(order)))
        items = [_session_to_list_item(s) for s in sessions]
        # One probe for the whole page, membership per row — the same shape
        # ``list_runs`` uses. Carrying it on the list item (not just the
        # detail) is what lets a surface reading the session list stay live as
        # background work starts and ends. Best-effort: a seam hiccup must
        # degrade the badge, never blank the list.
        try:
            bg_busy = set(await kernel_client.bg_busy_session_ids())
        except Exception:  # noqa: BLE001
            logger.debug("session list: bg-busy probe failed", exc_info=True)
        else:
            for item in items:
                item.background = item.id in bg_busy
        if query:
            q = query.lower()
            items = [i for i in items if i.name and q in i.name.lower()]
        return items

    async def get_session(self, session_id: str, user_id: str | None = None) -> SessionDetail:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        detail = _session_to_detail(session)
        # Liveness is computed on read (git/fs is the source of truth) — the
        # metadata snapshot only says where the session was created. The UI
        # uses this to grey out the worktree badge; sending a message will
        # self-heal (recreate) the worktree anyway.
        if detail.worktree is not None:
            from pathlib import Path as _Path

            detail.worktree.exists = await asyncio.to_thread(
                (_Path(detail.worktree.path) / ".git").exists
            )
        # Background work outlives the turn that launched it, so ``status``
        # reads ``idle`` while a task is still executing. The runs overview
        # already compensates from the orchestrator's live registry; read the
        # SAME seam here so the conversation header and the sidebar answer
        # this question identically instead of each deriving its own.
        # Best-effort, exactly as ``list_runs`` treats it: a seam hiccup must
        # degrade the badge, never fail the session read.
        try:
            detail.background = session_id in set(await kernel_client.bg_busy_session_ids())
        except Exception:  # noqa: BLE001
            logger.debug("session detail: bg-busy probe failed", exc_info=True)
        return detail

    async def list_events(
        self,
        session_id: str,
        user_id: str,
        after_seq: int = 0,
    ) -> list[SessionEventEnvelope]:
        """Fetch kernel events for *session_id* with id > *after_seq*.

        Wire shape matches the legacy pre-V5 contract the desktop renderer
        was authored against (``message.user``, ``message.assistant.delta``,
        ``tool.call.*``, ``run.failed``, ``runtime.engine.cost``). Kernel
        events that have no legacy counterpart are filtered.
        """
        # Verify session exists.
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        from valuz_agent.adapters.event_sse_adapter import list_events_after

        frames = await list_events_after(
            session_id,
            user_id=user_id,
            after_seq=after_seq,
            limit=2000,
        )
        return [
            SessionEventEnvelope(
                seq=frame.seq,
                event={"event_type": frame.event_type, "payload": frame.payload},
                timestamp=frame.timestamp,
                event_uid=frame.event_uid,
            )
            for frame in frames
        ]

    async def list_events_window(
        self,
        session_id: str,
        user_id: str,
        before_seq: int | None = None,
        turn_limit: int = 20,
    ) -> tuple[list[SessionEventEnvelope], bool]:
        """Fetch a turn-aligned window of events ending strictly before ``before_seq``.

        See ``event_sse_adapter.list_events_window`` for the slicing
        contract. The router uses this for the conversation page's
        upward pagination (initial load + scroll-to-top "load earlier
        turns"); the linear ``list_events`` / SSE path stays for
        incremental delivery.
        """
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        from valuz_agent.adapters.event_sse_adapter import list_events_window

        window = await list_events_window(
            session_id,
            user_id=user_id,
            before_seq=before_seq,
            turn_limit=turn_limit,
        )
        items = [
            SessionEventEnvelope(
                seq=frame.seq,
                event={"event_type": frame.event_type, "payload": frame.payload},
                timestamp=frame.timestamp,
                event_uid=frame.event_uid,
            )
            for frame in window.items
        ]
        return items, window.has_more

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_session_cwd(user_id: str, row) -> str:  # noqa: ANN001
        """Required session cwd — the kernel no longer has a project to fall
        back to, so every create path must pass an absolute directory."""
        from valuz_agent.infra.fs_registry import fs_registry

        kind = row.kind if row.kind in ("chat", "project") else "chat"
        return str(fs_registry.project_cwd(user_id, row.id, kind, row.root_path))

    @staticmethod
    async def _enter_worktree(
        user_id: str | None,
        project_row: ProjectRowLike,
        spec: SessionWorktreeSpec,
    ) -> WorktreeHandle:
        """Materialize the session's worktree; raises WorktreeNotAvailable
        (422) when the project isn't a git repo — no silent fallback."""
        if user_id is None:
            raise ValueError("user_id is required")
        from valuz_agent.modules.worktrees.service import worktree_service

        return await worktree_service.get_or_create(
            user_id, project_row, name=spec.name, origin="u"
        )

    @staticmethod
    def _worktree_snapshot(handle: WorktreeHandle) -> dict[str, object]:
        """The immutable metadata blob stamped into the session at creation.

        This is the session's permanent record of where it ran (survives the
        worktree's later removal) AND the input ``cleanup_if_clean`` trusts
        for teardown — keep the two consumers in mind when changing shape.
        """
        return {
            "name": handle.name,
            "branch": handle.branch,
            "path": handle.path,
            "git_root": handle.git_root,
            "base_sha": handle.base_sha,
        }

    @staticmethod
    async def _heal_worktree_if_missing(session: KernelSessionT) -> dict[str, object] | None:
        """Re-entry guard (design §4-R): recreate a removed worktree before a
        turn runs, so a historical worktree session stays usable instead of
        dying in the runtime with a missing-cwd error. Returns the refreshed
        metadata snapshot when a recreation happened (caller persists it)."""
        snapshot = _valuz_meta(session).get("worktree")
        if not isinstance(snapshot, dict):
            return None
        from valuz_agent.modules.worktrees.service import worktree_service

        return await worktree_service.heal_from_snapshot(snapshot)

    @staticmethod
    def _worktree_notice(handle: WorktreeHandle) -> str:
        from valuz_agent.adapters.system_prompt_builder import build_worktree_notice

        return build_worktree_notice(
            name=handle.name,
            branch=handle.branch,
            base_sha=handle.base_sha,
            worktree_path=handle.path,
            main_workspace=handle.git_root,
            submodules_ok=handle.submodules_ok,
        )

    async def _resolve_bound_agent(
        self, project_id: str, agent_slug: str, user_id: str | None = None
    ) -> tuple[str, KernelAgentConfig]:
        """Resolve a session's bound agent to ``(kernel_agent_id, AgentConfig)``.

        The returned config is built in memory from the host AgentRow and is
        embedded into the session as its ``agent_config`` snapshot.

        Two binding sources, tried in order:

        1. **Project member** — project conversations bind ``agent_slug`` to a
           per-project ``ProjectMemberRow`` (the 派驻 agent).
        2. **Global library agent** — temp / quick-chat conversations bind to a
           global library agent (e.g. the seeded ``default-assistant``), which is
           NOT a member of any project. The 09-assistant design has no agentless
           path, so every chat-default session carries such a slug.

        Either way the config is built in memory from the library AgentRow's
        current fields and embedded into the session as its snapshot.
        """
        from valuz_agent.modules.agents.datastore import (
            AgentDatastore,
            ProjectMemberDatastore,
        )
        from valuz_agent.modules.agents.service import (
            AgentService,
        )

        async with async_unit_of_work() as _db:
            member = await ProjectMemberDatastore(_db).get(user_id, project_id, agent_slug)
            if member is not None:
                # Live reference: the member points at a library AgentRow via
                # ``source_agent_slug`` — build the snapshot from the row's
                # CURRENT fields so every new session picks up library edits.
                if member.source_agent_slug:
                    row = await AgentDatastore(_db).get_agent(user_id, member.source_agent_slug)
                    if row is not None:
                        config = await AgentService(_db).build_agent_config(row)
                        return config.id, config
                # Member without provenance (legacy row whose backfill found
                # no matching library agent) — nothing to build a config from.
                raise SessionNotRunnable(
                    f"agent '{agent_slug}' has no source library agent — "
                    "re-deploy it from the agent library"
                )

        # Not a project member → resolve as a global library agent.
        async with async_unit_of_work() as _db:
            row = await AgentDatastore(_db).get_agent(user_id, agent_slug)
            if row is None:
                raise SessionNotRunnable(
                    f"agent '{agent_slug}' not found — pick a configured agent or add one first"
                )
            config = await AgentService(_db).build_agent_config(row)
            return config.id, config

    async def _create_agent_bound_session(
        self,
        *,
        project_id: str,
        agent_slug: str,
        origin: str,
        title: str | None,
        trigger_meta: dict[str, str] | None,
        creation_context: dict[str, str] | None,
        permission_mode: str | None,
        override_runtime_id: str | None = None,
        override_model_id: str | None = None,
        override_provider_id: str | None = None,
        override_effort: str | None = None,
        worktree: SessionWorktreeSpec | None = None,
        user_id: str | None = None,
    ) -> SessionDetail:
        """Create a session bound to an agent (project member OR global library).

        The agent supplies the session's defaults — runtime_provider / model /
        provider / effort, plus instructions / skills / mcp_servers. The
        ``override_*`` arguments let a single conversation start with a
        different runtime / model / provider / effort WITHOUT mutating the
        agent: they are written onto this session only (the agent row is never
        touched), and — per ADR-006 — frozen for the session's lifetime. This
        mirrors the dispatch
        ``build_member_session`` path but for a plain (non-task)
        conversation — no brief, no run_dir override (the kernel uses the
        project cwd).

        ``agent_slug`` resolves to a project member for project conversations,
        or to a global library agent (the seeded default-assistant) for temp /
        quick-chat conversations — see ``_resolve_bound_agent``.
        """
        from valuz_agent.adapters.provider_resolver import (
            ProviderNotResolvable,
            resolve_model_provider,
            resolve_runtime_provider,
        )
        from valuz_agent.modules.providers.service import (
            materialize_logged_in_subscription,
            subscription_login_hint,
        )

        # Temp / quick-chat sessions bind a global library agent to a fresh,
        # isolated chat project — materialize it first (same as the raw-model
        # path) so the runtime is isolated from sibling chats and the library
        # agent isn't (wrongly) looked up as a member of "chat-default".
        if project_id == "chat-default" and self._project_svc:
            fresh_ws = await self._project_svc.create_chat_project_for_session(user_id)
            project_id = fresh_ws.id

        kernel_agent_id, agent = await self._resolve_bound_agent(
            project_id, agent_slug, user_id=user_id
        )

        # v3 (M10 附录 E): the launcher/observability tools (create_task /
        # list_tasks / get_task) and the dispatch-tool stripping are applied at
        # agent CREATE/EDIT time (AgentService._prepare_conversation_tools), NOT
        # here — starting a conversation must never mutate or re-save the agent
        # (that previously triggered an agent save on every "send"). We read the
        # agent as-is.
        # Resolve the effective brain: the agent supplies the defaults, the
        # ``override_*`` args (one conversation's temporary picks) win when set.
        effective_model = override_model_id or agent.model
        effective_runtime_request = override_runtime_id or agent.runtime_provider
        model_overridden = bool(override_model_id) and override_model_id != agent.model

        # Provider resolution:
        #  - an explicit ``override_provider_id`` always wins;
        #  - if the MODEL was overridden, the agent's pinned provider may not
        #    host the new model, so skip it and resolve a provider that does;
        #  - otherwise prefer the agent's pinned provider (the common case for
        #    source-agent-instantiated members carries none — provider ids are
        #    install-local), falling back to any enabled provider hosting the
        #    model. We never pin the resolved provider back onto the agent —
        #    starting a conversation must never re-save the agent (M10 附录 E).
        provider_id = override_provider_id
        if not provider_id and not model_overridden:
            provider_id = (agent.metadata or {}).get("provider_id")
        if not provider_id:
            from valuz_agent.infra.eventbus import event_bus
            from valuz_agent.modules.providers.service import ProviderService

            prov_svc = ProviderService(
                datastore=self._providers,
                event_bus=event_bus,
            )
            match = await prov_svc.resolve_provider_for_model(user_id, effective_model)
            if match is not None:
                provider_id = match.id
        if not provider_id:
            raise SessionNotRunnable(
                f"agent '{agent_slug}' has no model provider configured and no "
                f"enabled provider hosts model '{effective_model}' — add a provider "
                "for that model or pin one on the agent"
            )

        # Backstop for a stale subscription reference. A logged-in subscription
        # is normally materialized into a real row the moment its CLI login is
        # detected (frontend onboarding / Settings). But a value saved before
        # that — an agent pinned to the virtual ``ch-codex-subscription`` id, or
        # a composer pick of a not-yet-materialized template — still carries the
        # catalog id, which owns no row and would resolve to a raw
        # "provider not found" 400. Materialize it now (CLI-login-gated) and swap
        # to the real uuid; if the CLI isn't logged in, raise an actionable hint
        # instead of the cryptic error.
        uid = user_id
        if await self._providers.get_by_id(uid, provider_id) is None:
            healed = await materialize_logged_in_subscription(self._providers, uid, provider_id)
            if healed is not None:
                provider_id = healed.id
            else:
                hint = subscription_login_hint(provider_id)
                if hint is not None:
                    raise SessionNotRunnable(hint)

        try:
            runtime_provider = await resolve_runtime_provider(
                provider_id=provider_id,
                model_id=effective_model,
                providers=self._providers,
                request_runtime_id=effective_runtime_request,
                user_id=user_id,
            )
            model_provider = await resolve_model_provider(
                provider_id=provider_id,
                model_id=effective_model,
                providers=self._providers,
                runtime_provider=runtime_provider,
                user_id=user_id,
            )
        except ProviderNotResolvable as exc:
            raise SessionNotRunnable(str(exc)) from exc

        # Snapshot the project prompt + the agent's persona instructions.
        project_row = await self._projects.get_by_id(user_id, project_id)
        if project_row is None:
            raise SessionNotRunnable(f"project '{project_id}' not found")
        session_cwd = self._resolve_session_cwd(user_id, project_row)

        # Worktree isolation (opt-in) — resolved BEFORE prompt assembly (the
        # notice is a prompt section) and before the skill resolution below,
        # which resolves relative to the session cwd. Raises 422 when the
        # project isn't a git repo — deliberately no mkdir fallback.
        wt_handle = None
        if worktree is not None:
            wt_handle = await self._enter_worktree(user_id, project_row, worktree)
            session_cwd = wt_handle.session_cwd

        project_ctx = await self._projects.get_context(user_id, project_id)
        project_prompt = build_project_system_prompt(
            project_name=project_row.name if project_row else "",
            instructions_md=project_ctx.instructions_md if project_ctx else None,
        )
        # VALUZ-CHATPLAN S3: project-conversation agents (i.e. chat sessions
        # bound to a project agent) carry the chat task playbook so the
        # model knows to draft → plan → commit (with user "go") instead of
        # creating tasks straight away, and to inject mid-flight rather
        # than starting new tasks. Lead/member agents have their own
        # playbooks (DISPATCH_PLAYBOOK / COMMITTED_LEAD_PLAYBOOK) and never
        # flow through this code path.
        from valuz_agent.adapters.agent_resolver import CHAT_TASK_PLAYBOOK
        from valuz_agent.adapters.system_prompt_builder import (
            AUTHORIZATION_BOUNDARY_INSTRUCTIONS,
            OUTPUT_FORMAT_INSTRUCTIONS,
            assemble_session_instructions,
        )
        from valuz_agent.modules.memory.injection import memory_instructions_block
        from valuz_agent.ports.instructions import (
            agent_inherits_global_instructions,
            resolve_global_instructions,
        )

        # Frozen memory snapshot (memory-system-design §8): rendered once here
        # and frozen into ``Session.instructions`` — one copy per session
        # instead of one per user message (the old additional-context path).
        mem_block = await memory_instructions_block(user_id=user_id, project_id=project_id)

        agent_meta = agent.metadata or {}
        inherits_global = agent_inherits_global_instructions(
            kind=agent_meta.get("agent_kind", "standard"),
            inherit_global_instructions=agent_meta.get("inherit_global_instructions", True),
        )
        prompt_snapshot = await resolve_global_instructions(user_id) if inherits_global else None
        all_available_manifest = (
            await self._resolve_all_available_resources(user_id, runtime_provider)
            if agent_meta.get("resource_policy") == "all_available"
            else None
        )
        instructions = assemble_session_instructions(
            [
                (
                    "global-instructions",
                    prompt_snapshot.content if prompt_snapshot is not None else "",
                ),
                ("authorization-boundary", AUTHORIZATION_BOUNDARY_INSTRUCTIONS),
                ("agent-instructions", agent.instructions or ""),
                ("project-instructions", project_prompt),
                ("memory", mem_block),
                ("task-playbook", CHAT_TASK_PLAYBOOK),
                (
                    "worktree-context",
                    self._worktree_notice(wt_handle) if wt_handle else "",
                ),
                ("output-format", OUTPUT_FORMAT_INSTRUCTIONS),
            ]
        )
        instructions = ensure_citation_system_policy(instructions)

        effective_permission_mode = _coerce_session_permission_mode(
            permission_mode or agent.permission_mode
        )
        # Effort is a per-agent opt-in: it travels as configured. DeepAgents
        # maps effort → OpenAI reasoning_effort, which most openai-compat
        # backends accept (mimo /v1 does); only some reject it (deepseek-v4-flash
        # 400s "thinking options type cannot be disabled when reasoning_effort is
        # set"). That's a per-model constraint — clear effort on those specific
        # agents — not a reason to drop it runtime-wide.
        effective_effort = override_effort or getattr(agent, "effort", None)
        model_settings = (
            ModelSettingsSchema(effort=_coerce_session_effort(effective_effort))
            if effective_effort
            else ModelSettingsSchema()
        )

        session_id = uuid4().hex

        # Guarantee the always-on baseline AT SESSION-CREATE (not "whatever the
        # agent happens to carry") — symmetric with the task path
        # (agent_resolver.build_member_session). Two halves:
        #  - in-process harness tools (memory / submit_skill / create_task etc.)
        #    bind via AgentConfig.tools, so ensure the bound agent carries them
        #    (idempotent; only re-saves when something was missing — no per-send
        #    save churn once the agent is prepared).
        #  - HTTP MCP (valuz_docs / valuz_schedules / valuz_connectors) + the
        #    baseline skills (valuz-project-docs / skill-creator) are session
        #    fields, injected here on top of the agent's own connectors/skills.
        if all_available_manifest is not None:
            caps = await resolve_session_capabilities(
                projects=self._projects,
                skills=self._skills,
                project_id=project_id,
                user_id=user_id,
                enabled_mcp_provider_slugs=all_available_manifest.connector_slugs,
                connectors=self._connectors,
                docs=self._docs,
                session_id=session_id,
                all_available_skill_paths=all_available_manifest.skill_paths,
            )
            session_mcp = list(caps.mcp_servers)
            session_skills = caps.skills
        else:
            from valuz_agent.adapters.capability_resolver import (
                always_on_http_mcp_servers,
                always_on_skill_paths,
                resolve_skill_slugs_to_paths,
            )

            existing_mcp_names = {getattr(m, "name", None) for m in (agent.mcp_servers or ())}
            from app.serializers import mcp_to_schema

            session_mcp = [mcp_to_schema(m) for m in (agent.mcp_servers or ())] + [
                m
                for m in await always_on_http_mcp_servers(session_id, owner_user_id=user_id)
                if m.name not in existing_mcp_names
            ]
            import os as _os

            own_skill_keys = {
                (s.name if hasattr(s, "name") else str(s)) for s in (agent.skills or ())
            }
            own_skill_paths = await resolve_skill_slugs_to_paths(
                agent.skills,
                session_cwd,
                user_id=user_id,
            )
            session_skills = tuple(own_skill_paths) + tuple(
                p
                for p in always_on_skill_paths(user_id=user_id)
                if _os.path.basename(p) not in own_skill_keys
            )

        valuz_meta: dict[str, object] = {
            "name": title,
            "origin": origin,
            "project_id": project_id,
            "trigger_meta": trigger_meta,
            "last_user_message_text": None,
            "locked_provider_id": provider_id,
            "extra_skill_ids": [],
            "agent_slug": agent_slug,
        }
        if prompt_snapshot is not None:
            valuz_meta["global_instructions"] = prompt_snapshot.metadata()
        if all_available_manifest is not None:
            valuz_meta["capability_manifest"] = all_available_manifest.session_metadata()
        if wt_handle is not None:
            valuz_meta["worktree"] = self._worktree_snapshot(wt_handle)
        if creation_context:
            valuz_meta["creation_context"] = {str(k): str(v) for k, v in creation_context.items()}

        from app.serializers import agent_config_to_schema

        created = await kernel_client.create_session(
            user_id,
            CreateSessionRequest(
                id=session_id,
                agent_config=agent_config_to_schema(agent),
                cwd=session_cwd,
                runtime_provider=runtime_provider,
                model=effective_model,
                model_provider=model_provider,
                model_settings=model_settings,
                instructions=instructions,
                skills=list(session_skills),
                mcp_servers=list(session_mcp),
                permission_mode=effective_permission_mode,
                metadata={"valuz": valuz_meta},
            ),
        )
        await project_index.record(
            project_id,
            session_id,
            kind="chat",
            origin=str(origin or "user"),
            user_id=user_id,
        )

        self._bus.publish(
            SESSION_CREATED,
            session_id=session_id,
            project_id=project_id,
        )
        return _session_to_detail(created)

    async def create_session(
        self,
        project_id: str,
        origin: str = "user",
        title: str | None = None,
        trigger_meta: dict[str, str] | None = None,
        model_id: str | None = None,
        provider_id: str | None = None,
        runtime_id: str | None = None,
        mcp_provider_slugs: list[str] | None = None,
        creation_context: dict[str, str] | None = None,
        permission_mode: str | None = None,
        effort: str | None = None,
        agent_slug: str | None = None,
        worktree: SessionWorktreeSpec | None = None,
        user_id: str | None = None,
    ) -> SessionDetail:
        """Create a new kernel session for *project_id*.

        Resolves model + capabilities from the valuz catalog, persists a kernel
        ``Session`` row, and publishes the ``SESSION_CREATED`` event.

        When ``agent_slug`` is given the session binds to that agent:
        instructions / skills / connectors always come from the agent, and
        runtime / model / provider / effort default to the agent's brain. An
        explicit model_id / provider_id / runtime_id / effort in that path
        OVERRIDES the agent's default for this one session only — the agent row
        is never modified, and the values are frozen for the session (ADR-006).
        """
        if agent_slug:
            return await self._create_agent_bound_session(
                project_id=project_id,
                agent_slug=agent_slug,
                origin=origin,
                title=title,
                trigger_meta=trigger_meta,
                creation_context=creation_context,
                permission_mode=permission_mode,
                override_runtime_id=runtime_id,
                override_model_id=model_id,
                override_provider_id=provider_id,
                override_effort=effort,
                worktree=worktree,
                user_id=user_id,
            )
        # Quick-chat sessions get an ephemeral, single-use project each
        # time. ``"chat-default"`` is the sentinel the chat launchers send
        # — we materialize a fresh ``kind="chat"`` project + kernel
        # project (with its own cwd under ``data_dir/projects/{id}/``)
        # so the runtime is isolated from sibling chats. Skill scoping
        # still uses the literal ``"chat-default"`` string as the scope
        # key, independent of any specific project id.
        if project_id == "chat-default" and self._project_svc:
            fresh_ws = await self._project_svc.create_chat_project_for_session(user_id)
            project_id = fresh_ws.id

        # Apply app-level defaults from Settings → "Default model" (the
        # global runtime/provider/model/effort tuple users configure
        # once). Any caller that passes an explicit value still wins —
        # these only fill in the unspecified fields. Covers every entry
        # point: quick chat, skill-creator sessions, scheduled-task runs.
        #
        # IMPORTANT: when the caller passed an explicit ``model_id`` but
        # left ``provider_id`` blank, we deliberately DO NOT fall back
        # to the user-level default provider — that combination would
        # silently route the explicit model to whichever provider
        # happens to be the global default, which is precisely how a
        # DeepSeek-pinned scheduled task ended up talking to MiMo when
        # the user later switched their default. Leave ``provider_id``
        # as ``None`` and let the ``resolve_provider_for_model`` lookup
        # below pick the provider that actually hosts the model.
        caller_supplied_model = model_id is not None
        if runtime_id is None or provider_id is None or model_id is None or effort is None:
            # Settings-prefs readers are async; read them on the loop through
            # one ``async_unit_of_work`` session.
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.settings import preferences as _prefs

            async with async_unit_of_work(commit=False) as _pref_db:
                if runtime_id is None:
                    runtime_id = await _prefs.get_default_runtime(_pref_db, user_id=user_id)
                if provider_id is None and not caller_supplied_model:
                    provider_id = await _prefs.get_default_provider_id(_pref_db, user_id=user_id)
                if model_id is None:
                    model_id = await _prefs.get_default_model(_pref_db, user_id=user_id)
                if effort is None:
                    # ``None`` from settings means "no override" — the runtime SDK
                    # picks its own default. The kernel ``ModelSettings.effort``
                    # Optional union expects exactly the EFFORT_VALUES set or
                    # ``None``; the settings helper guarantees that contract.
                    effort = await _prefs.get_default_effort(_pref_db, user_id=user_id)

        # Resolve model.
        project_row = await self._projects.get_by_id(user_id, project_id)
        resolution = await resolve_model(
            providers=self._providers,
            request_model_id=model_id,
            request_provider_id=provider_id,
            request_runtime_id=runtime_id,
            user_id=user_id,
        )

        # Bind a provider to the session at creation time so the runtime layer
        # has a single source of truth. If the caller passed an explicit
        # ``provider_id`` we trust it; otherwise we ask the provider service
        # which configured provider hosts the resolved model.
        resolved_provider_id: str | None = provider_id
        if not resolved_provider_id and resolution.model:
            from valuz_agent.infra.eventbus import event_bus
            from valuz_agent.modules.providers.service import ProviderService

            prov_svc = ProviderService(
                datastore=self._providers,
                event_bus=event_bus,
            )
            match = await prov_svc.resolve_provider_for_model(user_id, resolution.model)
            if match is not None:
                resolved_provider_id = match.id

        # Compose the kernel ModelProvider that will travel with the
        # session. Kernel V5 (post-MODEL_CATALOG drop) dispatches to a
        # runtime by ``api_protocol`` — ``"anthropic"`` → Claude SDK,
        # ``"openai"`` → DeepAgents/LangChain. ``provider_resolver``
        # reads the provider's ``secret_ref`` credential. No provider
        # selected and no fallback is acceptable: kernel rejects sessions
        # without a provider.
        from valuz_agent.adapters.provider_resolver import (
            ProviderNotResolvable,
            resolve_model_provider,
            resolve_runtime_provider,
        )
        from valuz_agent.modules.providers.service import (
            materialize_logged_in_subscription,
            subscription_login_hint,
        )

        if resolved_provider_id is None:
            raise SessionNotRunnable(
                "no provider selected — pick a model provider before creating "
                "a session, or configure a project default"
            )
        # Backstop for a composer pick / stale default that still carries a
        # virtual ``ch-*`` subscription id (no row → raw "provider not found").
        # Materialize it now if its CLI is logged in (mirrors the frontend's
        # detect-then-materialize), else raise an actionable login hint. See the
        # agent-conversation path above for the full rationale.
        _uid = user_id
        if await self._providers.get_by_id(_uid, resolved_provider_id) is None:
            _healed = await materialize_logged_in_subscription(
                self._providers, _uid, resolved_provider_id
            )
            if _healed is not None:
                resolved_provider_id = _healed.id
            else:
                _hint = subscription_login_hint(resolved_provider_id)
                if _hint is not None:
                    raise SessionNotRunnable(_hint)

        # Resolve the runtime BEFORE the model provider: dual-protocol
        # built-ins (DeepSeek / Zhipu / Moonshot / MiniMax) let the
        # runtime pick decide api_protocol + base_url, so the runtime
        # selection has to happen first. For OAuth subscription
        # providers ``resolve_model_provider`` returns ``None`` regardless
        # of runtime, so the ordering is safe for them too.
        try:
            runtime_provider = await resolve_runtime_provider(
                provider_id=resolved_provider_id,
                model_id=resolution.model,
                providers=self._providers,
                request_runtime_id=runtime_id,
                user_id=user_id,
            )
        except ProviderNotResolvable as exc:
            raise SessionNotRunnable(str(exc)) from exc
        try:
            model_provider = await resolve_model_provider(
                provider_id=resolved_provider_id,
                model_id=resolution.model,
                providers=self._providers,
                runtime_provider=runtime_provider,
                user_id=user_id,
            )
        except ProviderNotResolvable as exc:
            # Surface the underlying reason so the API layer can render a
            # clean error to the user.
            raise SessionNotRunnable(str(exc)) from exc

        # Agentless shares Valurion's all-available resource definition.
        # Caller-provided connector picks are retained in the public request
        # for compatibility but cannot narrow/broaden the authorized set.
        del mcp_provider_slugs
        all_available_manifest = await self._resolve_all_available_resources(
            user_id,
            runtime_provider,
        )

        # Allocate session id up-front so capability resolution can stamp
        # it into the in-process docs MCP URL (the URL embeds the session
        # id so the host can scope each request to a project). The id
        # then flows into the kernel session row unchanged below.
        session_id = uuid4().hex

        # Resolve skills / mcp_servers.
        try:
            caps = await resolve_session_capabilities(
                projects=self._projects,
                skills=self._skills,
                project_id=project_id,
                user_id=user_id,
                enabled_mcp_provider_slugs=all_available_manifest.connector_slugs,
                connectors=self._connectors,
                docs=self._docs,
                session_id=session_id,
                all_available_skill_paths=all_available_manifest.skill_paths,
            )
        except KeyError:
            caps_skills: tuple[str, ...] = ()
            caps_mcp: tuple = ()
        else:
            caps_skills = caps.skills
            caps_mcp = caps.mcp_servers

        agent_id = ProjectService._kernel_agent_id(project_id)
        # Synthetic per-project assistant config, built in memory and embedded
        # as the session's snapshot (mirrors what the kernel mirror used to
        # store in the agents table: identity + the conversation tool set).
        # Tool surfaces ride the session's ``harness`` MCP entry (appended
        # with the always-on servers below) — the synthetic config carries
        # no tool declarations.
        agent_config = KernelAgentConfig(
            id=agent_id,
            name=title or "Assistant",
            model=resolution.model,
            runtime_provider=runtime_provider,
            instructions="",  # the session field below is the source of truth
            permission_mode="full_access",
        )

        # Per ADR-008: snapshot the project's current ``instructions_md``
        # into ``Session.instructions`` at create time. The runtime reads
        # the session field, not the agent — so this is the moment that
        # locks the system prompt for the session's lifetime. Project
        # edits after this point apply only to *future* sessions.
        project_row = await self._projects.get_by_id(user_id, project_id)
        project_ctx = await self._projects.get_context(user_id, project_id)
        session_instructions = build_project_system_prompt(
            project_name=project_row.name if project_row else "",
            instructions_md=project_ctx.instructions_md if project_ctx else None,
        )

        # Deployment-level preamble (InstructionsPort) — the raw/no-agent path
        # covers quick chat, skill-creator sessions, and agent-less scheduled
        # runs, so it must carry the ``<global-instructions>`` section too,
        # same as the agent-bound and task paths. OSS binds no override →
        # no-op, prompt unchanged.
        from valuz_agent.adapters.system_prompt_builder import (
            assemble_session_instructions,
            prepend_global_instructions,
        )
        from valuz_agent.modules.memory.injection import memory_instructions_block
        from valuz_agent.ports.instructions import resolve_global_instructions

        prompt_snapshot = await resolve_global_instructions(user_id)
        session_instructions = await prepend_global_instructions(
            session_instructions,
            user_id=user_id,
            snapshot=prompt_snapshot,
        )

        # Frozen memory snapshot (memory-system-design §8), same section the
        # agent-bound and task paths get. Quick-chat ephemeral projects have
        # no project MEMORY file, so passing project_id is a no-op for them
        # (only user + global render); agent-less scheduled runs in a real
        # project pick up that project's memory.
        mem_block = await memory_instructions_block(user_id=user_id, project_id=project_id)
        if mem_block:
            mem_section = assemble_session_instructions([("memory", mem_block)])
            session_instructions = (
                f"{session_instructions}\n\n{mem_section}" if session_instructions else mem_section
            )

        # Build the valuz metadata blob.
        valuz_meta: dict[str, object] = {
            "name": title,
            "origin": origin,
            "project_id": project_id,
            "trigger_meta": trigger_meta,
            "last_user_message_text": None,
            "locked_provider_id": resolved_provider_id,
            "extra_skill_ids": [],
            "global_instructions": prompt_snapshot.metadata(),
            "capability_manifest": all_available_manifest.session_metadata(),
        }
        # Optional ``creation_context`` records *why* the session was
        # opened (chat / project / skills_library) so the
        # ``submit_skill`` confirm flow can apply per-entry side-effects
        # on user confirmation. Stored only when the caller passes it;
        # for organic sessions (no launcher), the confirm endpoint
        # infers the kind from the session's project at confirm time.
        if creation_context:
            valuz_meta["creation_context"] = {str(k): str(v) for k, v in creation_context.items()}

        # ``runtime_provider`` was resolved above (before model-provider
        # composition) so dual-protocol built-ins can pick the correct
        # api_protocol + base_url. Kernel V5+d5f2238 dispatches runtimes
        # via this explicit ``Session.runtime_provider`` enum.

        # Permission mode is per-session (live-reconcile via PATCH
        # ``/v1/sessions/{id}/permission-mode``). Default ``full_access``
        # mirrors the kernel default so legacy callers (no UI exposure
        # yet) keep their current auto-approve behavior. DeepAgents
        # rejects ``auto_review`` at the kernel boundary (only Claude
        # tier supports the LLM classifier), so we mirror that 400 here
        # before we even hit the kernel save path.
        effective_permission_mode = _coerce_session_permission_mode(permission_mode)
        if runtime_provider == "deepagents" and effective_permission_mode == "auto_review":
            raise SessionNotRunnable(
                "auto_review is not supported for deepagents runtimes; pick default or full_access"
            )

        # ``effort`` is per-session and live-reconcilable via PATCH
        # ``/v1/sessions/{id}/effort``. ``None`` lets the runtime fall
        # through to its SDK default. The kernel ``ModelSettings`` blob
        # also has temperature / max_tokens slots which we don't expose
        # to the UI yet — leaving them as ``None`` means the runtime
        # picks the SDK default for those as well.
        # Effort travels as requested — it's a per-model capability (mimo /v1
        # accepts reasoning_effort; deepseek-v4-flash 400s on it), not a
        # runtime-wide one. Don't strip it for deepagents wholesale.
        effective_effort = _coerce_session_effort(effort)
        model_settings = ModelSettingsSchema(effort=effective_effort)

        if project_row is None:
            raise SessionNotRunnable(f"project '{project_id}' not found")

        # Worktree isolation (opt-in): swap the session cwd for an isolated
        # git worktree of the project repo, stamp the immutable snapshot into
        # metadata, and tell the agent where it is (design §4). Raises 422
        # when the project isn't a git repo — deliberately no mkdir fallback.
        session_cwd = self._resolve_session_cwd(user_id, project_row)
        if worktree is not None:
            wt_handle = await self._enter_worktree(user_id, project_row, worktree)
            session_cwd = wt_handle.session_cwd
            valuz_meta["worktree"] = self._worktree_snapshot(wt_handle)
            notice = f"<worktree-context>\n{self._worktree_notice(wt_handle)}\n</worktree-context>"
            session_instructions = (
                f"{session_instructions}\n\n{notice}" if session_instructions else notice
            )
        session_instructions = ensure_citation_system_policy(session_instructions)

        from app.serializers import agent_config_to_schema

        created = await kernel_client.create_session(
            user_id,
            CreateSessionRequest(
                id=session_id,
                agent_config=agent_config_to_schema(agent_config),
                cwd=session_cwd,
                runtime_provider=runtime_provider,
                model=resolution.model,
                model_provider=model_provider,
                model_settings=model_settings,
                instructions=session_instructions,
                skills=list(caps_skills),
                mcp_servers=list(caps_mcp),
                permission_mode=effective_permission_mode,
                metadata={"valuz": valuz_meta},
            ),
        )
        await project_index.record(
            project_id,
            session_id,
            kind="chat",
            origin=str(origin or "user"),
            user_id=user_id,
        )

        self._bus.publish(
            SESSION_CREATED,
            session_id=session_id,
            project_id=project_id,
        )

        return _session_to_detail(created)

    async def send_message(
        self,
        session_id: str,
        content: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        user_id: str | None = None,
    ) -> SessionDetail:
        """Kick off an async agent turn in the background.  Returns immediately."""
        # Capability convergence (citation policy / docs caps / always-on MCP
        # re-stamp) is NOT done here. It rides the turn as ``pre_turn`` — see
        # ``sessions/pre_turn`` and ``kernel_client.run_turn``. Refreshing at
        # this point would write to the durable of an at-rest session and the
        # turn's freshly-seeded kernel would never read it.
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        status = _map_kernel_status(session.status)
        if status == "running":
            raise SessionConflict("Session is already running")
        if status in ("cancelled", "archived"):
            raise SessionNotRunnable(f"Session is {status} and cannot accept messages")

        await _enforce_budget(session, user_id=user_id)

        # Worktree re-entry guard: a removed worktree is recreated at its
        # deterministic path before the turn starts (raises an actionable
        # 422/500 instead of a cryptic runtime cwd failure).
        healed_worktree = await self._heal_worktree_if_missing(session)

        old_status = status

        # Optimistically set status to "running" so the router sees it immediately.
        meta = dict(session.metadata)
        valuz = dict(meta.get("valuz") or {})
        if not valuz.get("name"):
            valuz["name"] = _derive_session_name(content)
        if healed_worktree is not None:
            valuz["worktree"] = healed_worktree
        meta["valuz"] = valuz

        updated = await kernel_client.finalize_session(
            user_id,
            session_id,
            FinalizeSessionRequest(status="running", metadata=meta),
        )

        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=old_status,
            new_status="running",
        )
        self._bus.publish(
            SESSION_MESSAGE_SENT,
            session_id=session_id,
        )

        # A fresh user-initiated turn supersedes any interrupt soft-pause, so the
        # post-turn drain continues the queue. (Explicit resume is for continuing
        # the queue *without* a new message.) See session-input-queue §9.
        try:
            await project_index.set_queue_paused(session_id, False)
        except Exception:  # noqa: BLE001 — never block a send on queue bookkeeping
            logger.debug("send_message: clearing queue pause failed for %s", session_id)

        asyncio.create_task(
            _run_agent_background(
                session_id=session_id,
                content=content,
                event_bus=self._bus,
                user_id=user_id,
            )
        )

        return _session_to_detail(updated)

    async def send_message_sync(
        self,
        session_id: str,
        content: str,
        user_id: str | None = None,
        *,
        citation_enabled_override: bool | None = None,
        citation_verification_enabled_override: bool | None = None,
    ) -> SessionRunResponse:
        """Block until the agent turn completes.  Used by the schedule runner."""
        # Mirror ``send_message``: convergence rides the turn, not this call —
        # see ``sessions/pre_turn``. The citation overrides are bound into the
        # hook below so an internal document-summary run keeps its policy.
        pre_turn = chat_capability_hook(
            session_id,
            user_id,
            citation_enabled_override=citation_enabled_override,
            verification_enabled_override=citation_verification_enabled_override,
        )

        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        status = _map_kernel_status(session.status)
        if status == "running":
            raise SessionConflict("Session is already running")
        if status in ("cancelled", "archived"):
            raise SessionNotRunnable(f"Session is {status} and cannot accept messages")

        await _enforce_budget(session, user_id=user_id)

        # Mirror ``send_message``: worktree re-entry guard for schedule-driven
        # sessions too — a removed worktree is recreated before the turn.
        healed_worktree = await self._heal_worktree_if_missing(session)

        # Mirror ``send_message``: flip the session to ``status="running"``
        # before driving the turn. The frontend's auto-resume effect on
        # the conversation page only subscribes to SSE when it reads
        # ``status === "running"``; without this, opening a schedule-
        # driven session mid-turn would never wire up to the live event
        # stream and the user would see a static, blank page until the
        # turn finished. Status flips back to a terminal value via the
        # post-run metadata save below.
        running_meta = dict(session.metadata)
        running_valuz = dict(running_meta.get("valuz") or {})
        if not running_valuz.get("name"):
            running_valuz["name"] = content[:40].replace("\n", " ").strip()
        if healed_worktree is not None:
            running_valuz["worktree"] = healed_worktree
        running_meta["valuz"] = running_valuz
        await kernel_client.finalize_session(
            user_id,
            session_id,
            FinalizeSessionRequest(status="running", metadata=running_meta),
        )
        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=status,
            new_status="running",
        )

        try:
            # Live token deltas reach SSE subscribers through the kernel's
            # bus taps — no per-run sink to attach/detach.
            # Per-turn attachments — capture the pending set once,
            # ship it, then stamp it consumed in the ``finally`` so a
            # scheduled run doesn't keep re-attaching the same files
            # on every cron tick (see ``_run_agent_background`` for
            # the full rationale).
            pending_attachments = await _load_pending_attachments(session_id, user_id)
            consumed_attachment_ids = [row.id for row in pending_attachments]
            attachment_specs = _attachment_specs(pending_attachments, user_id)
            project_id = str(
                ((session.metadata or {}).get("valuz", {}) or {}).get("project_id") or ""
            )
            additional_context = await _build_additional_context(
                session_id,
                project_id,
                pending_attachments,
                user_id=user_id,
                worktree=worktree_name_of(session),
            )

            try:
                message = await kernel_client.run_turn(
                    user_id,
                    session_id,
                    content,
                    attachments=[
                        {"source_path": source, "parsed_path": parsed}
                        for source, parsed in attachment_specs
                    ],
                    additional_context=additional_context,
                    pre_turn=pre_turn,
                )
            finally:
                try:
                    await _mark_attachments_consumed(consumed_attachment_ids)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to mark attachments consumed for session %s",
                        session_id,
                    )

            # Update valuz metadata.
            reloaded = await data_reader().get_session(user_id, session_id)
            if reloaded is not None:
                meta = dict(reloaded.metadata)
                valuz = dict(meta.get("valuz") or {})
                valuz["last_user_message_text"] = content
                if not valuz.get("name"):
                    valuz["name"] = content[:40].replace("\n", " ").strip()
                meta["valuz"] = valuz

                final_session = await kernel_client.update_session(
                    user_id,
                    session_id,
                    UpdateSessionRequest(metadata=meta),
                )

                if message.input_tokens is not None or message.output_tokens is not None:
                    from valuz_agent.ports.billing import MeterEvent
                    from valuz_agent.ports.extensions import ext

                    uid = meta.get("owner_user_id") or user_id
                    try:
                        if uid is None:
                            raise LookupError("no owner user_id for billing meter")
                        await ext.billing.meter(
                            MeterEvent(
                                user_id=uid,
                                event_type="llm_call",
                                cost_usd=0.0,
                                metadata={
                                    "message_id": message.id,
                                    "session_id": session_id,
                                    "input_tokens": message.input_tokens or 0,
                                    "output_tokens": message.output_tokens or 0,
                                    "cache_read_tokens": message.cache_read_tokens or 0,
                                    "cache_write_tokens": message.cache_write_tokens or 0,
                                    "model_usage": message.model_usage,
                                },
                            )
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("Billing meter failed for session %s", session_id)

                self._bus.publish(
                    SESSION_FINISHED,
                    session_id=session_id,
                    status=_map_kernel_status(reloaded.status),
                )

                events = await kernel_client.get_events(user_id, session_id, limit=500)
                envelopes = [
                    SessionEventEnvelope(
                        seq=i,
                        event={"event_type": ev.type, "payload": ev.data},
                    )
                    for i, ev in enumerate(events, start=1)
                ]
                return SessionRunResponse(
                    session=_session_to_detail(final_session),
                    events=envelopes,
                )
        except Exception:
            logger.exception("send_message_sync failed for %s", session_id)
            raise

        # Fallback (should not reach here).
        reloaded2 = await data_reader().get_session(user_id, session_id)
        detail = _session_to_detail(reloaded2) if reloaded2 else _session_to_detail(session)
        return SessionRunResponse(session=detail, events=[])

    async def interrupt(self, session_id: str, user_id: str | None = None) -> SessionDetail:
        """Stop the in-flight agent turn and flip the session to idle.

        Three-step approach so the user always gets a responsive UI even
        when the kernel-side interrupt can't be delivered (runtime
        already exited, orchestrator never registered the session, etc.):

        1. Best-effort ``kernel_client.interrupt(user_id, session_id)`` —
           the *clean* path that asks the runtime to stop emitting tokens.
        2. Whatever happens to step 1, flip the kernel session row to
           ``status=idle`` with ``stop_reason=UserInterrupt`` so future
           ``send_message`` calls don't 409 with "already running".
        3. Append a ``session_error`` event when the interrupt path
           failed, so SSE subscribers see an explanation rather than a
           silent end-of-stream.

        The status flip in step 2 is the load-bearing one — without it
        a stranded ``running`` row wedges the session forever (same
        failure mode ``recover_running_sessions`` cleans up at boot).
        """
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        # Step 1 — best-effort kernel interrupt.
        interrupt_failed = False
        try:
            await kernel_client.interrupt(user_id, session_id)
        except Exception:  # noqa: BLE001 — runtime gone / never registered
            logger.warning(
                "Could not reach kernel to interrupt session %s",
                session_id,
                exc_info=True,
            )
            interrupt_failed = True

        # Step 2 — flip status to idle (always runs).
        old_status = _map_kernel_status(session.status)

        updated = await kernel_client.finalize_session(
            user_id,
            session_id,
            FinalizeSessionRequest(status="idle", stop_reason_type="user_interrupt"),
        )

        # Step 3 — surface a kernel event when step 1 failed so the SSE
        # client doesn't see a silent stream cut. Try to anchor it onto
        # the session's latest message; if no message exists yet the
        # event can't be persisted (kernel V5+messages requires every
        # event row to carry a message_id), so we fall back to a
        # live-only emit that still reaches live SSE subscribers.
        if interrupt_failed:
            err_event = EventPayload(
                type="session_error",
                data={
                    "category": "InterruptDeliveryFailed",
                    "message": (
                        "Session was interrupted but the runtime "
                        "could not be reached; session marked idle."
                    ),
                },
            )
            try:
                persisted = await kernel_client.append_event(user_id, session_id, err_event)
            except Exception:  # noqa: BLE001
                persisted = False
                logger.exception(
                    "Failed to persist session_error after interrupt delivery failure for %s",
                    session_id,
                )
            if not persisted:
                try:
                    await kernel_client.emit_live_event(
                        user_id, session_id, err_event.type, err_event.data
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to broadcast session_error after interrupt for %s",
                        session_id,
                    )

        # Soft-pause auto-drain so queued follow-ups don't auto-run after a stop;
        # the user resumes explicitly (or a fresh send_message clears it). Only
        # meaningful when items are actually waiting. See session-input-queue §9.
        try:
            async with async_unit_of_work(commit=False) as db:
                has_queued = await SessionDatastore(db).count_queued(user_id, session_id) > 0
            if has_queued:
                await project_index.set_queue_paused(session_id, True)
        except Exception:  # noqa: BLE001 — never fail the interrupt on queue bookkeeping
            logger.debug("interrupt: queue pause bookkeeping failed for %s", session_id)

        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=old_status,
            new_status="idle",
        )
        return _session_to_detail(updated)

    # ---- Session input queue (docs/design/session-input-queue.md) ----

    async def list_queue(self, session_id: str, user_id: str | None = None) -> QueuedInputList:
        uid = user_id
        # Snapshot BEFORE the reads: if the head flips to ``dispatched`` while
        # we query, we'd rather show it in both ``items`` and ``dispatching``
        # for one response than in neither.
        dispatching_id = get_dispatching_queue_id(session_id)
        async with async_unit_of_work(commit=False) as db:
            ds = SessionDatastore(db)
            rows = await ds.list_queued(uid, session_id)
            dispatching = (
                await ds.get_queued(uid, session_id, dispatching_id)
                if uid is not None and dispatching_id
                else None
            )
        paused = await project_index.get_queue_paused_at(session_id) is not None
        return QueuedInputList(
            session_id=session_id,
            items=[_queued_input_to_dto(r) for r in rows],
            paused=paused,
            # A dispatched item is invisible in ``items`` — surface the in-flight
            # drain so per-turn re-subscribers keep following (§14.5).
            draining=is_draining_queue(session_id),
            # ...and surface the dispatched head itself so its bubble survives
            # the gap until the turn's user message lands in the transcript.
            dispatching=_queued_input_to_dto(dispatching) if dispatching else None,
        )

    async def enqueue(
        self,
        session_id: str,
        content: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        user_id: str | None = None,
    ) -> QueuedInputList:
        """Append a follow-up input to the session queue.

        Snapshots + consumes the pending attachment set so the files ride THIS
        item only (no carry-over, see §8.6). If the session is idle, kicks an
        immediate drain; otherwise the post-turn drain picks it up.
        """
        uid = user_id
        session = await data_reader().get_session(uid, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        status = _map_kernel_status(session.status)
        if status in ("cancelled", "archived"):
            raise SessionNotRunnable(f"Session is {status} and cannot accept messages")
        project_id = str(_valuz_meta(session).get("project_id") or "") or None
        pending = await _load_pending_attachments(session_id, user_id)
        attachments_json = [
            {"source_path": source, "parsed_path": parsed}
            for source, parsed in _attachment_specs(pending, user_id)
        ]
        consumed_ids = [row.id for row in pending]

        async with async_unit_of_work() as db:
            ds = SessionDatastore(db)
            if await ds.count_queued(uid, session_id) >= QUEUE_SOFT_CAP:
                raise QueueFull()
            await ds.create_queued(
                uid,
                QueuedInputRow(
                    session_id=session_id,
                    project_id=project_id,
                    input={"text": content, "attachments": attachments_json},
                    status="queued",
                    provider_id=provider_id,
                    model_id=model_id,
                ),
            )
        if consumed_ids:
            await _mark_attachments_consumed(consumed_ids)

        self._bus.publish(SESSION_MESSAGE_SENT, session_id=session_id)

        # Idle-kick: nothing in flight → drain now so the item doesn't wait for a
        # turn boundary that never comes. If running / already draining, the
        # in-flight drain picks it up on its next peek.
        if status != "running" and not is_draining_queue(session_id):
            schedule_drain(session_id, self._bus)

        return await self.list_queue(session_id, user_id=user_id)

    async def edit_queued(
        self, session_id: str, queue_id: str, content: str, user_id: str | None = None
    ) -> QueuedInputList:
        uid = user_id
        async with async_unit_of_work() as db:
            ds = SessionDatastore(db)
            existing = await ds.get_queued(uid, session_id, queue_id)
            if existing is None:
                raise QueuedInputNotFound()
            if existing.status != "queued":
                raise SessionConflict("Queued input is no longer editable")
            payload = dict(existing.input or {})
            payload["text"] = content
            await ds.update_queued_input(uid, session_id, queue_id, payload)
        return await self.list_queue(session_id, user_id=user_id)

    async def delete_queued(
        self, session_id: str, queue_id: str, user_id: str | None = None
    ) -> QueuedInputList:
        uid = user_id
        async with async_unit_of_work() as db:
            deleted = await SessionDatastore(db).delete_queued(uid, session_id, queue_id)
        if not deleted:
            raise QueuedInputNotFound()
        return await self.list_queue(session_id, user_id=user_id)

    async def resume_queue(self, session_id: str, user_id: str | None = None) -> QueuedInputList:
        uid = user_id
        session = await data_reader().get_session(uid, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        await project_index.set_queue_paused(session_id, False)
        status = _map_kernel_status(session.status)
        if status != "running" and not is_draining_queue(session_id):
            schedule_drain(session_id, self._bus)
        return await self.list_queue(session_id, user_id=user_id)

    async def steer_queued(
        self, session_id: str, queue_id: str, user_id: str | None = None
    ) -> QueuedInputList:
        """Send a queued item now, interrupting the active turn (steer / send-now).

        Promotes the item to the FIFO head and clears any soft-pause, then — if a
        turn is in flight — interrupts it *silently* via the low-level kernel
        interrupt (NOT ``self.interrupt``, which would stamp ``user_interrupt``
        and re-pause the queue). The in-flight chain finalizes the cut turn as a
        clean idle and its post-turn drain dispatches the promoted head; we
        therefore do NOT kick a drain here (that would race the in-flight chain).
        Only a genuinely idle session needs the explicit kick. Lossy: the running
        turn's partial progress is discarded. Runtime-agnostic stand-in for Codex
        ``turn/steer`` (see docs/design/session-input-queue.md §11).
        """
        uid = user_id
        session = await data_reader().get_session(uid, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        async with async_unit_of_work() as db:
            promoted = await SessionDatastore(db).promote_to_front(uid, session_id, queue_id)
        if promoted is None:
            # Race-tolerant: the item already left the queue — almost always the
            # post-turn drain dispatched it the instant the user hit "Send now".
            # Steer's goal (get this item out of the queue and running) is then
            # already satisfied, so return the current queue rather than a
            # confusing 404 for a message that actually went out.
            return await self.list_queue(session_id, user_id=user_id)

        # Steer overrides any interrupt soft-pause so the drain proceeds.
        await project_index.set_queue_paused(session_id, False)

        status = _map_kernel_status(session.status)
        if status == "running" or is_draining_queue(session_id):
            # Silent interrupt: cut the in-flight turn so the existing post-turn
            # drain picks up the promoted head. Low-level kernel interrupt only —
            # no user_interrupt stamp, no re-pause (that's what makes it "silent";
            # the cut turn finalizes as a clean idle). Best-effort: if the runtime
            # is already gone the in-flight chain still drains the promoted head.
            try:
                await kernel_client.interrupt(uid, session_id)
            except Exception:  # noqa: BLE001 — runtime gone / never registered
                logger.warning("steer: kernel interrupt failed for %s", session_id, exc_info=True)
        else:
            schedule_drain(session_id, self._bus)

        return await self.list_queue(session_id, user_id=user_id)

    async def cancel(self, session_id: str, user_id: str | None = None) -> SessionDetail:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        old_status = _map_kernel_status(session.status)

        updated = await kernel_client.finalize_session(
            user_id, session_id, FinalizeSessionRequest(status="terminated")
        )

        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=old_status,
            new_status="cancelled",
        )
        return _session_to_detail(updated)

    async def regenerate(self, session_id: str, user_id: str | None = None) -> SessionDetail:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        meta = _valuz_meta(session)
        last_msg = meta.get("last_user_message_text")
        if not last_msg:
            raise SessionNotRunnable("No user message to regenerate from")
        return await self.send_message(session_id, str(last_msg), user_id=user_id)

    async def rename_session(
        self, session_id: str, name: str, user_id: str | None = None
    ) -> SessionDetail:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        meta = dict(session.metadata)
        valuz = dict(meta.get("valuz") or {})
        valuz["name"] = name
        meta["valuz"] = valuz

        updated = await kernel_client.update_session(
            user_id, session_id, UpdateSessionRequest(metadata=meta)
        )
        return _session_to_detail(updated)

    async def delete_session(self, session_id: str, user_id: str | None = None) -> None:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        # Snapshot worktree attribution BEFORE the kernel row disappears —
        # the post-delete teardown below needs it.
        _pre_meta = _valuz_meta(session)
        _wt_snapshot = _pre_meta.get("worktree")
        _wt_project_id = str(_pre_meta.get("project_id") or "")
        await kernel_client.delete_session(user_id, session_id)
        # Scope teardown: under per-scope sandbox allocation the deleted
        # session's sandbox has nothing left to serve — release it now instead
        # of waiting for the idle reaper. Best-effort and idempotent; the OSS
        # BootSingletonAllocator no-ops, and a task session's release targets
        # its (already gone) per-session scope, never the shared task sandbox.
        if user_id:
            try:
                from valuz_agent.ports.extensions import ext
                from valuz_agent.ports.sandbox_allocator import SandboxScope

                await ext.sandbox_allocator.release(
                    owner_user_id=user_id,
                    scope=SandboxScope(kind="session", id=session_id),
                )
            except Exception:  # noqa: BLE001 — cleanup must not fail the delete
                logger.debug("delete_session: sandbox release failed for %s", session_id)
        # Drop the chat-index row too, or the session haunts the activity feed as
        # a ghost "New chat" the user can't clear (``project_index.remove`` keys
        # on the globally-unique session_id).
        await project_index.remove(session_id, user_id=user_id)
        # Drop any pending input-queue rows for the gone session.
        try:
            async with async_unit_of_work() as db:
                await SessionDatastore(db).delete_queue_for_session(user_id, session_id)
        except Exception:  # noqa: BLE001 — cleanup must not fail the delete
            logger.debug("delete_session: queue cleanup failed for %s", session_id)

        # Worktree teardown (design §3): when the deleted session ran in a
        # worktree that no other session references, remove it iff clean.
        # Fail-closed at every step — a dirty / unverifiable worktree stays
        # and surfaces in the project's worktrees panel instead.
        if isinstance(_wt_snapshot, dict):
            await self._teardown_worktree_if_unused(_wt_snapshot, _wt_project_id, user_id)

    async def _teardown_worktree_if_unused(
        self,
        snapshot: dict[str, object],
        project_id: str,
        user_id: str | None,
    ) -> None:
        """Best-effort clean-teardown; never raises out of a delete."""
        try:
            name = str(snapshot.get("name") or "")
            if not name:
                return
            if project_id:
                siblings = await self.list_sessions(project_id=project_id, user_id=user_id)
                if any(s.worktree and s.worktree.name == name for s in siblings):
                    return  # still in use by a live session
            from valuz_agent.modules.worktrees.service import worktree_service

            removed = await worktree_service.cleanup_if_clean(
                snapshot, user_id=user_id, project_id=project_id or ""
            )
            if removed:
                logger.info(
                    "delete_session: removed clean worktree '%s' (%s)",
                    name,
                    snapshot.get("path"),
                )
        except Exception:  # noqa: BLE001 — teardown must not fail the delete
            logger.warning(
                "delete_session: worktree teardown failed for %s",
                snapshot.get("path"),
                exc_info=True,
            )

    async def get_extra_skills(self, session_id: str, user_id: str | None = None) -> list[str]:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        meta = _valuz_meta(session)
        raw = meta.get("extra_skill_ids")
        if not isinstance(raw, list):
            return []
        return [str(s) for s in raw if isinstance(s, str)]

    async def set_extra_skills(
        self, session_id: str, skill_ids: list[str], user_id: str | None = None
    ) -> SessionDetail:
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        cleaned = sorted({str(s) for s in skill_ids if s and isinstance(s, str)})
        meta = dict(session.metadata)
        valuz = dict(meta.get("valuz") or {})
        valuz["extra_skill_ids"] = cleaned
        meta["valuz"] = valuz

        updated = await kernel_client.update_session(
            user_id, session_id, UpdateSessionRequest(metadata=meta)
        )
        return _session_to_detail(updated)

    async def set_permission_mode(
        self, session_id: str, permission_mode: str, user_id: str | None = None
    ) -> SessionDetail:
        """Update the session's approval mode in the DB.

        Live-reconcile (kernel V5+bba3014): the new mode applies on the
        next Send. Each runtime picks it up its own way:
          * Claude: ``_reconcile_session_levers`` calls
            ``client.set_permission_mode`` for safe transitions, or
            forks the SDK session for ``bypassPermissions`` upgrades
            (G1/G2 CLI gotchas).
          * Codex: ``_build_turn_kwargs(session)`` reads the session
            live and emits per-turn ``approval_policy`` /
            ``sandbox_policy``.
          * DeepAgents: detects drift from ``_applied_permission_mode``
            and drops ``self._graph`` for cold rebuild.

        A turn already in flight keeps the mode it started with.
        """
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        target = _coerce_session_permission_mode(permission_mode)
        runtime_provider = getattr(session, "runtime_provider", "claude_agent")
        if runtime_provider == "deepagents" and target == "auto_review":
            raise SessionNotRunnable(
                "auto_review is not supported for deepagents runtimes; pick default or full_access"
            )

        updated = await kernel_client.update_session(
            user_id, session_id, UpdateSessionRequest(permission_mode=target)
        )
        return _session_to_detail(updated)

    async def set_session_effort(
        self, session_id: str, effort: str | None, user_id: str | None = None
    ) -> SessionDetail:
        """Update the session's reasoning-effort budget in the DB.

        Live-reconcile (kernel V5+bba3014): the new effort applies on the
        next Send. Each runtime picks it up its own way:
          * Claude: ``_reconcile_session_levers`` destroys the cached
            ``ClaudeSDKClient`` so the next ``_build_options`` reads the
            fresh value (effort is a build-time SDK option).
          * Codex: ``_build_turn_kwargs(session)`` drops it into
            ``turn_kwargs.reasoning_effort`` — survives ``--resume``.
          * DeepAgents: detects drift from ``_applied_effort`` and drops
            ``self._graph`` for cold rebuild with the new langchain
            ``reasoning_effort`` / ``thinking_level``.

        ``effort=None`` resets to the SDK default. Raises
        ``ValueError`` on an unknown effort value so the route layer
        can 400.
        """
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        target_effort = _coerce_session_effort(effort)
        previous = session.model_settings or ModelSettingsSchema()
        updated = await kernel_client.update_session(
            user_id,
            session_id,
            UpdateSessionRequest(
                model_settings=ModelSettingsSchema(
                    temperature=previous.temperature,
                    max_tokens=previous.max_tokens,
                    effort=target_effort,
                )
            ),
        )
        return _session_to_detail(updated)

    async def submit_action(
        self,
        session_id: str,
        *,
        pending_id: str,
        decision: str,
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, object] | None = None,
        user_id: str | None = None,
    ) -> dict[str, object]:
        """Resolve a pending ``requires_action`` event.

        Thin façade over ``orchestrator.submit_action``. The orchestrator
        owns validation, idempotency, and the kernel ``action_resolved``
        emit; the host route is only responsible for translating its
        typed exceptions into HTTP shapes (see ``api/routes/sessions``).

        The router that calls this is async, so we ``await`` the kernel
        coroutine directly rather than going through the sync facade —
        the running event-loop check would otherwise reject ``asyncio.run``
        from inside FastAPI's loop.

        V5+d008b53 (approval contract v2) added two payload-carrying
        verbs and two non-payload verbs:
          - ``approve_with_changes`` ↔ ``modified_input`` (replacement
            args; Pydantic invariant lives on the route's request body).
          - ``approve_for_session`` — kernel attaches a session-scoped
            rule from the staged pending's ``session_rule_preview`` and
            returns the new ``rule_id`` on the result.
          - ``auto_approved`` — kernel-only; never sent here.
        """
        # Verify session exists so we raise our own 404 before reaching
        # the orchestrator (which would also 404 but with a kernel-shaped
        # error message). Keeping host errors host-flavoured.
        session = await data_reader().get_session(user_id, session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        result = await kernel_client.submit_action(
            user_id,
            session_id,
            SubmitActionRequest(
                pending_id=pending_id,
                decision=decision,  # type: ignore[arg-type]
                message=message,
                answers=answers,
                modified_input=modified_input,
            ),
        )
        return dict(result)

    async def count_sessions_for_project(self, project_id: str, user_id: str | None = None) -> int:
        """Return the number of kernel sessions recorded for this project."""
        if user_id is None:
            raise ValueError("user_id is required")
        return await project_index.count_for_project(project_id, user_id=user_id)

    async def delete_sessions_for_project(self, project_id: str, user_id: str | None = None) -> int:
        """Delete all kernel sessions (and their events) for this project."""
        if user_id is None:
            raise ValueError("user_id is required")
        ids = await project_index.remove_for_project(project_id, user_id=user_id)
        for sid in ids:
            await kernel_client.delete_session(user_id, sid)
        return len(ids)
