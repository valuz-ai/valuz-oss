"""OAuth discovery and authorization URL compatibility tests."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.shared.auth import OAuthClientMetadata
from mcp.shared.auth import OAuthMetadata as SDKOAuthMetadata

import valuz_agent.integrations.connector_oauth as co
from valuz_agent.integrations.connector_oauth import McpOauthHelper, OAuthDiscoverHelper

_SERVER_URL = "https://broker.example/mcp"


def test_legacy_string_issuer_retains_every_sdk_metadata_field():
    assert co.McpOAuthMetadata.model_fields.keys() == SDKOAuthMetadata.model_fields.keys()
    for name, field in SDKOAuthMetadata.model_fields.items():
        if name != "issuer":
            assert co.McpOAuthMetadata.model_fields[name].annotation == field.annotation
            assert co.McpOAuthMetadata.model_fields[name].default == field.default
    value = co.McpOAuthMetadata.model_validate_json(
        json.dumps(
            {
                "issuer": "legacy-broker-identifier",
                "authorization_endpoint": "https://broker.example/authorize",
                "token_endpoint": "https://broker.example/token",
                "client_id_metadata_document_supported": True,
                "response_modes_supported": ["query"],
            }
        )
    )
    assert value.issuer == "legacy-broker-identifier"
    assert value.client_id_metadata_document_supported is True
    assert value.response_modes_supported == ["query"]


async def test_discovery_retains_legacy_issuer_and_sdk_response_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/.well-known/oauth-authorization-server" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "issuer": "legacy-broker-identifier",
                    "authorization_endpoint": "https://broker.example/authorize",
                    "token_endpoint": "https://broker.example/token",
                    "registration_endpoint": "https://broker.example/register",
                    "scopes_supported": ["read"],
                    "grant_types_supported": ["authorization_code"],
                    "client_id_metadata_document_supported": True,
                },
            )
        return httpx.Response(404)

    helper = await _make_helper(handler)
    try:
        value = await helper.get_oauth_metadata()
    finally:
        await helper.close()
    assert value is not None
    assert value.scopes_supported == ["read"]
    assert value.grant_types_supported == ["authorization_code"]
    assert value.registration_endpoint == "https://broker.example/register"
    assert value.client_id_metadata_document_supported is True


async def test_missing_redirect_uri_fails_before_token_network_request():
    helper = McpOauthHelper(
        server_url=_SERVER_URL,
        client_metadata=OAuthClientMetadata(redirect_uris=None),
        authorization_endpoint="https://broker.example/authorize",
        token_endpoint="https://broker.example/token",
    )
    try:
        with pytest.raises(ValueError, match="redirect URI"):
            await helper.get_authorization_url()
        with pytest.raises(ValueError, match="redirect URI"):
            await helper.get_oauth_token("code", "verifier")
    finally:
        await helper.close()


async def _make_helper(handler) -> OAuthDiscoverHelper:
    helper = OAuthDiscoverHelper(_SERVER_URL)
    await helper._client.aclose()
    helper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return helper


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(co.asyncio, "sleep", _no_sleep)


@pytest.mark.asyncio
async def test_discovery_uses_protected_resource_scopes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer resource_metadata="https://broker.example/mcp/metadata"'
                    )
                },
            )
        if request.url.path == "/mcp/metadata":
            return httpx.Response(
                200,
                json={
                    "resource": _SERVER_URL,
                    "authorization_servers": ["https://broker.example/oauth2"],
                    "scopes_supported": ["mcp.read", "mcp.write"],
                },
            )
        if request.url.path == "/.well-known/oauth-authorization-server/oauth2":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://broker.example",
                    "authorization_endpoint": "https://broker.example/oauth2/authorize",
                    "token_endpoint": "https://broker.example/oauth2/token",
                    "registration_endpoint": "https://broker.example/oauth2/register",
                    "scopes_supported": ["openid", "profile", "mcp.read", "mcp.write"],
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                },
            )
        return httpx.Response(404)

    helper = await _make_helper(handler)
    try:
        metadata = await helper.get_oauth_metadata()
    finally:
        await helper.close()

    assert metadata is not None
    assert metadata.scopes_supported == ["mcp.read", "mcp.write"]


@pytest.mark.asyncio
async def test_discovery_retries_transient_broker_gateway_rejection() -> None:
    attempts = {"authorization_metadata": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(401)
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(404)
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(404)
        if request.url.path == "/.well-known/oauth-authorization-server/mcp":
            attempts["authorization_metadata"] += 1
            if attempts["authorization_metadata"] < 3:
                return httpx.Response(403)
            return httpx.Response(
                200,
                json={
                    "issuer": "https://broker.example",
                    "authorization_endpoint": "https://broker.example/authorize",
                    "token_endpoint": "https://broker.example/token",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                },
            )
        return httpx.Response(404)

    helper = await _make_helper(handler)
    try:
        metadata = await helper.get_oauth_metadata()
    finally:
        await helper.close()

    assert metadata is not None
    assert attempts["authorization_metadata"] == 3


@pytest.mark.asyncio
async def test_authorization_url_includes_discovered_scopes() -> None:
    client_metadata = OAuthClientMetadata(
        client_name="Valuz",
        redirect_uris=["http://127.0.0.1:8000/v1/connectors/oauth/callback"],
        grant_types=["authorization_code"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="account:read order:read",
    )
    helper = McpOauthHelper(
        server_url=_SERVER_URL,
        client_metadata=client_metadata,
        authorization_endpoint="https://broker.example/authorize",
        token_endpoint="https://broker.example/token",
        resource="https://broker.example",
        client_id="client-1",
    )
    try:
        authorization_url, _state, _verifier = await helper.get_authorization_url()
    finally:
        await helper.close()

    query = parse_qs(urlparse(authorization_url).query)
    assert query["scope"] == ["account:read order:read"]


@pytest.mark.asyncio
async def test_client_registration_retries_explicit_gateway_rejection() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/register"
        assert request.headers["user-agent"] == "Valuz/1.0"
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(403, text="gateway rejected request")
        return httpx.Response(
            201,
            json={
                "client_id": "client-1",
                "redirect_uris": ["http://127.0.0.1:8000/v1/connectors/oauth/callback"],
            },
        )

    client_metadata = OAuthClientMetadata(
        client_name="Valuz",
        redirect_uris=["http://127.0.0.1:8000/v1/connectors/oauth/callback"],
        grant_types=["authorization_code"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    helper = McpOauthHelper(
        server_url=_SERVER_URL,
        client_metadata=client_metadata,
        authorization_endpoint="https://broker.example/authorize",
        token_endpoint="https://broker.example/token",
        registration_endpoint="https://broker.example/register",
    )
    await helper._client.aclose()
    helper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = await helper.register_client(
            ["http://127.0.0.1:8000/v1/connectors/oauth/callback"]
        )
    finally:
        await helper.close()

    assert client.client_id == "client-1"
    assert attempts["count"] == 3
