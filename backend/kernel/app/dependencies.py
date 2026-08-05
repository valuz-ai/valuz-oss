"""Dependency injection — manages DB engine, session factory, store, and orchestrator lifecycle."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Annotated

from app.config import AppConfig
from fastapi import Header, HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.adapters.remote_store import build_remote_store
from src.adapters.runtime_store import RuntimeStore
from src.adapters.sqlalchemy_store.engine import create_engine, create_session_factory
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core import NullTokenVerifier, StorePort, TokenVerifier
from src.core.claim_evidence_resolution import SemanticVerifierPort
from src.core.orchestrator import SessionOrchestrator
from src.core.semantic_verifier import build_session_semantic_verifier
from src.core.types import Session

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
# In-process durable Postgres engine (``kernel_store=pg``); disposed on shutdown
# alongside ``_engine``. ``None`` when local-only or the durable is HTTP-remote.
_durable_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_store: StorePort | None = None
_orchestrator: SessionOrchestrator | None = None
# The kernel's runtime store (sqlite authority + DataService mirror), held by
# its concrete type for introspection/tests. ``None`` only when no mirror
# backend is configured (bare local / collapsed DSN → plain local store).
_runtime_store: RuntimeStore | None = None
# Owner-from-token seam: OSS default never derives identity from a token, so
# ``get_owner_id`` keeps using the trusted ``X-Valuz-Owner-Id`` header. A SaaS
# overlay binds a real verifier via ``set_token_verifier``.
_token_verifier: TokenVerifier = NullTokenVerifier()


async def _session_semantic_verifier_factory(
    user_id: str,
    session: Session,
) -> SemanticVerifierPort | None:
    """Use the Session's explicit provider; unsupported providers return None."""

    return build_session_semantic_verifier(user_id, session)


async def init_dependencies(config: AppConfig) -> None:
    """Initialize DB engine, session factory, store, and orchestrator.

    Also runs the orphan-pending scan: any ``requires_action`` event left
    open across a host restart is sealed with
    ``action_resolved(decision="expired", resolved_by="system")``
    (per design doc §6.3 — D6 contract symmetry across runtimes).
    """
    global _engine, _session_factory, _store, _orchestrator  # noqa: PLW0603
    global _durable_engine, _runtime_store  # noqa: PLW0603
    # Model A: the LOCAL store ALWAYS exists (local-first). The kernel keeps its
    # own SQLite/PG via this engine; when a durable backend is configured
    # (remote DataService / central PG) every write is mirrored through it
    # (RuntimeStore). No "remote replaces local" branch.
    _engine = create_engine(config.database_url)
    _session_factory = create_session_factory(_engine)
    local: StorePort = SQLAlchemyStore(_session_factory)
    durable = _build_durable_store(config)
    if _durable_engine is not None:
        # In-process durable: create the kernel schema if absent. ``create_all``
        # is checkfirst (idempotent) — a no-op when the DB was already
        # provisioned by alembic, and it materializes the full current model
        # (incl. the ``event_uid`` unique index) on a fresh backend.
        await _ensure_durable_schema(_durable_engine)
    # ONE composition, every tier (see the RuntimeStore module docstring):
    # local sqlite is the kernel's sole runtime persistence source, and every
    # write is dual-written to the DataService mirror. ``KERNEL_STORE`` only
    # selects the mirror backend (local → host valuz.db, pg → central Postgres,
    # remote → HTTP DataService). No mirror configured (bare local / collapsed
    # DSN) → the plain local store alone.
    _runtime_store = None
    if durable is None:
        store: StorePort = local
    else:
        _runtime_store = RuntimeStore(local, durable)
        store = _runtime_store
    _store = store
    _orchestrator = SessionOrchestrator(
        store,
        max_warm_runtimes=_env_int("VALUZ_MAX_WARM_RUNTIMES"),
        runtime_idle_ttl_s=_env_float("VALUZ_RUNTIME_IDLE_TTL_S"),
        bg_busy_runtime_ttl_s=_env_float("VALUZ_BG_BUSY_RUNTIME_TTL_S"),
        semantic_verifier_factory=_session_semantic_verifier_factory,
    )
    # Start the warm-runtime idle sweeper (bounds leaked claude/codex
    # subprocesses; see SessionOrchestrator). Safe before the orphan scan's
    # possible early return so it runs regardless of migration state.
    _orchestrator.start()
    # Boot orphan scans sweep the kernel's OWN lineage — its runtime sqlite —
    # unconditionally: sessions live on other processes are structurally out of
    # reach (the kernel has no remote read path), so the sweep is safe in every
    # deployment. Cross-process reconciliation is the HOST's job.
    # Best-effort — schema may not be migrated yet (typical in unit tests that
    # skip Alembic and run against an empty in-memory DB).
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
    global _engine, _durable_engine, _session_factory, _store, _orchestrator  # noqa: PLW0603
    global _runtime_store  # noqa: PLW0603
    if _orchestrator is not None:
        # Cancel the idle sweeper and close every warm runtime — terminates all
        # live claude/codex subprocesses deterministically on shutdown.
        try:
            await _orchestrator.shutdown()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            logger.debug("orchestrator shutdown failed", exc_info=True)
    if _engine:
        await _engine.dispose()
    if _durable_engine:
        await _durable_engine.dispose()
    _engine = None
    _durable_engine = None
    _session_factory = None
    _store = None
    _orchestrator = None
    _runtime_store = None


def _env_int(name: str) -> int | None:
    """Parse an optional int env override (``<= 0`` disables the policy);
    ``None`` (use default) when unset or malformed."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring malformed %s=%r (expected int)", name, raw)
        return None


def _env_float(name: str) -> float | None:
    """Parse an optional float env override; ``None`` (use default) when unset
    or malformed."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring malformed %s=%r (expected number)", name, raw)
        return None


def get_owner_id(
    x_valuz_owner_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """FastAPI dependency — the request's owner id (``user_id``).

    Two sources, in order:

    1. **Verified token** (remote / SaaS): when a ``TokenVerifier`` is bound and
       a bearer token is present, the owner comes from the VERIFIED token claims
       — never from a caller-supplied header (an untrusted sandbox could forge
       ``X-Valuz-Owner-Id``). OSS binds ``NullTokenVerifier`` (always ``None``),
       so this branch is inert and behaviour is unchanged.
    2. **Header** (trusted host mount): the host sends the resolved per-request
       owner in ``X-Valuz-Owner-Id``. The in-process seam never reaches this
       dependency — it passes the owner explicitly. An absent header on a direct
       HTTP call is a 403; the kernel never serves owner-scoped data without one.
    """
    claims = _token_verifier.verify(_bearer_token(authorization))
    if claims is not None:
        return claims.user_id
    if not x_valuz_owner_id:
        raise HTTPException(status_code=403, detail="owner id required")
    return x_valuz_owner_id


def _bearer_token(authorization: str | None) -> str | None:
    """Extract the bearer credential from an ``Authorization`` header."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def set_token_verifier(verifier: TokenVerifier) -> None:
    """Bind the owner-from-token verifier. A SaaS overlay swaps the default
    ``NullTokenVerifier`` for a signing-key/JWKS-backed implementation so the
    sandbox's owner is derived from its verified JWT, not a header."""
    global _token_verifier  # noqa: PLW0603
    _token_verifier = verifier


async def _ensure_durable_schema(engine: AsyncEngine) -> None:
    """Create the kernel data schema on the in-process mirror engine if missing.

    Idempotent (``create_all`` is checkfirst): ``sessions`` / ``messages`` /
    ``events``, ready to receive the RuntimeStore's dual-writes.
    """
    from src.adapters.sqlalchemy_store.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _build_durable_store(config: AppConfig) -> StorePort | None:
    """The durable write-through target (the DataService backend), or ``None``.

    One config→backend factory, no per-tier special case:

    - ``local`` / ``pg`` → an **in-process ``SQLAlchemyStore`` on
      ``durable_database_url``** (same process, no HTTP). The only difference is
      the DSN: for ``pg`` it is the user's Postgres; for the OSS default
      (``local``) the host injects the host sqlite (``valuz.db``) so the
      DataService is still the data layer (DataService design §3 form 1). Its
      engine is stashed on ``_durable_engine`` for lifespan disposal.
    - ``remote`` → a client to the remote HTTP DataService (sandbox/SaaS); no
      DSN here, only the data-API URL + bearer-token hook.

    Returns ``None`` only when no durable is resolvable — an unconfigured
    ``local`` (no ``durable_database_url``; unit tests / bare kernel), or when the
    durable DSN **equals the local ``database_url``** (already one file — the
    dual-write collapses to a single write).
    """
    global _durable_engine  # noqa: PLW0603
    if config.kernel_store in ("local", "pg"):
        dsn = config.durable_database_url
        if not dsn:
            if config.kernel_store == "pg":
                raise RuntimeError("KERNEL_STORE=pg requires VALUZ_DURABLE_DATABASE_URL")
            return None  # bare local (tests / no DataService backend) → single write
        if dsn == config.database_url:
            return None  # collapse: durable == local file → single write
        _durable_engine = create_engine(dsn)
        return SQLAlchemyStore(create_session_factory(_durable_engine))
    if config.kernel_store != "remote":
        return None
    if not config.data_api_url:
        raise RuntimeError("KERNEL_STORE=remote requires VALUZ_DATA_API_URL")
    _ensure_remote_backend(config.data_api_kind)
    token = config.data_api_token or ""

    async def _access_token() -> str:
        # Static token from env for now; a refresh hook (re-mint short-lived
        # JWT from the host) plugs in here later.
        return token

    return build_remote_store(
        kind=config.data_api_kind,
        base_url=config.data_api_url,
        access_token=_access_token,
    )


def _ensure_remote_backend(kind: str) -> None:
    """Import the module that self-registers ``kind`` (Phase B: postgrest)."""
    module = {
        "http": "src.adapters.remote_store_http",
        "postgrest": "src.adapters.remote_store_postgrest",
    }.get(kind)
    if module:
        try:
            importlib.import_module(module)
        except ImportError:
            logger.debug("remote store backend module %s not importable yet", module)


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
