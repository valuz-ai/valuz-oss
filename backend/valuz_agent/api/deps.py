"""FastAPI dependency injection — wires Services to their datastores and ports."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends

from valuz_agent.infra import auth_context
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.integrations.skills_official import OfficialSkillSource
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore

# Decision Inbox (ADR-022): the process-scoped singleton is owned by the
# decisions module itself — the API layer is only one of its consumers.
# Re-exported so routes keep writing ``Depends(get_decision_aggregator)``.
from valuz_agent.modules.decisions.aggregator import (
    get_decision_aggregator as get_decision_aggregator,
)
from valuz_agent.modules.decisions.aggregator import (
    set_decision_aggregator as set_decision_aggregator,
)
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.modules.parser import ParserRouter, build_default_registry
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.service import ProviderService
from valuz_agent.modules.runs.service import RunsService
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.service import SessionService
from valuz_agent.modules.settings.datastore import SettingsDatastore
from valuz_agent.modules.settings.service import SettingsService
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.service import SkillLibraryService
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.ports.docs_runtime import get_docs_runtime

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.channels.service import ChannelIngressService
    from valuz_agent.modules.project_packs.service import ProjectPackService


def get_current_user_id() -> str:
    """Resolve the current request owner id; require a concrete user_id.

    This is the OSS-wide read-side entrypoint for owner-scoped service/datastore
    calls. When context is unset it raises ``OwnerContextUnsetError`` which the
    middleware maps to ``401``.
    """
    user_id = auth_context.get_current_user_id()
    if user_id is None:
        raise auth_context.OwnerContextUnsetError(
            "current_user_id is unset; owner-scoped reads require an owner"
        )
    return user_id


def get_current_user_id_optional() -> str | None:
    """Resolve the current request owner id if present.

    Kept for non-fatal call-sites that intentionally tolerate missing context
    (background jobs or legacy optional fallbacks).
    """
    return auth_context.get_current_user_id()


async def get_provider_service() -> AsyncGenerator[ProviderService, None]:
    async with async_unit_of_work() as db:
        yield ProviderService(
            datastore=ProviderDatastore(db),
            event_bus=event_bus,
        )


async def get_project_service() -> AsyncGenerator[ProjectService, None]:
    async with async_unit_of_work() as db:
        yield ProjectService(
            datastore=ProjectDatastore(db),
            event_bus=event_bus,
            session_datastore=SessionDatastore(db),
            document_datastore=DocumentDatastore(db),
            automation_datastore=AutomationDatastore(db),
            skill_datastore=SkillDatastore(db),
            connector_datastore=ConnectorDatastore(db),
            member_datastore=ProjectMemberDatastore(db),
        )


async def get_skill_service_for_user(
    user_id: str,
) -> AsyncGenerator[SkillLibraryService, None]:
    async with async_unit_of_work() as db:
        yield SkillLibraryService(
            datastore=SkillDatastore(db),
            skill_source=FilesystemSkillSource(),
            project_service=ProjectService(
                datastore=ProjectDatastore(db),
                event_bus=event_bus,
            ),
            extra_sources=[OfficialSkillSource()],
        )


async def get_skill_service(
    user_id: str = Depends(get_current_user_id),
) -> AsyncGenerator[SkillLibraryService, None]:
    async for svc in get_skill_service_for_user(user_id):
        yield svc


@lru_cache
def _parser_registry():  # type: ignore[no-untyped-def]
    """Process-wide parser plugin registry. ``lru_cache`` ensures plugins
    are instantiated exactly once per process — they hold caches and
    background state we do not want duplicated.

    The MinerU plugin needs a reference to the running
    ``PollingScheduler`` to enqueue work; we wire that in here so the
    plugin's first ``build()`` call can register its handler against
    the live scheduler.
    """
    return build_default_registry(scheduler=_polling_scheduler())


@lru_cache
def _setup_controller():  # type: ignore[no-untyped-def]
    """Process-wide setup-job controller (RapidOCR model download +
    future setup work). One per process; runs jobs as on-loop asyncio tasks."""
    from valuz_agent.modules.parser.setup_jobs import build_default_setup_controller

    return build_default_setup_controller()


def get_setup_controller():  # type: ignore[no-untyped-def]
    """FastAPI dependency for the setup controller singleton."""
    return _setup_controller()


@lru_cache
def _polling_scheduler():  # type: ignore[no-untyped-def]
    """Process-wide polling scheduler (on-loop asyncio task). Constructed
    lazily; its tick task is started/stopped by the app startup/shutdown
    hooks (``start_polling_scheduler``). Cloud plugins register their
    ``PollingHandler`` via ``register`` after construction."""
    from valuz_agent.modules.parser.polling import PollingScheduler

    return PollingScheduler(handlers=[])


def get_polling_scheduler():  # type: ignore[no-untyped-def]
    return _polling_scheduler()


class _SecretResolver:
    """Bridges ``ParserPlugin.SecretResolver`` to user-scoped secret files.
    Plugins call ``resolve(secret_ref)`` to fetch the API key at build
    time; we never plumb the plaintext through routing layers."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    def resolve(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        from valuz_agent.infra import secret_store

        return secret_store.get(self._user_id, secret_ref)


async def build_parser_router(db: AsyncSession, user_id: str) -> ParserRouter:
    """Build the config-aware ``ParserRouter`` — the SAME engine KB/Docs
    ingestion uses — from the process-wide plugin registry (+ polling
    scheduler), the secret resolver, and the user's routing snapshot loaded
    from settings.

    Shared by ``get_document_service`` and the conversation-attachment parse
    path so uploaded attachments honor the configured engine (MinerU /
    PaddleOCR), not just LightLocal. ``load_routing_config`` MUST be read in
    the caller's live session — the attachment background task passes its own
    fresh ``async_unit_of_work`` here because the request session is already
    closed by the time it runs.
    """
    from valuz_agent.modules.settings.parser_routing import load_routing_config

    routing_config = await load_routing_config(db, user_id=user_id)
    return ParserRouter(
        registry=_parser_registry(),
        secret_resolver=_SecretResolver(user_id),
        routing_config=routing_config,
        setup_complete_probe=_setup_controller().is_complete,
    )


async def get_document_service() -> AsyncGenerator[DocumentLibraryService, None]:
    from valuz_agent.infra.fs_registry import fs_registry

    user_id = get_current_user_id()
    async with async_unit_of_work() as db:
        docs_runtime = get_docs_runtime(user_id)
        # ``ParserRouter`` reads its routing config from an immutable snapshot
        # resolved here (one async read per request) instead of opening a sync
        # session per parse.
        parser = await build_parser_router(db, user_id)
        yield DocumentLibraryService(
            datastore=DocumentDatastore(db),
            parser=parser,
            docs_runtime=docs_runtime,
            event_bus=event_bus,
            scan_state_dir=fs_registry.docs_scan_state_dir(user_id),
            # ``session_factory=None`` → the background reindex runner uses
            # ``async_unit_of_work`` (its own fresh async session per job), so
            # the worker never reuses the request's closed session.
        )


async def get_session_service() -> AsyncGenerator[SessionService, None]:
    async with async_unit_of_work() as db:
        project_ds = ProjectDatastore(db)
        project_svc = ProjectService(datastore=project_ds, event_bus=event_bus)
        yield SessionService(
            event_bus=event_bus,
            project_svc=project_svc,
            providers=ProviderDatastore(db),
            skills=SkillDatastore(db),
            projects=project_ds,
            docs=DocumentDatastore(db),
            connectors=ConnectorDatastore(db),
            skill_source=FilesystemSkillSource(),
            extra_skill_sources=[OfficialSkillSource()],
        )


async def get_automation_service() -> AsyncGenerator[AutomationService, None]:
    """Construct an ``AutomationService`` per request.

    Locale + default tz come from settings preferences via the sync
    settings bridge, then the service is constructed with both the
    project and agent collaborator services so ``create`` can run the
    chat/project branching from ADR-021 §4.
    """
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    user_id = get_current_user_id()
    async with async_unit_of_work() as db:
        locale = await get_default_locale(db, user_id=user_id)
        # Effective default = configured tz, else the detected OS tz (so a
        # schedule created without an explicit tz lands on the user's local
        # clock, not UTC).
        default_timezone = await get_effective_default_timezone(db, user_id=user_id)
        project_svc = ProjectService(
            datastore=ProjectDatastore(db),
            event_bus=event_bus,
        )
        # AgentService needs a ConnectorService so library-agent instantiation
        # can resolve MCP servers from the agent's connector_types.
        connector_svc = ConnectorService(datastore=ConnectorDatastore(db))
        agent_svc = AgentService(db=db, connector_service=connector_svc)
        yield AutomationService(
            db=db,
            event_bus=event_bus,
            project_service=project_svc,
            agent_service=agent_svc,
            locale=locale,
            default_timezone=default_timezone,
        )


async def get_settings_service() -> AsyncGenerator[SettingsService, None]:
    async with async_unit_of_work() as db:
        yield SettingsService(
            datastore=SettingsDatastore(db),
            event_bus=event_bus,
        )


async def get_channel_ingress_service() -> AsyncGenerator[ChannelIngressService, None]:
    from valuz_agent.adapters.channel_placement_reader import (
        DatastoreAgentPlacementReader,
        DatastoreProjectMemberReader,
    )
    from valuz_agent.adapters.channel_session_runner import SessionServiceChannelRunner
    from valuz_agent.modules.channels.datastore import (
        ChannelChatBindingDatastore,
        ChannelThreadBindingDatastore,
    )
    from valuz_agent.modules.channels.service import ChannelIngressService

    async with async_unit_of_work() as db:
        project_ds = ProjectDatastore(db)
        session_service = SessionService(
            event_bus=event_bus,
            project_svc=ProjectService(datastore=project_ds, event_bus=event_bus),
            providers=ProviderDatastore(db),
            skills=SkillDatastore(db),
            projects=project_ds,
            docs=DocumentDatastore(db),
            connectors=ConnectorDatastore(db),
            skill_source=FilesystemSkillSource(),
            extra_skill_sources=[OfficialSkillSource()],
        )
        yield ChannelIngressService(
            placements=DatastoreAgentPlacementReader(
                members=ProjectMemberDatastore(db),
                projects=project_ds,
            ),
            bindings=ChannelThreadBindingDatastore(db),
            chat_bindings=ChannelChatBindingDatastore(db),
            project_members=DatastoreProjectMemberReader(
                members=ProjectMemberDatastore(db)
            ),
            sessions=SessionServiceChannelRunner(session_service),
        )


async def get_project_pack_service() -> AsyncGenerator[ProjectPackService, None]:
    """Construct a ``ProjectPackService`` per request.

    All four collaborators (``ProjectService``, ``AgentService``,
    ``AgentPackService``, ``AutomationService``) are wired over the SAME
    unit-of-work so the import's writes (project row + members +
    automations + project skills + project connectors + installed
    agents / skills / connectors) land in a single transactional view.
    """
    from valuz_agent.modules.agent_packs.service import AgentPackService
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.project_packs.service import ProjectPackService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    # Mirror ``get_automation_service``: resolve owner from ambient request
    # context up front and thread it through to the preference reads, which
    # require an explicit ``user_id`` (see ``_read`` in ``settings.preferences``).
    user_id = get_current_user_id()
    async with async_unit_of_work() as db:
        locale = await get_default_locale(db, user_id=user_id)
        default_timezone = await get_effective_default_timezone(db, user_id=user_id)
        project_svc = ProjectService(
            datastore=ProjectDatastore(db),
            event_bus=event_bus,
            automation_datastore=AutomationDatastore(db),
            skill_datastore=SkillDatastore(db),
            connector_datastore=ConnectorDatastore(db),
            session_datastore=SessionDatastore(db),
            document_datastore=DocumentDatastore(db),
        )
        connector_svc = ConnectorService(datastore=ConnectorDatastore(db))
        agent_svc = AgentService(db=db, connector_service=connector_svc)
        agent_pack_svc = AgentPackService(agent_svc)
        automation_svc = AutomationService(
            db=db,
            event_bus=event_bus,
            project_service=project_svc,
            agent_service=agent_svc,
            locale=locale,
            default_timezone=default_timezone,
        )
        yield ProjectPackService(
            project_service=project_svc,
            agent_service=agent_svc,
            agent_pack_service=agent_pack_svc,
            automation_service=automation_svc,
        )


async def get_runs_service() -> AsyncGenerator[RunsService, None]:
    async with async_unit_of_work() as db:
        yield RunsService(
            projects=ProjectDatastore(db),
            task_sessions=TaskSessionDatastore(db),
            tasks=TaskDatastore(db),
            task_events=TaskEventDatastore(db),
            automations=AutomationDatastore(db),
        )
