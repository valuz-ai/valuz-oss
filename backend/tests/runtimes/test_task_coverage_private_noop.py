from __future__ import annotations

from types import SimpleNamespace

from src.core.agent_config import AgentConfig
from src.core.task_coverage_continuation import build_task_coverage_noop_tool
from src.core.tools import ToolKit
from src.core.types import Session, UserMessage
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.codex.runtime import CodexRuntime
from src.runtimes.deepagents.runtime import DeepAgentsRuntime


def _session(tmp_path) -> Session:
    return Session(
        id="coverage-session",
        cwd=str(tmp_path),
        runtime_session_id="native-1",
        agent_config=AgentConfig(id="agent-1", name="tester"),
    )


async def test_deepagents_coverage_tool_is_terminal_and_turn_scoped(tmp_path) -> None:
    runtime = object.__new__(DeepAgentsRuntime)
    runtime.toolkit = ToolKit()
    runtime._graph = object()
    runtime.workspace_root = str(tmp_path)
    runtime._cur_session_id = "coverage-session"
    runtime.config = SimpleNamespace(hooks=None)
    tool = build_task_coverage_noop_tool()
    calls: list[str | None] = []

    converted = runtime._to_structured_tool(tool)
    assert converted.return_direct is True

    async def run(session: Session, _message: UserMessage) -> None:
        calls.append(session.runtime_session_id)
        assert runtime.toolkit.get(tool.name) is tool

    runtime.run = run  # type: ignore[method-assign]
    session = _session(tmp_path)
    await runtime.run_task_coverage(
        session,
        UserMessage(text="coverage"),
        no_op_tool=tool,
    )

    assert calls == ["native-1"]
    assert runtime.toolkit.get(tool.name) is None
    assert runtime._graph is None


async def test_claude_coverage_rebuilds_client_but_resumes_native_session(tmp_path) -> None:
    runtime = object.__new__(ClaudeAgentRuntime)
    runtime.toolkit = ToolKit()
    tool = build_task_coverage_noop_tool()
    destroys: list[None] = []
    calls: list[str | None] = []

    async def destroy() -> None:
        destroys.append(None)

    async def run(session: Session, _message: UserMessage) -> None:
        calls.append(session.runtime_session_id)
        assert runtime.toolkit.get(tool.name) is tool

    runtime._destroy_client = destroy  # type: ignore[method-assign]
    runtime.run = run  # type: ignore[method-assign]
    session = _session(tmp_path)
    await runtime.run_task_coverage(
        session,
        UserMessage(text="coverage"),
        no_op_tool=tool,
    )

    assert calls == ["native-1"]
    assert len(destroys) == 2
    assert runtime.toolkit.get(tool.name) is None


async def test_codex_coverage_reconnects_but_resumes_native_thread(tmp_path) -> None:
    runtime = object.__new__(CodexRuntime)
    runtime.toolkit = ToolKit()
    tool = build_task_coverage_noop_tool()
    closes: list[None] = []
    calls: list[str | None] = []

    async def close() -> None:
        closes.append(None)

    async def run(session: Session, _message: UserMessage) -> None:
        calls.append(session.runtime_session_id)
        assert runtime.toolkit.get(tool.name) is tool

    runtime.close = close  # type: ignore[method-assign]
    runtime.run = run  # type: ignore[method-assign]
    session = _session(tmp_path)
    await runtime.run_task_coverage(
        session,
        UserMessage(text="coverage"),
        no_op_tool=tool,
    )

    assert calls == ["native-1"]
    assert len(closes) == 2
    assert runtime.toolkit.get(tool.name) is None
