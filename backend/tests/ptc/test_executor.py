"""End-to-end ``execute_code``: real subprocess, real loopback HTTP, stub upstream.

The chain under test is the production one minus the external network:
generated wrapper (workspace) → stdlib client → urllib POST → uvicorn-served
``ptc_router`` → execution registry → (stubbed) upstream pool → canonical
value back into the program → only stdout returns on the ToolResult.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*/app.*
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from fastapi import FastAPI

from app import ptc_router
from src.core.agent_config import AgentConfig
from src.core.tools import ExecContext
from src.core.types import McpHttpServerConfig, Session
from src.ptc import executor as executor_mod
from src.ptc.execution_registry import _REGISTRY, reset_registry_for_tests
from src.ptc.executor import (
    PTC_ENDPOINT_ENV,
    PTC_TIMEOUT_ENV,
    build_execute_code_tool,
    eligible_ptc_servers,
)

from valuz_agent.modules.ptc.tool_generator import ToolFunctionGenerator, ToolInfo

SERVER = "testsrv"

_TOOL_SCHEMA = {
    "name": "get_data",
    "description": "Fetch one data row.",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    },
}


class FakePool:
    """Stands in for ``UpstreamPool`` — constructed by the executor itself."""

    last: FakePool | None = None

    def __init__(self, servers: dict[str, Any]) -> None:
        self.servers = servers
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False
        FakePool.last = self

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((server, tool, dict(arguments)))
        return SimpleNamespace(
            isError=False,
            structuredContent={"symbol": arguments.get("symbol"), "price": 123.4},
            content=[],
            meta=None,
        )

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(scope="module")
def loopback_port():
    """A real uvicorn serving the PTC router — subprocesses need a socket."""
    app = FastAPI()
    app.include_router(ptc_router.router)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A cwd carrying the generated PTC tools package (bare layout)."""
    generator = ToolFunctionGenerator()
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text('"""generated"""\n', encoding="utf-8")
    (tools_dir / "mcp_client.py").write_text(
        generator.generate_mcp_client_code([SERVER]), encoding="utf-8"
    )
    tool = ToolInfo.from_dict(_TOOL_SCHEMA, SERVER)
    (tools_dir / f"{SERVER}.py").write_text(
        generator.generate_tool_module(SERVER, [tool]), encoding="utf-8"
    )
    return tmp_path


def _session(workspace: Path, *, ptc_servers: list[str] | None = None) -> Session:
    metadata: dict[str, Any] = {}
    if ptc_servers is not None:
        metadata["ptc"] = {"servers": ptc_servers}
    return Session(
        id="sess-1",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(workspace),
        user_id="u1",
        mcp_servers=(
            McpHttpServerConfig(
                name=SERVER,
                url="http://upstream.invalid/mcp",
                headers={"Authorization": "Bearer SECRET-TOKEN"},
            ),
        ),
        metadata=metadata,
    )


class _Store:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        if user_id == self._session.user_id and session_id == self._session.id:
            return self._session
        return None


def _ctx(workspace: Path) -> ExecContext:
    return ExecContext(workspace=str(workspace), session_id="sess-1", user_id="u1")


@pytest.fixture()
def execute(monkeypatch, loopback_port, workspace):
    monkeypatch.setenv(PTC_ENDPOINT_ENV, f"http://127.0.0.1:{loopback_port}/kernel/v1/ptc")
    monkeypatch.setattr(executor_mod, "UpstreamPool", FakePool)
    FakePool.last = None

    async def _run(code: str, session: Session | None = None):
        sess = session if session is not None else _session(workspace, ptc_servers=[SERVER])
        tool = build_execute_code_tool(lambda: _Store(sess))
        assert tool.handler is not None
        return await tool.handler({"code": code}, _ctx(workspace))

    return _run


async def test_program_calls_tool_and_only_stdout_returns(execute, workspace):
    code = (
        "import json\n"
        "from tools.testsrv import get_data\n"
        "row = get_data(symbol='AAPL')\n"
        "print('price=', row['price'])\n"
    )
    result = await execute(code)
    assert result.is_error is False, result.content
    assert result.content.startswith("SUCCESS")
    assert "price= 123.4" in result.content
    # The upstream saw the call; the subprocess never saw the credential.
    assert FakePool.last is not None
    assert FakePool.last.calls == [(SERVER, "get_data", {"symbol": "AAPL"})]
    archived = list((workspace / ".ptc" / "runs").glob("exec_*.py"))
    assert len(archived) == 1
    assert "SECRET-TOKEN" not in archived[0].read_text(encoding="utf-8")
    # Token revoked on settle.
    assert not _REGISTRY


async def test_credentials_never_reach_subprocess_env(execute, workspace):
    code = (
        "import json, os\n"
        "print(json.dumps({k: v for k, v in os.environ.items() if 'SECRET' in v}))\n"
    )
    result = await execute(code)
    assert result.is_error is False
    assert "SECRET-TOKEN" not in result.content


async def test_files_created_are_reported(execute, workspace):
    # ``.ptc/work`` is pre-created by the executor — no makedirs in agent code.
    code = "open('.ptc/work/out.json', 'w').write('{}')\nprint('done')\n"
    result = await execute(code)
    assert result.is_error is False
    assert "Files created:" in result.content
    assert ".ptc/work/out.json" in result.content
    # The archived program (written pre-snapshot) is never reported.
    assert "runs/exec_" not in result.content


async def test_failing_program_returns_error_with_traceback(execute):
    result = await execute("raise ValueError('boom-marker')\n")
    assert result.is_error is True
    assert result.content.startswith("ERROR")
    assert "boom-marker" in result.content


async def test_timeout_kills_the_program(execute, monkeypatch):
    monkeypatch.setenv(PTC_TIMEOUT_ENV, "1")
    started = time.monotonic()
    result = await execute("import time\ntime.sleep(30)\n")
    assert time.monotonic() - started < 15
    assert result.is_error is True
    assert "timed out" in result.content


async def test_session_without_ptc_servers_is_refused(execute, workspace):
    result = await execute("print('hi')\n", session=_session(workspace, ptc_servers=None))
    assert result.is_error is True
    assert "no PTC-enabled data servers" in result.content


async def test_missing_python3_is_a_clean_error(execute, monkeypatch):
    monkeypatch.setattr(executor_mod, "python3_unavailable_reason", lambda: "python3 not found")
    result = await execute("print('hi')\n")
    assert result.is_error is True
    assert "python3 not found" in result.content


def test_eligible_servers_filters_stdio_and_unlisted(workspace):
    from src.core.types import McpStdioServerConfig

    session = _session(workspace, ptc_servers=[SERVER, "stdio-one", "ghost"])
    session = Session(
        **{
            **session.__dict__,
            "mcp_servers": (
                *session.mcp_servers,
                McpStdioServerConfig(name="stdio-one", command="echo"),
            ),
        }
    )
    eligible = eligible_ptc_servers(session)
    assert set(eligible) == {SERVER}
