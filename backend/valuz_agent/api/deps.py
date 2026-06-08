"""FastAPI dependency injection — wires Services to their datastores and ports."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Request

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime
from valuz_agent.integrations.identity_local import LocalIdentityResolver
from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.integrations.skills_official import OfficialSkillSource
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.modules.parser import ParserRouter, build_default_registry
from valuz_agent.modules.projects.datastore import WorkspaceDatastore
from valuz_agent.modules.projects.service import WorkspaceService
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
from valuz_agent.ports.identity import ANONYMOUS, IdentityResolver, UserIdentity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.decisions.aggregator import DecisionAggregator
    from valuz_agent.modules.skills.contracts import RuntimeContext

# ---------------------------------------------------------------------------
# Identity resolver — replaceable by commercial app via set_identity_resolver()
# ---------------------------------------------------------------------------

_identity_resolver: IdentityResolver = LocalIdentityResolver()


def set_identity_resolver(resolver: IdentityResolver) -> None:
    """Replace the identity resolver (called by commercial app at startup)."""
    global _identity_resolver
    _identity_resolver = resolver


def get_current_user(request: Request) -> UserIdentity:
    """Resolve the current user from the request. OSS → ANONYMOUS."""
    result = _identity_resolver.resolve(request)
    return result or ANONYMOUS


def build_runtime_context(user: UserIdentity | None = None) -> RuntimeContext:
    """Build a RuntimeContext from a UserIdentity.

    OSS mode: ``user`` is None or ANONYMOUS → ``user_id="local-user", org_id=None``.
    Commercial mode: fields populated from JWT-resolved identity.
    """
    from valuz_agent.modules.skills.contracts import RuntimeContext

    if user is None:
        user = ANONYMOUS
    return RuntimeContext(user_id=user.user_id, org_id=user.org_id)


@lru_cache
def _secret_store() -> FileSecretStore:
    from valuz_agent.infra.config import settings

    return FileSecretStore(settings.secrets_dir)


async def get_provider_service() -> AsyncGenerator[ProviderService, None]:
    async with async_unit_of_work() as db:
        yield ProviderService(
            datastore=ProviderDatastore(db),
            secret_store=_secret_store(),
            event_bus=event_bus,
        )


async def get_workspace_service() -> AsyncGenerator[WorkspaceService, None]:
    async with async_unit_of_work() as db:
        yield WorkspaceService(
            datastore=WorkspaceDatastore(db),
            event_bus=event_bus,
            session_datastore=SessionDatastore(db),
            document_datastore=DocumentDatastore(db),
            automation_datastore=AutomationDatastore(db),
            skill_datastore=SkillDatastore(db),
            connector_datastore=ConnectorDatastore(db),
        )


async def get_skill_service() -> AsyncGenerator[SkillLibraryService, None]:
    async with async_unit_of_work() as db:
        yield SkillLibraryService(
            datastore=SkillDatastore(db),
            skill_source=FilesystemSkillSource(),
            workspace_service=WorkspaceService(
                datastore=WorkspaceDatastore(db),
                event_bus=event_bus,
            ),
            session_datastore=SessionDatastore(db),
            event_bus=event_bus,
            extra_sources=[OfficialSkillSource()],
        )


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


class _SecretStoreResolver:
    """Bridges ``ParserPlugin.SecretResolver`` to ``FileSecretStore``.
    Plugins call ``resolve(secret_ref)`` to fetch the API key at build
    time; we never plumb the plaintext through routing layers."""

    def __init__(self, store: FileSecretStore) -> None:
        self._store = store

    def resolve(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        return self._store.get(secret_ref)


async def build_parser_router(db: AsyncSession) -> ParserRouter:
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

    routing_config = await load_routing_config(db)
    return ParserRouter(
        registry=_parser_registry(),
        secret_resolver=_SecretStoreResolver(_secret_store()),
        routing_config=routing_config,
        setup_complete_probe=_setup_controller().is_complete,
    )


async def get_document_service() -> AsyncGenerator[DocumentLibraryService, None]:
    from valuz_agent.infra.config import settings

    async with async_unit_of_work() as db:
        preview_dir = settings.docs_dir / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        docs_runtime = EmbeddedDocsRuntime(preview_dir=preview_dir)
        # ``ParserRouter`` reads its routing config from an immutable snapshot
        # resolved here (one async read per request) instead of opening a sync
        # session per parse.
        parser = await build_parser_router(db)
        yield DocumentLibraryService(
            datastore=DocumentDatastore(db),
            parser=parser,
            docs_runtime=docs_runtime,
            event_bus=event_bus,
            scan_state_dir=settings.docs_dir / "scan_state",
            # ``session_factory=None`` → the background reindex runner uses
            # ``async_unit_of_work`` (its own fresh async session per job), so
            # the worker never reuses the request's closed session.
        )


async def get_session_service() -> AsyncGenerator[SessionService, None]:
    async with async_unit_of_work() as db:
        workspace_ds = WorkspaceDatastore(db)
        workspace_svc = WorkspaceService(datastore=workspace_ds, event_bus=event_bus)
        yield SessionService(
            event_bus=event_bus,
            workspace_svc=workspace_svc,
            providers=ProviderDatastore(db),
            skills=SkillDatastore(db),
            workspaces=workspace_ds,
            docs=DocumentDatastore(db),
            secrets=_secret_store(),
            connectors=ConnectorDatastore(db),
            skill_source=FilesystemSkillSource(),
            extra_skill_sources=[OfficialSkillSource()],
        )


async def get_automation_service() -> AsyncGenerator[AutomationService, None]:
    """Construct an ``AutomationService`` per request.

    Locale + default tz come from settings preferences via the sync
    settings bridge, then the service is constructed with both the
    workspace and agent collaborator services so ``create`` can run the
    chat/project branching from ADR-021 §4.
    """
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    async with async_unit_of_work() as db:
        locale = await get_default_locale(db)
        # Effective default = configured tz, else the detected OS tz (so a
        # schedule created without an explicit tz lands on the user's local
        # clock, not UTC).
        default_timezone = await get_effective_default_timezone(db)
        workspace_svc = WorkspaceService(
            datastore=WorkspaceDatastore(db),
            event_bus=event_bus,
        )
        # AgentService needs a ConnectorService so library-agent instantiation
        # can resolve MCP servers from the agent's connector_types.
        connector_svc = ConnectorService(
            datastore=ConnectorDatastore(db),
            secrets=_secret_store(),
        )
        agent_svc = AgentService(db=db, connector_service=connector_svc)
        yield AutomationService(
            db=db,
            event_bus=event_bus,
            workspace_service=workspace_svc,
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


async def get_runs_service() -> AsyncGenerator[RunsService, None]:
    async with async_unit_of_work() as db:
        yield RunsService(
            workspaces=WorkspaceDatastore(db),
            task_sessions=TaskSessionDatastore(db),
            tasks=TaskDatastore(db),
            task_events=TaskEventDatastore(db),
        )


# ---------------------------------------------------------------------------
# Decision Inbox (ADR-022) — process-scoped singleton, set at startup
# ---------------------------------------------------------------------------

_decision_aggregator: DecisionAggregator | None = None


def set_decision_aggregator(agg: DecisionAggregator) -> None:
    """Register the process-scoped aggregator. Called by app startup."""
    global _decision_aggregator
    _decision_aggregator = agg


def get_decision_aggregator() -> DecisionAggregator:
    """FastAPI Depends provider for the inbox aggregator.

    Returns the singleton wired up at startup. Raises ``RuntimeError`` if
    called before startup — defensive: indicates a misconfigured app
    (route is registered but the lifecycle hook didn't fire).
    """
    if _decision_aggregator is None:
        raise RuntimeError("decision aggregator not initialized — startup hook didn't run")
    return _decision_aggregator
