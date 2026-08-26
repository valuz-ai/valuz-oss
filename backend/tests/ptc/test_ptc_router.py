"""PTC loopback router: token auth, allowlist, budget, envelopes, trace."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*/app.*
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from fastapi import FastAPI

from app.ptc_router import router
from app.routes import KERNEL_API_PREFIX
from src.core.types import McpHttpServerConfig
from src.ptc.execution_registry import (
    register_execution,
    reset_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


class FakePool:
    """Scripted upstream: returns canned CallToolResult-shaped objects."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.raise_exc: Exception | None = None
        self.result: Any = SimpleNamespace(
            isError=False,
            structuredContent={"result": {"rows": [1, 2]}},
            content=[],
            meta={"dev.valuz/source-metadata": {"resources": []}},
        )

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((server, tool, dict(arguments)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result

    async def close(self) -> None:
        pass


def _register(max_sub_calls: int = 200) -> tuple[Any, FakePool]:
    record = register_execution(
        session_id="sess-1",
        user_id="u1",
        cwd="/tmp/ws",
        servers={"srv": McpHttpServerConfig(name="srv", url="http://upstream.invalid/mcp")},
        max_sub_calls=max_sub_calls,
    )
    pool = FakePool()
    record.upstream_pool = pool
    return record, pool


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _url(token: str) -> str:
    return f"{KERNEL_API_PREFIX}/v1/ptc/exec/{token}/call"


async def test_unknown_token_is_404():
    async with _client() as client:
        reply = await client.post(_url("nope"), json={"server": "srv", "tool": "t"})
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "unknown_execution"


async def test_server_outside_allowlist_is_403():
    record, pool = _register()
    async with _client() as client:
        reply = await client.post(_url(record.token), json={"server": "other", "tool": "t"})
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "server_not_allowed"
    assert pool.calls == []


async def test_budget_exhaustion_is_429():
    record, _pool = _register(max_sub_calls=1)
    async with _client() as client:
        first = await client.post(_url(record.token), json={"server": "srv", "tool": "t"})
        second = await client.post(_url(record.token), json={"server": "srv", "tool": "t"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "sub_call_budget_exhausted"


async def test_success_unwraps_value_and_records_trace():
    record, pool = _register()
    async with _client() as client:
        reply = await client.post(
            _url(record.token),
            json={"server": "srv", "tool": "get_rows", "arguments": {"a": 1}},
        )
    assert reply.status_code == 200
    assert reply.json() == {"ok": True, "value": {"rows": [1, 2]}}
    assert pool.calls == [("srv", "get_rows", {"a": 1})]
    assert len(record.trace) == 1
    entry = record.trace[0]
    assert entry.server == "srv" and entry.tool == "get_rows"
    assert entry.is_error is False
    assert entry.result_sha256 and entry.result_size
    assert entry.source_metadata == {"resources": []}


async def test_tool_error_result_is_ok_false_envelope():
    record, pool = _register()
    pool.result = SimpleNamespace(
        isError=True,
        structuredContent=None,
        content=[SimpleNamespace(type="text", text="bad symbol")],
        meta=None,
    )
    async with _client() as client:
        reply = await client.post(_url(record.token), json={"server": "srv", "tool": "t"})
    assert reply.status_code == 200
    body = reply.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool_error"
    assert "bad symbol" in body["error"]["message"]
    # Errors are traced (for the log) but never fingerprinted.
    assert record.trace[0].is_error is True
    assert record.trace[0].result_sha256 is None
    assert record.trace[0].source_metadata is None


async def test_upstream_exception_is_502():
    record, pool = _register()
    pool.raise_exc = RuntimeError("connection refused")
    async with _client() as client:
        reply = await client.post(_url(record.token), json={"server": "srv", "tool": "t"})
    assert reply.status_code == 502
    assert reply.json()["error"]["code"] == "upstream_error"
    assert "connection refused" in reply.json()["error"]["message"]
    assert record.trace == []  # nothing observed, nothing traced
