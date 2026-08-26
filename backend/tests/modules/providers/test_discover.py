"""Tests for valuz_agent.modules.channels.discover.

Async tests run via ``asyncio.run`` rather than pytest-asyncio to avoid
adding a new test dependency to the backend.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

import httpx
import pytest
from src.runtimes.network_egress import configure_network_egress, get_network_egress_registry

from valuz_agent.modules.providers import discover as discover_module
from valuz_agent.modules.providers.discover import (
    DiscoveredModel,
    ModelDiscoveryError,
    discover_model_entries,
    discover_models,
    ping_credentials,
)


def _client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_should_return_sorted_unique_model_ids_when_openai_responds_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-4o-mini"},
                    {"id": "gpt-4o"},
                ],
            },
        )

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    assert _run(go()) == ["gpt-4o", "gpt-4o-mini"]


def test_should_preserve_upstream_display_name_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "kimi-for-coding", "display_name": "K2.7 Code"},
                ],
            },
        )

    async def go() -> list[DiscoveredModel]:
        async with _client_with(handler) as client:
            return await discover_model_entries(
                base_url="https://api.kimi.com/coding/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    models = _run(go())
    assert isinstance(models, list)
    assert len(models) == 1
    model = models[0]
    assert model.id == "kimi-for-coding"
    assert model.label == "K2.7 Code"


def test_should_strip_date_suffix_from_anthropic_model_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sk-ant-test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-sonnet-4-6-20251015"},
                    {"id": "claude-haiku-4-5"},
                ]
            },
        )

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.anthropic.com",
                api_key="sk-ant-test",
                protocol="anthropic",
                client=client,
            )

    assert _run(go()) == ["claude-haiku-4-5", "claude-sonnet-4-6"]


def test_should_try_v1_models_when_base_url_lacks_v1() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/models":
            return httpx.Response(404)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-5"}]})
        return httpx.Response(500)

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://proxy.example.com",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    assert _run(go()) == ["gpt-5"]
    assert seen_paths == ["/models", "/v1/models"]


def test_should_skip_v1_fallback_when_base_already_has_v1() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(404)

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError):
        _run(go())
    assert seen_paths == ["/v1/models"]


def test_should_raise_auth_failed_on_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="bad-key",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError) as exc:
        _run(go())
    assert "API Key 无效" in exc.value.reason


def test_should_raise_when_no_endpoint_returns_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://proxy.example.com",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError) as exc:
        _run(go())
    assert "未提供" in exc.value.reason and "模型列表" in exc.value.reason


def test_should_raise_on_empty_data_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": []})

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError) as exc:
        _run(go())
    assert "未返回任何可用模型" in exc.value.reason


def test_should_skip_items_without_id_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o"},
                    {"object": "model"},
                    {"id": ""},
                    {"id": "gpt-5"},
                ]
            },
        )

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    assert _run(go()) == ["gpt-4o", "gpt-5"]


def test_should_raise_on_non_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError) as exc:
        _run(go())
    reason = exc.value.reason
    assert "格式异常" in reason or "未提供" in reason


def test_should_raise_on_500_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server"})

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError) as exc:
        _run(go())
    assert "500" in exc.value.reason


def test_should_raise_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    async def go() -> list[str]:
        async with _client_with(handler) as client:
            return await discover_models(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                protocol="openai",
                client=client,
            )

    with pytest.raises(ModelDiscoveryError) as exc:
        _run(go())
    assert "超时" in exc.value.reason


def test_connection_ping_leases_and_revokes_the_explicit_egress_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    control_paths: list[tuple[str, str]] = []

    async def fake_ping_openai(**kwargs: object) -> None:
        observed.update(kwargs)

    async def go() -> None:
        configure_network_egress(
            {
                "mode": "auto",
                "controlEndpoint": "http://127.0.0.1:43123",
                "bootstrapToken": "x" * 43,
                "expiresAt": int(time.time() * 1000) + 60_000,
            }
        )
        registry = get_network_egress_registry()
        assert registry is not None

        async def handler(request: httpx.Request) -> httpx.Response:
            control_paths.append((request.method, request.url.path))
            if request.method == "POST":
                client_id = json.loads(request.content)["clientId"]
                return httpx.Response(
                    201,
                    json={
                        "kind": "forward_proxy",
                        "proxyUrl": "http://runtime:secret@127.0.0.1:43212",
                        "clientId": client_id,
                        "expiresAt": int(time.time() * 1000) + 60_000,
                    },
                )
            return httpx.Response(200, json={"revoked": True})

        registry._client = httpx.AsyncClient(  # noqa: SLF001 - control seam under test
            base_url=registry.bootstrap.control_endpoint,
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        monkeypatch.setattr(discover_module, "_ping_openai", fake_ping_openai)
        try:
            await ping_credentials(
                base_url="https://api.example/v1",
                api_key="sk-test",
                protocol="openai",
                model="test-model",
            )
        finally:
            await registry.close()
            configure_network_egress(None)

    _run(go())
    assert observed["proxy_url"] == "http://runtime:secret@127.0.0.1:43212"
    assert control_paths[0] == ("POST", "/v1/clients/forward-proxy")
    assert control_paths[1][0] == "DELETE"
    assert control_paths[1][1].startswith("/v1/clients/")


def test_connection_ping_surfaces_egress_failure_as_user_facing_error() -> None:
    async def go() -> None:
        configure_network_egress(None, required_unavailable=True)
        try:
            await ping_credentials(
                base_url="https://api.example/v1",
                api_key="sk-test",
                protocol="openai",
                model="test-model",
            )
        finally:
            configure_network_egress(None)

    with pytest.raises(ModelDiscoveryError, match="模型网络出口不可用"):
        _run(go())
