"""Phase B — data-service / RemoteStoreHttp contract drift guard.

The data service must expose exactly one ``POST /rpc/{op}`` per StorePort
method the client calls — no missing route (client breaks) and no extra
(dead). Adding/removing a StorePort op forces a conscious update here.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import httpx

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from app.data_service import create_data_service_app
from src.core.token_verifier import NullTokenVerifier, OwnerClaims

# The StorePort surface the remote transport carries (1:1 with RemoteStoreHttp
# _*_once methods and the data-service /rpc routes).
EXPECTED_OPS = {
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "save_message",
    "load_message",
    "list_messages_for_session",
    "append_event",
    "get_events",
    "get_events_for_message",
    "get_events_after",
    "get_events_after_for_user",
    "get_events_window",
    "usage_rollup",
}


def test_data_service_exposes_exactly_the_storeport_ops():
    app = create_data_service_app(store=object(), verifier=NullTokenVerifier())
    rpc_ops = {
        route.path.removeprefix("/rpc/")
        for route in app.routes
        if getattr(route, "path", "").startswith("/rpc/")
    }
    assert rpc_ops == EXPECTED_OPS


def test_client_implements_every_op():
    from src.adapters.remote_store_http import RemoteStoreHttp

    for op in EXPECTED_OPS:
        assert callable(getattr(RemoteStoreHttp, f"_{op}_once", None)), f"client missing _{op}_once"


async def test_data_service_awaits_async_credential_verifier() -> None:
    calls: list[str | None] = []
    owners: list[str] = []

    class _Verifier:
        async def verify(self, credential: str | None) -> OwnerClaims | None:
            calls.append(credential)
            return OwnerClaims(user_id="owner-async")

    class _Store:
        async def list_sessions(self, owner_id: str, **_kwargs):
            owners.append(owner_id)
            return []

    app = create_data_service_app(store=_Store(), verifier=_Verifier())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://data-service"
    ) as client:
        response = await client.post(
            "/rpc/list_sessions",
            headers={"Authorization": "Bearer opaque-sandbox-credential"},
            json={},
        )

    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert calls == ["opaque-sandbox-credential"]
    assert owners == ["owner-async"]


async def test_data_service_fails_closed_when_async_verifier_errors() -> None:
    class _BrokenVerifier:
        async def verify(self, credential: str | None) -> OwnerClaims | None:
            raise RuntimeError("identity backend unavailable")

    app = create_data_service_app(store=object(), verifier=_BrokenVerifier())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://data-service"
    ) as client:
        response = await client.post(
            "/rpc/list_sessions",
            headers={"Authorization": "Bearer opaque-sandbox-credential"},
            json={},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "credential verification failed"}
