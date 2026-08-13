"""Per-runtime capture of the native fork anchor (docs/design/session-fork.md).

Each runtime records, while ``run()`` streams, the native identifier of the
turn a kernel Message maps to — Claude: the transcript ``uuid`` of the last
main-chain stream message (what ``fork_session(up_to_message_id=...)``
slices on); codex: the ``turn/start`` turn id; deepagents: the settled
langgraph checkpoint id. The orchestrator reads it via
``consume_turn_anchor`` — read-and-clear, so a turn that captures nothing
never inherits the previous message's anchor.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace

import kernel  # noqa: F401 — puts the kernel ``src/`` on sys.path

from claude_agent_sdk import AssistantMessage
from claude_agent_sdk import UserMessage as SdkUserMessage

from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.codex.runtime import CodexRuntime
from src.runtimes.deepagents.runtime import DeepAgentsRuntime, _state_checkpoint_id


# -- Claude: anchor tracks the last main-chain transcript uuid --------------


def _claude_runtime() -> ClaudeAgentRuntime:
    """A ``ClaudeAgentRuntime`` with the SDK-touching ``__init__`` bypassed —
    only the attributes the anchor path reads are set."""
    rt = object.__new__(ClaudeAgentRuntime)
    rt._turn_anchor = None
    return rt


def _claude_session() -> SimpleNamespace:
    return SimpleNamespace(runtime_session_id="sdk-sess-1")


def test_claude_notes_main_chain_assistant_uuid() -> None:
    rt = _claude_runtime()
    rt._note_turn_anchor(
        _claude_session(),
        AssistantMessage(content=[], model="m", uuid="uuid-a1"),
    )
    assert rt._turn_anchor == {
        "provider": "claude_agent",
        "native_session_id": "sdk-sess-1",
        "message_uuid": "uuid-a1",
    }


def test_claude_last_write_wins_including_tool_result_user_messages() -> None:
    rt = _claude_runtime()
    session = _claude_session()
    rt._note_turn_anchor(session, AssistantMessage(content=[], model="m", uuid="uuid-a1"))
    rt._note_turn_anchor(session, SdkUserMessage(content="tool result", uuid="uuid-u2"))
    assert rt._turn_anchor is not None
    assert rt._turn_anchor["message_uuid"] == "uuid-u2"


def test_claude_skips_sidechain_and_uuidless_messages() -> None:
    rt = _claude_runtime()
    session = _claude_session()
    rt._note_turn_anchor(session, AssistantMessage(content=[], model="m", uuid="uuid-a1"))
    # Sidechain (subagent) entries are dropped from forked transcripts —
    # their uuids must never become the anchor.
    rt._note_turn_anchor(
        session,
        AssistantMessage(content=[], model="m", uuid="uuid-side", parent_tool_use_id="tu-1"),
    )
    rt._note_turn_anchor(session, AssistantMessage(content=[], model="m", uuid=None))
    assert rt._turn_anchor is not None
    assert rt._turn_anchor["message_uuid"] == "uuid-a1"


def test_claude_skips_when_native_session_id_unknown() -> None:
    rt = _claude_runtime()
    session = SimpleNamespace(runtime_session_id=None)
    rt._note_turn_anchor(session, AssistantMessage(content=[], model="m", uuid="uuid-a1"))
    assert rt._turn_anchor is None


def test_claude_consume_clears() -> None:
    rt = _claude_runtime()
    rt._note_turn_anchor(_claude_session(), AssistantMessage(content=[], model="m", uuid="u1"))
    assert rt.consume_turn_anchor() is not None
    assert rt.consume_turn_anchor() is None


# -- Codex: consume is read-and-clear ---------------------------------------


def _codex_runtime() -> CodexRuntime:
    from src.core.agent_config import AgentConfig

    return CodexRuntime(config=AgentConfig(id="a", name="a"), model="gpt-5.5", event_sink=None)


def test_codex_consume_clears() -> None:
    rt = _codex_runtime()
    assert rt.consume_turn_anchor() is None
    rt._turn_anchor = {"provider": "codex", "thread_id": "th-1", "turn_id": "turn-1"}
    assert rt.consume_turn_anchor() == {
        "provider": "codex",
        "thread_id": "th-1",
        "turn_id": "turn-1",
    }
    assert rt.consume_turn_anchor() is None


# -- DeepAgents: checkpoint id extraction + consume --------------------------


def test_state_checkpoint_id_reads_configurable() -> None:
    state = SimpleNamespace(config={"configurable": {"thread_id": "t", "checkpoint_id": "ckpt-9"}})
    assert _state_checkpoint_id(state) == "ckpt-9"


def test_state_checkpoint_id_tolerates_missing_shapes() -> None:
    assert _state_checkpoint_id(None) is None
    assert _state_checkpoint_id(SimpleNamespace(config=None)) is None
    assert _state_checkpoint_id(SimpleNamespace(config={})) is None
    assert _state_checkpoint_id(SimpleNamespace(config={"configurable": {}})) is None
    # A fresh thread reports an empty checkpoint_id — not an anchor.
    assert (
        _state_checkpoint_id(SimpleNamespace(config={"configurable": {"checkpoint_id": ""}}))
        is None
    )


def test_deepagents_consume_clears() -> None:
    rt = object.__new__(DeepAgentsRuntime)
    rt._turn_anchor = None
    assert rt.consume_turn_anchor() is None
    rt._turn_anchor = {
        "provider": "deepagents",
        "thread_id": "sess-1",
        "checkpoint_id": "ckpt-2",
        "parent_checkpoint_id": "ckpt-1",
    }
    assert rt.consume_turn_anchor() == {
        "provider": "deepagents",
        "thread_id": "sess-1",
        "checkpoint_id": "ckpt-2",
        "parent_checkpoint_id": "ckpt-1",
    }
    assert rt.consume_turn_anchor() is None
