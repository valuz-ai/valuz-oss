"""DeepAgents plan mode, filled in by the hook bus (ADR-033 §9).

Claude, Codex and DSH lower plan mode natively; DeepAgents has no primitive,
so: the builtin ``plan_gate`` refuses its own shell / file writers, toolkit
tools that are not read-only are refused in the runtime, those calls no
longer park for approval first, plan instructions ride the turn's prompt,
and the answer's ``<proposed_plan>`` block becomes the same plan card codex
produces (``plan_proposed``).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from app.routes.sessions import set_session_mode
from app.schemas import SetSessionModeRequest
from src.core.agent_config import AgentConfig
from src.core.hooks import TOOL_CALL, SessionRef, hook_registry
from src.core.hooks.builtin.plan_gate import OWNER as PLAN_GATE_OWNER
from src.core.hooks.builtin.plan_gate import PLAN_MODE_DENY_REASON
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.types import Session, UserMessage
from src.runtimes.deepagents.runtime import PLAN_MODE_INSTRUCTIONS, DeepAgentsRuntime


class _Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def _runtime(tmp_path, *, mode: str = "plan") -> DeepAgentsRuntime:  # noqa: ANN001
    async def echo(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        return ToolResult(content=f"ran {args}")

    toolkit = ToolKit()
    toolkit.register(ToolDef(name="remember", description="", handler=echo))
    toolkit.register(ToolDef(name="recall", description="", handler=echo, read_only=True))
    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _Sink(),
        workspace_root=str(tmp_path),
        toolkit=toolkit,
    )
    runtime._hook_session_ref = SessionRef(
        session_id="s1", runtime_provider="deepagents", permission_mode="default", mode=mode
    )
    return runtime


def _request(name: str, args: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "c1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


def _session(tmp_path, mode: str) -> Session:  # noqa: ANN001
    session = Session(
        id="plan-session",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        runtime_provider="deepagents",
    )
    session.mode = mode  # type: ignore[assignment]
    return session


# -- the bus gate -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("execute", {"command": "rm -rf build"}),
        ("write_file", {"file_path": "/a.md", "content": "x"}),
        ("edit_file", {"file_path": "/a.md", "old_string": "x", "new_string": "y"}),
    ],
)
async def test_plan_mode_refuses_the_builtin_shell_and_writers(
    tmp_path,  # noqa: ANN001
    name: str,
    args: dict[str, Any],
) -> None:
    [middleware] = _runtime(tmp_path)._hooks_middlewares()
    ran: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        ran.append(request.tool_call["name"])
        return ToolMessage(content="done", tool_call_id="c1", name=name)

    result = await middleware.awrap_tool_call(_request(name, args), handler)

    assert ran == []
    assert isinstance(result, ToolMessage)
    assert result.content == PLAN_MODE_DENY_REASON
    assert result.status == "error"


async def test_plan_mode_leaves_read_only_builtins_alone(tmp_path) -> None:  # noqa: ANN001
    [middleware] = _runtime(tmp_path)._hooks_middlewares()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="file body", tool_call_id="c1", name="read_file")

    result = await middleware.awrap_tool_call(
        _request("read_file", {"file_path": "/a.md"}), handler
    )
    assert isinstance(result, ToolMessage)
    assert result.content == "file body"


def test_the_gate_is_off_outside_deepagents_plan_mode(tmp_path) -> None:  # noqa: ANN001
    # Nothing else listens to tool.call, so the graph is exactly what it was.
    assert _runtime(tmp_path, mode="default")._hooks_middlewares() == []
    # Runtimes with a native plan mode keep it.
    for runtime in ("claude_agent", "codex", "deepseek_harness"):
        ref = SessionRef(session_id="s", runtime_provider=runtime, mode="plan")
        owners = {spec.owner for spec in hook_registry.specs_for(TOOL_CALL, ref)}
        assert PLAN_GATE_OWNER not in owners


# -- toolkit gate and approvals ----------------------------------------------


async def test_plan_turn_refuses_toolkit_tools_that_are_not_read_only(tmp_path) -> None:  # noqa: ANN001
    runtime = _runtime(tmp_path)
    runtime._plan_turn = True
    mutating = runtime._to_structured_tool(runtime.toolkit.get("remember"))  # type: ignore[arg-type]
    read_only = runtime._to_structured_tool(runtime.toolkit.get("recall"))  # type: ignore[arg-type]

    assert await mutating.coroutine() == PLAN_MODE_DENY_REASON  # type: ignore[misc]
    assert await read_only.coroutine() == "ran {}"  # type: ignore[misc]

    runtime._plan_turn = False
    assert await mutating.coroutine() == "ran {}"  # type: ignore[misc]


def test_plan_mode_does_not_park_calls_it_will_refuse(tmp_path) -> None:  # noqa: ANN001
    runtime = _runtime(tmp_path)
    tools = [SimpleNamespace(name=n) for n in ("remember", "recall", "mcp_search")]

    default = runtime._build_interrupt_on("default", tools)
    planning = runtime._build_interrupt_on("default", tools, plan_mode=True)

    assert {"execute", "write_file", "edit_file", "remember"} <= set(default)
    assert set(planning) == {"recall", "mcp_search"}


def test_entering_or_leaving_plan_rebuilds_the_graph(tmp_path) -> None:  # noqa: ANN001
    runtime = _runtime(tmp_path)
    session = _session(tmp_path, "default")
    runtime._graph = object()
    runtime._applied_permission_mode = session.permission_mode
    runtime._applied_effort = None
    runtime._applied_citation_mode = False
    runtime._applied_plan_mode = False

    runtime._reconcile_session_levers(session)
    assert runtime._graph is not None

    session.mode = "plan"  # type: ignore[assignment]
    runtime._reconcile_session_levers(session)
    assert runtime._graph is None


# -- prompt and proposal card -------------------------------------------------


async def _run_turn(tmp_path, mode: str, answer: str) -> tuple[list[Any], list[Any]]:  # noqa: ANN001
    sink = _Sink()
    inputs: list[Any] = []
    settled = SimpleNamespace(values={"messages": []}, interrupts=(), next=())

    class _Graph:
        async def aget_state(self, _config: Any) -> Any:
            return settled

        async def astream_events(self, stream_input: Any, *_a: Any, **_k: Any):  # noqa: ANN202
            inputs.append(stream_input)
            yield {"event": "on_chat_model_end", "data": {"output": AIMessage(content=answer)}}

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"), "model", sink, workspace_root=str(tmp_path)
    )

    async def _graph(*_a: Any, **_k: Any) -> _Graph:
        return _Graph()

    runtime._ensure_graph = _graph  # type: ignore[method-assign]
    await runtime.run(_session(tmp_path, mode), UserMessage(text="Refactor the parser."))
    return sink.events, inputs


async def test_plan_turn_carries_the_instructions_and_yields_a_plan_card(tmp_path) -> None:  # noqa: ANN001
    answer = (
        "I read the parser and its tests.\n\n"
        "<proposed_plan>\n1. Split the tokenizer\n2. Add tests\n</proposed_plan>"
    )
    events, inputs = await _run_turn(tmp_path, "plan", answer)

    [prompt] = [m["content"] for m in inputs[0]["messages"]]
    assert PLAN_MODE_INSTRUCTIONS in prompt
    visible = [e for e in events if e.type in {"plan_proposed", "assistant_message"}]
    assert [(e.type, e.data) for e in visible] == [
        ("plan_proposed", {"plan": "1. Split the tokenizer\n2. Add tests"}),
        ("assistant_message", {"text": "I read the parser and its tests."}),
    ]


async def test_default_turn_is_unchanged(tmp_path) -> None:  # noqa: ANN001
    answer = "Done. <proposed_plan>not a plan turn</proposed_plan>"
    events, inputs = await _run_turn(tmp_path, "default", answer)

    [prompt] = [m["content"] for m in inputs[0]["messages"]]
    assert PLAN_MODE_INSTRUCTIONS not in prompt
    assert [e.type for e in events].count("plan_proposed") == 0
    [message] = [e for e in events if e.type == "assistant_message"]
    assert message.data == {"text": answer}


# -- the mode route -------------------------------------------------------------


class _Store:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def load_session(self, _owner: Any, _session_id: str) -> Session:
        return self.session

    async def save_session(self, session: Session) -> None:
        self.session = session


class _Orchestrator:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit_session_event(self, _session_id: str, event: Any) -> None:
        self.events.append(event)


async def test_the_route_accepts_plan_and_still_refuses_goal(tmp_path) -> None:  # noqa: ANN001
    store, orchestrator = _Store(_session(tmp_path, "default")), _Orchestrator()

    await set_session_mode(
        "plan-session", SetSessionModeRequest(mode="plan"), store, orchestrator, "owner"
    )  # type: ignore[arg-type]
    assert store.session.mode == "plan"
    assert [e.data for e in orchestrator.events] == [{"mode": "plan", "by": "user"}]

    with pytest.raises(HTTPException) as exc:
        await set_session_mode(
            "plan-session", SetSessionModeRequest(mode="goal"), store, orchestrator, "owner"
        )  # type: ignore[arg-type]
    assert exc.value.status_code == 400
    assert store.session.mode == "plan"
