"""DeepSeekHarnessRuntime end-to-end turns against the fake dsh server.

The fake server (``dsh_fake_server.py``) speaks the real stdio JSON-RPC
protocol; ``launch_spec`` injection points the runtime at it, so these tests
exercise the full path: composition write → spawn → initialize →
session/prompt → notification consumption → event mapping → stop reason →
transcript sidecar — with no Node or network dependency.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import EndTurn, Error, ModelProvider, Session, UserMessage
from src.runtimes.deepseek_harness.composition import DshLaunchSpec
from src.runtimes.deepseek_harness.runtime import DeepSeekHarnessRuntime

FAKE_SERVER = str(Path(__file__).parent / "dsh_fake_server.py")


class _CollectSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]


def _session(session_id: str = "s-dsh", cwd: str = "/tmp") -> Session:
    return Session(
        id=session_id,
        agent_config=AgentConfig(id="a", name="a"),
        cwd=cwd,
        runtime_provider="deepseek_harness",
        model="deepseek-v4-flash",
        model_provider=ModelProvider(api_key="k", api_protocol="openai_completion"),
    )


def _runtime(
    tmp_path: Path, sink: _CollectSink, *, mode: str = "ok"
) -> DeepSeekHarnessRuntime:
    return DeepSeekHarnessRuntime(
        AgentConfig(id="a", name="a"),
        "deepseek-v4-flash",
        sink,
        workspace_root=str(tmp_path / "ws"),
        model_provider=ModelProvider(api_key="k", api_protocol="openai_completion"),
        state_dir=str(tmp_path / "state"),
        launch_spec=DshLaunchSpec(
            argv=(sys.executable, FAKE_SERVER),
            cwd=None,
            config_parent_dir=str(tmp_path / "cfg"),
        ),
    )


@pytest.fixture(autouse=True)
def _fake_mode_env(monkeypatch):
    monkeypatch.setenv("FAKE_DSH_MODE", "ok")
    yield


@pytest.mark.asyncio
async def test_completed_turn_maps_events_and_stop_reason(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    session = _session()
    try:
        await runtime.run(session, UserMessage(text="what is 6*7?"))
    finally:
        await runtime.close()

    assert session.status == "idle"
    assert isinstance(session.stop_reason, EndTurn)
    types = sink.types()
    assert types.count("text_delta") == 2
    assert "assistant_message" in types
    assert types[-1] == "session_idle"
    idle = sink.events[-1]
    assert idle.data["stop_reason"] == {"type": "end_turn"}

    usage = next(e for e in sink.events if e.type == "usage_update")
    # dsh counts are disjoint already: inputTokens IS the uncached portion.
    assert usage.data["input_tokens"] == 10
    assert usage.data["output_tokens"] == 2
    assert usage.data["cache_read_tokens"] == 3
    assert "deepseek-v4-flash" in usage.data["model_usage"]

    anchor = runtime.consume_turn_anchor()
    assert anchor is not None
    assert anchor["provider"] == "deepseek_harness"
    assert anchor["seq"] == 99
    assert anchor["native_session_id"] == session.runtime_session_id
    assert runtime.consume_turn_anchor() is None  # read-and-clear

    transcript = tmp_path / "state" / session.id / "transcript.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert [entry["role"] for entry in lines] == ["user", "assistant"]
    assert lines[1]["text"] == "42"


@pytest.mark.asyncio
async def test_megabyte_frame_survives_the_reader(tmp_path: Path, monkeypatch) -> None:
    """asyncio's 64 KiB readline default must not apply — real dsh frames
    (request/header with full prompt + tool schemas) exceed it routinely."""
    monkeypatch.setenv("FAKE_DSH_MODE", "bigframe")
    (tmp_path / "ws").mkdir()
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    session = _session()
    try:
        await runtime.run(session, UserMessage(text="hi"))
    finally:
        await runtime.close()

    assert isinstance(session.stop_reason, EndTurn)
    text = next(e.data["text"] for e in sink.events if e.type == "assistant_message")
    assert len(text) > 1_000_000


@pytest.mark.asyncio
async def test_error_turn_surfaces_session_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_DSH_MODE", "error")
    (tmp_path / "ws").mkdir()
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    session = _session()
    try:
        await runtime.run(session, UserMessage(text="hi"))
    finally:
        await runtime.close()

    assert isinstance(session.stop_reason, Error)
    assert session.stop_reason.category == "execution_error"
    assert "no such model" in session.stop_reason.message
    assert "session_error" in sink.types()


@pytest.mark.asyncio
async def test_interrupt_kills_process_and_settles_user_interrupt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FAKE_DSH_MODE", "hang")
    (tmp_path / "ws").mkdir()
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    session = _session()
    try:
        run_task = asyncio.create_task(runtime.run(session, UserMessage(text="hi")))
        # Let the turn reach its hung state (prompt acknowledged, no idle).
        for _ in range(200):
            if "turn_phase" in sink.types() and any(
                e.type == "turn_phase" and e.data.get("phase") == "dispatch"
                for e in sink.events
            ):
                break
            await asyncio.sleep(0.02)
        await runtime.interrupt()
        await asyncio.wait_for(run_task, timeout=5)
    finally:
        await runtime.close()

    assert session.status == "idle"
    assert isinstance(session.stop_reason, Error)
    assert session.stop_reason.category == "user_interrupt"


@pytest.mark.asyncio
async def test_replay_block_prepended_after_process_restart(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    session = _session()
    try:
        await runtime.run(session, UserMessage(text="remember the word PINEAPPLE"))
        first_native = session.runtime_session_id
        # Simulate an eviction/restart: close the process, keep the state dir.
        await runtime.close()

        sink2 = _CollectSink()
        runtime2 = _runtime(tmp_path, sink2)
        runtime2.update_sink(sink2)
        replay = runtime2._build_replay_block(session)
        assert replay is not None
        assert "PINEAPPLE" in replay
        assert "<conversation-history>" in replay

        await runtime2.run(session, UserMessage(text="what word?"))
        # A fresh process gets a fresh native session id.
        assert session.runtime_session_id != first_native
        await runtime2.close()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_capability_drift_respawns_the_process(tmp_path: Path) -> None:
    """A changed MCP credential (pre-turn re-stamp) must reach the live
    subprocess: the composition fingerprint drifts and the runtime respawns
    with a fresh composition instead of serving stale headers forever."""
    from src.core.types import McpHttpServerConfig

    (tmp_path / "ws").mkdir()
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    session = _session()
    session.mcp_servers = (
        McpHttpServerConfig(name="harness", url="http://x/mcp", headers={"X-Valuz-Internal": "t1"}),
    )
    try:
        await runtime.run(session, UserMessage(text="one"))
        first_native = session.runtime_session_id

        # Same capability state → same process (native continuation).
        await runtime.run(session, UserMessage(text="two"))
        assert session.runtime_session_id == first_native

        # Rotated credential → respawn with a fresh composition.
        session.mcp_servers = (
            McpHttpServerConfig(
                name="harness", url="http://x/mcp", headers={"X-Valuz-Internal": "t2"}
            ),
        )
        await runtime.run(session, UserMessage(text="three"))
        second_native = session.runtime_session_id
        assert second_native != first_native

        # Live-reconciled effort (PATCH /effort mutates session.model_settings
        # between turns) must reach the subprocess too.
        from src.core.types import ModelSettings

        session.model_settings = ModelSettings(effort="low")
        await runtime.run(session, UserMessage(text="four"))
        assert session.runtime_session_id != second_native
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unimplemented_port_surface(tmp_path: Path) -> None:
    sink = _CollectSink()
    runtime = _runtime(tmp_path, sink)
    assert runtime.supports_native_continuation is False
    with pytest.raises(NotImplementedError):
        await runtime.fork_session(_session(), source_native_session_id="x")
    with pytest.raises(NotImplementedError):
        await runtime.submit_action("p1", "approve")
