"""Tests for the authentication extension points (Slice 2).

Verifies that:
1. OSS mode → all requests resolve to the local install identity
2. Custom IdentityResolver injection works
3. get_current_user returns ANONYMOUS when resolver returns None
4. Auth middleware injection returns 401 on missing token
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from valuz_agent.api.deps import get_current_user, set_identity_resolver
from valuz_agent.integrations.identity_local import LocalIdentityResolver
from valuz_agent.ports.identity import ANONYMOUS, UserIdentity


class TestLocalIdentityResolver:
    def test_returns_local_install_identity(self) -> None:
        from valuz_agent.infra.local_identity import resolve_local_user_id

        resolver = LocalIdentityResolver()
        result = resolver.resolve(MagicMock())
        # OSS resolves every request to the device-derived local install id
        # (stamped on every row's ``user_id`` column), not the ANONYMOUS literal.
        assert result.user_id == resolve_local_user_id()
        assert result.user_id.startswith("local-")
        assert result.org_id is None


class TestGetCurrentUser:
    def setup_method(self) -> None:
        set_identity_resolver(LocalIdentityResolver())

    def teardown_method(self) -> None:
        set_identity_resolver(LocalIdentityResolver())

    def test_default_returns_local_install_user(self) -> None:
        from valuz_agent.infra.local_identity import resolve_local_user_id

        request = MagicMock()
        user = get_current_user(request)
        assert user.user_id == resolve_local_user_id()
        assert user.org_id is None

    def test_custom_resolver_injection(self) -> None:
        class MockResolver:
            def resolve(self, request: object) -> UserIdentity:
                return UserIdentity(
                    user_id="test-user",
                    email="test@example.com",
                    org_id="org-1",
                    roles=["admin"],
                )

        set_identity_resolver(MockResolver())
        request = MagicMock()
        user = get_current_user(request)
        assert user.user_id == "test-user"
        assert user.org_id == "org-1"
        assert user.email == "test@example.com"

    def test_resolver_returning_none_falls_back_to_anonymous(self) -> None:
        class NoneResolver:
            def resolve(self, request: object) -> UserIdentity | None:
                return None

        set_identity_resolver(NoneResolver())
        request = MagicMock()
        user = get_current_user(request)
        assert user is ANONYMOUS


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
                    return JSONResponse(
                        status_code=401, content={"detail": "Missing token"}
                    )
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
                    return JSONResponse(
                        status_code=401, content={"detail": "Missing token"}
                    )
                return await call_next(request)

        app.add_middleware(AuthMiddleware)

        @app.get("/test")
        def _test_route() -> dict:
            return {"ok": True}

        client = TestClient(app)

        resp = client.get("/test", headers={"Authorization": "Bearer test-jwt"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_should_work_with_identity_resolver_and_middleware(self) -> None:
        """Full integration: middleware validates token, resolver extracts identity."""

        class JWTResolver:
            def resolve(self, request: object) -> UserIdentity | None:
                auth = getattr(request, "headers", {}).get("Authorization", "")
                if auth.startswith("Bearer "):
                    return UserIdentity(
                        user_id="jwt-user", org_id="org-jwt"
                    )
                return None

        set_identity_resolver(JWTResolver())
        try:
            request = MagicMock()
            request.headers = {"Authorization": "Bearer valid-token"}
            user = get_current_user(request)
            assert user.user_id == "jwt-user"
            assert user.org_id == "org-jwt"

            request_no_token = MagicMock()
            request_no_token.headers = {}
            user2 = get_current_user(request_no_token)
            assert user2 is ANONYMOUS
        finally:
            set_identity_resolver(LocalIdentityResolver())
