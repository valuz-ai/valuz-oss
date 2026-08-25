"""PTC workspace client: stdlib-only, env-gated, faithful envelope handling.

The client under test is the exact source deployed into workspaces, so these
tests run it against a real local HTTP server (stdlib ``http.server``) — the
same transport path a subprocess uses, no mocking of urllib internals.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from valuz_agent.modules.ptc import client_runtime


@pytest.fixture(autouse=True)
def _fresh_config():
    yield
    client_runtime._apply_config_dict({})


class _Endpoint(BaseHTTPRequestHandler):
    """Scripted PTC endpoint: replies per the requested tool name."""

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler contract
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)  # type: ignore[attr-defined]
        tool = body["tool"]
        if tool == "ok":
            self._reply(200, {"ok": True, "value": {"rows": [1, 2, 3]}})
        elif tool == "tool_error":
            self._reply(200, {"ok": False, "error": {"code": "tool_error", "message": "bad args"}})
        elif tool == "http_403":
            self._reply(
                403, {"ok": False, "error": {"code": "server_not_allowed", "message": "nope"}}
            )
        else:
            self._reply(500, {"unexpected": True})

    def _reply(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture()
def endpoint(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Endpoint)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/call"
    monkeypatch.setenv(client_runtime._CALL_URL_ENV, url)
    yield server
    server.shutdown()
    thread.join(timeout=5)


def test_call_outside_execute_code_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(client_runtime._CALL_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match="only callable from the execute_code tool"):
        client_runtime._call_mcp_tool("srv", "ok", {})


def test_happy_path_returns_canonical_value(endpoint):
    value = client_runtime._call_mcp_tool("srv", "ok", {"symbol": "AAPL"})
    assert value == {"rows": [1, 2, 3]}
    sent = endpoint.requests[-1]  # type: ignore[attr-defined]
    assert sent == {"server": "srv", "tool": "ok", "arguments": {"symbol": "AAPL"}}


def test_tool_error_envelope_raises_tool_call_error(endpoint):
    with pytest.raises(client_runtime.ToolCallError, match="bad args") as info:
        client_runtime._call_mcp_tool("srv", "tool_error", {})
    assert info.value.server == "srv"
    assert info.value.tool == "tool_error"


def test_http_error_with_envelope_raises_tool_call_error(endpoint):
    with pytest.raises(client_runtime.ToolCallError, match="nope"):
        client_runtime._call_mcp_tool("srv", "http_403", {})


def test_unknown_server_fails_early_without_network(monkeypatch):
    monkeypatch.setenv(client_runtime._CALL_URL_ENV, "http://127.0.0.1:1/never")
    client_runtime._apply_config_dict({"servers": ["known"]})
    with pytest.raises(client_runtime.ToolCallError, match="not part of this workspace"):
        client_runtime._call_mcp_tool("other", "ok", {})


def test_endpoint_unreachable_is_tool_call_error(monkeypatch):
    monkeypatch.setenv(client_runtime._CALL_URL_ENV, "http://127.0.0.1:9/call")
    with pytest.raises(client_runtime.ToolCallError, match="unreachable"):
        client_runtime._call_mcp_tool("srv", "ok", {})
