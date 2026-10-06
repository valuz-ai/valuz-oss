"""Connector tools API against a real MCP server (docs task card 04 §D).

A tiny FastMCP server (``tiny_mcp_server.py``) runs as a subprocess — streamable
HTTP on a random port, and stdio — and is registered as a connector row. The
routes are exercised through ASGI: list, call, the write guard, ``allow_write``,
per-user isolation, the deployment gate for stdio, and the probe that shares the
same session code.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Side-effect import — kernel ``app``/``src`` on sys.path before the connectors
# routes reach the mcp resolver.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes import connectors as routes
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors import mcp_client
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.connectors.service import ConnectorService

SERVER = Path(__file__).parent / "tiny_mcp_server.py"
USER_A = "user-A"
USER_B = "user-B"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def http_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """``(url, write_log)`` of a streamable-HTTP MCP server on a random port."""
    port = _free_port()
    write_log = tmp_path_factory.mktemp("tiny-mcp") / "writes.log"
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "http", str(port)],
        env={**os.environ, "TINY_MCP_WRITE_LOG": str(write_log)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("tiny MCP server did not start") from None
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp", write_log
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@dataclass
class Harness:
    client: httpx.AsyncClient
    sessions: Any
    state: dict[str, str]

    async def add(self, owner: str, slug: str, **fields: Any) -> str:
        args = fields.pop("args", None)
        row = ConnectorRow(
            slug=slug,
            display_name=slug,
            connector_type="custom",
            auth_type="none",
            transport=fields.pop("transport", "http"),
            enabled=fields.pop("enabled", True),
            args=json.dumps(args) if args is not None else None,
            **fields,
        )
        async with self.sessions() as db:
            await ConnectorDatastore(db).create(owner, row)
            return str(row.id)

    def service(self, db: Any) -> ConnectorService:
        return ConnectorService(datastore=ConnectorDatastore(db))


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    db_file = tmp_path / "connectors.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[ConnectorRow.__table__, ConnectorAttrRow.__table__, ConnectorOAuthRow.__table__],
    )
    sync_engine.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    sessions = async_sessionmaker(bind=engine, expire_on_commit=False)
    state = {"user": USER_A}

    async def service() -> AsyncIterator[ConnectorService]:
        async with sessions() as db:
            yield ConnectorService(datastore=ConnectorDatastore(db))

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: state["user"]
    app.dependency_overrides[routes._get_service] = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60
    ) as client:
        yield Harness(client, sessions, state)
    await engine.dispose()


# ── list ─────────────────────────────────────────────────────────────────


async def test_lists_tools_with_schemas_annotations_and_read_only(
    harness: Harness, http_server: tuple[str, Path]
) -> None:
    url, _ = http_server
    connector_id = await harness.add(USER_A, "tiny", url=url)

    by_id = await harness.client.get(f"/v1/connectors/{connector_id}/tools")
    by_slug = await harness.client.get("/v1/connectors/tiny/tools")
    assert by_id.status_code == 200, by_id.text
    assert by_slug.json() == by_id.json()

    body = by_id.json()
    assert body["connector_id"] == connector_id
    tools = {t["name"]: t for t in body["tools"]}
    assert set(tools) == {"add", "write_note", "drop_everything", "boom", "slow"}
    add = tools["add"]
    assert add["description"] == "Add two integers."
    assert set(add["input_schema"]["properties"]) == {"a", "b"}
    assert add["annotations"]["readOnlyHint"] is True
    assert add["read_only"] is True
    # No annotation, and an explicit readOnlyHint=False, are both writes (fail-closed).
    assert tools["write_note"]["read_only"] is False
    assert tools["write_note"]["annotations"] == {}
    assert tools["drop_everything"]["read_only"] is False
    assert tools["drop_everything"]["annotations"]["destructiveHint"] is True


# ── call ─────────────────────────────────────────────────────────────────


async def test_calls_a_read_only_tool(harness: Harness, http_server: tuple[str, Path]) -> None:
    url, _ = http_server
    await harness.add(USER_A, "tiny", url=url)

    res = await harness.client.post(
        "/v1/connectors/tiny/tools/add/call", json={"arguments": {"a": 2, "b": 3}}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_error"] is False
    assert body["content"][0]["type"] == "text"
    assert body["content"][0]["text"] == "5"
    assert body["structured_content"] == {"result": 5}


async def test_a_failing_tool_is_a_200_with_is_error(
    harness: Harness, http_server: tuple[str, Path]
) -> None:
    url, _ = http_server
    await harness.add(USER_A, "tiny", url=url)

    res = await harness.client.post("/v1/connectors/tiny/tools/boom/call", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_error"] is True
    assert "kaboom" in body["content"][0]["text"]


@pytest.mark.parametrize("tool", ["write_note", "drop_everything"])
async def test_write_tool_needs_allow_write_and_does_not_run_without_it(
    harness: Harness, http_server: tuple[str, Path], tool: str
) -> None:
    url, write_log = http_server
    write_log.unlink(missing_ok=True)
    await harness.add(USER_A, "tiny", url=url)

    refused = await harness.client.post(
        f"/v1/connectors/tiny/tools/{tool}/call", json={"arguments": {"text": "nope"}}
    )
    assert refused.status_code == 403
    assert refused.json() == {"detail": {"code": "write_tool_requires_confirmation"}}
    assert not write_log.exists(), "a refused write tool must never reach the server"

    explicit_false = await harness.client.post(
        f"/v1/connectors/tiny/tools/{tool}/call",
        json={"arguments": {"text": "nope"}, "allow_write": False},
    )
    assert explicit_false.status_code == 403


async def test_write_tool_runs_with_allow_write(
    harness: Harness, http_server: tuple[str, Path]
) -> None:
    url, write_log = http_server
    write_log.unlink(missing_ok=True)
    await harness.add(USER_A, "tiny", url=url)

    res = await harness.client.post(
        "/v1/connectors/tiny/tools/write_note/call",
        json={"arguments": {"text": "hello"}, "allow_write": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["content"][0]["text"] == "written"
    assert write_log.read_text(encoding="utf-8") == "hello\n"

    # allow_write on a read-only tool is simply ignored.
    ok = await harness.client.post(
        "/v1/connectors/tiny/tools/add/call",
        json={"arguments": {"a": 1, "b": 1}, "allow_write": True},
    )
    assert ok.status_code == 200 and ok.json()["structured_content"] == {"result": 2}


# ── not found / disabled / isolation ─────────────────────────────────────


async def test_unknown_tool_unknown_connector_and_disabled(
    harness: Harness, http_server: tuple[str, Path]
) -> None:
    url, _ = http_server
    await harness.add(USER_A, "tiny", url=url)
    await harness.add(USER_A, "off", url=url, enabled=False)

    unknown_tool = await harness.client.post("/v1/connectors/tiny/tools/nope/call", json={})
    assert unknown_tool.status_code == 404
    assert unknown_tool.json()["detail"]["code"] == "tool_not_found"

    for path in ("/v1/connectors/ghost/tools", "/v1/connectors/ghost/tools/add/call"):
        res = await (
            harness.client.get(path)
            if path.endswith("/tools")
            else harness.client.post(path, json={})
        )
        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "connector_not_found"

    disabled_list = await harness.client.get("/v1/connectors/off/tools")
    disabled_call = await harness.client.post("/v1/connectors/off/tools/add/call", json={})
    assert disabled_list.status_code == disabled_call.status_code == 409
    assert disabled_call.json()["detail"]["code"] == "connector_disabled"


async def test_another_users_connector_is_not_found(
    harness: Harness, http_server: tuple[str, Path]
) -> None:
    url, _ = http_server
    connector_id = await harness.add(USER_A, "tiny", url=url)

    harness.state["user"] = USER_B
    by_slug = await harness.client.get("/v1/connectors/tiny/tools")
    by_id = await harness.client.post(
        f"/v1/connectors/{connector_id}/tools/add/call", json={"arguments": {"a": 1, "b": 2}}
    )
    assert by_slug.status_code == 404
    assert by_id.status_code == 404


async def test_unreachable_connector_is_a_502_without_leaking_the_url(
    harness: Harness,
) -> None:
    port = _free_port()  # nothing listens here
    await harness.add(USER_A, "dead", url=f"http://127.0.0.1:{port}/mcp?api_key=SECRET123")

    res = await harness.client.get("/v1/connectors/dead/tools")
    assert res.status_code == 502
    assert res.json()["detail"]["code"] == "connector_unreachable"
    assert "SECRET123" not in res.text


# ── timeout ──────────────────────────────────────────────────────────────


async def test_a_slow_tool_times_out(harness: Harness, http_server: tuple[str, Path]) -> None:
    url, _ = http_server
    await harness.add(USER_A, "tiny", url=url)
    async with harness.sessions() as db:
        started = time.monotonic()
        with pytest.raises(mcp_client.ConnectorTimeoutError):
            await mcp_client.call_connector_tool(
                harness.service(db), USER_A, "tiny", "slow", {"seconds": 10}, timeout=1.0
            )
        assert time.monotonic() - started < 8


# ── stdio ────────────────────────────────────────────────────────────────


async def test_stdio_connector_on_a_local_deployment(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "deployment_type", "local")
    monkeypatch.setattr(mcp_client, "_detect_shell_path", lambda default: default)
    await harness.add(
        USER_A, "tiny-stdio", transport="stdio", command=sys.executable, args=[str(SERVER), "stdio"]
    )

    listed = await harness.client.get("/v1/connectors/tiny-stdio/tools")
    assert listed.status_code == 200, listed.text
    assert {t["name"] for t in listed.json()["tools"]} >= {"add", "write_note"}

    called = await harness.client.post(
        "/v1/connectors/tiny-stdio/tools/add/call", json={"arguments": {"a": 20, "b": 22}}
    )
    assert called.status_code == 200, called.text
    assert called.json()["structured_content"] == {"result": 42}


async def test_stdio_connector_is_refused_off_a_local_deployment(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    await harness.add(
        USER_A, "tiny-stdio", transport="stdio", command=sys.executable, args=[str(SERVER), "stdio"]
    )

    listed = await harness.client.get("/v1/connectors/tiny-stdio/tools")
    called = await harness.client.post("/v1/connectors/tiny-stdio/tools/add/call", json={})
    assert listed.status_code == called.status_code == 403
    assert called.json()["detail"]["code"] == "stdio_unavailable"


# ── the probe still works on the shared session code ─────────────────────


async def test_probe_counts_tools_and_records_the_result(
    harness: Harness, http_server: tuple[str, Path]
) -> None:
    url, _ = http_server
    connector_id = await harness.add(USER_A, "tiny", url=url)
    async with harness.sessions() as db:
        result = await routes._probe_connector(connector_id, harness.service(db), USER_A)
    assert result.ok is True
    assert result.tool_count == 5
    assert sorted(result.tools) == ["add", "boom", "drop_everything", "slow", "write_note"]
    async with harness.sessions() as db:
        row = await ConnectorDatastore(db).get_by_id(USER_A, connector_id)
        assert row is not None and row.status == "connected" and row.tool_count == 5


async def test_probe_reports_config_gaps_and_failures(harness: Harness) -> None:
    no_url = await harness.add(USER_A, "no-url")
    no_command = await harness.add(USER_A, "no-command", transport="stdio")
    dead = await harness.add(USER_A, "dead", url=f"http://127.0.0.1:{_free_port()}/mcp")
    async with harness.sessions() as db:
        svc = harness.service(db)
        missing = await routes._probe_connector("nope", svc, USER_A)
        assert (missing.ok, missing.error) == (False, "Connector not found")
        assert (await routes._probe_connector(no_url, svc, USER_A)).error == (
            "Connector has no URL configured"
        )
        assert (await routes._probe_connector(no_command, svc, USER_A)).error == (
            "Stdio connector has no command configured"
        )
        failed = await routes._probe_connector(dead, svc, USER_A)
        assert failed.ok is False and failed.error
    async with harness.sessions() as db:
        row = await ConnectorDatastore(db).get_by_id(USER_A, dead)
        assert row is not None and row.status == "error"
