"""Kernel data service — StorePort exposed over HTTP (the T1 transport server).

A thin, standalone FastAPI app that REUSES the kernel's own ``SQLAlchemyStore``
(one store implementation, local + remote) and exposes each StorePort method as
``POST /rpc/{op}``. The sandbox's :class:`RemoteStoreHttp` is its only client.

Security: every request derives its owner from the VERIFIED bearer token (a
``TokenVerifier``), never from the request body — ``save_session`` overwrites
the body's ``user_id`` with the token owner, and reads/writes are scoped to it.
A missing/invalid token is a hard 401. The DB lives here (trusted side); the
sandbox holds only a token + this URL.

``create_data_service_app(store, verifier)`` (used by tests and the seatbelt
closed loop) stashes the store + verifier on ``app.state``; the module-level
router resolves them per request via dependencies. The DB engine + verifier
wiring for a standalone deployment is assembled by the caller.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.adapters import store_wire as sw
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core import StorePort
from src.core.token_signer import HmacTokenVerifier, InvalidTokenError
from src.core.token_verifier import (
    AsyncTokenVerifier,
    CompatibleAsyncTokenVerifier,
    TokenVerifierLike,
)

router = APIRouter()
logger = logging.getLogger(__name__)

def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


async def _owner_dep(request: Request) -> str:
    """Owner from the VERIFIED token — never from the body (anti-spoof)."""
    verifier: AsyncTokenVerifier = request.app.state.verifier
    try:
        claims = await verifier.verify(_bearer(request.headers.get("authorization")))
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — auth backend failure must fail closed
        logger.warning("Data Service credential verification failed", exc_info=True)
        raise HTTPException(status_code=401, detail="credential verification failed") from exc
    if claims is None:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return claims.user_id


def _store_dep(request: Request) -> StorePort:
    store: StorePort = request.app.state.store
    return store


# Module-level Annotated aliases so ``from __future__ import annotations``
# (lazy string annotations) still resolve under FastAPI's get_type_hints.
OwnerDep = Annotated[str, Depends(_owner_dep)]
StoreDep = Annotated[StorePort, Depends(_store_dep)]
JsonBody = Annotated[dict[str, Any], Body()]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# -- writes (owner forced from token; body user_id ignored) --


@router.post("/rpc/save_session")
async def save_session(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    session = replace(sw.row_to_session(body["session"]), user_id=owner_id)
    await store.save_session(session)
    return {"data": None}


@router.post("/rpc/save_message")
async def save_message(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    await store.save_message(owner_id, sw.row_to_message(body["message"]))
    return {"data": None}


@router.post("/rpc/append_event")
async def append_event(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    result_seq = await store.append_event(
        owner_id,
        body["session_id"],
        body["message_id"],
        sw.row_to_event(body["event"]),
        request_id=body.get("request_id"),
        seq=body.get("seq"),
    )
    return {"data": result_seq}


@router.post("/rpc/delete_session")
async def delete_session(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    return {"data": await store.delete_session(owner_id, body["session_id"])}


# -- reads --


@router.post("/rpc/load_session")
async def load_session(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    s = await store.load_session(owner_id, body["session_id"])
    return {"data": sw.session_to_row(s) if s else None}


@router.post("/rpc/list_sessions")
async def list_sessions(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.list_sessions(
        owner_id,
        status=body.get("status"),
        ids=body.get("ids"),
        limit=body.get("limit", 50),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.session_to_row(s) for s in rows]}


@router.post("/rpc/load_message")
async def load_message(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    m = await store.load_message(owner_id, body["message_id"])
    return {"data": sw.message_to_row(m) if m else None}


@router.post("/rpc/list_messages_for_session")
async def list_messages_for_session(
    body: JsonBody, owner_id: OwnerDep, store: StoreDep
) -> dict[str, Any]:
    rows = await store.list_messages_for_session(
        owner_id,
        body["session_id"],
        limit=body.get("limit", 50),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.message_to_row(m) for m in rows]}


@router.post("/rpc/get_events")
async def get_events(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.get_events(
        owner_id,
        body["session_id"],
        limit=body.get("limit", 200),
        offset=body.get("offset", 0),
        types=body.get("types"),
    )
    return {"data": [sw.event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_for_message")
async def get_events_for_message(
    body: JsonBody, owner_id: OwnerDep, store: StoreDep
) -> dict[str, Any]:
    rows = await store.get_events_for_message(
        owner_id,
        body["message_id"],
        limit=body.get("limit", 200),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_after")
async def get_events_after(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.get_events_after(
        owner_id,
        body["session_id"],
        after_seq=body.get("after_seq", 0),
        limit=body.get("limit", 200),
    )
    return {"data": [sw.stored_event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_after_for_user")
async def get_events_after_for_user(
    body: JsonBody, owner_id: OwnerDep, store: StoreDep
) -> dict[str, Any]:
    types = body.get("types")
    rows = await store.get_events_after_for_user(
        owner_id,
        after_seq=body.get("after_seq", 0),
        types=tuple(types) if types else None,
        limit=body.get("limit", 200),
    )
    return {"data": [sw.stored_event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_window")
async def get_events_window(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    events, has_more = await store.get_events_window(
        owner_id,
        body["session_id"],
        before_seq=body.get("before_seq"),
        turn_limit=body.get("turn_limit", 20),
    )
    return {
        "data": {
            "events": [sw.stored_event_to_row(e) for e in events],
            "has_more": has_more,
        }
    }


@router.post("/rpc/usage_rollup")
async def usage_rollup(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.usage_rollup(owner_id, body["start_ms"], body["end_ms"])
    return {"data": [sw.usage_rollup_to_row(u) for u in rows]}


def create_data_service_app(store: StorePort, verifier: TokenVerifierLike) -> FastAPI:
    """Build the data-service ASGI app over ``store``, authed by ``verifier``."""
    app = FastAPI(title="Valuz Kernel Data Service", version="0.1.0")
    app.state.store = store
    app.state.verifier = CompatibleAsyncTokenVerifier(verifier)
    app.include_router(router)
    return app


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def build_app_from_env() -> FastAPI:
    """Standalone data service from env (uvicorn ``--factory`` entrypoint).

    ``VALUZ_DATA_SERVICE_DATABASE_URL`` (or ``DATABASE_URL``) — the DB DSN. This
    is the ONLY place a DB credential lives; the sandbox never gets it.
    ``VALUZ_DATA_SERVICE_JWT_SECRET`` — the HS256 secret shared with the host
    token signer. Assumes the schema is already migrated.
    """
    db_url = os.environ.get("VALUZ_DATA_SERVICE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    secret = os.environ.get("VALUZ_DATA_SERVICE_JWT_SECRET")
    if not db_url:
        raise RuntimeError(
            "data service requires VALUZ_DATA_SERVICE_DATABASE_URL (or DATABASE_URL)"
        )
    if not secret:
        raise RuntimeError("data service requires VALUZ_DATA_SERVICE_JWT_SECRET")
    engine = create_async_engine(_to_async_url(db_url))
    store = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
    # The engine lives for the process lifetime; the OS reclaims its
    # connections on exit (a standalone, long-running service).
    return create_data_service_app(store, HmacTokenVerifier(secret))
