"""Tests for the authentication extension points (Slice 2).

Verifies that:
1. OSS mode → all requests resolve to the local install identity
2. Custom IdentityResolver injection works via ext.identity
3. Auth middleware injection returns 401 on missing token
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from valuz_agent.integrations.identity_local import LocalIdentityResolver


class TestLocalIdentityResolver:
    async def test_returns_local_install_identity(self) -> None:
        from valuz_agent.infra.local_identity import resolve_local_user_id

        resolver = LocalIdentityResolver()
        result = await resolver.resolve(MagicMock())
        assert result == resolve_local_user_id()
        assert result.startswith("local-")


class TestAuthMiddlewareIntegration:
    """Simulate the pattern a commercial app would use:
    inject auth middleware that rejects unauthenticated requests.
    """

    def test_should_return_401_when_middleware_rejects_unauthenticated(self) -> None:
        app = FastAPI()

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
                token = request.headers.get("Authorization")
                if not token:
                    return JSONResponse(status_code=401, content={"detail": "Missing token"})
                return await call_next(request)

        app.add_middleware(AuthMiddleware)

        @app.get("/test")
        def _test_route() -> dict:
            return {"ok": True}

        client = TestClient(app)

        resp = client.get("/test")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing token"

    def test_should_pass_when_middleware_accepts_token(self) -> None:
        app = FastAPI()

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
                token = request.headers.get("Authorization")
                if not token:
                    return JSONResponse(status_code=401, content={"detail": "Missing token"})
                return await call_next(request)

        app.add_middleware(AuthMiddleware)

        @app.get("/test")
        def _test_route() -> dict:
            return {"ok": True}

        client = TestClient(app)

        resp = client.get("/test", headers={"Authorization": "Bearer test-jwt"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_custom_resolver_via_ext_identity(self) -> None:
        """Set ext.identity to a custom resolver; verify it resolves correctly."""
        from valuz_agent.ports.extensions import ext

        class JWTResolver:
            async def resolve(self, request: object) -> str | None:
                auth = getattr(request, "headers", {}).get("Authorization", "")
                if auth.startswith("Bearer "):
                    return "jwt-user"
                return None

        ext.identity = JWTResolver()
        try:
            request = MagicMock()
            request.headers = {"Authorization": "Bearer valid-token"}
            user_id = await ext.identity.resolve(request)
            assert user_id == "jwt-user"

            request_no_token = MagicMock()
            request_no_token.headers = {}
            user_id2 = await ext.identity.resolve(request_no_token)
            assert user_id2 is None
        finally:
            ext.identity = None
