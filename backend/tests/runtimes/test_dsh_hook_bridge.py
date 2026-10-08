"""A real dsh session on the Valuz hook bus (end to end).

Boots one session child on the managed profile with the hook bridge armed,
serves the kernel's hook-bridge endpoint over real HTTP, and lets a fake model
call dsh's built-in ``bash``. The ``tool.call`` handler registered in this
process sees the call, and what it makes of the result is what the model gets
on its next request — or, when it answers itself, the tool never runs.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from src.core.agent_config import AgentConfig
from src.core.hooks import TOOL_CALL, SessionRef, ToolOutcome, hook_registry
from src.core.hooks.registry import SessionHooks
from src.core.hooks.remote import (
    RemoteHookSession,
    register_remote_hooks,
    unregister_remote_hooks,
)
from src.core.types import Session
from src.runtimes.deepseek_harness import composition
from src.runtimes.deepseek_harness.composition import (
    PROFILE_NAME,
    SESSION_ROLE,
    build_session_patch,
    process_env,
    write_classic_hooks,
)

from tests.runtimes.test_dsh_upstream_compat import (
    DSH_BIN,
    LAUNCHER,
    NODE,
    NODE_ENV,
)

pytestmark = pytest.mark.skipif(
    not DSH_BIN.is_file() or not LAUNCHER.is_file() or NODE is None,
    reason="vendored dsh closure not installed or no node",
)

OWNER = "test.dsh-hook-bridge"


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    hook_registry.unregister_owner(OWNER)


# -- a model that calls bash once, then answers --------------------------------


class _ToolCallingModel(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    # The tool the first response calls, and its input.
    tool: tuple[str, dict[str, Any]] = (
        "bash",
        {"command": "echo bridge-ran", "description": "Print a marker"},
    )

    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        request = json.loads(body or b"{}")
        type(self).requests.append(request)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def emit(name: str, data: dict[str, Any]) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_{len(type(self).requests)}",
                    "type": "message",
                    "role": "assistant",
                    "model": request.get("model", "fake"),
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            },
        )
        if len(type(self).requests) == 1:
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_bridge",
                        "name": type(self).tool[0],
                        "input": {},
                    },
                },
            )
            emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(type(self).tool[1]),
                    },
                },
            )
            emit("content_block_stop", {"type": "content_block_stop", "index": 0})
            stop = "tool_use"
        else:
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "done"},
                },
            )
            emit("content_block_stop", {"type": "content_block_stop", "index": 0})
            stop = "end_turn"
        emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop},
                "usage": {"output_tokens": 5},
            },
        )
        emit("message_stop", {"type": "message_stop"})


@pytest.fixture
def model_url() -> Iterator[str]:
    _ToolCallingModel.requests = []
    _ToolCallingModel.tool = (
        "bash",
        {"command": "echo bridge-ran", "description": "Print a marker"},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ToolCallingModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# -- the kernel's hook-bridge endpoint over real HTTP ----------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def bridge_base() -> Iterator[str]:
    from app.hook_bridge_router import router

    app = FastAPI()
    app.include_router(router)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/kernel/v1/hook-bridge"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _tool_results(request: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for message in request.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            results.extend(
                block
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            )
    return results


def _result_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, str):
        return content
    return " ".join(block.get("text", "") for block in content or [] if isinstance(block, dict))


def _run_turn(
    tmp_path: Path,
    model_url: str,
    bridge_base: str,
    *,
    events: tuple[str, ...] = ("tool.call",),
    mcp_servers: tuple[Any, ...] = (),
    workspace_files: dict[str, str] | None = None,
    required: bool = False,
    extra_patch: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    home = tmp_path / "dsh-home"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for rel, text in (workspace_files or {}).items():
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(text)
    session = Session(
        id="s-hook-bridge",
        agent_config=AgentConfig(id="a", name="a"),
        cwd=str(workspace),
        mcp_servers=mcp_servers,
    )
    ref = SessionRef.from_session(session)
    token = register_remote_hooks(
        RemoteHookSession(SessionHooks(hook_registry, ref), "deepseek_harness")
    )
    patch_path = tmp_path / "session.patch.json"
    patch_path.write_text(
        json.dumps(
            build_session_patch(
                session,
                model_base_url=model_url,
                hook_bridge={
                    "endpoint": f"{bridge_base}/{token}",
                    "events": list(events),
                    "required": required,
                }
                if events
                else None,
                classic_hooks=write_classic_hooks(session, tmp_path),
            )
            + (extra_patch or [])
        )
    )
    env = {
        **os.environ,
        **NODE_ENV,
        **process_env(home=home, role=SESSION_ROLE, permission_mode="full_access"),
        "DEEPSEEK_API_KEY": "sk-fake",
    }
    proc = subprocess.Popen(
        [
            NODE,
            *composition.NODE_FLAGS,
            str(LAUNCHER),
            "--profile",
            PROFILE_NAME,
            "--patch",
            str(patch_path),
        ],
        cwd=workspace,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    lines: queue.Queue = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def send(message: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        proc.stdin.flush()

    def read_until(predicate: Any, timeout: float = 120.0) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                break
            message = json.loads(line)
            seen.append(message)
            if predicate(message):
                return seen
        raise AssertionError(f"dsh did not reach the expected state; saw {seen[-5:]}")

    try:
        send(
            {
                "id": "1",
                "method": "initialize",
                "params": {
                    "cwd": str(workspace),
                    "provider": "deepseek-official",
                    "model": "deepseek-chat",
                },
            }
        )
        read_until(lambda m: m.get("id") == "1")
        if mcp_servers:
            time.sleep(4)  # MCP client connects and registers its tools
        send(
            {
                "id": "2",
                "method": "session/prompt",
                "params": {
                    "sessionId": session.id,
                    "contentBlocks": [{"type": "text", "text": "run the marker"}],
                },
            }
        )
        ended = {"done": False}

        def finished(message: dict[str, Any]) -> bool:
            event = message.get("params", {}).get("event", {})
            if event.get("type") == "turn/end":
                ended["done"] = True
            return ended["done"] and message.get("method") == "session.status"

        messages = read_until(finished)
        send({"id": "3", "method": "shutdown"})
    finally:
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        import asyncio

        asyncio.run(unregister_remote_hooks(token))
    return messages


def test_a_handler_sees_and_rewrites_a_dsh_builtin_tool_call(
    tmp_path: Path, model_url: str, bridge_base: str
) -> None:
    seen: list[tuple[str, str | None]] = []

    async def stamp(ctx, event, next_):  # noqa: ANN001
        seen.append((event.get("tool.kind"), event.get("tool.command")))
        result = await next_()
        content = list(result.content) + [{"type": "text", "text": "[seen by a Valuz hook]"}]
        return ToolOutcome(content=content, is_error=result.is_error)

    hook_registry.register(TOOL_CALL, stamp, owner=OWNER, matcher={"tool.kind": "shell"})
    _run_turn(tmp_path, model_url, bridge_base)

    assert seen == [("shell", "echo bridge-ran")]
    assert len(_ToolCallingModel.requests) >= 2, "the model was not called after the tool"
    results = _tool_results(_ToolCallingModel.requests[1])
    assert results, "the second model request carries no tool result"
    text = _result_text(results[-1])
    assert "bridge-ran" in text and "[seen by a Valuz hook]" in text


def test_a_handler_answer_replaces_the_call(
    tmp_path: Path, model_url: str, bridge_base: str
) -> None:
    async def refuse(ctx, event, next_):  # noqa: ANN001
        return ToolOutcome(content="shell is off today (Valuz hook)", is_error=True, executed=False)

    hook_registry.register(TOOL_CALL, refuse, owner=OWNER, matcher={"tool": "bash"})
    _run_turn(tmp_path, model_url, bridge_base)

    results = _tool_results(_ToolCallingModel.requests[1])
    text = _result_text(results[-1])
    assert "shell is off today (Valuz hook)" in text
    assert "bridge-ran" not in text


def test_tool_check_sees_and_can_deny_a_dsh_mcp_tool(
    tmp_path: Path, model_url: str, bridge_base: str
) -> None:
    """MCP tools skip the bridge's tool.call (the kernel MCP proxy owns that),
    but their approval step is the bus's tool.check like any other tool's."""
    import sys

    from src.core.hooks import TOOL_CHECK, ToolDecision
    from src.core.types import McpStdioServerConfig

    seen: list[tuple[str, str | None, str]] = []

    async def no_mcp(ctx, event, next_):  # noqa: ANN001
        seen.append((event.get("tool.source"), event.get("tool.server"), event.get("tool.name")))
        return ToolDecision(behavior="deny", reason="mcp is off today (Valuz hook)")

    hook_registry.register(TOOL_CHECK, no_mcp, owner=OWNER, matcher={"tool.source": "mcp"})
    _ToolCallingModel.tool = ("mcp__upstream__echo", {"text": "hi"})
    fixture = Path(__file__).parent / "fixtures" / "hook_bus_mcp_server.py"
    _run_turn(
        tmp_path,
        model_url,
        bridge_base,
        events=("tool.call", "tool.check"),
        mcp_servers=(
            McpStdioServerConfig(name="upstream", command=sys.executable, args=[str(fixture)]),
        ),
    )

    assert seen == [("mcp", "upstream", "mcp__upstream__echo")]
    results = _tool_results(_ToolCallingModel.requests[1])
    text = _result_text(results[-1])
    assert "mcp is off today (Valuz hook)" in text
    assert "upstream:hi" not in text


def test_dsh_runs_the_workspace_claude_hooks_with_its_own_bridge(
    tmp_path: Path, model_url: str, bridge_base: str
) -> None:
    """A trusted workspace's ``.claude/settings.json`` hooks run in DSH through
    DSH's own ``dsh-hooks-claude-code`` plugin; the ``Bash`` matcher selects
    DSH's ``bash`` tool."""
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo 'no shell here (workspace hook)' >&2; exit 2",
                        }
                    ],
                }
            ]
        }
    }
    _run_turn(
        tmp_path,
        model_url,
        bridge_base,
        events=(),
        workspace_files={".claude/settings.json": json.dumps(settings)},
    )

    results = _tool_results(_ToolCallingModel.requests[1])
    text = _result_text(results[-1])
    assert "no shell here (workspace hook)" in text
    assert "bridge-ran" not in text


def test_required_bridge_failure_never_executes_native_bash(tmp_path: Path, model_url: str) -> None:
    """Real dsh pre-execute must deny a down required transport, even full access."""
    effect = tmp_path / "should-not-exist"
    _ToolCallingModel.tool = (
        "bash",
        {"command": f"printf executed > {effect}", "description": "Synthetic effect"},
    )
    _run_turn(tmp_path, model_url, "http://127.0.0.1:1/unavailable", required=True)
    assert not effect.exists()
    assert "required execution guard unavailable" in _result_text(
        _tool_results(_ToolCallingModel.requests[1])[-1]
    )


def test_required_native_guard_denies_before_real_dsh_effect(
    tmp_path: Path, model_url: str, bridge_base: str
) -> None:
    async def deny(ctx: Any, event: Any, next_: Any) -> ToolOutcome:
        return ToolOutcome(content="exact action approval required", is_error=True, executed=False)

    hook_registry.register(
        TOOL_CALL, deny, owner=OWNER, tier="builtin", fail_closed=True, required=True
    )
    effect = tmp_path / "guarded-effect"
    _ToolCallingModel.tool = (
        "bash",
        {"command": f"printf executed > {effect}", "description": "Synthetic effect"},
    )
    _run_turn(tmp_path, model_url, bridge_base, required=True)
    assert not effect.exists()
    assert "exact action approval required" in _result_text(
        _tool_results(_ToolCallingModel.requests[1])[-1]
    )


def test_required_monotonic_guard_still_denies_short_circuited_pre_waterfall(
    tmp_path: Path, model_url: str, bridge_base: str
) -> None:
    plugin = tmp_path / "short-circuit.mjs"
    plugin.write_text(
        'export function apply(ctx) { '
        'ctx.on("tools/pre-execute", async (exec, next) => ({ kind: "allow" })); }'
    )
    effect = tmp_path / "must-not-exist"
    _ToolCallingModel.tool = (
        "bash",
        {"command": f"printf executed > {effect}", "description": "Synthetic effect"},
    )
    _run_turn(
        tmp_path,
        model_url,
        bridge_base,
        required=True,
        extra_patch=[{"insert": [{"id": "synthetic-short-circuit", "name": str(plugin)}]}],
    )
    assert not effect.exists()
