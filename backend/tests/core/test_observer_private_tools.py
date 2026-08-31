"""_MessageObserverSink: host-declared private tools leave no trace.

Protected-builtins v2 runs a decrypt tool whose result is unsealed plaintext.
The model must see that output (it runs inside the runtime loop, before the
observer sees a normalized copy), but the user's transcript and live stream must
not. The observer sits above the persist→broadcast split, so dropping the
private tool's ``tool_use``/``tool_result`` and their streaming deltas here
scrubs BOTH surfaces at once. These tests pin that a matching tool is dropped by
name, by MCP-prefixed name, and by shell command, while unrelated tools and the
turn's other events pass through untouched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink, _session_private_tool_patterns


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _observer(inner: _Recorder) -> _MessageObserverSink:
    return _MessageObserverSink(inner, private_tool_patterns=("unseal",))


@pytest.mark.asyncio
async def test_specific_server_name_matches_but_a_users_own_unseal_survives() -> None:
    """The deployed pattern is the specific server name ``valuz_unseal``, so a
    user's own tool that happens to be named ``unseal`` is NOT scrubbed."""
    inner = _Recorder()
    sink = _MessageObserverSink(inner, private_tool_patterns=("valuz_unseal",))

    # Ours is dropped.
    await sink.emit(Event(type="tool_use", data={"id": "a", "name": "mcp__valuz_unseal__unseal"}))
    await sink.emit(Event(type="tool_result", data={"id": "a", "content": "plaintext"}))
    # A user's own connector tool named `unseal` passes through untouched.
    await sink.emit(Event(type="tool_use", data={"id": "b", "name": "mcp__my_tools__unseal"}))
    await sink.emit(Event(type="tool_result", data={"id": "b", "content": "user output"}))

    assert [e.type for e in inner.events] == ["tool_use", "tool_result"]
    assert inner.events[0].data["name"] == "mcp__my_tools__unseal"


@pytest.mark.asyncio
async def test_input_delta_before_tool_use_is_still_dropped() -> None:
    """The streamed argument deltas of a tool call arrive BEFORE the canonical
    tool_use on Claude/deepseek, so an id-only match would leak them live. They
    carry the tool name, so they must be matched by name too."""
    inner = _Recorder()
    sink = _observer(inner)

    # Delta first (id not yet registered), carrying the tool name.
    await sink.emit(
        Event(
            type="tool_input_delta",
            data={"id": "u1", "name": "mcp__valuz_unseal__unseal", "text": '{"slug'},
        )
    )
    await sink.emit(Event(type="tool_use", data={"id": "u1", "name": "mcp__valuz_unseal__unseal"}))
    await sink.emit(Event(type="tool_output_delta", data={"id": "u1", "text": "KESTREL-7"}))
    await sink.emit(Event(type="tool_result", data={"id": "u1", "content": "plaintext"}))

    assert inner.events == []


def test_env_backstop_supplies_patterns_when_metadata_has_none(monkeypatch) -> None:
    """The scrub fails closed: with no per-session metadata, the process-level
    env still supplies the patterns the observer is built with."""
    monkeypatch.setenv("VALUZ_PRIVATE_TOOL_PATTERNS", "unseal, other")
    session = SimpleNamespace(metadata={})
    assert _session_private_tool_patterns(session) == ("unseal", "other")


def test_env_and_metadata_are_unioned_and_deduped(monkeypatch) -> None:
    monkeypatch.setenv("VALUZ_PRIVATE_TOOL_PATTERNS", "unseal")
    session = SimpleNamespace(metadata={"valuz": {"private_tool_patterns": ["unseal", "extra"]}})
    assert _session_private_tool_patterns(session) == ("unseal", "extra")


def test_no_env_and_no_metadata_is_empty(monkeypatch) -> None:
    monkeypatch.delenv("VALUZ_PRIVATE_TOOL_PATTERNS", raising=False)
    session = SimpleNamespace(metadata={})
    assert _session_private_tool_patterns(session) == ()


@pytest.mark.asyncio
async def test_private_tool_use_and_result_are_dropped_from_both_surfaces() -> None:
    inner = _Recorder()
    sink = _observer(inner)

    await sink.emit(Event(type="tool_use", data={"id": "u1", "name": "mcp__valuz__unseal"}))
    await sink.emit(Event(type="tool_output_delta", data={"id": "u1", "text": "KESTREL-7…"}))
    await sink.emit(Event(type="tool_result", data={"id": "u1", "content": "full plaintext"}))

    assert inner.events == []


@pytest.mark.asyncio
async def test_bare_and_slashed_mcp_spellings_match_the_pattern() -> None:
    for name in ("unseal", "valuz/unseal", "valuz__unseal"):
        inner = _Recorder()
        sink = _observer(inner)
        await sink.emit(Event(type="tool_use", data={"id": "u1", "name": name}))
        await sink.emit(Event(type="tool_result", data={"id": "u1", "content": "plaintext"}))
        assert inner.events == [], name


@pytest.mark.asyncio
async def test_shell_form_is_dropped_by_command_string() -> None:
    inner = _Recorder()
    sink = _observer(inner)

    await sink.emit(
        Event(
            type="tool_use",
            data={
                "id": "b1",
                "name": "Bash",
                "input": {"command": "vzskill unseal dividend-screen"},
            },
        )
    )
    await sink.emit(Event(type="tool_result", data={"id": "b1", "content": "full plaintext"}))

    assert inner.events == []


@pytest.mark.asyncio
async def test_unrelated_tool_passes_through_untouched() -> None:
    inner = _Recorder()
    sink = _observer(inner)

    await sink.emit(Event(type="tool_use", data={"id": "w1", "name": "web_search"}))
    await sink.emit(Event(type="tool_result", data={"id": "w1", "content": "results"}))

    assert [event.type for event in inner.events] == ["tool_use", "tool_result"]


@pytest.mark.asyncio
async def test_no_patterns_means_no_suppression() -> None:
    inner = _Recorder()
    sink = _MessageObserverSink(inner)  # default: no private patterns

    await sink.emit(Event(type="tool_use", data={"id": "u1", "name": "mcp__valuz__unseal"}))
    await sink.emit(Event(type="tool_result", data={"id": "u1", "content": "plaintext"}))

    assert [event.type for event in inner.events] == ["tool_use", "tool_result"]


@pytest.mark.asyncio
async def test_result_without_preceding_use_is_not_dropped() -> None:
    """Suppression keys on the id remembered at ``tool_use``. A stray result
    whose id was never marked private must not be dropped — the correlation, not
    a name guess on the result, is the gate (results carry no tool name)."""
    inner = _Recorder()
    sink = _observer(inner)

    await sink.emit(Event(type="tool_result", data={"id": "orphan", "content": "x"}))

    assert [event.type for event in inner.events] == ["tool_result"]
