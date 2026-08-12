"""generate_ui tool — handler + def tests."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401
import valuz_agent.modules.genui.tools as t
from valuz_agent.modules.genui.tools import build_generative_ui_tool_defs


def _ctx(session_id="s1", user_id="u1"):
    ctx = ExecContext(session_id=session_id)
    ctx.user_id = user_id  # HostExecContext adds this at runtime
    return ctx


@pytest.fixture
def patched(monkeypatch):
    async def _get_session(user_id, sid):
        return SimpleNamespace(
            model="claude-sonnet-4-6",
            runtime_provider="claude_agent",
            metadata={"valuz": {"locked_provider_id": "p1"}},
            agent_config=SimpleNamespace(metadata={}),
        )

    async def _resolve(**kw):
        return SimpleNamespace(base_url=None, api_key="k", api_protocol="anthropic")

    monkeypatch.setattr(t.kernel_client, "get_session", _get_session)

    async def _list_messages(user_id, sid, *, limit=1):
        return [SimpleNamespace(user_message=SimpleNamespace(text="请生成销售图表"))]

    monkeypatch.setattr(t.kernel_client, "list_messages", _list_messages)
    monkeypatch.setattr(t, "resolve_model_provider", _resolve)

    async def _fake_completer(prompt):
        return '{"version":"v0.9.1","createSurface":{"surfaceId":"s"}}'

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _fake_completer)

    async def _no_tool_use_id(**kw):
        return None

    monkeypatch.setattr(t, "resolve_tool_use_id", _no_tool_use_id)


async def test_handler_wraps_the_stream_in_the_client_envelope(patched):
    defs = build_generative_ui_tool_defs()
    handler = defs[0].handler
    res = await handler({"request": "sales chart"}, _ctx())
    assert res.is_error is False
    assert json.loads(res.content)["protocol"] == "a2ui-json"


async def test_handler_defaults_to_a2ui_payload(monkeypatch, patched):
    seen = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return '{"version":"v0.9.1","createSurface":{"surfaceId":"dashboard"}}'

    def _make(**kw):
        seen["make_kwargs"] = kw
        return _comp

    monkeypatch.setattr(t, "_make_completer", _make)
    handler = build_generative_ui_tool_defs()[0].handler

    res = await handler({"request": "sales chart"}, _ctx())

    assert res.is_error is False
    assert json.loads(res.content) == {
        "protocol": "a2ui-json",
        "content": '{"version":"v0.9.1","createSurface":{"surfaceId":"dashboard"}}',
    }
    assert "A2UI" in seen["prompt"]
    assert seen["make_kwargs"]["output_format"] == "A2UI v0.9.1 JSON message stream"
    assert "A2UI" in seen["make_kwargs"]["session_instructions"]


async def test_handler_requires_request(patched):
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "   "}, _ctx())
    assert res.is_error is True
    assert "request" in res.content


async def test_handler_rejects_dashboard_when_user_only_asked_for_a_list(monkeypatch, patched):
    async def _list_messages(user_id, sid, *, limit=1):
        # The gate now looks back a few turns, so the whole window has to be
        # free of a visual request for the rejection to be the one under test.
        return [
            SimpleNamespace(user_message=SimpleNamespace(text="列出最近四个季度的数据")),
            SimpleNamespace(user_message=SimpleNamespace(text="这些数字怎么来的")),
        ]

    monkeypatch.setattr(t.kernel_client, "list_messages", _list_messages)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "Create a quarterly dashboard"}, _ctx())

    assert res.is_error is True
    assert "no recent user message asked for" in res.content


async def test_handler_passes_data_into_prompt(monkeypatch, patched):
    seen = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return "Table"

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    handler = build_generative_ui_tool_defs()[0].handler
    await handler({"request": "table", "data": {"rows": [1, 2]}}, _ctx())
    assert "rows" in seen["prompt"]


async def test_hosted_edit_passes_the_bound_a2ui_revision_into_prompt(
    monkeypatch, patched
):
    current = (
        '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}\n'
        '{"version":"v0.9.1","updateComponents":{"surfaceId":"main",'
        '"components":[{"id":"root","component":"Stack"}]}}'
    )
    host = t.UiArtifactTargetHost(
        host_type="finance.research-desk", host_id="desk", slot="main"
    )
    context = t._HostGenerationContext(
        target_host=host,
        expected_revision_id="rev_5",
        current_document=current,
    )
    seen = {}

    async def _load(user_id, target_host):
        assert user_id == "u1"
        assert target_host == host
        return context

    async def _comp(prompt):
        seen["prompt"] = prompt
        return (
            '{"version":"v0.9.1","createSurface":{"surfaceId":"next"}}\n'
            '{"version":"v0.9.1","updateComponents":{"surfaceId":"next",'
            '"components":[{"id":"root","component":"Stack"}]}}'
        )

    async def _deliver(**kwargs):
        seen["delivery"] = kwargs
        return ""

    monkeypatch.setattr(t, "_load_host_generation_context", _load)
    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    monkeypatch.setattr(t, "_deliver_generated_ui", _deliver)
    handler = build_generative_ui_tool_defs()[0].handler

    await handler(
        {
            "request": "change only the chart",
            "target_host": {
                "host_type": host.host_type,
                "host_id": host.host_id,
                "slot": host.slot,
            },
        },
        _ctx(),
    )

    assert current in seen["prompt"]
    assert seen["delivery"]["host_context"] is context


async def test_unhosted_generation_does_not_add_a_current_document(
    monkeypatch, patched
):
    seen = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return '{"version":"v0.9.1","createSurface":{"surfaceId":"new"}}'

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    handler = build_generative_ui_tool_defs()[0].handler
    await handler({"request": "new chart"}, _ctx())

    assert "CURRENT HOST DOCUMENT" not in seen["prompt"]


async def test_handler_no_session(patched, monkeypatch):
    async def _none(user_id, sid):
        return None

    monkeypatch.setattr(t.kernel_client, "get_session", _none)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "x"}, _ctx(session_id=""))
    assert res.is_error is True


async def test_handler_empty_output_is_error(monkeypatch, patched):
    async def _blank(prompt):
        return "   "

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _blank)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "x"}, _ctx())
    assert res.is_error is True


def test_tool_def_shape():
    defs = build_generative_ui_tool_defs()
    assert len(defs) == 1
    assert defs[0].name == "generate_ui"
    assert defs[0].handler is not None
    assert defs[0].parameters["required"] == ["request"]


async def test_handler_resolves_tool_use_id_and_streams(monkeypatch, patched):
    """handler 解析 R 并把 calling_session_id + tool_use_id 透给 completer。"""
    captured: dict = {}

    async def _resolve(**kw):
        captured["resolve_args"] = kw
        return "R-FOUND"

    completer_calls: dict = {}

    async def _comp(prompt):
        completer_calls["prompt"] = prompt
        return "Chart"

    def _make(**kw):
        completer_calls["kw"] = kw
        return _comp

    monkeypatch.setattr(t, "resolve_tool_use_id", _resolve)
    monkeypatch.setattr(t, "_make_completer", _make)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "chart"}, _ctx())
    assert json.loads(res.content)["content"] == "Chart" and res.is_error is False
    assert completer_calls["kw"]["calling_session_id"] == "s1"
    assert completer_calls["kw"]["tool_use_id"] == "R-FOUND"
    assert captured["resolve_args"]["session_id"] == "s1"


async def test_handler_falls_back_to_sync_when_no_r(monkeypatch, patched):
    async def _none(**kw):
        return None

    completer_calls: dict = {}

    def _make(**kw):
        completer_calls["kw"] = kw

        async def _comp(prompt):
            return "Chart"

        return _comp

    monkeypatch.setattr(t, "resolve_tool_use_id", _none)
    monkeypatch.setattr(t, "_make_completer", _make)
    handler = build_generative_ui_tool_defs()[0].handler
    await handler({"request": "chart"}, _ctx())
    assert completer_calls["kw"]["tool_use_id"] is None
    assert completer_calls["kw"]["calling_session_id"] is None


async def test_handler_retries_when_generation_returns_blank(monkeypatch, patched, caplog):
    calls = 0

    async def _no_sleep(seconds):
        return None

    async def _comp(prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "   "
        return "Chart"

    monkeypatch.setattr(t.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    handler = build_generative_ui_tool_defs()[0].handler

    with caplog.at_level(logging.INFO, logger=t.__name__):
        res = await handler({"request": "chart"}, _ctx())

    assert res.is_error is False
    assert json.loads(res.content)["content"] == "Chart"
    assert calls == 2
    assert "generate_ui: generation returned blank output on attempt 1/2" in caplog.text


async def test_handler_retries_when_generation_raises(monkeypatch, patched, caplog):
    calls = 0

    async def _no_sleep(seconds):
        return None

    async def _comp(prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("stream broke")
        return "Chart"

    monkeypatch.setattr(t.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    handler = build_generative_ui_tool_defs()[0].handler

    with caplog.at_level(logging.INFO, logger=t.__name__):
        res = await handler({"request": "chart"}, _ctx())

    assert res.is_error is False
    assert json.loads(res.content)["content"] == "Chart"
    assert calls == 2
    assert "generate_ui: generation attempt 1/2 failed" in caplog.text
