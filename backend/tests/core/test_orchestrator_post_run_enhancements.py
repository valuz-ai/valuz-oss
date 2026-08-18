"""Native primary execution and append-only post-run enhancements.

These tests pin the orchestrator boundary rather than the legacy Task
Coverage parser.  The first Runtime invocation must receive the user's input
unchanged.  When enabled, Task Coverage is one ordinary continuation on the
same Runtime and native thread; it never replaces or blocks primary output.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
import copy
import json

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.orchestrator import SessionOrchestrator, _session_task_coverage_enabled
from src.core.task_coverage_continuation import TASK_COVERAGE_NOOP_TOOL_NAME
from src.core.tools import ToolDef
from src.core.types import BudgetExhausted, EndTurn, Error, Session, UserMessage


class _FakeStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.appended: list[Event] = []
        self.messages: list[object] = []
        self._next_seq = 0

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session if session_id == self._session.id else None

    async def save_session(self, session: Session) -> None:
        self._session = session

    async def save_message(self, user_id: str, message: object) -> None:
        self.messages.append(copy.deepcopy(message))

    async def append_event(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        event: Event,
        **kwargs: object,
    ) -> int:
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq


class _RecordingRuntime:
    def __init__(
        self,
        sink: object,
        *,
        fail_continuation: bool = False,
        fail_primary: bool = False,
        primary_stop_reason: object | None = None,
        primary_text: str = "Primary answer.",
        silent_continuation: bool = False,
        raise_continuation: bool = False,
        cancel_continuation: str | None = None,
        supports_native_continuation: bool = True,
        evidence_payload: dict[str, object] | None = None,
        coverage_noop: bool = False,
        called_tool_name: str | None = None,
        called_tool_external: bool | None = None,
        tool_result_is_error: bool = False,
    ) -> None:
        self.sink = sink
        self.fail_continuation = fail_continuation
        self.fail_primary = fail_primary
        self.primary_stop_reason = primary_stop_reason
        self.primary_text = primary_text
        self.silent_continuation = silent_continuation
        self.raise_continuation = raise_continuation
        # ``"stray"`` raises a CancelledError that is NOT a cancel request on
        # the current task (the runtime-teardown misfire); ``"genuine"``
        # really cancels the current task and lets it land.
        self.cancel_continuation = cancel_continuation
        self.supports_native_continuation = supports_native_continuation
        self.evidence_payload = evidence_payload
        self.coverage_noop = coverage_noop
        self.called_tool_name = called_tool_name
        self.called_tool_external = called_tool_external
        self.tool_result_is_error = tool_result_is_error
        self.prompts: list[UserMessage] = []
        self.coverage_tools: list[ToolDef] = []
        self.session_object_ids: list[int] = []
        self.native_thread_ids: list[str | None] = []
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def run(self, session: Session, user_message: UserMessage) -> None:
        run_index = len(self.prompts)
        self.prompts.append(copy.deepcopy(user_message))
        self.session_object_ids.append(id(session))
        self.native_thread_ids.append(session.runtime_session_id)

        if run_index == 0:
            session.runtime_session_id = "native-thread-1"
            if self.evidence_payload is not None:
                await self.sink.emit(
                    Event(
                        type="citation_evidence",
                        data={
                            "content": json.dumps(self.evidence_payload),
                            "tool_name": "search",
                        },
                    )
                )
            if self.called_tool_name is not None:
                tool_use_id = "primary-tool-1"
                await self.sink.emit(
                    Event(
                        type="tool_use",
                        data={
                            "id": tool_use_id,
                            "name": self.called_tool_name,
                            "input": {},
                            **(
                                {"external": self.called_tool_external}
                                if self.called_tool_external is not None
                                else {}
                            ),
                        },
                    )
                )
                await self.sink.emit(
                    Event(
                        type="tool_result",
                        data={
                            "id": tool_use_id,
                            "content": "ok",
                            "is_error": self.tool_result_is_error,
                        },
                    )
                )
            await self.sink.emit(Event(type="assistant_message", data={"text": self.primary_text}))
            if self.primary_stop_reason is not None:
                session.stop_reason = self.primary_stop_reason
            else:
                session.stop_reason = (
                    Error(category="execution_error", message="primary failed")
                    if self.fail_primary
                    else EndTurn()
                )
        elif self.raise_continuation:
            raise RuntimeError("coverage provider crashed")
        elif self.cancel_continuation == "stray":
            raise asyncio.CancelledError()
        elif self.cancel_continuation == "genuine":
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            await asyncio.sleep(0)
            raise AssertionError("cancel was not delivered")  # pragma: no cover
        elif self.fail_continuation:
            session.stop_reason = Error(
                category="execution_error",
                message="coverage continuation failed",
            )
        elif self.silent_continuation:
            session.stop_reason = EndTurn()
        else:
            await self.sink.emit(
                Event(
                    type="assistant_message",
                    data={"text": "No important omissions."},
                )
            )
            session.stop_reason = EndTurn()

        session.status = "idle"
        stop_reason = session.stop_reason
        await self.sink.emit(
            Event(
                type="session_idle",
                data={
                    "stop_reason": {"type": getattr(stop_reason, "type", "end_turn")},
                    "num_turns": 1,
                },
            )
        )

    async def run_task_coverage(
        self,
        session: Session,
        user_message: UserMessage,
        *,
        no_op_tool: ToolDef,
    ) -> None:
        self.coverage_tools.append(no_op_tool)
        if not self.coverage_noop:
            await self.run(session, user_message)
            return

        self.prompts.append(copy.deepcopy(user_message))
        self.session_object_ids.append(id(session))
        self.native_thread_ids.append(session.runtime_session_id)
        tool_use_id = "coverage-noop-1"
        await self.sink.emit(
            Event(
                type="tool_use",
                data={"id": tool_use_id, "name": no_op_tool.name, "input": {}},
            )
        )
        assert no_op_tool.handler is not None
        result = await no_op_tool.handler({}, object())  # type: ignore[arg-type]
        await self.sink.emit(
            Event(
                type="tool_result",
                data={"id": tool_use_id, "content": result.content, "is_error": False},
            )
        )
        session.stop_reason = EndTurn()
        session.status = "idle"
        await self.sink.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:  # pragma: no cover
        pass


def _session(
    tmp_path,
    *,
    task_coverage_enabled: bool,
    citation_enabled: bool = False,
    verification_enabled: bool = False,
    task_coverage_policy: dict[str, object] | None = None,
) -> Session:
    return Session(
        id="sess-native-post-run",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        skills=("/skills/research",),
        metadata={
            "valuz": {
                "citation_enabled": citation_enabled,
                "citation_verification_enabled": verification_enabled,
                "task_coverage_enabled": task_coverage_enabled,
                **(
                    {"task_coverage_policy": task_coverage_policy}
                    if task_coverage_policy is not None
                    else {}
                ),
            }
        },
    )


def test_task_coverage_defaults_disabled_for_unstamped_session(tmp_path) -> None:
    session = Session(
        id="sess-legacy-without-task-coverage-setting",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        metadata={},
    )

    assert _session_task_coverage_enabled(session) is False


async def test_primary_prompt_is_not_rewritten_or_short_circuited_by_host(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            called_tool_name="valuz_docs/document_search",
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    request = UserMessage(
        text="按用户给定的连续季度阈值判断是否触发，不重新制定规则。",
        additional_context="normal upstream context",
    )

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        request,
    )

    assert len(runtimes) == 1
    assert runtimes[0].prompts[0] == request
    assert len(runtimes[0].prompts) == 2
    assert message.assistant_message == "Primary answer.\nNo important omissions."
    assert "pending_task_clarification" not in store._session.metadata["valuz"]


async def test_task_coverage_is_one_continuation_on_same_runtime_and_thread(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    request = UserMessage(text="Summarize the available information concisely.")

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        request,
    )

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert len(runtime.prompts) == 2
    assert runtime.prompts[0] == request
    assert "original user request" in runtime.prompts[1].text.lower()
    assert "important omission" in runtime.prompts[1].text.lower()
    assert TASK_COVERAGE_NOOP_TOOL_NAME in runtime.prompts[1].text
    assert runtime.prompts[1].additional_context == ""
    assert runtime.session_object_ids[0] == runtime.session_object_ids[1]
    assert runtime.native_thread_ids == [None, "native-thread-1"]
    assert store._session.runtime_session_id == "native-thread-1"
    assert [event.type for event in store.appended].count("assistant_message") == 2
    assert [event.type for event in store.appended].count("session_idle") == 1
    post_run_events = [
        event
        for event in store.appended
        if event.type == "turn_phase" and event.data.get("phase") == "post_run_verification"
    ]
    assert [event.data.get("state") for event in post_run_events] == [
        "started",
        "completed",
    ]
    event_types = [event.type for event in store.appended]
    assert event_types.index("assistant_message") < event_types.index("turn_phase")
    assert event_types.index("turn_phase") < event_types.index("assistant_message_sidecar")
    assert event_types.index("assistant_message_sidecar") < max(
        index for index, event_type in enumerate(event_types) if event_type == "turn_phase"
    )
    assert max(
        index for index, event_type in enumerate(event_types) if event_type == "turn_phase"
    ) < event_types.index("session_idle")
    assert message.assistant_message == "Primary answer.\nNo important omissions."
    assert message.metadata["task_coverage"] == {
        "status": "complete",
        "supplemented": True,
        "assistant_segment_indices": [1],
    }
    coverage_sidecars = [
        event
        for event in store.appended
        if event.type == "assistant_message_sidecar"
        and isinstance(event.data.get("task_coverage"), dict)
    ]
    assert len(coverage_sidecars) == 1
    assert coverage_sidecars[0].data["assistant_segment_index"] == 1
    assert coverage_sidecars[0].data["task_coverage"] == message.metadata["task_coverage"]


async def test_post_run_checks_skip_when_primary_uses_only_internal_tools(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=True,
        citation_enabled=False,
        verification_enabled=True,
    )
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            called_tool_name="mcp__harness__deliver_artifacts",
            evidence_payload=_text_evidence(),
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Create a local artifact and answer briefly."),
    )

    assert len(runtimes[0].prompts) == 1
    assert "task_coverage" not in message.metadata
    assert "claim_audits" not in message.metadata
    assert not any(
        event.type == "turn_phase"
        and event.data.get("phase") == "post_run_verification"
        for event in store.appended
    )


async def test_post_run_checks_run_for_bare_external_connector_tool(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            called_tool_name="search_documents",
            called_tool_external=True,
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Search the connector and summarize the result."),
    )

    assert len(runtimes[0].prompts) == 2


async def test_successful_generate_ui_skips_task_coverage_for_that_turn(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=True,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            primary_text="The preview is ready.",
            called_tool_name="generate_ui",
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Generate a chart."),
    )

    assert len(runtimes[0].prompts) == 1
    assert message.assistant_message == "The preview is ready."
    assert "task_coverage" not in message.metadata
    assert "citation_bundle" not in message.metadata
    assert "claim_audits" not in message.metadata
    assert not any(
        event.type == "turn_phase"
        and event.data.get("phase") == "post_run_verification"
        for event in store.appended
    )


async def test_namespaced_generate_ui_skips_post_run_checks_for_that_turn(
    tmp_path,
    monkeypatch,
) -> None:
    """Claude exposes host MCP tools with a namespace on the wire."""

    session = _session(
        tmp_path,
        task_coverage_enabled=True,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            primary_text="The preview is ready.",
            called_tool_name="mcp__harness__generate_ui",
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Generate a chart."),
    )

    assert len(runtimes[0].prompts) == 1
    assert "task_coverage" not in message.metadata
    assert "citation_bundle" not in message.metadata
    assert "claim_audits" not in message.metadata
    assert not any(
        event.type == "turn_phase"
        and event.data.get("phase") == "post_run_verification"
        for event in store.appended
    )


async def test_failed_generate_ui_also_skips_post_run_checks_for_that_turn(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=True,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            primary_text="The UI could not be generated.",
            called_tool_name="mcp__harness__generate_ui",
            tool_result_is_error=True,
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Generate a chart."),
    )

    assert len(runtimes[0].prompts) == 1
    assert message.assistant_message == "The UI could not be generated."
    assert "task_coverage" not in message.metadata
    assert "citation_bundle" not in message.metadata
    assert "claim_audits" not in message.metadata
    assert not any(
        event.type == "turn_phase"
        and event.data.get("phase") == "post_run_verification"
        for event in store.appended
    )


async def test_namespaced_generate_ui_skips_citation_before_early_idle_finalize(
    tmp_path,
    monkeypatch,
) -> None:
    """Hosted finance turns already disable Task Coverage before runtime run.

    Their session_idle therefore finalizes sidecars inside the observer.  The
    attempted tool call has to disable Citation/Audit before that event, not
    after ``runtime.run`` returns to the outer orchestrator.
    """

    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(
            args[2],
            primary_text="The preview is ready.",
            called_tool_name="mcp__harness__generate_ui",
        )

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Generate a workspace."),
    )

    assert "citation_bundle" not in message.metadata
    assert "claim_audits" not in message.metadata
    assert not any(
        event.type == "turn_phase" and event.data.get("phase") == "post_run_verification"
        for event in store.appended
    )


async def test_citation_audit_emits_post_run_verification_lifecycle_without_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(
            args[2],
            called_tool_name="mcp__valuz_docs__document_search",
        )

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer with sources."),
    )

    post_run_events = [
        event
        for event in store.appended
        if event.type == "turn_phase" and event.data.get("phase") == "post_run_verification"
    ]
    assert [event.data.get("state") for event in post_run_events] == [
        "started",
        "completed",
    ]
    assert post_run_events[0].data["features"] == ["citation", "claim_audit"]


async def test_task_coverage_continuation_receives_static_layer_guidance_only(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=True,
        task_coverage_policy={
            "revision": "finance-task-coverage-v2",
            "review_guidance": {
                "material_gap_types": ["missing-financial-slot"],
                "completion_dimensions": ["security-entity", "fiscal-period"],
                "source_boundary_notes": ["Keep security and period aligned."],
                "supplement_rules": {
                    "append_only": True,
                    "do_not_repeat_completed_content": True,
                    "preserve_visible_history": True,
                },
            },
        },
    )
    store = _FakeStore(session)
    runtimes: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            silent_continuation=True,
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    request = UserMessage(text="用户原始请求保持不变")

    await SessionOrchestrator(store).run_turn("owner-1", session.id, request)

    assert runtimes[0].prompts[0] == request
    continuation = runtimes[0].prompts[1].text
    assert "missing-financial-slot" in continuation
    assert "security-entity; fiscal-period" in continuation
    assert "Keep security and period aligned." in continuation
    assert "tool_patterns" not in continuation
    assert "candidate_selection" not in continuation


async def test_task_coverage_skips_runtime_without_native_continuation(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            supports_native_continuation=False,
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder) == 1
    assert len(runtime_holder[0].prompts) == 1
    assert message.assistant_message == "Primary answer."
    assert message.metadata["task_coverage"] == {
        "status": "unavailable",
        "reason": "runtime-native-continuation-unsupported",
    }
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_task_coverage_disabled_runs_only_primary(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, task_coverage_enabled=False)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(args[2])
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder) == 1
    assert len(runtime_holder[0].prompts) == 1
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_task_coverage_may_finish_silently_without_host_confirmation(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            silent_continuation=True,
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder[0].prompts) == 2
    assert message.status == "completed"
    assert message.assistant_message == "Primary answer."
    assert message.metadata["task_coverage"] == {
        "status": "complete",
        "supplemented": False,
        "assistant_segment_indices": [],
    }
    assert [event.type for event in store.appended].count("assistant_message") == 1
    coverage_sidecars = [
        event
        for event in store.appended
        if event.type == "assistant_message_sidecar"
        and isinstance(event.data.get("task_coverage"), dict)
    ]
    assert len(coverage_sidecars) == 1
    assert coverage_sidecars[0].data["assistant_segment_index"] == 0
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_task_coverage_no_gap_uses_private_runtime_noop_without_assistant_event(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            coverage_noop=True,
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    runtime = runtime_holder[0]
    assert len(runtime.prompts) == 2
    assert [tool.name for tool in runtime.coverage_tools] == [TASK_COVERAGE_NOOP_TOOL_NAME]
    assert TASK_COVERAGE_NOOP_TOOL_NAME in runtime.prompts[1].text
    assert message.assistant_message == "Primary answer."
    assert message.metadata["task_coverage"] == {
        "status": "complete",
        "supplemented": False,
        "assistant_segment_indices": [],
        "decision": "no-gap",
    }
    assert [event.type for event in store.appended].count("assistant_message") == 1
    assert all(
        event.data.get("name") != TASK_COVERAGE_NOOP_TOOL_NAME
        for event in store.appended
        if event.type == "tool_use"
    )
    assert all(
        event.data.get("id") != "coverage-noop-1"
        for event in store.appended
        if event.type == "tool_result"
    )


async def test_host_does_not_strip_runtime_authored_progress_or_answer_text(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=False)
    store = _FakeStore(session)
    original = (
        "已经收集到充足的数据，现在撰写完整报告。\n\n"
        "## 结论\n\n所有 Runtime Agent 消息都必须保持可见。"
    )

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(args[2], primary_text=original)

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="只输出最终结论。"),
    )

    assert message.assistant_message == original
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == original


async def test_primary_error_does_not_start_task_coverage(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(args[2], fail_primary=True)
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder[0].prompts) == 1
    assert message.assistant_message == "Primary answer."
    assert message.status == "errored"


async def test_user_interrupt_does_not_start_task_coverage(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            primary_stop_reason=Error(
                category="user_interrupt",
                message="cancelled by user",
            ),
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder[0].prompts) == 1
    assert message.assistant_message == "Primary answer."
    assert message.status == "cancelled"


async def test_budget_exhaustion_does_not_start_task_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            primary_stop_reason=BudgetExhausted(reason="max_turns"),
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder[0].prompts) == 1
    assert message.assistant_message == "Primary answer."
    assert message.status == "completed"


async def test_failed_task_coverage_preserves_primary_output(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            fail_continuation=True,
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder[0].prompts) == 2
    assert message.status == "completed"
    assert message.assistant_message == "Primary answer."
    assert [event.type for event in store.appended].count("assistant_message") == 1
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_task_coverage_exception_preserves_and_finalizes_primary_output(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            raise_continuation=True,
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert len(runtime_holder[0].prompts) == 2
    assert message.status == "completed"
    assert message.assistant_message == "Primary answer."
    assert [event.type for event in store.appended].count("assistant_message") == 1
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_task_coverage_stray_cancellation_preserves_and_finalizes_primary_output(
    tmp_path,
    monkeypatch,
) -> None:
    """A CancelledError escaping the continuation that is NOT a live cancel
    request on this task (the Claude runtime's client rebuild used to leak the
    idle drainer's own cancellation this way) must be absorbed like any other
    continuation failure: the completed primary answer is finalized normally,
    the post-run window closes, and nothing propagates to the host — which
    would otherwise mint ``session_error{category: CancelledError}`` and show a
    "run failed" card under a finished answer."""
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)
    runtime_holder: list[_RecordingRuntime] = []

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        runtime = _RecordingRuntime(
            args[2],
            cancel_continuation="stray",
            called_tool_name="mcp__valuz_docs__document_search",
        )
        runtime_holder.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    async def turn():
        message = await SessionOrchestrator(store).run_turn(
            "owner-1",
            session.id,
            UserMessage(text="Answer normally."),
        )
        current = asyncio.current_task()
        assert current is not None
        return message, current.cancelling()

    message, cancelling = await asyncio.create_task(turn())

    assert cancelling == 0
    assert len(runtime_holder[0].prompts) == 2
    assert message.status == "completed"
    assert message.assistant_message == "Primary answer."
    assert message.metadata["task_coverage"]["status"] == "failed"
    assert message.metadata["task_coverage"]["reason"] == "cancelled"
    types = [event.type for event in store.appended]
    assert types.count("assistant_message") == 1
    assert types.count("session_idle") == 1
    assert types.count("session_error") == 0
    phases = [
        (event.data.get("phase"), event.data.get("state"))
        for event in store.appended
        if event.type == "turn_phase"
    ]
    assert phases == [
        ("post_run_verification", "started"),
        ("post_run_verification", "completed"),
    ]
    assert store._session.status == "idle"


async def test_task_coverage_genuine_cancellation_still_propagates(
    tmp_path,
    monkeypatch,
) -> None:
    """A real cancel of the turn task during the continuation is not ours to
    swallow — it must propagate (after restoring the primary outcome on the
    session) so host teardown semantics hold."""
    session = _session(tmp_path, task_coverage_enabled=True)
    store = _FakeStore(session)

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(
            args[2],
            cancel_continuation="genuine",
            called_tool_name="mcp__valuz_docs__document_search",
        )

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    async def turn():
        await SessionOrchestrator(store).run_turn(
            "owner-1",
            session.id,
            UserMessage(text="Answer normally."),
        )

    task = asyncio.create_task(turn())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    # The turn is not finalized here (no terminal events) — the host
    # classifies the escaped CancelledError as an interruption and closes the
    # turn itself; the primary outcome is restored on the session object.
    types = [event.type for event in store.appended]
    assert types.count("session_idle") == 0
    assert types.count("session_error") == 0
    assert isinstance(session.stop_reason, EndTurn)


async def test_semantic_verifier_factory_is_owner_scoped_and_only_loaded_when_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)
    calls: list[tuple[str, str]] = []

    async def semantic_factory(user_id: str, loaded: Session) -> None:
        calls.append((user_id, loaded.id))
        return None

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(args[2])

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(
        store,
        semantic_verifier_factory=semantic_factory,
    ).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Search the current annual report and explain the result."),
    )

    assert calls == [("owner-1", session.id)]
    assert message.assistant_message == "Primary answer."


async def test_semantic_verifier_factory_failure_never_blocks_primary(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=True,
        verification_enabled=True,
    )
    store = _FakeStore(session)

    async def semantic_factory(_user_id: str, _loaded: Session) -> None:
        raise RuntimeError("verifier setup failed")

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(args[2])

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(
        store,
        semantic_verifier_factory=semantic_factory,
    ).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Search the current annual report and explain the result."),
    )

    assert message.status == "completed"
    assert message.assistant_message == "Primary answer."


async def test_semantic_verifier_factory_is_not_loaded_when_verification_is_disabled(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=True,
        verification_enabled=False,
    )
    store = _FakeStore(session)
    calls = 0

    async def semantic_factory(_user_id: str, _loaded: Session) -> None:
        nonlocal calls
        calls += 1
        return None

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(args[2])

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    await SessionOrchestrator(
        store,
        semantic_verifier_factory=semantic_factory,
    ).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Search the current annual report and explain the result."),
    )

    assert calls == 0


def _text_evidence() -> dict[str, object]:
    return {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2026_q2",
            "source": {
                "sourceId": "doc-revenue-2026-q2",
                "providerId": "reportify",
                "documentId": "doc-revenue-2026-q2",
                "sourceType": "document",
                "title": "Quarterly results",
                "retrievedAt": "2026-08-04T00:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue was 100 USD in 2026 Q2.",
                "snippet": "Revenue was 100 USD in 2026 Q2.",
                "capturedAt": "2026-08-04T00:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-revenue"},
        }
    }


async def test_runtime_message_is_persisted_unchanged_before_citation_sidecar(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=True,
        verification_enabled=False,
    )
    store = _FakeStore(session)
    original = "Revenue was 100 USD in 2026 Q2 [source](evidence://ev_revenue_2026_q2)."

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(
            args[2],
            primary_text=original,
            evidence_payload=_text_evidence(),
        )

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="What was revenue?"),
    )

    relevant = [
        event
        for event in store.appended
        if event.type in {"assistant_message", "assistant_message_sidecar"}
    ]
    assert [event.type for event in relevant] == [
        "assistant_message",
        "assistant_message_sidecar",
    ]
    assert relevant[0].data["text"] == original
    assert "citation_bundle" not in relevant[0].data
    assert relevant[1].data["assistant_segment_index"] == 0
    assert relevant[1].data["citation_bundle"]["citations"]
    assert "quality" not in relevant[1].data["citation_bundle"]
    assert message.assistant_message == original


async def test_audit_only_registers_evidence_without_public_citation_projection(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=False,
        verification_enabled=True,
    )
    store = _FakeStore(session)
    original = "Revenue was 100 USD in 2026 Q2 [source](evidence://ev_revenue_2026_q2)."

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(
            args[2],
            primary_text=original,
            evidence_payload=_text_evidence(),
            called_tool_name="mcp__valuz_docs__document_search",
        )

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="What was revenue?"),
    )

    sidecar = next(event for event in store.appended if event.type == "assistant_message_sidecar")
    assert "citation_bundle" not in sidecar.data
    assert sidecar.data["claim_audit"]["claims"]
    assert "citation_bundle" not in message.metadata
    assert message.metadata["claim_audits"][0]["claims"]
    assert message.assistant_message == original


async def test_all_sidecars_disabled_publish_no_sidecar_event(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(
        tmp_path,
        task_coverage_enabled=False,
        citation_enabled=False,
        verification_enabled=False,
    )
    store = _FakeStore(session)

    def create_runtime(*args, **kwargs) -> _RecordingRuntime:  # noqa: ANN002, ANN003
        return _RecordingRuntime(
            args[2],
            evidence_payload=_text_evidence(),
        )

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    await SessionOrchestrator(store).run_turn(
        "owner-1",
        session.id,
        UserMessage(text="Answer normally."),
    )

    assert not any(event.type == "assistant_message_sidecar" for event in store.appended)
