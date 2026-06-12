"""Tests for the authentication extension points.

Verifies that:
1. OSS mode → ``AuthMiddleware.resolve_user_id`` returns the local install id.
2. Identity can be customized by subclassing ``AuthMiddleware`` and overriding
   ``resolve_user_id`` — the single auth seam (swapped in via
   ``ext.auth_middleware``, a ``(cls, kwargs)`` tuple).
3. Auth middleware injection returns 401 on missing token.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from valuz_agent.api.middleware import AuthMiddleware


class TestResolveUserId:
    async def test_returns_local_install_identity(self) -> None:
        """OSS default: every request resolves to the device-local install id."""
        from valuz_agent.infra.local_identity import resolve_local_user_id

        mw = AuthMiddleware(MagicMock())  # app arg unused by resolve_user_id
        result = await mw.resolve_user_id(MagicMock())
        assert result == resolve_local_user_id()
        assert result.startswith("local-")

    async def test_subclass_can_override_resolution(self) -> None:
        """Identity is customized by overriding ``resolve_user_id`` (the seam the
        commercial overlay uses to verify a JWT)."""

        class JWTAuthMiddleware(AuthMiddleware):
            async def resolve_user_id(self, request: Request) -> str | None:
                auth = getattr(request, "headers", {}).get("Authorization", "")
                return "jwt-user" if auth.startswith("Bearer ") else None

        mw = JWTAuthMiddleware(MagicMock())

        req = MagicMock()
        req.headers = {"Authorization": "Bearer valid-token"}
        assert await mw.resolve_user_id(req) == "jwt-user"

        req_no_token = MagicMock()
        req_no_token.headers = {}
        assert await mw.resolve_user_id(req_no_token) is None


class TestAuthMiddlewareSwapPoint:
    def test_ext_auth_middleware_is_cls_kwargs_tuple(self) -> None:
        """The overlay swaps the whole auth middleware via ``ext.auth_middleware``
        — a ``(cls, kwargs)`` tuple — defaulting to OSS's ``AuthMiddleware``."""
        from valuz_agent.ports.extensions import ext

        cls, kwargs = ext.auth_middleware
        assert cls is AuthMiddleware
        assert isinstance(kwargs, dict)


class TestAuthMiddlewareIntegration:
    """Simulate the pattern a commercial app would use: inject auth middleware
    that rejects unauthenticated requests."""

    def test_should_return_401_when_middleware_rejects_unauthenticated(self) -> None:
        app = FastAPI()

        class RejectingMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
                token = request.headers.get("Authorization")
                if not token:
                    return JSONResponse(status_code=401, content={"detail": "Missing token"})
                return await call_next(request)

        app.add_middleware(RejectingMiddleware)

        @app.get("/test")
        def _test_route() -> dict:
            return {"ok": True}

        resp = TestClient(app).get("/test")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing token"

    def test_should_pass_when_middleware_accepts_token(self) -> None:
        app = FastAPI()

        class RejectingMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
                token = request.headers.get("Authorization")
                if not token:
                    return JSONResponse(status_code=401, content={"detail": "Missing token"})
                return await call_next(request)

        app.add_middleware(RejectingMiddleware)

        @app.get("/test")
        def _test_route() -> dict:
            return {"ok": True}

        resp = TestClient(app).get("/test", headers={"Authorization": "Bearer test-jwt"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
