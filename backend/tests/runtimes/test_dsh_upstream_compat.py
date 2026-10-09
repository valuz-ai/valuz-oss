"""Compatibility with the pinned upstream dsh distribution — the real closure.

These are the guards ``scripts/dsh-upstream-sync.sh`` runs after moving the
pin to a new dsh release. They need the vendored closure installed
(``bash scripts/vendor-dsh-runtime.sh``) and Node; otherwise they skip.

* pins move together — closure dependency, bundle peer, installed version;
* every upstream row id the Valuz bundle (or the session patch) addresses
  still exists upstream — a renamed/removed row would otherwise be a silent
  no-op patch;
* one real session turn against a local fake model: the agent joins the
  profile's preset (tools), live stream frames reach the kernel, Valuz
  instructions land in the system prompt, and nothing is shipped to the
  vendor besides the model request;
* dsh's native addon accepts the runtime, and the desktop pins the exact
  Electron it needs.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from src.core.agent_config import AgentConfig
from src.core.types import Session
from src.runtimes.deepseek_harness import composition
from src.runtimes.deepseek_harness.composition import (
    PROFILE_NAME,
    SESSION_ROLE,
    VALUZ_BUNDLE,
    build_session_patch,
    process_env,
)

VENDOR = composition._VENDOR_DIR
NODE_MODULES = VENDOR / "node_modules"
DSH_BIN = NODE_MODULES / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
LAUNCHER = NODE_MODULES / VALUZ_BUNDLE / "bin" / "dsh.mjs"
BUNDLE_SRC = VENDOR / "valuz-plugins" / VALUZ_BUNDLE
FIXTURES = Path(__file__).parent / "fixtures"
#: The desktop app whose Electron the packaged build runs dsh under.
DESKTOP_PACKAGE = Path(__file__).resolve().parents[3] / "frontend/apps/desktop/package.json"
MANAGED = json.loads((BUNDLE_SRC / "managed-profile.json").read_text(encoding="utf-8"))
MANAGED_BUNDLES = MANAGED["bundles"]


def profile_dir(home: Path) -> Path:
    return home / "profiles" / PROFILE_NAME


# The same Node the runtime spawns: VALUZ_NODE_PATH (+ VALUZ_NODE_IS_ELECTRON=1)
# when set — run this suite with them pointing at the desktop's Electron binary
# to cover the packaged carrier — else node from PATH.
_RESOLVED_NODE = composition._resolve_node()
NODE = _RESOLVED_NODE[0] if _RESOLVED_NODE else None
NODE_ENV: dict[str, str] = _RESOLVED_NODE[1] if _RESOLVED_NODE else {}

pytestmark = pytest.mark.skipif(
    not DSH_BIN.is_file() or not LAUNCHER.is_file() or NODE is None,
    reason="vendored dsh closure not installed (bash scripts/vendor-dsh-runtime.sh) or no node",
)

#: Tools the shipped ``standard`` preset must hand a Valuz session.
CORE_TOOLS = {
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "todo_write",
    "skill",
    "ask_user_question",
    "exit_plan_mode",
}

_STUBS_SOURCE = (BUNDLE_SRC / "lib" / "session-tool-stubs.js").read_text(encoding="utf-8")
#: Names valuz-session-tool-stubs stands in for (read from the plugin itself).
STUBBED_TOOLS = set(
    re.findall(r'"([a-z_]+)"', re.search(r"STUBBED_TOOLS = \[([^\]]*)\]", _STUBS_SOURCE)[1])
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_pins_move_together() -> None:
    pinned = _json(VENDOR / "package.json")["dependencies"]["@deepseek-ai/dsh"]
    peer = _json(BUNDLE_SRC / "package.json")["peerDependencies"]["@deepseek-ai/dsh"]
    installed = _json(NODE_MODULES / "@deepseek-ai" / "dsh" / "package.json")["version"]
    assert pinned == peer == installed, (pinned, peer, installed)
    # The renderer's Cordis must be the one dsh itself depends on (dsh client
    # plugins share it through the module table) — the sync script moves it.
    dsh_cordis = _json(NODE_MODULES / "@deepseek-ai" / "dsh" / "package.json")["dependencies"][
        "@deepseek-ai/cordis"
    ]
    core = Path(__file__).resolve().parents[3] / "frontend" / "packages" / "core" / "package.json"
    frontend_cordis = _json(core)["dependencies"]["@deepseek-ai/cordis"]
    assert frontend_cordis == dsh_cordis, (frontend_cordis, dsh_cordis)


def _dump_ids(home: Path, bundles: list[str]) -> set[str]:
    """Row ids of a profile composed from ``bundles`` (``dsh --dump-config``)."""
    directory = profile_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps({"name": "probe", "private": True, "dsh": {"profile": {"bundles": bundles}}})
    )
    (directory / "cordis.patch.yml").write_text("[]\n")
    out = subprocess.run(
        [NODE, *composition.NODE_FLAGS, str(DSH_BIN), "--profile", PROFILE_NAME, "--dump-config"],
        env={**os.environ, **NODE_ENV, "DSH_HOME": str(home), "VALUZ_DSH_ROLE": SESSION_ROLE},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    return set(re.findall(r"^\s*- id: ([A-Za-z0-9._-]+)\s*$", out, flags=re.MULTILINE))


def _bundle_override_ids() -> set[str]:
    """Upstream ids the Valuz bundle patch addresses (not its own inserts)."""
    text = (BUNDLE_SRC / "cordis.patch.yml").read_text(encoding="utf-8")
    top_level = re.findall(r"^- id: ([A-Za-z0-9._-]+)\s*$", text, flags=re.MULTILINE)
    return set(top_level)


def test_bundle_addresses_only_rows_upstream_still_ships(tmp_path: Path) -> None:
    upstream = _dump_ids(tmp_path / "home", [b for b in MANAGED_BUNDLES if b != VALUZ_BUNDLE])
    session_patch_ids = {
        entry["id"]
        for entry in build_session_patch(
            Session(id="s", agent_config=AgentConfig(id="a", name="a"), cwd="/tmp"),
            model_base_url="http://example.invalid",
        )
        if "id" in entry and not entry["id"].startswith("valuz-")
    }
    missing = (_bundle_override_ids() | session_patch_ids) - upstream
    assert not missing, (
        f"upstream dsh no longer ships row(s) {sorted(missing)} — update "
        "valuz-dsh-bundle/cordis.patch.yml / composition.build_session_patch"
    )


def _launcher_dump(home: Path) -> subprocess.CompletedProcess[str]:
    """Run the launcher in dump mode — it initializes the managed profile first."""
    return subprocess.run(
        [NODE, *composition.NODE_FLAGS, str(LAUNCHER), "--profile", PROFILE_NAME, "--dump-config"],
        env={
            **NODE_ENV,
            **os.environ,
            **process_env(home=home, role=SESSION_ROLE),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def test_launcher_initializes_the_managed_profile(tmp_path: Path) -> None:
    home = tmp_path / "dsh-home"
    out = _launcher_dump(home).stdout
    manifest = _json(profile_dir(home) / "package.json")
    assert manifest["dsh"]["profile"]["bundles"] == MANAGED_BUNDLES
    assert (profile_dir(home) / "cordis.patch.yml").exists()
    assert "nodeLinker: hoisted" in (profile_dir(home) / "pnpm-workspace.yaml").read_text()
    # The composed tree carries the Valuz rows, anchored beside the bundle.
    assert "id: valuz-kernel-bridge" in out


def test_launcher_keeps_user_bundles_and_restores_managed_ones(tmp_path: Path) -> None:
    home = tmp_path / "dsh-home"
    directory = profile_dir(home)
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps(
            {
                "name": "dsh-profile-valuz",
                "private": True,
                "dependencies": {},
                # A user dropped one of ours and selected their own (uninstalled
                # here, so dsh skips it with a warning — the list is what counts).
                "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "dsh-user-bundle"]}},
            }
        )
    )
    (directory / "cordis.patch.yml").write_text("- id: hmr\n  disabled: true\n")
    _launcher_dump(home)
    manifest = _json(directory / "package.json")
    assert manifest["dsh"]["profile"]["bundles"] == [*MANAGED_BUNDLES, "dsh-user-bundle"]
    assert (directory / "cordis.patch.yml").read_text() == "- id: hmr\n  disabled: true\n"


SPAWN_MARKER = "SPAWN-SUBAGENT"


def _spawn_request(request: dict[str, Any]) -> bool:
    """The root's first request in a subagent turn: no tool result yet."""
    messages = json.dumps(request.get("messages", []))
    return SPAWN_MARKER in messages and "tool_result" not in messages


class _FakeModel(BaseHTTPRequestHandler):
    """Anthropic Messages SSE fake: one short streamed text reply, or — for a
    prompt carrying ``SPAWN_MARKER`` — one ``subagent`` call first."""

    requests: list[dict[str, Any]] = []

    def log_message(self, *args: Any) -> None:  # noqa: D401 — silence
        pass

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        request = json.loads(body or b"{}")
        type(self).requests.append({"path": self.path, **request})
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
                    "id": "msg_fake",
                    "type": "message",
                    "role": "assistant",
                    "model": request.get("model", "fake"),
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            },
        )
        if _spawn_request(request):
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_fake_subagent",
                        "name": "subagent",
                        "input": {},
                    },
                },
            )
            child = {"description": "child check", "prompt": "Reply with ok."}
            emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(child)},
                },
            )
            emit("content_block_stop", {"type": "content_block_stop", "index": 0})
            emit(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 5},
                },
            )
            emit("message_stop", {"type": "message_stop"})
            return
        emit(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        for piece in ("Hello", " from", " the", " fake", " model."):
            emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )
        emit("content_block_stop", {"type": "content_block_stop", "index": 0})
        emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 5},
            },
        )
        emit("message_stop", {"type": "message_stop"})


@pytest.fixture
def fake_model():
    _FakeModel.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _reader(stream, sink: queue.Queue) -> None:
    for line in stream:
        sink.put(line)
    sink.put(None)


def _run_session_turn(
    home: Path,
    workspace: Path,
    session: Session,
    model_url: str,
    patch_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
    prompt: str = "say hi",
    model_requests: int = 0,
) -> list[dict[str, Any]]:
    """Boot one session child on the managed profile and run one prompt."""
    patch_path.write_text(json.dumps(build_session_patch(session, model_base_url=model_url)))
    env = {
        **os.environ,
        **NODE_ENV,
        **process_env(home=home, role=SESSION_ROLE, permission_mode="full_access"),
        "DEEPSEEK_API_KEY": "sk-fake",
        **(extra_env or {}),
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
    threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True).start()

    def send(message: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        proc.stdin.flush()

    def read_until(predicate, timeout: float = 90.0) -> list[dict[str, Any]]:
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
        send(
            {
                "id": "2",
                "method": "session/prompt",
                "params": {
                    "sessionId": session.id,
                    "contentBlocks": [{"type": "text", "text": prompt}],
                },
            }
        )
        turn_ended = {"done": False}

        def finished(message: dict[str, Any]) -> bool:
            event = message.get("params", {}).get("event", {})
            if event.get("type") == "turn/end":
                turn_ended["done"] = True
            return turn_ended["done"] and message.get("method") == "session.status"

        messages = read_until(finished)
        # Background work (a spawned subagent) may call the model after the
        # root's turn ended; keep the child alive until it has.
        deadline = time.monotonic() + 60
        while len(_FakeModel.requests) < model_requests and time.monotonic() < deadline:
            time.sleep(0.2)
        send({"id": "3", "method": "shutdown"})
    finally:
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return messages


def _request_tools(request: dict[str, Any]) -> set[str]:
    return {tool["name"] for tool in request.get("tools", [])}


def test_one_real_session_turn(tmp_path: Path, fake_model: str) -> None:
    home = tmp_path / "dsh-home"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    session = Session(
        id="s-compat",
        agent_config=AgentConfig(id="a", name="a"),
        cwd=str(workspace),
        instructions="VALUZ-INSTRUCTIONS-MARKER: you are a research analyst.",
    )
    messages = _run_session_turn(
        home, workspace, session, fake_model, tmp_path / "session.patch.json"
    )

    events = [m["params"]["event"] for m in messages if m.get("method") == "session.event"]
    types = [e["type"] for e in events]
    assert "agent-preset/selected" in types, types
    assert not any(t.startswith("session-log-deepseek/") for t in types), types
    message = next(e for e in events if e["type"] == "assistant/message")
    assert message["data"]["message"]["content"][0]["text"] == "Hello from the fake model."
    end = next(e for e in events if e["type"] == "turn/end")
    assert end["data"]["reason"]["kind"] == "completed"

    frames = [m["params"]["frame"] for m in messages if m.get("method") == "valuz.assistant-stream"]
    assert any(f["type"] == "chunk" for f in frames), "stream frames must reach the kernel"

    assert _FakeModel.requests, "the model was never called"
    request = _FakeModel.requests[0]
    tools = _request_tools(request)
    assert CORE_TOOLS <= tools, sorted(CORE_TOOLS - tools)
    system = request.get("system")
    system_text = (
        system
        if isinstance(system, str)
        else " ".join(block.get("text", "") for block in (system or []))
    )
    assert "VALUZ-INSTRUCTIONS-MARKER" in system_text


def test_a_subagent_spawns_in_a_session(tmp_path: Path, fake_model: str) -> None:
    """The shipped subagent rows deny ``schedule_*``, which the session role
    never registers; without the Valuz stand-ins every spawn failed with
    ``tools.restrict() names unknown global tools``."""
    home = tmp_path / "dsh-home"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    session = Session(
        id="s-subagent",
        agent_config=AgentConfig(id="a", name="a"),
        cwd=str(workspace),
    )
    messages = _run_session_turn(
        home,
        workspace,
        session,
        fake_model,
        tmp_path / "session.patch.json",
        prompt=f"{SPAWN_MARKER}: delegate a check.",
        model_requests=3,
    )

    events = [m["params"]["event"] for m in messages if m.get("method") == "session.event"]
    result = next(
        e["data"]["message"]
        for e in events
        if e["type"] == "tool/result"
        and e["data"]["message"].get("toolCallId") == "toolu_fake_subagent"
    )
    text = json.dumps(result["content"])
    # The child composed (its toolFilter applied) and runs in the background.
    assert result["isError"] is False, text
    assert "started subagent" in text, text
    end = next(e for e in events if e["type"] == "turn/end")
    assert end["data"]["reason"]["kind"] == "completed"

    assert "subagent" in _request_tools(_FakeModel.requests[0])
    # The child itself reached the model, with the shipped filter applied.
    child = next(
        (r for r in _FakeModel.requests if "Reply with ok." in json.dumps(r.get("messages"))),
        None,
    )
    assert child is not None, f"the child never called the model ({len(_FakeModel.requests)})"
    assert {"bash", "read"} <= _request_tools(child), sorted(_request_tools(child))
    # The stand-ins never reach a model.
    for request in _FakeModel.requests:
        assert not _request_tools(request) & STUBBED_TOOLS, sorted(_request_tools(request))


def _preset_filter_names() -> set[str]:
    names: set[str] = set()
    for preset in (NODE_MODULES / "@deepseek-ai" / "dsh-web-app" / "presets").glob("*.yml"):
        lines = preset.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.strip() != "toolFilter:":
                continue
            depth = len(line) - len(line.lstrip())
            for entry in lines[index + 1 :]:
                if entry.strip() and len(entry) - len(entry.lstrip()) <= depth:
                    break
                item = re.match(r"\s*-\s*([A-Za-z0-9_]+)\s*$", entry)
                if item:
                    names.add(item.group(1))
    return names


def test_every_shipped_tool_filter_name_resolves_in_sessions() -> None:
    """``tools.restrict()`` rejects unknown names, so every name a shipped
    preset filters must be a tool a session registers. Those the session role
    never registers are stubbed by valuz-session-tool-stubs; a new one upstream
    has to be added there (or the role must start registering it)."""
    names = _preset_filter_names()
    assert names, "no toolFilter found in the shipped presets"
    unresolved = names - CORE_TOOLS - STUBBED_TOOLS
    assert not unresolved, sorted(unresolved)


def _write_skill(root: Path, name: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Marker skill {name}.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return directory


def test_user_agents_skills_stay_out_of_sessions(tmp_path: Path, fake_model: str) -> None:
    """A session's skills come from the Valuz library, materialized into
    ``<cwd>/.agents/skills``; the user's personal ``~/.agents/skills`` (dsh's
    default user root) bypasses the library's enabled state and stays out."""
    from src.runtimes.skills_materialize import prepare_codex_skills

    home = tmp_path / "dsh-home"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    user_home = tmp_path / "user-home"
    _write_skill(user_home / ".agents" / "skills", "personal-agents-skill")
    enabled = _write_skill(tmp_path / "library", "valuz-enabled-skill")
    prepare_codex_skills(str(workspace), [str(enabled)])

    session = Session(
        id="s-skills",
        agent_config=AgentConfig(id="a", name="a"),
        cwd=str(workspace),
        skills=(str(enabled),),
    )
    _run_session_turn(
        home,
        workspace,
        session,
        fake_model,
        tmp_path / "session.patch.json",
        extra_env={"HOME": str(user_home)},
    )

    assert _FakeModel.requests, "the model was never called"
    request = json.dumps(_FakeModel.requests[0])
    assert "valuz-enabled-skill" in request
    assert "personal-agents-skill" not in request


async def test_a_plugin_installed_the_dsh_way_is_live_in_sessions(
    tmp_path: Path, fake_model: str, monkeypatch
) -> None:
    """The whole compatibility promise in one test: a standard third-party dsh
    bundle, installed through dsh's own pluginManager (the resident manager
    host Valuz runs), is a tool in the next Valuz session."""
    from valuz_agent.modules.dsh_plugins.manager import DshManagerHost

    home = tmp_path / "dsh-home"
    plugin = tmp_path / "dsh-hello-tool"
    shutil.copytree(FIXTURES / "dsh-hello-tool", plugin)
    installed = _json(NODE_MODULES / "@deepseek-ai" / "dsh" / "package.json")["version"]
    manifest = _json(plugin / "package.json")
    manifest["peerDependencies"]["@deepseek-ai/dsh"] = installed
    (plugin / "package.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("VALUZ_DSH_HOME", str(home))
    monkeypatch.setenv("VALUZ_DSH_MANAGER_ENABLED", "1")
    monkeypatch.setenv("VALUZ_DSH_RUNTIME_ENTRY", str(LAUNCHER))

    manager = DshManagerHost()
    try:
        inspected = await manager.call("inspect", {"spec": str(plugin)})
        assert inspected["status"] == "accepted" and inspected["bundle"] is True
        result = await manager.call(
            "installBundle", {"spec": str(plugin), "options": {"enabled": True}}
        )
        assert result["enabled"] is True and result["packageResult"]["exitCode"] == 0
        bundles = {b["name"] for b in await manager.call("listBundles")}
        assert "dsh-hello-tool" in bundles

        # Every other runtime reaches the same tool through the backend's
        # always-on MCP server, which fronts the manager host's tool bridge.
        from valuz_agent.modules.dsh_plugins.mcp_server import (
            call_bridge_tool,
            list_bridge_tools,
        )

        deadline = time.monotonic() + 30
        bridged: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            bridged = await list_bridge_tools()
            if any(tool["name"] == "hello_valuz" for tool in bridged):
                break
            await asyncio.sleep(0.5)
        assert any(tool["name"] == "hello_valuz" for tool in bridged), bridged
        is_error, text = await call_bridge_tool("hello_valuz", {})
        assert (is_error, text) == (False, "hello from a standard dsh plugin")
    finally:
        await manager.stop()
    from valuz_agent.modules.dsh_plugins.mcp_server import read_bridge

    assert read_bridge() is None, "the bridge withdraws its endpoint when the host stops"

    workspace = tmp_path / "ws"
    workspace.mkdir()
    session = Session(id="s-plugin", agent_config=AgentConfig(id="a", name="a"), cwd=str(workspace))
    _run_session_turn(home, workspace, session, fake_model, tmp_path / "session.patch.json")
    assert _FakeModel.requests, "the model was never called"
    assert "hello_valuz" in _request_tools(_FakeModel.requests[-1])


def test_guarded_bundle_names_still_ship() -> None:
    """The manager proxy guards dsh bundles by name; a rename upstream must
    fail here instead of silently dropping the guard. Its managed set must
    also be exactly what the launcher keeps in the profile."""
    from valuz_agent.modules.dsh_plugins import manager as dsh_manager

    assert dsh_manager.MANAGED_BUNDLES == set(MANAGED_BUNDLES)
    missing = sorted(
        name
        for name in dsh_manager.MANAGED_BUNDLES | dsh_manager.SURFACE_BUNDLES
        if not (NODE_MODULES / name / "package.json").is_file()
    )
    assert missing == []


def test_native_addon_accepts_the_runtime() -> None:
    """dsh's node-addon-require-builtin fingerprints the runtime: node, or only
    the exact Electron releases it was built against. Load it the way dsh
    does — no --expose-internals — under the Node this suite resolved, so with
    VALUZ_NODE_PATH at the desktop's Electron an Electron pin that drifted
    from dsh's fails here with the addon's own message, not as a dead session."""
    probe = (
        "require('node:module').createRequire(process.argv[1])"
        "('node-addon-require-builtin').requireBuiltin('internal/modules/esm/loader')"
    )
    assert NODE is not None
    result = subprocess.run(
        [NODE, "-e", probe, str(VENDOR / "package.json")],
        env={**os.environ, **NODE_ENV},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_desktop_pins_the_exact_electron() -> None:
    """The closure runs dsh's own addon (no override), so the desktop's Electron
    must be one exact release — a range would let an install float to a patch
    release the addon refuses. It moves with the Electron dsh's desktop locks."""
    lock = json.loads((VENDOR / "package-lock.json").read_text(encoding="utf-8"))
    addon = lock["packages"]["node_modules/node-addon-require-builtin"]
    assert not addon.get("resolved", "").startswith("file:"), addon
    desktop = json.loads(DESKTOP_PACKAGE.read_text(encoding="utf-8"))
    pin = {**desktop.get("dependencies", {}), **desktop.get("devDependencies", {})}["electron"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", pin), pin
