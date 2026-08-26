from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from src.runtimes.network_egress import (
    EgressRegistrationError,
    NetworkEgressRegistry,
    _parse_bootstrap,
    configure_network_egress,
    consume_network_egress_bootstrap,
    desktop_control_authorized,
    get_network_egress_registry,
    provider_test_egress,
)

import kernel  # noqa: F401
from valuz_agent.boot.steps import initialize_network_egress


def _bootstrap() -> dict[str, object]:
    return {
        "mode": "auto",
        "controlEndpoint": "http://127.0.0.1:43123",
        "bootstrapToken": "x" * 43,
        "expiresAt": int(time.time() * 1000) + 60_000,
    }


def test_stdin_bootstrap_is_consumed_and_marker_removed() -> None:
    env = {"VALUZ_EGRESS_BOOTSTRAP_STDIN": "1", "KEEP": "yes"}
    assert consume_network_egress_bootstrap(
        environ=env,
        stdin=io.StringIO(json.dumps(_bootstrap()) + "\n"),
    )
    assert env == {"KEEP": "yes"}
    assert get_network_egress_registry() is not None
    configure_network_egress(None)


def test_desktop_bootstrap_installs_control_capability_even_when_egress_is_off() -> None:
    token = "desktop-control-" + "x" * 32
    env = {"VALUZ_DESKTOP_BOOTSTRAP_STDIN": "1", "KEEP": "yes"}
    assert consume_network_egress_bootstrap(
        environ=env,
        stdin=io.StringIO(
            json.dumps(
                {
                    "version": 1,
                    "desktopControlToken": token,
                    "egressBootstrap": None,
                    "egressRequired": False,
                }
            )
            + "\n"
        ),
    )
    assert env == {"KEEP": "yes"}
    assert desktop_control_authorized(token)
    assert not desktop_control_authorized("wrong-token")
    assert get_network_egress_registry() is None


def test_private_dev_bootstrap_file_is_consumed_and_deleted(tmp_path: Path) -> None:
    # pytest's temp parent can be group/world visible; the launcher-owned
    # rendezvous directory itself must be private.
    root = tmp_path / "egress"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    bootstrap_file = root / "bootstrap.json"
    bootstrap_file.write_text(json.dumps(_bootstrap()) + "\n")
    os.chmod(bootstrap_file, 0o600)
    env = {"VALUZ_EGRESS_BOOTSTRAP_FILE": str(bootstrap_file), "KEEP": "yes"}

    assert consume_network_egress_bootstrap(environ=env, stdin=io.StringIO(""))
    assert env == {"KEEP": "yes"}
    assert not bootstrap_file.exists()
    assert get_network_egress_registry() is not None
    configure_network_egress(None)


def test_control_endpoint_must_be_loopback() -> None:
    payload = _bootstrap()
    payload["controlEndpoint"] = "https://control.example:443"
    with pytest.raises(ValueError, match="invalid_egress_control_endpoint"):
        _parse_bootstrap(payload)


@pytest.mark.asyncio
async def test_required_but_unavailable_marker_fails_loud_and_is_removed() -> None:
    env = {"VALUZ_EGRESS_REQUIRED": "1", "KEEP": "yes"}
    assert consume_network_egress_bootstrap(environ=env, stdin=io.StringIO(""))
    assert env == {"KEEP": "yes"}
    with pytest.raises(EgressRegistrationError, match="egress_manager_unavailable"):
        async with provider_test_egress():
            pass
    configure_network_egress(None)


@pytest.mark.asyncio
async def test_invalid_bootstrap_keeps_backend_available_but_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_bootstrap() -> bool:
        raise RuntimeError("invalid_egress_bootstrap_payload")

    monkeypatch.setattr(
        "src.runtimes.network_egress.consume_network_egress_bootstrap",
        reject_bootstrap,
    )
    initialize_network_egress()
    try:
        with pytest.raises(EgressRegistrationError, match="egress_manager_unavailable"):
            async with provider_test_egress():
                pass
    finally:
        configure_network_egress(None)


@pytest.mark.asyncio
async def test_registry_validates_descriptor_identity_without_logging_secrets() -> None:
    registry = NetworkEgressRegistry(_parse_bootstrap(_bootstrap()))

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "kind": "model_ingress",
                "baseUrl": "http://127.0.0.1:43210/_valuz/egress/token/v1",
                "clientId": body["clientId"],
                "expiresAt": int(time.time() * 1000) + 60_000,
                "supportsWebSocket": True,
            },
        )

    registry._client = httpx.AsyncClient(  # noqa: SLF001 - transport seam under test
        base_url=registry.bootstrap.control_endpoint,
        transport=httpx.MockTransport(handler),
        headers={"authorization": "Bearer hidden"},
        trust_env=False,
    )
    descriptor = await registry.register_model_ingress(
        "runtime-key",
        runtime="claude",
        upstream_base_url="https://api.example/v1",
        supports_websocket=True,
    )
    assert descriptor.client_id
    assert descriptor.base_url.startswith("http://127.0.0.1:")
    await registry.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"kind": "forward_proxy"},
        {"baseUrl": "https://127.0.0.1:43210/_valuz/egress/token/v1"},
        {"baseUrl": "http://127.0.0.1:43210/"},
        {"expiresAt": 0},
        {"supportsWebSocket": "yes"},
        {"supportsWebSocket": False},
    ],
    ids=["kind", "tls", "missing-capability", "expiry", "ws-type", "ws-mismatch"],
)
async def test_registry_rejects_invalid_model_ingress_descriptors(
    override: dict[str, object],
) -> None:
    registry = NetworkEgressRegistry(_parse_bootstrap(_bootstrap()))

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload: dict[str, object] = {
            "kind": "model_ingress",
            "baseUrl": "http://127.0.0.1:43210/_valuz/egress/token/v1",
            "clientId": body["clientId"],
            "expiresAt": int(time.time() * 1000) + 60_000,
            "supportsWebSocket": True,
        }
        payload.update(override)
        return httpx.Response(201, json=payload)

    registry._client = httpx.AsyncClient(  # noqa: SLF001 - transport seam under test
        base_url=registry.bootstrap.control_endpoint,
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    with pytest.raises(EgressRegistrationError, match="invalid_model_ingress_descriptor"):
        await registry.register_model_ingress(
            "runtime-key",
            runtime="codex",
            upstream_base_url="https://api.example/v1",
            supports_websocket=True,
        )
    await registry.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"kind": "model_ingress"},
        {"proxyUrl": "http://runtime:secret@proxy.example:43211"},
        {"proxyUrl": "http://127.0.0.1:43211"},
        {"proxyUrl": "http://runtime:secret@127.0.0.1:43211/not-a-proxy-root"},
        {"expiresAt": True},
    ],
    ids=["kind", "remote-host", "missing-auth", "path", "expiry-type"],
)
async def test_registry_rejects_invalid_forward_proxy_descriptors(
    override: dict[str, object],
) -> None:
    registry = NetworkEgressRegistry(_parse_bootstrap(_bootstrap()))

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload: dict[str, object] = {
            "kind": "forward_proxy",
            "proxyUrl": "http://runtime:secret@127.0.0.1:43211",
            "clientId": body["clientId"],
            "expiresAt": int(time.time() * 1000) + 60_000,
        }
        payload.update(override)
        return httpx.Response(201, json=payload)

    registry._client = httpx.AsyncClient(  # noqa: SLF001 - transport seam under test
        base_url=registry.bootstrap.control_endpoint,
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    with pytest.raises(EgressRegistrationError, match="invalid_forward_proxy_descriptor"):
        await registry.register_forward_proxy("runtime-key", runtime="deepagents")
    await registry.close()


@pytest.mark.asyncio
async def test_registry_renews_bootstrap_and_active_descriptor_leases() -> None:
    registry = NetworkEgressRegistry(_parse_bootstrap(_bootstrap()))
    renewed_expiry = int(time.time() * 1000) + 120_000

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/v1/clients/forward-proxy":
            return httpx.Response(
                201,
                json={
                    "kind": "forward_proxy",
                    "proxyUrl": "http://runtime:secret@127.0.0.1:43211",
                    "clientId": body["clientId"],
                    "expiresAt": int(time.time() * 1000) + 60_000,
                },
            )
        assert request.url.path == "/v1/lease/renew"
        assert body["clientIds"] == [
            registry._descriptors["runtime-key"].client_id  # noqa: SLF001
        ]
        return httpx.Response(200, json={"expiresAt": renewed_expiry})

    registry._client = httpx.AsyncClient(  # noqa: SLF001 - transport seam under test
        base_url=registry.bootstrap.control_endpoint,
        transport=httpx.MockTransport(handler),
        headers={"authorization": "Bearer hidden"},
        trust_env=False,
    )
    await registry.register_forward_proxy("runtime-key", runtime="deepagents")

    await registry.renew_lease()

    assert registry.bootstrap.expires_at == renewed_expiry
    assert registry._descriptors["runtime-key"].expires_at == renewed_expiry  # noqa: SLF001
    await registry.close()
