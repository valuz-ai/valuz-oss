"""OAuth Client ID Metadata Documents for MCP connectors.

Authorization servers such as Binance Agent OS have no dynamic registration
endpoint and instead accept an HTTPS URL as ``client_id`` from which they
fetch the client's metadata. Covers: discovery propagates the capability
flag; the client_id helper only fires when the server supports it and the
deployment publishes an https document with a path; the served document has
the required shape; the settings parser accepts comma and JSON lists.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import valuz_agent.integrations.connector_oauth as co
from valuz_agent.infra.config import Settings, settings
from valuz_agent.integrations.connector_oauth import (
    OAuthDiscoverHelper,
    OauthMetadata,
    build_client_metadata_document,
    client_id_from_metadata_document,
)

_SERVER_URL = "https://agent.example/mcp/agentic"
_DOC_URL = "https://api.valuz.example/agent/v1/connectors/oauth/client-metadata"


async def _make_helper(handler) -> OAuthDiscoverHelper:  # noqa: ANN001
    helper = OAuthDiscoverHelper(_SERVER_URL)
    await helper._client.aclose()
    helper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return helper


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(co.asyncio, "sleep", _no_sleep)


@pytest.mark.asyncio
async def test_discovery_propagates_client_id_metadata_document_support() -> None:
    """A Binance-shaped authorization server: PKCE public client, no
    registration endpoint, ``client_id_metadata_document_supported``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/agentic":
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer resource_metadata="https://agent.example/.well-known/oauth-protected-resource"'
                    )
                },
            )
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={"resource": _SERVER_URL, "authorization_servers": ["https://agent.example"]},
            )
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://agent.example",
                    "authorization_endpoint": "https://accounts.example/agentic-oauth/authorize",
                    "token_endpoint": "https://accounts.example/oauth-agentic/token",
                    "token_endpoint_auth_methods_supported": ["none"],
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": True,
                },
            )
        return httpx.Response(404)

    helper = await _make_helper(handler)
    try:
        metadata = await helper.get_oauth_metadata()
    finally:
        await helper.close()
    assert metadata is not None
    assert metadata.client_id_metadata_document_supported is True
    assert metadata.registration_endpoint is None
    assert client_id_from_metadata_document(metadata, _DOC_URL) == _DOC_URL


def _meta(supported: bool) -> OauthMetadata:
    return OauthMetadata(
        authorization_endpoint="https://accounts.example/authorize",
        token_endpoint="https://accounts.example/token",
        client_id_metadata_document_supported=supported,
    )


def test_client_id_helper_requires_support_and_an_https_url_with_a_path() -> None:
    assert client_id_from_metadata_document(_meta(True), _DOC_URL) == _DOC_URL
    # Server does not support it → never used, even when configured.
    assert client_id_from_metadata_document(_meta(False), _DOC_URL) is None
    # Not configured → nothing to offer.
    assert client_id_from_metadata_document(_meta(True), None) is None
    assert client_id_from_metadata_document(_meta(True), "") is None
    # Spec: https scheme AND a path component; no fragment.
    assert client_id_from_metadata_document(_meta(True), "http://api.valuz.example/x.json") is None
    assert client_id_from_metadata_document(_meta(True), "https://api.valuz.example") is None
    assert client_id_from_metadata_document(_meta(True), "https://api.valuz.example/") is None
    assert client_id_from_metadata_document(_meta(True), f"{_DOC_URL}#frag") is None


def test_metadata_document_shape_and_dedupe() -> None:
    doc = build_client_metadata_document(
        client_id=_DOC_URL,
        client_name="Valuz",
        client_uri="https://valuz.example",
        redirect_uris=[
            "http://127.0.0.1:8000/v1/connectors/oauth/callback",
            "http://127.0.0.1:8000/v1/connectors/oauth/callback",
            "",
            "https://api.valuz.example/agent/v1/connectors/oauth/callback",
        ],
    )
    assert doc["client_id"] == _DOC_URL
    assert doc["client_name"] == "Valuz" and doc["client_uri"] == "https://valuz.example"
    assert doc["redirect_uris"] == [
        "http://127.0.0.1:8000/v1/connectors/oauth/callback",
        "https://api.valuz.example/agent/v1/connectors/oauth/callback",
    ]
    assert doc["token_endpoint_auth_method"] == "none"
    assert doc["grant_types"] == ["authorization_code", "refresh_token"]
    assert doc["response_types"] == ["code"]
    assert "client_uri" not in build_client_metadata_document(
        client_id=_DOC_URL, client_name="Valuz", redirect_uris=[]
    )


def test_settings_parse_redirect_uris_from_comma_or_json() -> None:
    comma = Settings(
        oauth_client_metadata_redirect_uris=" http://127.0.0.1:8000/cb, https://x/cb ,http://127.0.0.1:8000/cb"
    )
    assert comma.oauth_client_metadata_redirect_uris == ["http://127.0.0.1:8000/cb", "https://x/cb"]
    as_json = Settings(oauth_client_metadata_redirect_uris='["https://a/cb", "https://b/cb"]')
    assert as_json.oauth_client_metadata_redirect_uris == ["https://a/cb", "https://b/cb"]
    assert (
        Settings(oauth_client_metadata_redirect_uris="").oauth_client_metadata_redirect_uris == []
    )


def _client() -> TestClient:
    from valuz_agent.api.routes.connectors import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_public_route_serves_the_document_for_the_configured_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "backend_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "oauth_client_metadata_url", _DOC_URL)
    monkeypatch.setattr(
        settings,
        "oauth_client_metadata_redirect_uris",
        ["https://api.valuz.example/agent/v1/connectors/oauth/callback"],
    )
    monkeypatch.setattr(settings, "oauth_client_name", "Valuz Finance")
    monkeypatch.setattr(settings, "oauth_client_uri", "https://valuz.example")

    r = _client().get("/v1/connectors/oauth/client-metadata")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=3600"
    doc = r.json()
    assert doc["client_id"] == _DOC_URL
    assert doc["client_name"] == "Valuz Finance"
    assert doc["redirect_uris"] == [
        "http://127.0.0.1:8000/v1/connectors/oauth/callback",
        "https://api.valuz.example/agent/v1/connectors/oauth/callback",
    ]


def test_public_route_falls_back_to_its_own_url_as_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "backend_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "oauth_client_metadata_url", None)
    monkeypatch.setattr(settings, "oauth_client_metadata_redirect_uris", [])
    r = _client().get("/v1/connectors/oauth/client-metadata?x=1")
    assert r.status_code == 200
    assert r.json()["client_id"] == "http://testserver/v1/connectors/oauth/client-metadata"
    assert r.json()["redirect_uris"] == ["http://127.0.0.1:8000/v1/connectors/oauth/callback"]
