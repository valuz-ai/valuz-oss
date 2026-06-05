"""Dependency injection — manages DB engine, session factory, store, and orchestrator lifecycle."""

from __future__ import annotations

import logging

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import AppConfig
from src.adapters.sqlalchemy_store.engine import create_engine, create_session_factory
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core import StorePort
from src.core.orchestrator import SessionOrchestrator

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_store: SQLAlchemyStore | None = None
_orchestrator: SessionOrchestrator | None = None


async def init_dependencies(config: AppConfig) -> None:
    """Initialize DB engine, session factory, store, and orchestrator.

    Also runs the orphan-pending scan: any ``requires_action`` event left
    open across a host restart is sealed with
    ``action_resolved(decision="expired", resolved_by="system")``
    (per design doc §6.3 — D6 contract symmetry across runtimes).
    """
    global _engine, _session_factory, _store, _orchestrator  # noqa: PLW0603
    _engine = create_engine(config.database_url)
    _session_factory = create_session_factory(_engine)
    _store = SQLAlchemyStore(_session_factory)
    _orchestrator = SessionOrchestrator(_store)
    # Best-effort scan — schema may not be migrated yet (typical in unit
    # tests that skip Alembic and run against an empty in-memory DB).
    try:
        sealed = await _orchestrator.scan_orphan_pendings()
        reset_runs = await _orchestrator.scan_orphan_runs()
    except OperationalError as exc:
        logger.debug("Orphan scan skipped (schema not migrated): %s", exc)
        return
    if sealed:
        logger.info("Sealed %d orphan pending approval(s) on startup", sealed)
    if reset_runs:
        logger.info("Reset %d orphan running session(s) on startup", reset_runs)


async def shutdown_dependencies() -> None:
    """Dispose engine and clear singletons. Called during app lifespan shutdown."""
    global _engine, _session_factory, _store, _orchestrator  # noqa: PLW0603
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    _store = None
    _orchestrator = None


def get_store() -> StorePort:
    """FastAPI dependency — returns the StorePort singleton."""
    if _store is None:
        raise RuntimeError("Dependencies not initialized — is the app lifespan running?")
    return _store


def get_orchestrator() -> SessionOrchestrator:
    """FastAPI dependency — returns the SessionOrchestrator singleton."""
    if _orchestrator is None:
        raise RuntimeError("Dependencies not initialized — is the app lifespan running?")
    return _orchestrator
