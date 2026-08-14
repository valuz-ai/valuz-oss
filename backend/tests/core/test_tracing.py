"""Langfuse tracing bootstrap — gating, no-op guarantees, and active wiring.

The active-path tests inject a fake ``langfuse`` into ``sys.modules`` so
they run identically whether or not the optional ``tracing`` extra is
installed.
"""

import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from src.core import tracing

_ENV_KEYS = (
    "LANGFUSE_BASE_URL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "SERVICE_NAME",
)


@pytest.fixture(autouse=True)
def _clean_tracing_state(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    tracing.reset_tracing_for_tests()
    yield
    tracing.reset_tracing_for_tests()


def _set_full_env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.local:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")


@dataclass
class _Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class _FakeObservation:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.updates: list[dict] = []
        self.children: list[_FakeObservation] = []
        self.ended = False

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return self

    def start_observation(self, **kwargs):
        child = _FakeObservation(**kwargs)
        self.children.append(child)
        return child

    def end(self):
        self.ended = True


class _FakeLangfuse:
    instances: list["_FakeLangfuse"] = []

    def __init__(self):
        self.shutdown_calls = 0
        self.observations: list[_FakeObservation] = []
        _FakeLangfuse.instances.append(self)

    def start_observation(self, **kwargs):
        observation = _FakeObservation(**kwargs)
        self.observations.append(observation)
        return observation

    def shutdown(self):
        self.shutdown_calls += 1


class _FakeHandler:
    pass


def _install_fake_sdk(monkeypatch, propagate_captured: list | None = None):
    _FakeLangfuse.instances = []

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        if propagate_captured is not None:
            propagate_captured.append(kwargs)
        yield

    langfuse_mod = types.ModuleType("langfuse")
    langfuse_mod.Langfuse = _FakeLangfuse
    langfuse_mod.propagate_attributes = fake_propagate_attributes
    langchain_mod = types.ModuleType("langfuse.langchain")
    langchain_mod.CallbackHandler = _FakeHandler
    langfuse_mod.langchain = langchain_mod

    monkeypatch.setitem(sys.modules, "langfuse", langfuse_mod)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", langchain_mod)


def _init_active(monkeypatch, propagate_captured: list | None = None):
    _set_full_env(monkeypatch)
    _install_fake_sdk(monkeypatch, propagate_captured)
    assert tracing.init_tracing() is True


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_disabled_without_env():
    assert tracing.tracing_configured() is False
    assert tracing.init_tracing() is False
    assert tracing.tracing_active() is False
    assert tracing.langchain_config_overlay(session_id="s1", user_id="u1") == {}
    assert tracing.start_turn_trace(runtime_provider="claude_agent", prompt="p") is None
    # The turn context must be a usable no-op.
    with tracing.turn_trace_context(user_id="u1", session_id="s1", message_id="m1"):
        pass


def test_partial_env_stays_disabled(monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.local:3000")
    assert tracing.tracing_configured() is False
    assert tracing.init_tracing() is False


def test_missing_package_stays_disabled(monkeypatch):
    _set_full_env(monkeypatch)
    # ``None`` in sys.modules makes ``import langfuse`` raise ImportError even
    # when the package is actually installed.
    monkeypatch.setitem(sys.modules, "langfuse", None)
    assert tracing.tracing_configured() is True
    assert tracing.init_tracing() is False


def test_init_is_idempotent(monkeypatch):
    _init_active(monkeypatch)
    assert tracing.init_tracing() is True
    assert len(_FakeLangfuse.instances) == 1


def test_deepagents_is_not_event_traced(monkeypatch):
    """deepagents rides the native LangChain handler — no TurnTrace."""
    _init_active(monkeypatch)
    assert tracing.start_turn_trace(runtime_provider="deepagents", prompt="p") is None
    assert tracing.start_turn_trace(runtime_provider="codex", prompt="p") is not None


def test_deepseek_harness_is_event_traced(monkeypatch):
    """dsh emits the standard cross-runtime events — TurnTrace applies."""
    _init_active(monkeypatch)
    assert tracing.start_turn_trace(runtime_provider="deepseek_harness", prompt="p") is not None


# ---------------------------------------------------------------------------
# Attribution + LangChain overlay + shutdown
# ---------------------------------------------------------------------------


def test_active_wiring_and_shutdown(monkeypatch):
    captured: list = []
    _init_active(monkeypatch, propagate_captured=captured)

    overlay = tracing.langchain_config_overlay(session_id="sess-1", user_id="user-1")
    assert isinstance(overlay["callbacks"][0], _FakeHandler)
    assert overlay["metadata"] == {
        "langfuse_session_id": "sess-1",
        "langfuse_user_id": "user-1",
    }
    # Empty owner (kernel Session.user_id defaults to "") must not emit a key.
    assert tracing.langchain_config_overlay(session_id="sess-1", user_id="")["metadata"] == {
        "langfuse_session_id": "sess-1"
    }

    with tracing.turn_trace_context(user_id="user-1", session_id="sess-1", message_id="m-1"):
        pass
    assert captured == [
        {"user_id": "user-1", "session_id": "sess-1", "metadata": {"message_id": "m-1"}}
    ]

    # Deploy-time SERVICE_NAME rides along in the propagated metadata.
    monkeypatch.setenv("SERVICE_NAME", "valuz-backend-team")
    with tracing.turn_trace_context(user_id="user-1", session_id="sess-1", message_id="m-2"):
        pass
    assert captured[-1]["metadata"] == {
        "message_id": "m-2",
        "service_name": "valuz-backend-team",
    }

    client = _FakeLangfuse.instances[0]
    tracing.shutdown_tracing()
    assert client.shutdown_calls == 1
    assert tracing.tracing_active() is False
    assert tracing.langchain_config_overlay(session_id="s", user_id="u") == {}
    tracing.shutdown_tracing()  # safe to call twice
    assert client.shutdown_calls == 1


# ---------------------------------------------------------------------------
# TurnTrace: event-driven observation tree
# ---------------------------------------------------------------------------


def _turn(monkeypatch, runtime="claude_agent"):
    _init_active(monkeypatch)
    trace = tracing.start_turn_trace(runtime_provider=runtime, prompt="user asks")
    assert trace is not None
    return trace, _FakeLangfuse.instances[0].observations[0]


def test_turn_root_observation_shape(monkeypatch):
    _trace, root = _turn(monkeypatch, runtime="codex")
    assert root.kwargs == {
        "name": "codex.turn",
        "as_type": "agent",
        "input": "user asks",
        "metadata": {"runtime": "codex"},
    }


def test_tool_call_result_pair_opens_and_closes_child(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("tool_use", {"id": "t1", "name": "Bash", "input": {"cmd": "ls"}}))
    tool = root.children[0]
    assert tool.kwargs == {"name": "Bash", "as_type": "tool", "input": {"cmd": "ls"}}
    assert tool.ended is False

    trace.observe(_Event("tool_result", {"id": "t1", "content": "ok", "is_error": False}))
    assert tool.updates == [{"output": "ok"}]
    assert tool.ended is True


def test_tool_error_marks_child_span(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("tool_use", {"id": "t1", "name": "Bash", "input": {}}))
    trace.observe(_Event("tool_result", {"id": "t1", "content": "boom", "is_error": True}))
    assert root.children[0].updates == [{"output": "boom", "level": "ERROR"}]


def test_subagent_tools_nest_under_task_tool(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("tool_use", {"id": "task1", "name": "Task", "input": {}}))
    trace.observe(
        _Event(
            "tool_use",
            {"id": "sub1", "name": "Read", "input": {}, "parent_tool_use_id": "task1"},
        )
    )
    task = root.children[0]
    assert task.children[0].kwargs["name"] == "Read"
    trace.observe(_Event("tool_result", {"id": "sub1", "content": "", "is_error": False}))
    trace.observe(_Event("tool_result", {"id": "task1", "content": "done", "is_error": False}))
    assert task.children[0].ended is True
    assert task.ended is True


def test_huge_tool_output_is_truncated(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("tool_use", {"id": "t1", "name": "Bash", "input": {}}))
    trace.observe(_Event("tool_result", {"id": "t1", "content": "x" * 100_000}))
    output = root.children[0].updates[0]["output"]
    assert len(output) < 50_000
    assert output.endswith("… [truncated]")


def test_output_usage_and_end(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("assistant_message", {"text": "part one"}))
    # Subagent-internal text must not pollute the turn output.
    trace.observe(_Event("assistant_message", {"text": "sub", "parent_tool_use_id": "t9"}))
    trace.observe(_Event("assistant_message", {"text": "part two"}))
    # Last usage_update wins (claude re-emits per wake-up bracket).
    trace.observe(_Event("usage_update", {"input_tokens": 1, "output_tokens": 1}))
    trace.observe(
        _Event(
            "usage_update",
            {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_read_tokens": 7,
                "cache_write_tokens": 0,
                "model_usage": {"claude-x": {}},
                "cost_usd": 0.42,
            },
        )
    )
    trace.end()

    generation = root.children[0]
    assert generation.kwargs["name"] == "usage"
    assert generation.kwargs["as_type"] == "generation"
    assert generation.updates == [
        {
            "model": "claude-x",
            "usage_details": {"input": 100, "output": 25, "cache_read": 7, "cache_write": 0},
            "cost_details": {"total": 0.42},
        }
    ]
    assert generation.ended is True
    assert {"output": "part one\n\npart two"} in root.updates
    assert root.ended is True

    # Idempotent + post-end events ignored.
    updates_after_end = list(root.updates)
    trace.end(error="late")
    trace.observe(_Event("assistant_message", {"text": "ghost"}))
    assert root.updates == updates_after_end


def test_end_closes_in_flight_tools_and_marks_error(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("tool_use", {"id": "t1", "name": "Bash", "input": {}}))
    trace.end(error="user_interrupt: cancelled")
    assert root.children[0].ended is True
    assert {"level": "ERROR", "status_message": "user_interrupt: cancelled"} in root.updates


def test_session_error_event_marks_turn(monkeypatch):
    trace, root = _turn(monkeypatch)
    trace.observe(_Event("session_error", {"message": "cli exploded"}))
    trace.end()
    assert {"level": "ERROR", "status_message": "cli exploded"} in root.updates


async def test_tracing_sink_forwards_and_survives_observe_errors(monkeypatch):
    trace, _root = _turn(monkeypatch)

    class _Recorder:
        def __init__(self):
            self.events = []

        async def emit(self, event):
            self.events.append(event)

    inner = _Recorder()
    sink = tracing.TurnTracingSink(inner, trace)
    bad = _Event("tool_use", {"id": "t1", "name": "Bash", "input": {}})
    # Force an observe failure: a broken observation factory must not stop
    # the event from reaching the inner sink.
    trace._observation.start_observation = None  # type: ignore[assignment]
    await sink.emit(bad)
    assert inner.events == [bad]
