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
from uuid import uuid4

from src.core.types import (  # type: ignore[import-not-found]
    Attachment,
    ModelSettings,
    UserMessage,
)
from src.core.types import (
    Session as KernelSession,
)

# Kernel types (resolved via sys.path injection from kernel_bootstrap).
import valuz_agent.boot.kernel  # noqa: F401 — side-effect: puts kernel on sys.path
from valuz_agent.adapters import kernel_store, kernel_sync
from valuz_agent.adapters.capability_resolver import resolve_session_capabilities
from valuz_agent.adapters.model_resolver import resolve_model
from valuz_agent.adapters.system_prompt_builder import build_workspace_system_prompt
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import WorkspaceDatastore
from valuz_agent.modules.projects.service import WorkspaceService
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.sessions.attachments import (
    _attachment_specs,
    _load_pending_attachments,
    _mark_attachments_consumed,
)
from valuz_agent.modules.sessions.capabilities import (
    refresh_always_on_mcp_for_session,
    refresh_docs_capabilities_for_session,
)
from valuz_agent.modules.sessions.context_builder import _build_additional_context
from valuz_agent.modules.sessions.dto import (
    SessionDetail,
    SessionEventEnvelope,
    SessionListItem,
    SessionRunResponse,
)
from valuz_agent.modules.sessions.errors import (
    BudgetExceeded,
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
    _copy_session,
    _kernel_session_not_found,
    _map_kernel_status,
    _session_to_detail,
    _session_to_list_item,
    _valuz_meta,
)
from valuz_agent.modules.sessions.run_orchestrator import (
    _derive_session_name,
    _run_agent_background,
)
from valuz_agent.modules.skills.datastore import SkillDatastore

logger = logging.getLogger(__name__)


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
        workspace_svc: WorkspaceService,
        providers: ProviderDatastore,
        skills: SkillDatastore,
        workspaces: WorkspaceDatastore,
        # KB integration — optional. When supplied, session creation
        # auto-injects the ``valuz-project-docs`` builtin skill into
        # ``session.skills`` if the workspace has any KB binding. Tests that
        # don't care about KB can omit it.
        docs: DocumentDatastore | None = None,
        # MCP integration — optional so legacy callers (and tests that don't
        # need data sources) can omit them. When provided the capability
        # resolver injects ``McpServerConfig`` rows into the kernel session
        # at creation time.
        secrets: FileSecretStore | None = None,
        connectors: ConnectorDatastore | None = None,
        # User-library skill source — when supplied, chat (non-project)
        # workspaces auto-include every discovered user-scoped skill in
        # ``Session.skills``. Tests that don't care about skill discovery
        # can omit it.
        skill_source: FilesystemSkillSource | None = None,
        # Additional skill sources (e.g. ``OfficialSkillSource``) walked
        # alongside ``skill_source`` for chat workspaces. Each source's
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
        self._workspace_svc = workspace_svc
        self._providers = providers
        self._skills = skills
        self._workspaces = workspaces
        self._secrets = secrets
        self._connectors = connectors
        self._docs = docs
        self._skill_source = skill_source
        self._extra_skill_sources = extra_skill_sources or []
        self._auth = auth_facade

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

    async def _auto_default_mcp_slugs(self, workspace_id: str) -> list[str]:
        if self._connectors is None:
            return []

        workspace_row = await self._workspaces.get_by_id(workspace_id)
        is_project = workspace_row is not None and workspace_row.kind == "project"

        try:
            if is_project:
                return await self._connectors.get_workspace_connectors(workspace_row)  # type: ignore[arg-type]
            # Chat workspace: all enabled connectors that are connected or unknown
            return [
                conn.slug
                for conn in await self._connectors.list_enabled()
                if conn.status in ("connected", "unknown")
            ]
        except Exception:  # noqa: BLE001
            logger.warning("auto-default connector discovery failed", exc_info=True)
            return []

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def get_workspace_last_pick(self, workspace_id: str) -> dict[str, str | None] | None:
        """Return the most recent (runtime, provider, model) the user picked
        in this workspace.

        Reads the latest kernel session of the workspace and surfaces the
        three composer-relevant fields. The frontend uses this to seed
        the picker on new-session entry so users don't have to re-pick
        the same runtime/model every time they re-open the same project.

        Returns ``None`` if the workspace has no sessions yet (caller
        falls back to the global Settings → Default tuple).

        Skips sessions whose ``locked_provider_id`` is missing — OAuth
        subscription rows and partially-created sessions have an empty
        slot and would yield a useless ``(runtime, None, None)`` triple.
        Scans a small recent window in case the very latest is one of
        those incomplete rows.
        """
        sessions = kernel_sync.list_sessions_sync(
            project_id=workspace_id,
            limit=10,
        )
        for s in sessions:
            meta = _valuz_meta(s)
            provider_id = meta.get("locked_provider_id") or None
            if not provider_id:
                continue
            return {
                "runtime_provider": getattr(s, "runtime_provider", None) or None,
                "provider_id": str(provider_id),
                "model_id": s.model or None,
            }
        return None

    def list_sessions(
        self,
        workspace_id: str | None = None,
        query: str | None = None,
    ) -> list[SessionListItem]:
        # Task-internal sessions (lead / dispatched sub-runs) belong to
        # tasks and are reachable from the task detail page; the sidebar
        # 对话 rail only wants user-initiated chats. The SQL-layer filter
        # (json_extract on metadata.valuz.task_id) applies LIMIT *after*
        # excluding task sessions, so we get exactly N chats — no over-
        # fetching, no chat/task ratio assumptions.
        sessions = kernel_sync.list_user_sessions_sync(
            project_id=workspace_id,
            limit=200,
        )
        items = [_session_to_list_item(s) for s in sessions]
        if query:
            q = query.lower()
            items = [i for i in items if i.name and q in i.name.lower()]
        return items

    def get_session(self, session_id: str) -> SessionDetail:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        return _session_to_detail(session)

    def list_events(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> list[SessionEventEnvelope]:
        """Fetch kernel events for *session_id* with id > *after_seq*.

        Wire shape matches the legacy pre-V5 contract the desktop renderer
        was authored against (``message.user``, ``message.assistant.delta``,
        ``tool.call.*``, ``run.failed``, ``runtime.engine.cost``). Kernel
        events that have no legacy counterpart are filtered.
        """
        # Verify session exists.
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        from valuz_agent.adapters.event_sse_adapter import list_events_after

        frames = kernel_sync._run_in_thread(  # noqa: SLF001
            lambda: list_events_after(session_id, after_seq=after_seq, limit=2000)
        )
        return [
            SessionEventEnvelope(
                seq=frame.seq,
                event={"event_type": frame.event_type, "payload": frame.payload},
                timestamp=frame.timestamp,
            )
            for frame in frames
        ]

    def list_events_window(
        self,
        session_id: str,
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
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        from valuz_agent.adapters.event_sse_adapter import list_events_window

        window = kernel_sync._run_in_thread(  # noqa: SLF001
            lambda: list_events_window(session_id, before_seq=before_seq, turn_limit=turn_limit)
        )
        items = [
            SessionEventEnvelope(
                seq=frame.seq,
                event={"event_type": frame.event_type, "payload": frame.payload},
                timestamp=frame.timestamp,
            )
            for frame in window.items
        ]
        return items, window.has_more

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    async def _resolve_bound_kernel_agent_id(self, workspace_id: str, agent_slug: str) -> str:
        """Resolve a session's bound agent to a kernel ``AgentConfig`` id.

        Two binding sources, tried in order:

        1. **Project member** — project conversations bind ``agent_slug`` to a
           per-workspace ``ProjectMemberRow`` (the 派驻 agent).
        2. **Global library agent** — temp / quick-chat conversations bind to a
           global library agent (e.g. the seeded ``default-assistant``), which is
           NOT a member of any project. The 09-assistant design has no agentless
           path, so every chat-default session carries such a slug.

        For a library agent the kernel ``AgentConfig`` is built lazily; we
        materialize it on first use (committing the ``kernel_agent_id`` backfill)
        and reuse it thereafter — no per-send re-save (Settings changes keep the
        kernel config in sync via ``AgentService.update_agent``).
        """
        from valuz_agent.modules.agents.datastore import (
            AgentDatastore,
            ProjectMemberDatastore,
        )
        from valuz_agent.modules.agents.service import AgentService

        async with async_unit_of_work(commit=False) as _db:
            member = await ProjectMemberDatastore(_db).get(workspace_id, agent_slug)
            if member is not None:
                return member.kernel_agent_id

        # Not a project member → resolve as a global library agent. Use a
        # committed unit of work so a lazy kernel_agent_id backfill persists.
        async with async_unit_of_work() as _db:
            row = await AgentDatastore(_db).get_agent(agent_slug)
            if row is None:
                raise SessionNotRunnable(
                    f"agent '{agent_slug}' not found — pick a configured agent or add one first"
                )
            if row.kernel_agent_id:
                return row.kernel_agent_id
            return await AgentService(_db).ensure_kernel_agent(row)

    async def _create_agent_bound_session(
        self,
        *,
        workspace_id: str,
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
        quick-chat conversations — see ``_resolve_bound_kernel_agent_id``.
        """
        from valuz_agent.adapters.provider_resolver import (
            ProviderNotResolvable,
            resolve_model_provider,
            resolve_runtime_provider,
        )

        if self._secrets is None:
            raise RuntimeError(
                "SessionService is missing secrets wiring — required "
                "for provider resolution since kernel V5"
            )

        # Temp / quick-chat sessions bind a global library agent to a fresh,
        # isolated chat workspace — materialize it first (same as the raw-model
        # path) so the runtime is isolated from sibling chats and the library
        # agent isn't (wrongly) looked up as a member of "chat-default".
        if workspace_id == "chat-default" and self._workspace_svc:
            fresh_ws = await self._workspace_svc.create_chat_workspace_for_session()
            workspace_id = fresh_ws.id

        kernel_agent_id = await self._resolve_bound_kernel_agent_id(workspace_id, agent_slug)

        agent = await kernel_store.load_agent(kernel_agent_id)
        if agent is None:
            raise SessionNotRunnable(
                f"agent '{agent_slug}' has no kernel config (id "
                f"{kernel_agent_id}) — re-create the agent"
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
            from valuz_agent.infra.config import settings
            from valuz_agent.infra.eventbus import event_bus
            from valuz_agent.infra.secret_store import FileSecretStore
            from valuz_agent.modules.providers.service import ProviderService

            prov_svc = ProviderService(
                datastore=self._providers,
                secret_store=FileSecretStore(settings.secrets_dir),
                event_bus=event_bus,
            )
            match = await prov_svc.resolve_provider_for_model(effective_model)
            if match is not None:
                provider_id = match.id
        if not provider_id:
            raise SessionNotRunnable(
                f"agent '{agent_slug}' has no model provider configured and no "
                f"enabled provider hosts model '{effective_model}' — add a provider "
                "for that model or pin one on the agent"
            )

        try:
            runtime_provider = await resolve_runtime_provider(
                provider_id=provider_id,
                model_id=effective_model,
                providers=self._providers,
                request_runtime_id=effective_runtime_request,
            )
            model_provider = await resolve_model_provider(
                provider_id=provider_id,
                model_id=effective_model,
                providers=self._providers,
                secrets=self._secrets,
                runtime_provider=runtime_provider,
            )
        except ProviderNotResolvable as exc:
            raise SessionNotRunnable(str(exc)) from exc

        # Snapshot the workspace prompt + the agent's persona instructions.
        workspace_row = await self._workspaces.get_by_id(workspace_id)
        workspace_ctx = await self._workspaces.get_context(workspace_id)
        workspace_prompt = build_workspace_system_prompt(
            workspace_name=workspace_row.name if workspace_row else "",
            instructions_md=workspace_ctx.instructions_md if workspace_ctx else None,
        )
        # VALUZ-CHATPLAN S3: project-conversation agents (i.e. chat sessions
        # bound to a workspace agent) carry the chat task playbook so the
        # model knows to draft → plan → commit (with user "go") instead of
        # creating tasks straight away, and to inject mid-flight rather
        # than starting new tasks. Lead/member agents have their own
        # playbooks (DISPATCH_PLAYBOOK / COMMITTED_LEAD_PLAYBOOK) and never
        # flow through this code path.
        from valuz_agent.adapters.agent_resolver import CHAT_TASK_PLAYBOOK

        instructions = "\n\n".join(
            p for p in (agent.instructions, workspace_prompt, CHAT_TASK_PLAYBOOK) if p and p.strip()
        )

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
            ModelSettings(effort=_coerce_session_effort(effective_effort))
            if effective_effort
            else ModelSettings()
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
        from valuz_agent.adapters.capability_resolver import (
            always_on_http_mcp_servers,
            always_on_skill_paths,
            resolve_skill_slugs_to_paths,
        )
        from valuz_agent.modules.agents.service import _prepare_conversation_tools

        prepared = _prepare_conversation_tools(agent)
        if tuple(getattr(prepared, "tools", ()) or ()) != tuple(agent.tools or ()):
            await kernel_store.save_agent(prepared)
            agent = prepared

        existing_mcp_names = {getattr(m, "name", None) for m in (agent.mcp_servers or ())}
        session_mcp = tuple(agent.mcp_servers or ()) + tuple(
            m for m in always_on_http_mcp_servers(session_id) if m.name not in existing_mcp_names
        )
        import os as _os

        own_skill_keys = {(s.name if hasattr(s, "name") else str(s)) for s in (agent.skills or ())}
        # Resolve the agent's skill SLUGS → absolute source dirs (same shared
        # chokepoint the task path uses). Passing raw slugs crashed the kernel
        # materializer with "Skill source path not found ...: <slug>" the moment
        # an agent carried any skill.
        own_skill_paths = await resolve_skill_slugs_to_paths(agent.skills, None)
        session_skills = tuple(own_skill_paths) + tuple(
            p for p in always_on_skill_paths() if _os.path.basename(p) not in own_skill_keys
        )

        valuz_meta: dict[str, object] = {
            "name": title,
            "origin": origin,
            "trigger_meta": trigger_meta,
            "last_user_message_text": None,
            "locked_provider_id": provider_id,
            "extra_skill_ids": [],
            "agent_slug": agent_slug,
        }
        if creation_context:
            valuz_meta["creation_context"] = {str(k): str(v) for k, v in creation_context.items()}

        kernel_session = KernelSession(
            id=session_id,
            project_id=workspace_id,
            agent_id=kernel_agent_id,
            runtime_provider=runtime_provider,
            model=effective_model,
            model_provider=model_provider,
            model_settings=model_settings,
            instructions=instructions,
            skills=session_skills,
            mcp_servers=session_mcp,
            permission_mode=effective_permission_mode,
            status="created",
            metadata={"valuz": valuz_meta},
        )
        await kernel_store.save_session(kernel_session)

        self._bus.publish(
            SESSION_CREATED,
            session_id=session_id,
            workspace_id=workspace_id,
        )
        return _session_to_detail(kernel_session)

    async def create_session(
        self,
        workspace_id: str,
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
    ) -> SessionDetail:
        """Create a new kernel session for *workspace_id*.

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
                workspace_id=workspace_id,
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
            )
        # Quick-chat sessions get an ephemeral, single-use workspace each
        # time. ``"chat-default"`` is the sentinel the chat launchers send
        # — we materialize a fresh ``kind="chat"`` workspace + kernel
        # project (with its own cwd under ``data_dir/workspaces/{id}/``)
        # so the runtime is isolated from sibling chats. Skill scoping
        # still uses the literal ``"chat-default"`` string as the scope
        # key, independent of any specific workspace id.
        if workspace_id == "chat-default" and self._workspace_svc:
            fresh_ws = await self._workspace_svc.create_chat_workspace_for_session()
            workspace_id = fresh_ws.id

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
                    runtime_id = await _prefs.get_default_runtime(_pref_db)
                if provider_id is None and not caller_supplied_model:
                    provider_id = await _prefs.get_default_provider_id(_pref_db)
                if model_id is None:
                    model_id = await _prefs.get_default_model(_pref_db)
                if effort is None:
                    # ``None`` from settings means "no override" — the runtime SDK
                    # picks its own default. The kernel ``ModelSettings.effort``
                    # Optional union expects exactly the EFFORT_VALUES set or
                    # ``None``; the settings helper guarantees that contract.
                    effort = await _prefs.get_default_effort(_pref_db)

        # Resolve model.
        workspace_row = await self._workspaces.get_by_id(workspace_id)
        resolution = await resolve_model(
            providers=self._providers,
            request_model_id=model_id,
            request_provider_id=provider_id,
            request_runtime_id=runtime_id,
        )

        # Bind a provider to the session at creation time so the runtime layer
        # has a single source of truth. If the caller passed an explicit
        # ``provider_id`` we trust it; otherwise we ask the provider service
        # which configured provider hosts the resolved model.
        resolved_provider_id: str | None = provider_id
        if not resolved_provider_id and resolution.model:
            from valuz_agent.infra.config import settings
            from valuz_agent.infra.eventbus import event_bus
            from valuz_agent.infra.secret_store import FileSecretStore
            from valuz_agent.modules.providers.service import ProviderService

            prov_svc = ProviderService(
                datastore=self._providers,
                secret_store=FileSecretStore(settings.secrets_dir),
                event_bus=event_bus,
            )
            match = await prov_svc.resolve_provider_for_model(resolution.model)
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

        if resolved_provider_id is None:
            raise SessionNotRunnable(
                "no provider selected — pick a model provider before creating "
                "a session, or configure a workspace default"
            )
        if self._secrets is None:
            raise RuntimeError(
                "SessionService is missing secrets wiring — required "
                "for provider resolution since kernel V5"
            )

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
            )
        except ProviderNotResolvable as exc:
            raise SessionNotRunnable(str(exc)) from exc
        try:
            model_provider = await resolve_model_provider(
                provider_id=resolved_provider_id,
                model_id=resolution.model,
                providers=self._providers,
                secrets=self._secrets,
                runtime_provider=runtime_provider,
            )
        except ProviderNotResolvable as exc:
            # Surface the underlying reason so the API layer can render a
            # clean error to the user.
            raise SessionNotRunnable(str(exc)) from exc

        # MCP defaulting: when the caller passes ``None`` (front-end omits
        # the field) auto-include every connected provider so users don't
        # have to repick their connections per session. ``[]`` is honoured
        # as "explicitly none". An explicit non-empty list is also honoured.
        effective_mcp_slugs = mcp_provider_slugs
        if effective_mcp_slugs is None and self._connectors is not None:
            effective_mcp_slugs = await self._auto_default_mcp_slugs(workspace_id)

        # Allocate session id up-front so capability resolution can stamp
        # it into the in-process docs MCP URL (the URL embeds the session
        # id so the host can scope each request to a project). The id
        # then flows into the kernel session row unchanged below.
        session_id = uuid4().hex

        # Resolve skills / mcp_servers.
        try:
            caps = await resolve_session_capabilities(
                workspaces=self._workspaces,
                skills=self._skills,
                workspace_id=workspace_id,
                skill_source=self._skill_source,
                extra_skill_sources=self._extra_skill_sources,
                official_entitled=await self._has_official_entitlement(),
                secrets=self._secrets,
                enabled_mcp_provider_slugs=effective_mcp_slugs,
                connectors=self._connectors,
                docs=self._docs,
                session_id=session_id,
            )
        except KeyError:
            caps_skills: tuple[str, ...] = ()
            caps_mcp: tuple = ()
        else:
            caps_skills = caps.skills
            caps_mcp = caps.mcp_servers

        agent_id = WorkspaceService._kernel_agent_id(workspace_id)

        # Per ADR-008: snapshot the workspace's current ``instructions_md``
        # into ``Session.instructions`` at create time. The runtime reads
        # the session field, not the agent — so this is the moment that
        # locks the system prompt for the session's lifetime. Workspace
        # edits after this point apply only to *future* sessions.
        workspace_row = await self._workspaces.get_by_id(workspace_id)
        workspace_ctx = await self._workspaces.get_context(workspace_id)
        session_instructions = build_workspace_system_prompt(
            workspace_name=workspace_row.name if workspace_row else "",
            instructions_md=workspace_ctx.instructions_md if workspace_ctx else None,
        )

        # Build the valuz metadata blob.
        valuz_meta: dict[str, object] = {
            "name": title,
            "origin": origin,
            "trigger_meta": trigger_meta,
            "last_user_message_text": None,
            "locked_provider_id": resolved_provider_id,
            "extra_skill_ids": [],
        }
        # Optional ``creation_context`` records *why* the session was
        # opened (chat / project / skills_library) so the
        # ``submit_skill`` confirm flow can apply per-entry side-effects
        # on user confirmation. Stored only when the caller passes it;
        # for organic sessions (no launcher), the confirm endpoint
        # infers the kind from the session's workspace at confirm time.
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
        model_settings = ModelSettings(effort=effective_effort)

        kernel_session = KernelSession(
            id=session_id,
            project_id=workspace_id,
            agent_id=agent_id,
            runtime_provider=runtime_provider,
            model=resolution.model,
            model_provider=model_provider,
            model_settings=model_settings,
            instructions=session_instructions,
            skills=caps_skills,
            mcp_servers=caps_mcp,
            permission_mode=effective_permission_mode,
            status="created",
            metadata={"valuz": valuz_meta},
        )
        await kernel_store.save_session(kernel_session)

        self._bus.publish(
            SESSION_CREATED,
            session_id=session_id,
            workspace_id=workspace_id,
        )

        detail = _session_to_detail(kernel_session)
        return detail

    async def send_message(
        self,
        session_id: str,
        content: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> SessionDetail:
        """Kick off an async agent turn in the background.  Returns immediately."""
        # Lazy refresh — if the user bound docs to this workspace AFTER
        # the session was created, the docs skill+MCP would be missing
        # from session.{skills,mcp_servers} (capability_resolver only
        # fires at create-time). The proactive eventbus subscriber
        # already handles the binding-change moment, but a lazy refresh
        # here is a belt-and-braces guarantee — by the time the user
        # actually types a message, the docs caps are present.
        try:
            refresh_docs_capabilities_for_session(session_id)
        except Exception:  # noqa: BLE001 — never block send on refresh
            logger.exception(
                "send_message: docs capability refresh failed for %s",
                session_id,
            )

        # Re-stamp the always-on in-process MCP token: it rotates per process,
        # so a session resumed after a backend restart would otherwise carry a
        # stale X-Valuz-Internal → gate 403 → Claude Code parks the server in
        # needsAuth (only OAuth stubs, real tools hidden). Self-heals here.
        try:
            refresh_always_on_mcp_for_session(session_id)
        except Exception:  # noqa: BLE001 — never block send on refresh
            logger.exception(
                "send_message: always-on MCP re-stamp failed for %s",
                session_id,
            )

        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        status = _map_kernel_status(session.status)
        if status == "running":
            raise SessionConflict("Session is already running")
        if status in ("cancelled", "archived"):
            raise SessionNotRunnable(f"Session is {status} and cannot accept messages")

        from valuz_agent.ports.billing import get_billing_port

        billing = get_billing_port()
        uid = session.metadata.get("owner_user_id", "local-user")
        budget = await billing.check_budget(uid)
        if not budget.allowed:
            raise BudgetExceeded(budget.reason or "insufficient credits")

        old_status = status

        # Optimistically set status to "running" so the router sees it immediately.
        meta = dict(session.metadata)
        valuz = dict(meta.get("valuz") or {})
        if not valuz.get("name"):
            valuz["name"] = _derive_session_name(content)
        meta["valuz"] = valuz

        updated = _copy_session(
            session,
            status="running",
            metadata=meta,
        )
        kernel_sync.save_session_sync(updated)

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

        asyncio.create_task(
            _run_agent_background(
                session_id=session_id,
                content=content,
                event_bus=self._bus,
            )
        )

        return _session_to_detail(updated)

    async def send_message_sync(
        self,
        session_id: str,
        content: str,
    ) -> SessionRunResponse:
        """Block until the agent turn completes.  Used by the schedule runner."""
        # Mirror send_message: lazy refresh of docs caps before the turn
        # so scheduled runs (which never go through the eventbus
        # subscriber on bind-time) also pick up KB bindings added since
        # the session was created.
        try:
            refresh_docs_capabilities_for_session(session_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "send_message_sync: docs capability refresh failed for %s",
                session_id,
            )

        # See send_message: re-stamp always-on MCP token so scheduled/automation
        # runs resuming across a backend restart don't hit the stale-token 403.
        try:
            refresh_always_on_mcp_for_session(session_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "send_message_sync: always-on MCP re-stamp failed for %s",
                session_id,
            )

        session = await kernel_store.load_session(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        status = _map_kernel_status(session.status)
        if status == "running":
            raise SessionConflict("Session is already running")
        if status in ("cancelled", "archived"):
            raise SessionNotRunnable(f"Session is {status} and cannot accept messages")

        from valuz_agent.ports.billing import get_billing_port

        billing = get_billing_port()
        uid = session.metadata.get("owner_user_id", "local-user")
        budget = await billing.check_budget(uid)
        if not budget.allowed:
            raise BudgetExceeded(budget.reason or "insufficient credits")

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
        running_meta["valuz"] = running_valuz
        running_session = _copy_session(session, status="running", metadata=running_meta)
        await kernel_store.save_session(running_session)
        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=status,
            new_status="running",
        )

        try:
            from app.dependencies import (  # type: ignore[import-not-found]
                get_orchestrator,
                get_store,
            )

            from valuz_agent.adapters.broadcast_sink import BroadcastEventSink

            store = get_store()
            orchestrator = get_orchestrator()
            # V5+SessionEventBus: attach our broadcast sink to the
            # session's bus so live token deltas reach SSE subscribers,
            # then run the turn (no sink arg). Detach in a finally below
            # so the bus doesn't hold a reference across runs.
            sink = BroadcastEventSink(session_id)
            await orchestrator.attach_session_sink(session_id, sink)
            # Per-turn attachments — capture the pending set once,
            # ship it, then stamp it consumed in the ``finally`` so a
            # scheduled run doesn't keep re-attaching the same files
            # on every cron tick (see ``_run_agent_background`` for
            # the full rationale).
            pending_attachments = await _load_pending_attachments(session_id)
            consumed_attachment_ids = [row.id for row in pending_attachments]
            attachment_specs = _attachment_specs(pending_attachments)
            workspace_id = str(session.project_id)
            additional_context = await _build_additional_context(
                session_id,
                workspace_id,
                pending_attachments,
            )
            user_msg = UserMessage(
                text=content,
                attachments=tuple(
                    Attachment(source_path=source, parsed_path=parsed)
                    for source, parsed in attachment_specs
                ),
                additional_context=additional_context,
            )

            try:
                message = await orchestrator.run_turn(session_id, user_msg)
            finally:
                try:
                    await orchestrator.detach_session_sink(session_id, sink)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "detach_session_sink failed for %s",
                        session_id,
                        exc_info=True,
                    )
                try:
                    await _mark_attachments_consumed(consumed_attachment_ids)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to mark attachments consumed for session %s",
                        session_id,
                    )

            # Update valuz metadata.
            reloaded = await store.load_session(session_id)
            if reloaded is not None:
                meta = dict(reloaded.metadata)
                valuz = dict(meta.get("valuz") or {})
                valuz["last_user_message_text"] = content
                if not valuz.get("name"):
                    valuz["name"] = content[:40].replace("\n", " ").strip()
                meta["valuz"] = valuz

                final_session = _copy_session(
                    reloaded,
                    metadata=meta,
                )
                await store.save_session(final_session)

                if message.input_tokens is not None or message.output_tokens is not None:
                    from valuz_agent.ports.billing import MeterEvent, get_billing_port

                    uid = meta.get("owner_user_id", "local-user")
                    try:
                        await get_billing_port().meter(
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

                events = await kernel_store.get_events(session_id, limit=500)
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
        reloaded2 = await kernel_store.load_session(session_id)
        detail = _session_to_detail(reloaded2) if reloaded2 else _session_to_detail(session)
        return SessionRunResponse(session=detail, events=[])

    def interrupt(self, session_id: str) -> SessionDetail:
        """Stop the in-flight agent turn and flip the session to idle.

        Three-step approach so the user always gets a responsive UI even
        when the kernel-side interrupt can't be delivered (runtime
        already exited, orchestrator never registered the session, etc.):

        1. Best-effort ``orchestrator.interrupt(session_id)`` in a
           background thread — this is the *clean* path that asks the
           runtime to stop emitting tokens.
        2. Whatever happens to step 1, flip the kernel session row to
           ``status=idle`` with ``stop_reason=UserInterrupt`` so future
           ``send_message`` calls don't 409 with "already running".
        3. Append a ``session_error`` event when the orchestrator path
           failed, so SSE subscribers see an explanation rather than a
           silent end-of-stream.

        The status flip in step 2 is the load-bearing one — without it
        a stranded ``running`` row wedges the session forever (same
        failure mode ``recover_running_sessions`` cleans up at boot).
        """
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        # Step 1 — best-effort kernel interrupt.
        orchestrator_failed = False
        try:
            from app.dependencies import get_orchestrator  # type: ignore[import-not-found]

            orchestrator = get_orchestrator()
            # Fire-and-forget in a thread since we're in a sync context.
            # ``daemon=True`` so a stuck interrupt doesn't block process
            # shutdown — recover_running_sessions catches the leftover.
            import threading

            def _interrupt() -> None:
                try:
                    asyncio.run(orchestrator.interrupt(session_id))
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "orchestrator.interrupt failed for session %s",
                        session_id,
                        exc_info=True,
                    )

            threading.Thread(target=_interrupt, daemon=True).start()
        except Exception:  # noqa: BLE001 — kernel module import failure
            logger.warning(
                "Could not reach orchestrator to interrupt session %s",
                session_id,
                exc_info=True,
            )
            orchestrator_failed = True

        # Step 2 — flip status to idle (always runs).
        old_status = _map_kernel_status(session.status)

        from src.core.types import UserInterrupt  # type: ignore[import-not-found]

        updated = _copy_session(
            session,
            status="idle",
            stop_reason=UserInterrupt(),
        )
        kernel_sync.save_session_sync(updated)

        # Step 3 — surface a kernel event when step 1 failed so the SSE
        # client doesn't see a silent stream cut. Try to anchor it onto
        # the session's latest message; if no message exists yet the
        # event can't be persisted (kernel V5+messages requires every
        # event row to carry a message_id), so we fall back to an
        # in-memory broadcast that still reaches live SSE subscribers.
        if orchestrator_failed:
            from src.core.events import Event as KernelEvent  # type: ignore[import-not-found]

            from valuz_agent.adapters.broadcast_sink import broadcast as broadcast_event

            err_event = KernelEvent(
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
                persisted = kernel_sync.append_session_scoped_event_sync(session_id, err_event)
            except Exception:  # noqa: BLE001
                persisted = False
                logger.exception(
                    "Failed to persist session_error after interrupt delivery failure for %s",
                    session_id,
                )
            if not persisted:
                try:
                    asyncio.run(broadcast_event(session_id, err_event))
                except RuntimeError:
                    # Already inside a loop — schedule it.
                    asyncio.get_event_loop().create_task(broadcast_event(session_id, err_event))
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to broadcast session_error after interrupt for %s",
                        session_id,
                    )

        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=old_status,
            new_status="idle",
        )
        return _session_to_detail(updated)

    def cancel(self, session_id: str) -> SessionDetail:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        old_status = _map_kernel_status(session.status)

        updated = _copy_session(
            session,
            status="terminated",
        )
        kernel_sync.save_session_sync(updated)

        self._bus.publish(
            SESSION_STATUS_CHANGED,
            session_id=session_id,
            old_status=old_status,
            new_status="cancelled",
        )
        return _session_to_detail(updated)

    async def regenerate(self, session_id: str) -> SessionDetail:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        meta = _valuz_meta(session)
        last_msg = meta.get("last_user_message_text")
        if not last_msg:
            raise SessionNotRunnable("No user message to regenerate from")
        return await self.send_message(session_id, str(last_msg))

    def rename_session(self, session_id: str, name: str) -> SessionDetail:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        meta = dict(session.metadata)
        valuz = dict(meta.get("valuz") or {})
        valuz["name"] = name
        meta["valuz"] = valuz

        updated = _copy_session(
            session,
            metadata=meta,
        )
        kernel_sync.save_session_sync(updated)
        return _session_to_detail(updated)

    def delete_session(self, session_id: str) -> None:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        kernel_sync.delete_session_sync(session_id)

    def get_extra_skills(self, session_id: str) -> list[str]:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)
        meta = _valuz_meta(session)
        raw = meta.get("extra_skill_ids")
        if not isinstance(raw, list):
            return []
        return [str(s) for s in raw if isinstance(s, str)]

    def set_extra_skills(self, session_id: str, skill_ids: list[str]) -> SessionDetail:
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        cleaned = sorted({str(s) for s in skill_ids if s and isinstance(s, str)})
        meta = dict(session.metadata)
        valuz = dict(meta.get("valuz") or {})
        valuz["extra_skill_ids"] = cleaned
        meta["valuz"] = valuz

        updated = _copy_session(
            session,
            metadata=meta,
        )
        kernel_sync.save_session_sync(updated)
        return _session_to_detail(updated)

    def set_permission_mode(self, session_id: str, permission_mode: str) -> SessionDetail:
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
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        target = _coerce_session_permission_mode(permission_mode)
        runtime_provider = getattr(session, "runtime_provider", "claude_agent")
        if runtime_provider == "deepagents" and target == "auto_review":
            raise SessionNotRunnable(
                "auto_review is not supported for deepagents runtimes; pick default or full_access"
            )

        updated = _copy_session(session, permission_mode=target)
        kernel_sync.save_session_sync(updated)
        return _session_to_detail(updated)

    def set_session_effort(self, session_id: str, effort: str | None) -> SessionDetail:
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
        session = kernel_sync.load_session_sync(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        target_effort = _coerce_session_effort(effort)
        previous = session.model_settings or ModelSettings()
        new_settings = ModelSettings(
            temperature=previous.temperature,
            max_tokens=previous.max_tokens,
            effort=target_effort,
        )
        updated = _copy_session(session, model_settings=new_settings)
        kernel_sync.save_session_sync(updated)
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
        session = await kernel_store.load_session(session_id)
        if session is None:
            raise _kernel_session_not_found(session_id)

        from app.dependencies import get_orchestrator  # type: ignore[import-not-found]

        orchestrator = get_orchestrator()
        result = await orchestrator.submit_action(
            session_id,
            pending_id=pending_id,
            decision=decision,
            message=message,
            answers=answers,
            modified_input=modified_input,
        )
        return {
            "pending_id": result.pending_id,
            "decision": result.decision,
            "accepted_at": result.accepted_at,
            "idempotent": result.idempotent,
            "rule_id": result.rule_id,
        }

    def count_sessions_for_workspace(self, workspace_id: str) -> int:
        """Return the number of kernel sessions for this workspace (project_id)."""
        sessions = kernel_sync.list_sessions_sync(project_id=workspace_id, limit=1000)
        return len(sessions)

    def delete_sessions_for_workspace(self, workspace_id: str) -> int:
        """Delete all kernel sessions (and their events) for this workspace."""
        sessions = kernel_sync.list_sessions_sync(project_id=workspace_id, limit=1000)
        for s in sessions:
            kernel_sync.delete_session_sync(s.id)
        return len(sessions)
