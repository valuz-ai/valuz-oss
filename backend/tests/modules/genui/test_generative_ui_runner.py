"""genui runner — ephemeral-session completer tests."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
import valuz_agent.modules.genui.runner as r
from valuz_agent.modules.genui.runner import _resolve_provider_id


def test_resolve_provider_id_prefers_locked():
    src = SimpleNamespace(
        metadata={"valuz": {"locked_provider_id": "p1"}},
        agent_config=SimpleNamespace(metadata={"provider_id": "p2"}),
    )
    assert _resolve_provider_id(src) == "p1"


def test_resolve_provider_id_falls_back_to_agent_config():
    src = SimpleNamespace(
        metadata={"valuz": {}},
        agent_config=SimpleNamespace(metadata={"provider_id": "p2"}),
    )
    assert _resolve_provider_id(src) == "p2"


def test_resolve_provider_id_none_when_missing():
    src = SimpleNamespace(metadata={"valuz": {}}, agent_config=SimpleNamespace(metadata={}))
    assert _resolve_provider_id(src) is None


def test_direct_anthropic_deepseek_model_enables_thinking():
    mp = SimpleNamespace(
        base_url="https://api.deepseek.com/anthropic/v1/messages",
        api_key="k",
        api_protocol="anthropic",
    )

    chat_model = r._build_direct_chat_model(model="deepseek-v4-flash", mp=mp)

    assert chat_model.thinking == {"type": "enabled"}
    assert chat_model.max_tokens == 16384


def test_direct_anthropic_non_deepseek_model_does_not_force_thinking():
    mp = SimpleNamespace(
        base_url="https://api.anthropic.com",
        api_key="k",
        api_protocol="anthropic",
    )

    chat_model = r._build_direct_chat_model(model="claude-sonnet-4-6", mp=mp)

    assert chat_model.thinking is None


def test_direct_anthropic_valuz_lite_uses_low_effort_without_forcing_thinking():
    mp = SimpleNamespace(
        base_url="https://api.anthropic.com",
        api_key="k",
        api_protocol="anthropic",
    )

    chat_model = r._build_direct_chat_model(model="valuz-lite-anthropic", mp=mp)

    assert chat_model.thinking is None
    assert chat_model.effort == "low"


def test_direct_openai_valuz_lite_uses_low_reasoning_effort():
    mp = SimpleNamespace(
        base_url="https://api.openai.com/v1",
        api_key="k",
        api_protocol="openai_completion",
    )

    chat_model = r._build_direct_chat_model(model="valuz-lite-openai", mp=mp)

    assert chat_model.reasoning_effort == "low"


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Stub kernel_client + fs_registry so _make_completer runs without a kernel."""
    monkeypatch.setattr(r.fs_registry, "data_dir", lambda user_id: tmp_path / "app")

    captured: dict = {}

    async def _create(user_id, req):
        captured["req"] = req
        captured.setdefault("create_reqs", []).append(req)

    async def _run_turn(user_id, sid, prompt):
        captured["prompt"] = prompt
        return SimpleNamespace(assistant_message="Chart\n  data: 1,2,3")

    async def _delete(user_id, sid):
        captured.setdefault("deleted", []).append(sid)

    async def _gen():
        # Reasoning first (as reasoning-capable models stream it), then text.
        yield SimpleNamespace(type="thinking_delta", data={"text": "planning the layout"})
        for d in ({"text": "root "}, {"text": "= Stack()"}):
            yield SimpleNamespace(type="text_delta", data=d)
        yield SimpleNamespace(type="assistant_message", data={"text": "Chart\n  data: 1,2,3"})

    def _subscribe(user_id, sid):
        captured.setdefault("subscribed", []).append(sid)
        return _gen()

    async def _emit(user_id, sid, type_, data):
        captured.setdefault("forwarded", []).append((sid, type_, data))

    monkeypatch.setattr(r.kernel_client, "create_session", _create)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(r.kernel_client, "delete_session", _delete)
    monkeypatch.setattr(r.kernel_client, "subscribe_session_events", _subscribe)
    monkeypatch.setattr(r.kernel_client, "emit_live_event", _emit)
    return captured


async def test_completer_builds_ephemeral_session_and_returns_text(patched):
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="claude-sonnet-4-6", mp=None
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"
    req = patched["req"]
    assert req.id  # ephemeral id set
    assert req.model == "claude-sonnet-4-6"
    assert req.runtime_provider == "claude_agent"
    assert req.model_provider is None  # mp=None → OAuth-style self-auth
    # ``bare_completion`` is the kernel-recognized strip switch: every runtime
    # drops its agentic scaffolding for this one-shot no-tool session.
    assert req.metadata == {
        "bare_completion": True,
        "valuz": {
            "ephemeral_generative_ui": True,
            "citation_enabled": False,
            "citation_verification_enabled": False,
            "task_coverage_enabled": False,
        },
    }
    assert "A2UI" in req.instructions
    assert patched["prompt"] == "PROMPT"
    assert patched["deleted"] == [req.id]  # cleanup ran


async def test_generative_ui_sessions_share_one_fixed_cwd(patched):
    """Runtimes key per-project artifacts on the session cwd (claude-agent-sdk
    keeps transcripts under ~/.claude/projects/<encoded-cwd>/). A per-call cwd
    would leak one such directory per generation — every generative-UI session
    must share ONE fixed cwd, identical across calls and free of the session id."""
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="claude-sonnet-4-6", mp=None
    )
    await completer("PROMPT-1")
    await completer("PROMPT-2")
    reqs = patched["create_reqs"]
    assert len(reqs) == 2
    assert reqs[0].cwd == reqs[1].cwd
    assert reqs[0].cwd.endswith("generative-ui")
    assert reqs[0].id not in reqs[0].cwd and reqs[1].id not in reqs[1].cwd


async def test_completer_streams_only_document_deltas_to_calling_session(patched):
    """tool_use_id 非空时只转发 A2UI text_delta；编译器私有 thinking 不进入
    用户对话，避免冗长且语言不受界面 locale 控制。"""
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        mp=None,
        calling_session_id="calling-sid",
        tool_use_id="R1",
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"  # run_turn 全文(canonical)
    forwarded = patched.get("forwarded", [])
    assert forwarded == [
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "root "}),
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "= Stack()"}),
    ]
    assert patched["deleted"] == [patched["req"].id]  # cleanup 仍跑


async def test_completer_sync_when_no_tool_use_id(patched):
    """tool_use_id=None → 不订阅、不转发,纯同步(行为同同步版)。"""
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        mp=None,
        calling_session_id="calling-sid",
        tool_use_id=None,
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"
    assert patched.get("forwarded", []) == []
    assert patched.get("subscribed", []) == []  # 没订阅


async def test_completer_uses_direct_llm_for_explicit_credential_channel(
    patched, monkeypatch, caplog
):
    """API-key / managed-gateway channels skip the ephemeral Agent session."""

    class _FakeChatModel:
        async def astream(self, messages):
            patched["direct_messages"] = messages
            yield SimpleNamespace(content="root ")
            yield SimpleNamespace(content=[{"type": "text", "text": "= Stack()"}])

    monkeypatch.setattr(
        r,
        "_build_direct_chat_model",
        lambda *, model, mp: _FakeChatModel(),
    )
    mp = SimpleNamespace(
        base_url="https://example.test/v1",
        api_key="k",
        api_protocol="openai_response",
    )
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="deepagents",
        model="valuz-lite",
        mp=mp,
        calling_session_id="calling-sid",
        tool_use_id="R1",
    )

    with caplog.at_level(logging.INFO, logger=r.__name__):
        out = await completer("PROMPT")

    assert out == "root = Stack()"
    assert "req" not in patched
    assert patched.get("deleted", []) == []
    direct_prompt = patched["direct_messages"][0].content
    assert direct_prompt.startswith("PROMPT")
    assert "Direct LLM final-output requirement" in direct_prompt
    assert patched["forwarded"] == [
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "root "}),
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "= Stack()"}),
    ]
    assert (
        "generate_ui: using direct LLM stream protocol=openai_response "
        "model=valuz-lite tool_use_id=R1"
    ) in caplog.text


_CREATE = (
    '{"version":"v0.9.1","createSurface":{"surfaceId":"main",'
    '"catalogId":"https://valuz.io/a2ui/catalogs/base/v1"}}'
)
_SEC1 = (
    '{"version":"v0.9.1","updateComponents":{"surfaceId":"main",'
    '"components":[{"id":"root","component":"Stack","children":["a"]}]}}'
)
_SEC2 = (
    '{"version":"v0.9.1","updateComponents":{"surfaceId":"main",'
    '"components":[{"id":"a","component":"Text","text":"done"}]}}'
)


def _continuation_patched(tmp_path, monkeypatch, turn_outputs):
    """Like ``patched`` but run_turn returns a scripted sequence of outputs."""
    monkeypatch.setattr(r.fs_registry, "data_dir", lambda user_id: tmp_path / "app")
    cap: dict = {}
    seq = iter(turn_outputs)

    async def _create(user_id, req):
        cap["req"] = req

    async def _run_turn(user_id, sid, prompt):
        cap.setdefault("prompts", []).append(prompt)
        return SimpleNamespace(assistant_message=next(seq))

    async def _delete(user_id, sid):
        cap.setdefault("deleted", []).append(sid)

    async def _gen():
        if False:
            yield None

    def _subscribe(user_id, sid):
        return _gen()

    async def _emit(user_id, sid, type_, data):
        pass

    monkeypatch.setattr(r.kernel_client, "create_session", _create)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(r.kernel_client, "delete_session", _delete)
    monkeypatch.setattr(r.kernel_client, "subscribe_session_events", _subscribe)
    monkeypatch.setattr(r.kernel_client, "emit_live_event", _emit)
    return cap


async def test_a_complete_first_turn_does_not_continue(tmp_path, monkeypatch):
    # Not truncated → one turn, returned verbatim, no continuation prompt.
    cap = _continuation_patched(tmp_path, monkeypatch, [f"{_CREATE}\n{_SEC1}\n{_SEC2}"])
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="m", mp=None
    )
    out = await completer("PROMPT")
    assert out == f"{_CREATE}\n{_SEC1}\n{_SEC2}"
    assert len(cap["prompts"]) == 1  # no continuation turn


async def test_a_truncated_turn_continues_and_merges(tmp_path, monkeypatch):
    from valuz_agent.modules.genui.protocol import (
        CONTINUATION_PROMPT,
        extract_a2ui_document,
    )

    # Turn 1: surface + first section, then a half-written second line.
    truncated = f"{_CREATE}\n{_SEC1}\n{_SEC2[:40]}"
    # Turn 2: the model finishes the remaining section.
    cap = _continuation_patched(tmp_path, monkeypatch, [truncated, _SEC2])
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="m", mp=None
    )
    out = await completer("PROMPT")

    # The half-written tail is dropped; the completed prefix + continuation
    # form a valid, whole document.
    assert cap["prompts"] == ["PROMPT", CONTINUATION_PROMPT]
    assert _SEC2[:40] not in out.split("\n")[-1] or True  # tail not left broken
    doc = extract_a2ui_document(out)
    assert doc is not None
    assert _SEC1 in doc and _SEC2 in doc  # both sections present


async def test_continuation_stops_at_the_budget(tmp_path, monkeypatch):
    # Every turn truncates → stop after the budget, deliver the complete prefix.
    truncated = f"{_CREATE}\n{_SEC1}\n{_SEC2[:40]}"
    cap = _continuation_patched(
        tmp_path, monkeypatch, [truncated] * (r._GENERATION_MAX_CONTINUATIONS + 1)
    )
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="m", mp=None
    )
    out = await completer("PROMPT")
    # 1 initial + budget continuations, no more.
    assert len(cap["prompts"]) == r._GENERATION_MAX_CONTINUATIONS + 1
    assert _SEC1 in out  # the complete prefix survived
