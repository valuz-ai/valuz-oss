"""Runtime adapters must pass the Primary prompt through without Host planning."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from src.core.agent_config import AgentConfig
from src.core.task_coverage_continuation import build_task_coverage_noop_tool
from src.core.types import Session, UserMessage
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.codex.runtime import CodexRuntime
from src.runtimes.deepagents.runtime import DeepAgentsRuntime, _build_local_shell_backend


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def test_deepagents_large_result_backend_supports_virtual_paths_and_line_paging(
    tmp_path,
) -> None:
    backend = _build_local_shell_backend(str(tmp_path))
    virtual_path = "/large_tool_results/tool-call-1"
    payload = '{"rows":[' + ",".join(
        f'{{"index":{index},"value":"row-{index}"}}' for index in range(400)
    ) + "]}"

    write_result = backend.write(virtual_path, payload)
    assert write_result.error is None

    # One-line MCP JSON must expose stable logical lines to read_file instead
    # of rejecting every non-zero line offset.
    page = backend.read(virtual_path, offset=150, limit=5)
    assert page.error is None
    assert page.file_data is not None
    assert page.file_data["content"]

    # Shell execution runs at the workspace root. Virtual absolute artifact
    # paths must resolve to the same file that read_file sees.
    result = backend.execute(f"jq -r '.rows[0].value' {virtual_path}")
    assert result.exit_code == 0
    assert "row-0" in result.output


def _session(
    tmp_path,
    *,
    runtime: str,
    citation_enabled: bool = True,
    verification_enabled: bool = False,
) -> Session:
    return Session(
        id=f"native-prompt-{runtime}",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        runtime_provider=runtime,  # type: ignore[arg-type]
        instructions="Keep the existing agent instructions.",
        metadata={
            "valuz": {
                "citation_enabled": citation_enabled,
                "citation_verification_enabled": verification_enabled,
            }
        },
    )


async def test_claude_primary_uses_only_the_shared_user_prompt(tmp_path, monkeypatch) -> None:
    sentinel_prompt = "PRIMARY_PROMPT_WITHOUT_HOST_PLAN"
    captured: list[str] = []

    class _Client:
        async def query(self, prompt: str) -> None:
            captured.append(prompt)

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    runtime = ClaudeAgentRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    runtime._client = _Client()  # type: ignore[assignment]
    monkeypatch.setattr(
        "src.core.prompt_builder.build_user_prompt",
        lambda *_a, **_k: sentinel_prompt,
    )
    monkeypatch.setattr(runtime, "_stop_idle_drainer", _noop)
    monkeypatch.setattr(runtime, "_reconcile_session_levers", _noop)
    monkeypatch.setattr(runtime, "_consume_turn_stream", _noop)
    monkeypatch.setattr(runtime, "_stop_workflow_pollers", _noop)
    monkeypatch.setattr(runtime, "_start_idle_drainer", lambda *_a, **_k: None)

    await runtime.run(
        _session(tmp_path, runtime="claude_agent"),
        UserMessage(text="User request", additional_context="Normal upstream context"),
    )

    assert captured == [sentinel_prompt]


async def test_deepagents_primary_uses_only_the_shared_user_prompt(tmp_path, monkeypatch) -> None:
    sentinel_prompt = "PRIMARY_PROMPT_WITHOUT_HOST_PLAN"
    captured: list[Any] = []
    settled_state = SimpleNamespace(values={"messages": []}, interrupts=())

    class _Graph:
        async def aget_state(self, _config: Any) -> Any:
            return settled_state

        async def astream_events(self, stream_input: Any, *_args: Any, **_kwargs: Any):
            captured.append(stream_input)
            if False:
                yield None

    async def _graph(*_args: Any, **_kwargs: Any) -> _Graph:
        return _Graph()

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(
        "src.core.prompt_builder.build_user_prompt",
        lambda *_a, **_k: sentinel_prompt,
    )
    monkeypatch.setattr(runtime, "_ensure_graph", _graph)

    await runtime.run(
        _session(tmp_path, runtime="deepagents"),
        UserMessage(text="User request", additional_context="Normal upstream context"),
    )

    assert captured == [{"messages": [{"role": "user", "content": sentinel_prompt}]}]


async def test_deepagents_preserves_evidence_continuity_for_same_turn_coverage_only(
    tmp_path,
    monkeypatch,
) -> None:
    settled_state = SimpleNamespace(values={"messages": []}, interrupts=(), next=())

    class _Graph:
        async def aget_state(self, _config: Any) -> Any:
            return settled_state

        async def astream_events(self, *_args: Any, **_kwargs: Any):
            if False:
                yield None

    async def _graph(*_args: Any, **_kwargs: Any) -> _Graph:
        return _Graph()

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime, "_ensure_graph", _graph)
    session = _session(tmp_path, runtime="deepagents")

    await runtime.run(session, UserMessage(text="primary"))
    registry = runtime._turn_evidence_registry
    generation = registry.generation
    registry.register_tool_result(
        {
            "_valuz_evidence": [
                {
                    "evidenceHandle": "ev_coverage_12345678",
                    "source": {
                        "sourceId": "coverage:source",
                        "providerId": "test",
                        "sourceType": "dataset",
                        "title": "Coverage source",
                        "retrievedAt": "2026-08-05T00:00:00Z",
                    },
                    "evidence": {
                        "kind": "structured-data",
                        "datasetId": "coverage",
                        "toolName": "coverage_lookup",
                        "recordKey": "coverage|1",
                        "field": "value",
                        "metric": "value",
                        "value": 9,
                        "unit": "count",
                        "capturedAt": "2026-08-05T00:00:00Z",
                    },
                }
            ]
        }
    )
    registry.remember_continuity_reference("ev_coverage_12345678")

    await runtime.run_task_coverage(
        session,
        UserMessage(text="coverage"),
        no_op_tool=build_task_coverage_noop_tool(),
    )

    assert registry.generation == generation
    assert registry.resolve("ev_coverage_12345678") is not None
    assert registry.continuity_references == ("ev_coverage_12345678",)

    await runtime.run(session, UserMessage(text="next primary turn"))
    assert registry.generation == generation + 1
    assert registry.resolve("ev_coverage_12345678") is None
    assert registry.continuity_references == ()


async def test_deepagents_preserves_assistant_messages_between_tool_calls(
    tmp_path,
    monkeypatch,
) -> None:
    from langchain_core.messages import AIMessage

    sink = _RecordingSink()
    settled_state = SimpleNamespace(values={"messages": []}, interrupts=(), next=())

    class _Graph:
        async def aget_state(self, _config: Any) -> Any:
            return settled_state

        async def astream_events(self, *_args: Any, **_kwargs: Any):
            yield {
                "event": "on_chat_model_end",
                "data": {"output": AIMessage(content="先说明已找到一份材料。")},
            }
            yield {
                "event": "on_tool_start",
                "name": "document_fetch",
                "run_id": "tool-1",
                "data": {"input": {"document_id": "doc-1"}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "tool-1",
                "data": {"output": SimpleNamespace(content="document content", status="success")},
            }
            yield {
                "event": "on_chat_model_end",
                "data": {"output": AIMessage(content="再给出最终结论。")},
            }

    async def _graph(*_args: Any, **_kwargs: Any) -> _Graph:
        return _Graph()

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        sink,
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime, "_ensure_graph", _graph)

    await runtime.run(
        _session(tmp_path, runtime="deepagents"),
        UserMessage(text="读取文档后回答。"),
    )

    visible = [
        event
        for event in sink.events
        if event.type in {"assistant_message", "tool_use", "tool_result"}
    ]
    assert [event.type for event in visible] == [
        "assistant_message",
        "tool_use",
        "tool_result",
        "assistant_message",
    ]
    assert [event.data["text"] for event in visible if event.type == "assistant_message"] == [
        "先说明已找到一份材料。",
        "再给出最终结论。",
    ]


async def test_deepagents_hides_only_internal_summarizer_model_events(
    tmp_path,
    monkeypatch,
) -> None:
    from langchain_core.messages import AIMessage, AIMessageChunk

    sink = _RecordingSink()
    settled_state = SimpleNamespace(values={"messages": []}, interrupts=(), next=())

    class _Graph:
        async def aget_state(self, _config: Any) -> Any:
            return settled_state

        async def astream_events(self, *_args: Any, **_kwargs: Any):
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"lc_source": "summarization"},
                "data": {"chunk": AIMessageChunk(content="## SESSION INTENT\ninternal")},
            }
            yield {
                "event": "on_chat_model_end",
                "metadata": {"lc_source": "summarization"},
                "data": {"output": AIMessage(content="## SESSION INTENT\ninternal")},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {},
                "data": {"chunk": AIMessageChunk(content="正常最终回答")},
            }
            yield {
                "event": "on_chat_model_end",
                "metadata": {},
                "data": {"output": AIMessage(content="正常最终回答")},
            }

    async def _graph(*_args: Any, **_kwargs: Any) -> _Graph:
        return _Graph()

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        sink,
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime, "_ensure_graph", _graph)

    await runtime.run(
        _session(tmp_path, runtime="deepagents"),
        UserMessage(text="长任务完成后回答。"),
    )

    visible = [
        event
        for event in sink.events
        if event.type in {"text_delta", "assistant_message"}
    ]
    assert [(event.type, event.data["text"]) for event in visible] == [
        ("text_delta", "正常最终回答"),
        ("assistant_message", "正常最终回答"),
    ]


async def test_deepagents_summarization_is_a_compaction_like_the_other_runtimes(
    tmp_path,
    monkeypatch,
) -> None:
    """The summarizer replacing older history is a compaction: the shared
    ``compaction`` marker and an observe-only ``session.compact``."""
    from langchain_core.messages import AIMessage
    from src.core.hooks import SESSION_COMPACT, hook_registry

    sink = _RecordingSink()
    settled_state = SimpleNamespace(values={"messages": []}, interrupts=(), next=())
    compacted: list[str] = []

    class _Graph:
        async def aget_state(self, _config: Any) -> Any:
            return settled_state

        async def astream_events(self, *_args: Any, **_kwargs: Any):
            yield {
                "event": "on_chat_model_end",
                "metadata": {"lc_source": "summarization"},
                "data": {"output": AIMessage(content="## SESSION INTENT\ninternal")},
            }
            yield {
                "event": "on_chat_model_end",
                "metadata": {},
                "data": {"output": AIMessage(content="答案")},
            }

    async def _graph(*_args: Any, **_kwargs: Any) -> _Graph:
        return _Graph()

    async def on_compact(ctx, event, next_):  # noqa: ANN001, ANN202
        compacted.append(str(event.get("trigger")))
        return await next_()

    remove = hook_registry.register(SESSION_COMPACT, on_compact, owner="test-da-compact")
    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        sink,
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime, "_ensure_graph", _graph)
    try:
        await runtime.run(_session(tmp_path, runtime="deepagents"), UserMessage(text="继续"))
    finally:
        remove()

    assert [e.type for e in sink.events].count("compaction") == 1
    assert compacted == ["auto"]


async def test_codex_primary_uses_only_the_shared_user_prompt(tmp_path, monkeypatch) -> None:
    from src.runtimes.codex import runtime as runtime_module

    sentinel_prompt = "PRIMARY_PROMPT_WITHOUT_HOST_PLAN"
    captured: list[str] = []

    class _Client:
        async def turn_start(self, thread_id: str, prompt: str, params: Any) -> Any:
            del thread_id, params
            captured.append(prompt)
            return SimpleNamespace(turn=SimpleNamespace(id="turn-1"))

    class _TurnHandle:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def stream(self):  # type: ignore[no-untyped-def]
            async def _empty():  # type: ignore[no-untyped-def]
                if False:
                    yield None

            return _empty()

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    runtime = CodexRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    runtime._codex = SimpleNamespace(_client=_Client())  # type: ignore[assignment]
    runtime._thread = SimpleNamespace(id="thread-1")  # type: ignore[assignment]
    monkeypatch.setattr(
        "src.core.prompt_builder.build_user_prompt",
        lambda *_a, **_k: sentinel_prompt,
    )
    monkeypatch.setattr(runtime, "_materialize_skills", lambda _session: None)
    monkeypatch.setattr(runtime, "_ensure_codex", _noop)
    monkeypatch.setattr(runtime, "_ensure_thread", _noop)
    monkeypatch.setattr(runtime_module, "AsyncTurnHandle", _TurnHandle)

    await runtime.run(
        _session(tmp_path, runtime="codex"),
        UserMessage(text="User request", additional_context="Normal upstream context"),
    )

    assert captured == [sentinel_prompt]


async def test_codex_native_compaction_is_announced_on_the_bus(tmp_path, monkeypatch) -> None:
    """Codex compacts on its own and reports it after the fact (a
    ``contextCompaction`` item): the runtime emits the shared marker and an
    observe-only ``session.compact``."""
    from openai_codex.generated.v2_all import (
        ContextCompactionThreadItem,
        ItemCompletedNotification,
        ThreadItem,
    )
    from openai_codex.models import Notification
    from src.core.hooks import SESSION_COMPACT, hook_registry
    from src.runtimes.codex import runtime as runtime_module

    compacted: list[str] = []
    sink = _RecordingSink()

    class _Client:
        async def turn_start(self, thread_id: str, prompt: str, params: Any) -> Any:
            return SimpleNamespace(turn=SimpleNamespace(id="turn-1"))

    class _TurnHandle:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def stream(self):  # type: ignore[no-untyped-def]
            async def _items():  # type: ignore[no-untyped-def]
                yield Notification(
                    method="item/completed",
                    payload=ItemCompletedNotification.model_validate(
                        {
                            "item": ThreadItem(
                                root=ContextCompactionThreadItem(
                                    id="cc_1", type="contextCompaction"
                                )
                            ),
                            "completedAtMs": 1,
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                        }
                    ),
                )

            return _items()

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def on_compact(ctx, event, next_):  # noqa: ANN001, ANN202
        compacted.append(str(event.get("trigger")))
        return await next_()

    remove = hook_registry.register(SESSION_COMPACT, on_compact, owner="test-codex-compact")
    runtime = CodexRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        sink,
        workspace_root=str(tmp_path),
    )
    runtime._codex = SimpleNamespace(_client=_Client())  # type: ignore[assignment]
    runtime._thread = SimpleNamespace(id="thread-1")  # type: ignore[assignment]
    monkeypatch.setattr(runtime, "_materialize_skills", lambda _session: None)
    monkeypatch.setattr(runtime, "_ensure_codex", _noop)
    monkeypatch.setattr(runtime, "_ensure_thread", _noop)
    monkeypatch.setattr(runtime_module, "AsyncTurnHandle", _TurnHandle)
    try:
        await runtime.run(_session(tmp_path, runtime="codex"), UserMessage(text="go on"))
    finally:
        remove()

    assert [e.type for e in sink.events].count("compaction") == 1
    assert compacted == ["auto"]


async def test_deepagents_production_graph_has_no_host_research_controller(
    tmp_path,
    monkeypatch,
) -> None:
    from src.runtimes.deepagents import runtime as runtime_module

    captured: dict[str, Any] = {}
    graph = object()

    def _create_deep_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return graph

    async def _no_mcp(_session: Session) -> list[Any]:
        return []

    async def _open_checkpointer() -> None:
        return None

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime_module, "create_deep_agent", _create_deep_agent)
    monkeypatch.setattr(
        runtime_module,
        "_build_local_shell_backend",
        lambda _root, **_kwargs: object(),
    )
    monkeypatch.setattr(runtime, "_build_model_client", lambda _session: object())
    monkeypatch.setattr(runtime, "_build_tools", lambda: [])
    monkeypatch.setattr(runtime, "_build_mcp_tools", _no_mcp)
    monkeypatch.setattr(runtime, "_build_subagents", lambda **_kwargs: [])
    monkeypatch.setattr(runtime, "_open_checkpointer", _open_checkpointer)
    monkeypatch.setattr(runtime, "_materialize_skills", lambda _session: [])

    built = await runtime._ensure_graph(_session(tmp_path, runtime="deepagents"))

    assert built is graph
    middleware_names = [type(item).__name__ for item in captured["middleware"]]
    assert middleware_names == [
        "InvalidToolCallPairMiddleware",
        "ToolErrorTolerantMiddleware",
        "WindowsPathVirtualizerMiddleware",
        "CitationEvidenceCompactionMiddleware",
    ]
    assert all("Research" not in name and "Budget" not in name for name in middleware_names)


async def test_deepagents_always_registers_source_metadata_when_citation_is_off(
    tmp_path,
    monkeypatch,
) -> None:
    """Evidence Registry is infrastructure, not a Citation-rendering feature."""

    from src.runtimes.deepagents import runtime as runtime_module

    captured: dict[str, Any] = {}

    def _create_deep_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    async def _no_mcp(_session: Session) -> list[Any]:
        return []

    async def _open_checkpointer() -> None:
        return None

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime_module, "create_deep_agent", _create_deep_agent)
    monkeypatch.setattr(
        runtime_module,
        "_build_local_shell_backend",
        lambda _root, **_kwargs: object(),
    )
    monkeypatch.setattr(runtime, "_build_model_client", lambda _session: object())
    monkeypatch.setattr(runtime, "_build_tools", lambda: [])
    monkeypatch.setattr(runtime, "_build_mcp_tools", _no_mcp)
    monkeypatch.setattr(runtime, "_build_subagents", lambda **_kwargs: [])
    monkeypatch.setattr(runtime, "_open_checkpointer", _open_checkpointer)
    monkeypatch.setattr(runtime, "_materialize_skills", lambda _session: [])

    await runtime._ensure_graph(
        _session(
            tmp_path,
            runtime="deepagents",
            citation_enabled=False,
            verification_enabled=False,
        )
    )

    compaction = next(
        item
        for item in captured["middleware"]
        if type(item).__name__ == "CitationEvidenceCompactionMiddleware"
    )
    assert compaction._citation_artifact_emitter is not None
    assert compaction._evidence_registry is runtime._turn_evidence_registry


async def test_deepagents_subagents_receive_only_the_minimal_citation_protocol(
    tmp_path,
    monkeypatch,
) -> None:
    """Nested agents must see/register handles without inheriting Host plans."""

    from src.runtimes.deepagents import runtime as runtime_module

    captured: dict[str, Any] = {}

    def _create_deep_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    async def _no_mcp(_session: Session) -> list[Any]:
        return []

    async def _open_checkpointer() -> None:
        return None

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _RecordingSink(),
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(runtime_module, "create_deep_agent", _create_deep_agent)
    monkeypatch.setattr(
        runtime_module,
        "_build_local_shell_backend",
        lambda _root, **_kwargs: object(),
    )
    monkeypatch.setattr(runtime, "_build_model_client", lambda _session: object())
    monkeypatch.setattr(runtime, "_build_tools", lambda: [])
    monkeypatch.setattr(runtime, "_build_mcp_tools", _no_mcp)
    monkeypatch.setattr(runtime, "_open_checkpointer", _open_checkpointer)
    monkeypatch.setattr(runtime, "_materialize_skills", lambda _session: ["/tmp/skills"])

    session = _session(tmp_path, runtime="deepagents")
    session.instructions = (
        "Normal agent instructions.\n\n"
        '<citation-system-policy revision="citation-v6">\n'
        "Use registered Evidence handles only.\n"
        "</citation-system-policy>"
    )
    await runtime._ensure_graph(session)

    general_purpose = next(
        subagent for subagent in captured["subagents"] if subagent["name"] == "general-purpose"
    )
    assert "Use registered Evidence handles only." in general_purpose["system_prompt"]
    assert "Normal agent instructions." not in general_purpose["system_prompt"]
    assert general_purpose["skills"] == ["/tmp/skills"]
    assert [type(item).__name__ for item in general_purpose["middleware"]] == [
        "InvalidToolCallPairMiddleware",
        "WindowsPathVirtualizerMiddleware",
        "CitationEvidenceCompactionMiddleware",
    ]
    main_compaction = next(
        item
        for item in captured["middleware"]
        if type(item).__name__ == "CitationEvidenceCompactionMiddleware"
    )
    nested_compaction = next(
        item
        for item in general_purpose["middleware"]
        if type(item).__name__ == "CitationEvidenceCompactionMiddleware"
    )
    assert main_compaction._evidence_registry is runtime._turn_evidence_registry
    assert nested_compaction._evidence_registry is runtime._turn_evidence_registry
