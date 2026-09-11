"""Per-instance endpoint headers — the header-routed-fleet seam.

A provider that gives each sandbox its own URL is fully described by
``base_url``. One that fronts its whole fleet behind a SINGLE gateway domain
and picks the instance by request header (Volcengine veFaaS:
``X-Faas-Instance-Name``) is not: drop the header and the call lands on an
arbitrary instance of the same function, with no error anywhere.

So these pin three things ``base_url`` alone used to carry:

* the client cache key (``_endpoint_key``) — else every owner shares one
  cached client and therefore one sandbox;
* ``current_kernel_id`` — else a real handover reads as "same kernel" and the
  SSE tap never rebinds;
* both wire channels — REST/SSE via the httpx client defaults, and the ``run``
  WS handshake, which is hand-rolled and so has its own way to lose them.

Empty headers must stay byte-identical to the previous behavior — that is what
keeps local / seatbelt / per-URL cloud drivers untouched.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import websockets

import valuz_agent.boot.kernel  # noqa: F401  (sys.path bootstrap)

from app.schemas import MessageData, UserMessageSchema

from valuz_agent.adapters import kernel_client as kc
from valuz_agent.adapters.kernel_client_http import HttpKernelClient
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.sandbox_allocator import SandboxLease, SandboxScope
from valuz_agent.ports.sandbox_provider import SandboxEndpoint

GATEWAY = "https://fleet.apigateway.example"


def _ep(instance: str, **headers: str) -> SandboxEndpoint:
    """An endpoint on the SHARED gateway domain, named only by its headers."""
    return SandboxEndpoint(
        sandbox_id=instance, base_url=GATEWAY, token="tok", headers=dict(headers)
    )


# -- the key ---------------------------------------------------------------


def test_endpoint_key_without_headers_is_exactly_base_url() -> None:
    ep = SandboxEndpoint(sandbox_id="s", base_url="https://u1.pool", token="t")
    assert ep.headers == {}  # the default every existing driver gets
    assert kc._endpoint_key(ep) == "https://u1.pool"


def test_endpoint_key_separates_instances_behind_one_domain() -> None:
    a = _ep("a", **{"X-Faas-Instance-Name": "inst-a"})
    b = _ep("b", **{"X-Faas-Instance-Name": "inst-b"})
    assert a.base_url == b.base_url  # the whole point: one gateway
    assert kc._endpoint_key(a) != kc._endpoint_key(b)


def test_endpoint_key_is_header_order_independent() -> None:
    one = _ep("a", **{"X-A": "1", "X-B": "2"})
    two = SandboxEndpoint(
        sandbox_id="a", base_url=GATEWAY, token="tok", headers={"X-B": "2", "X-A": "1"}
    )
    assert kc._endpoint_key(one) == kc._endpoint_key(two)


# -- the client cache ------------------------------------------------------


class _FixedAllocator:
    def __init__(self, per_owner: dict[str, SandboxEndpoint]) -> None:
        self._per_owner = per_owner

    async def ensure(self, *, owner_user_id: str) -> SandboxLease:
        return SandboxLease(endpoint=self._per_owner[owner_user_id])

    async def peek(self, *, owner_user_id: str, scope: SandboxScope | None = None) -> SandboxLease:
        return SandboxLease(endpoint=self._per_owner[owner_user_id])

    async def release(self, *, owner_user_id: str) -> None:
        return None


async def test_one_domain_two_instances_get_two_clients(monkeypatch) -> None:
    monkeypatch.setattr(kc, "_endpoint_clients", {})
    monkeypatch.setattr(
        ext,
        "sandbox_allocator",
        _FixedAllocator(
            {
                "A": _ep("a", **{"X-Faas-Instance-Name": "inst-a"}),
                "B": _ep("b", **{"X-Faas-Instance-Name": "inst-b"}),
            }
        ),
    )
    a = await kc._kernel_for("A")
    b = await kc._kernel_for("B")
    assert isinstance(a, HttpKernelClient) and a is not b  # NOT one shared sandbox
    assert a is await kc._kernel_for("A")  # still cached per instance


async def test_current_kernel_id_follows_the_instance_not_the_url(monkeypatch) -> None:
    monkeypatch.setattr(kc, "_endpoint_clients", {})
    monkeypatch.setattr(kc, "_scope_cache", {})
    monkeypatch.setattr(
        ext,
        "sandbox_allocator",
        _FixedAllocator(
            {
                "A": _ep("a", **{"X-Faas-Instance-Name": "inst-a"}),
                "B": _ep("b", **{"X-Faas-Instance-Name": "inst-b"}),
            }
        ),
    )
    first = await kc.current_kernel_id("A", "sess-hdr-1")
    second = await kc.current_kernel_id("B", "sess-hdr-2")
    assert first and second and first != second


# -- the wire: REST / SSE --------------------------------------------------


class _HeaderEchoServer:
    """Minimal real HTTP server that records the headers it was sent."""

    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []
        self.port = 0
        self._srv: ThreadingHTTPServer | None = None

    def __enter__(self) -> _HeaderEchoServer:
        seen = self.seen

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
                seen.append({k.lower(): v for k, v in self.headers.items()})
                body = json.dumps({"data": {}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # silence the test log
                return

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: Any) -> None:
        assert self._srv is not None
        self._srv.shutdown()
        self._srv.server_close()


async def test_rest_requests_carry_the_extra_headers() -> None:
    with _HeaderEchoServer() as srv:
        client = HttpKernelClient(
            f"http://127.0.0.1:{srv.port}",
            token="tok",
            extra_headers={"X-Faas-Instance-Name": "inst-a"},
        )
        try:
            await client.runtime_availability()
        finally:
            await client.aclose()
    assert srv.seen and srv.seen[0]["x-faas-instance-name"] == "inst-a"
    assert srv.seen[0]["authorization"] == "Bearer tok"


async def test_extra_headers_cannot_override_the_kernel_credential() -> None:
    with _HeaderEchoServer() as srv:
        client = HttpKernelClient(
            f"http://127.0.0.1:{srv.port}",
            token="real",
            extra_headers={"Authorization": "Bearer smuggled"},
        )
        try:
            await client.runtime_availability()
        finally:
            await client.aclose()
    assert srv.seen[0]["authorization"] == "Bearer real"


# -- the wire: the run WS --------------------------------------------------


class _HandshakeRecorder:
    """Fake kernel ``/run`` channel that records the handshake headers."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.port = 0
        self._server: Any = None

    async def _handler(self, ws: Any) -> None:
        self.headers = {k.lower(): v for k, v in ws.request.headers.items()}
        await ws.recv()
        await ws.send(json.dumps({"type": "session_idle", "data": {}, "timestamp": 1}))
        try:
            await asyncio.wait_for(ws.wait_closed(), timeout=3)
        except TimeoutError:
            pass

    async def __aenter__(self) -> _HandshakeRecorder:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._server.close()
        await self._server.wait_closed()


async def test_run_turn_handshake_carries_the_extra_headers() -> None:
    async with _HandshakeRecorder() as fake:
        client = HttpKernelClient(
            f"http://127.0.0.1:{fake.port}",
            token="tok",
            extra_headers={"X-Faas-Instance-Name": "inst-a"},
        )

        async def _fake_list_messages(user_id, session_id, *, limit=50, offset=0):
            return [
                MessageData(
                    id="m-1",
                    session_id=session_id,
                    user_message=UserMessageSchema(text="hi", attachments=[]),
                    assistant_message="done",
                    status="completed",
                    total_turns=1,
                    started_at=0,
                )
            ]

        client.list_messages = _fake_list_messages  # type: ignore[method-assign]
        try:
            await client.run_turn("owner-a", "sess-1", "hi")
        finally:
            await client.aclose()

    assert fake.headers["x-faas-instance-name"] == "inst-a"
    assert fake.headers["authorization"] == "Bearer tok"
    assert fake.headers["x-valuz-owner-id"] == "owner-a"
