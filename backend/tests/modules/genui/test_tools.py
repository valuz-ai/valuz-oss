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

    async def _compiler(**kwargs):
        return t._CompilerModel(
            provider_id=kwargs["source_provider_id"],
            model=kwargs["source_model"],
            runtime_provider=kwargs["source_runtime_provider"],
            model_provider=await _resolve(),
        )

    monkeypatch.setattr(t, "_resolve_compiler_model", _compiler)

    async def _fake_completer(prompt):
        return '{"version":"v0.9.1","createSurface":{"surfaceId":"s"}}'

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _fake_completer)

    async def _no_tool_use_id(**kw):
        return None

    monkeypatch.setattr(t, "resolve_tool_use_id", _no_tool_use_id)


async def test_compiler_prefers_owner_available_valuz_lite(monkeypatch):
    lite_model = SimpleNamespace(
        id="valuz-lite-anthropic",
        label="Valuz Lite",
        runtimes=("claude_agent", "deepagents"),
    )
    channel = SimpleNamespace(
        id="valuz-channel",
        source="system",
        enabled=True,
        compatible_protocols=["anthropic"],
        models=[lite_model],
    )
    resolved = SimpleNamespace(
        base_url="https://gateway.test",
        api_key="key",
        api_protocol="anthropic",
    )

    async def _list(*, user_id):
        assert user_id == "u1"
        return [channel]

    async def _resolve(**kwargs):
        assert kwargs == {
            "user_id": "u1",
            "provider_id": "valuz-channel",
            "model_id": "valuz-lite-anthropic",
            "runtime_provider": "deepagents",
        }
        return resolved

    monkeypatch.setattr(t.ext.llm_provider, "list", _list)
    monkeypatch.setattr(t, "resolve_model_provider", _resolve)

    compiler = await t._resolve_compiler_model(
        user_id="u1",
        source_provider_id="p1",
        source_model="valuz-pro-anthropic",
        source_runtime_provider="claude_agent",
    )

    assert compiler.is_lite is True
    assert compiler.provider_id == "valuz-channel"
    assert compiler.model == "valuz-lite-anthropic"
    assert compiler.runtime_provider == "deepagents"
    assert compiler.model_provider is resolved


async def test_compiler_falls_back_when_lite_is_not_available(monkeypatch):
    async def _list(*, user_id):
        return []

    fallback = SimpleNamespace(
        base_url=None,
        api_key="key",
        api_protocol="anthropic",
    )

    async def _resolve(**kwargs):
        assert kwargs["provider_id"] == "p1"
        assert kwargs["model_id"] == "claude-sonnet-4-6"
        return fallback

    monkeypatch.setattr(t.ext.llm_provider, "list", _list)
    monkeypatch.setattr(t, "resolve_model_provider", _resolve)

    compiler = await t._resolve_compiler_model(
        user_id="u1",
        source_provider_id="p1",
        source_model="claude-sonnet-4-6",
        source_runtime_provider="claude_agent",
    )

    assert compiler.is_lite is False
    assert compiler.model == "claude-sonnet-4-6"
    assert compiler.model_provider is fallback


async def test_handler_falls_back_to_caller_model_when_lite_document_is_invalid(
    monkeypatch, patched
):
    lite = t._CompilerModel(
        provider_id="valuz-channel",
        model="valuz-lite",
        runtime_provider="deepagents",
        model_provider=SimpleNamespace(api_key="lite"),
        is_lite=True,
    )
    caller = t._CompilerModel(
        provider_id="p1",
        model="claude-sonnet-4-6",
        runtime_provider="claude_agent",
        model_provider=SimpleNamespace(api_key="caller"),
    )
    async def _lite(**kwargs):
        return lite

    monkeypatch.setattr(t, "_resolve_compiler_model", _lite)

    async def _source(**kwargs):
        return caller

    monkeypatch.setattr(t, "_resolve_source_compiler_model", _source)
    calls: list[tuple[str, str | None]] = []

    def _make(**kwargs):
        calls.append((kwargs["model"], kwargs["tool_use_id"]))

        async def _complete(prompt):
            if kwargs["model"] == "valuz-lite":
                return "not an A2UI document"
            return '{"version":"v0.9.1","createSurface":{"surfaceId":"s"}}'

        return _complete

    monkeypatch.setattr(t, "_make_completer", _make)
    result = await build_generative_ui_tool_defs()[0].handler(
        {"request": "chart"}, _ctx()
    )

    assert result.is_error is False
    assert calls == [("valuz-lite", None), ("claude-sonnet-4-6", None)]
    assert '"surfaceId":"s"' in json.loads(result.content)["content"]


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
    assert "请生成销售图表" in seen["prompt"]
    assert "Agent-authored REQUEST may be a translation" in seen["prompt"]
    assert seen["make_kwargs"]["output_format"] == "A2UI v0.9.1 JSON message stream"
    assert "A2UI" in seen["make_kwargs"]["session_instructions"]


async def test_hosted_result_tells_the_agent_not_to_recap_live_values(
    monkeypatch, patched
):
    async def _deliver(**kwargs):
        del kwargs
        return ""

    monkeypatch.setattr(t, "_deliver_generated_ui", _deliver)
    res = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "sales chart",
            "target_host": {
                "host_type": "finance.research-desk",
                "host_id": "desk",
            },
        },
        _ctx(),
    )

    payload = json.loads(res.content.split("\n[[ui-artifact-receipt]]", 1)[0])
    assert "exactly one short sentence" in payload["agent_instruction"]
    assert "Do not recap values" in payload["agent_instruction"]


async def test_handler_previews_the_same_final_surface_it_records(monkeypatch, patched):
    first = [
        {"version": "v0.9.1", "createSurface": {"surfaceId": "main"}},
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": "main",
                "components": [
                    {"id": "root", "component": "TextContent", "text": "draft"}
                ],
            },
        },
    ]
    final = [
        {"version": "v0.9.1", "createSurface": {"surfaceId": "main"}},
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": "main",
                "components": [
                    {"id": "root", "component": "TextContent", "text": "final"}
                ],
            },
        },
    ]
    raw = "\n".join(json.dumps(message) for message in first + final)
    delivered: dict[str, str] = {}

    async def _comp(prompt):
        del prompt
        return raw

    async def _deliver(**kwargs):
        delivered["document"] = kwargs["document"]
        return ""

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    monkeypatch.setattr(t, "_deliver_generated_ui", _deliver)

    res = await build_generative_ui_tool_defs()[0].handler(
        {"request": "生成比较图"}, _ctx()
    )
    preview = json.loads(res.content)["content"]

    assert preview == delivered["document"]
    assert "final" in preview
    assert "draft" not in preview
    assert preview.count('"createSurface"') == 1


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


@pytest.mark.parametrize(
    "prompt",
    [
        "请生成一个人工智能主题跟踪页面",
        "请生成一个紧凑的大宗商品行情页面",
        "请生成 AMD 的研究监控页面",
        "Create an English research workspace with two live modules",
    ],
)
def test_visual_intent_accepts_named_generated_pages(prompt):
    messages = [SimpleNamespace(user_message=SimpleNamespace(text=prompt))]
    assert t._requested_visual_output(messages) is True


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


async def test_hosted_replace_does_not_send_the_old_document(monkeypatch, patched):
    host = t.UiArtifactTargetHost(
        host_type="finance.research-desk", host_id="desk", slot="main"
    )
    context = t._HostGenerationContext(
        target_host=host,
        expected_revision_id="rev_5",
        current_document="OLD COMPLETE DOCUMENT",
    )
    seen: dict[str, str] = {}

    async def _load(user_id, target_host):
        return context

    async def _comp(prompt):
        seen["prompt"] = prompt
        return '{"version":"v0.9.1","createSurface":{"surfaceId":"next"}}'

    monkeypatch.setattr(t, "_load_host_generation_context", _load)
    monkeypatch.setattr(t, "_make_completer", lambda **kwargs: _comp)

    await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "rebuild the whole workbench",
            "generation_mode": "replace",
            "target_host": {"host_type": host.host_type, "host_id": host.host_id},
        },
        _ctx(),
    )

    assert "OLD COMPLETE DOCUMENT" not in seen["prompt"]
    assert "CURRENT HOST DOCUMENT" not in seen["prompt"]


async def test_strict_user_scope_overrides_agent_edit_mode(monkeypatch, patched):
    host = t.UiArtifactTargetHost(
        host_type="finance.research-desk", host_id="desk", slot="main"
    )
    context = t._HostGenerationContext(
        target_host=host,
        expected_revision_id="rev_5",
        current_document="OLD COMPLETE DOCUMENT",
    )
    seen: dict[str, str] = {}

    async def _messages(user_id, sid, *, limit=1):
        return [SimpleNamespace(user_message=SimpleNamespace(text="只要一张可刷新折线图"))]

    async def _load(user_id, target_host):
        return context

    async def _comp(prompt):
        seen["prompt"] = prompt
        return '{"version":"v0.9.1","createSurface":{"surfaceId":"next"}}'

    monkeypatch.setattr(t.kernel_client, "list_messages", _messages)
    monkeypatch.setattr(t, "_load_host_generation_context", _load)
    monkeypatch.setattr(t, "_make_completer", lambda **kwargs: _comp)

    await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "add one chart",
            "generation_mode": "edit",
            "target_host": {"host_type": host.host_type, "host_id": host.host_id},
        },
        _ctx(),
    )

    assert "OLD COMPLETE DOCUMENT" not in seen["prompt"]
    assert "CURRENT HOST DOCUMENT" not in seen["prompt"]


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
    assert "component_names" in defs[0].parameters["properties"]
    assert "data_sources" in defs[0].parameters["properties"]
    assert "generation_mode" in defs[0].parameters["properties"]


def test_tool_def_advertises_registered_source_contracts(monkeypatch):
    monkeypatch.setattr(
        t,
        "registered_data_source_tool_guide",
        lambda: "\nRegistered live sources:\n- finance.market.kline {symbols} -> TimeSeriesChart",
    )

    description = build_generative_ui_tool_defs()[0].description

    assert "finance.market.kline {symbols} -> TimeSeriesChart" in description


async def test_handler_rejects_unknown_component_candidate(patched):
    res = await build_generative_ui_tool_defs()[0].handler(
        {"request": "chart", "component_names": ["ImaginaryChart"]}, _ctx()
    )

    assert res.is_error is True
    assert "unknown component" in res.content


async def test_handler_passes_validated_choices_to_the_compiler(monkeypatch, patched):
    monkeypatch.setattr(
        t,
        "registered_data_source_ids",
        lambda: ("finance.market.quote",),
    )
    seen: dict[str, str] = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return "\n".join(
            (
                '{"version":"v0.9.1","createSurface":{"surfaceId":"s"}}',
                '{"version":"v0.9.1","updateDataModel":{"surfaceId":"s","path":"/refs/quote","value":{"source":"finance.market.quote","params":{"symbol":"US:NVDA"}}}}',
                '{"version":"v0.9.1","updateComponents":{"surfaceId":"s","components":[{"id":"root","component":"Stack","children":["metrics"]},{"id":"metrics","component":"MetricGroup","metrics":{"path":"/data/quote/metrics"}}]}}',
            )
        )

    monkeypatch.setattr(t, "_make_completer", lambda **kwargs: _comp)
    result = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "quote strip",
            "component_names": ["TextContent", "MetricGroup"],
            "data_sources": [
                {
                    "slot": "quote",
                    "source": "finance.market.quote",
                    "params": {"symbol": "US:NVDA"},
                    "refresh_interval": 60,
                }
            ],
        },
        _ctx(),
    )

    assert result.is_error is False
    assert "MetricGroup(" in seen["prompt"]
    assert "LineChart(" not in seen["prompt"]
    assert "PLANNED LIVE BINDINGS" in seen["prompt"]
    assert '"source": "finance.market.quote"' in seen["prompt"]


async def test_handler_rejects_missing_registered_source_params(monkeypatch, patched):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip"),
    )
    monkeypatch.setattr(
        t,
        "registered_data_source_ids",
        lambda: ("finance.macro.commodities",),
    )
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.macro.commodities": {
                "required_params": ("commodity",),
                "accepted_components": ("QuoteStrip",),
            }
        },
    )
    result = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "gold chart",
            "component_names": ["QuoteStrip"],
            "data_sources": [
                {
                    "slot": "gold",
                    "source": "finance.macro.commodities",
                    "params": {},
                }
            ],
        },
        _ctx(),
    )

    assert result.is_error is True
    assert "missing required param(s): commodity" in result.content


async def test_handler_rejects_source_without_compatible_component(monkeypatch, patched):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "RelativePerformanceChart", "TimeSeriesChart"),
    )
    monkeypatch.setattr(
        t,
        "registered_data_source_ids",
        lambda: ("finance.market.kline",),
    )
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.kline": {
                "required_params": ("symbols",),
                "accepted_components": ("TimeSeriesChart",),
            }
        },
    )
    result = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "relative chart",
            "component_names": ["RelativePerformanceChart"],
            "data_sources": [
                {
                    "slot": "prices",
                    "source": "finance.market.kline",
                    "params": {"symbols": "US:NVDA,US:AMD"},
                }
            ],
        },
        _ctx(),
    )

    assert result.is_error is True
    assert "requires compatible component(s): TimeSeriesChart" in result.content


def test_generation_choices_normalize_string_lists_and_validate_param_shapes(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "TimeSeriesChart"),
    )
    monkeypatch.setattr(t, "registered_data_source_ids", lambda: ("finance.market.kline",))
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.kline": {
                "required_params": ("symbols",),
                "accepted_components": ("TimeSeriesChart",),
                "param_specs": {
                    "symbols": {"kind": "string", "description": "comma-separated"},
                    "rangeDays": {"kind": "number", "minimum": 2, "maximum": 3650},
                    "normalize": {"kind": "boolean"},
                },
            }
        },
    )

    components, sources, error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        data_sources=[
            {
                "slot": "prices",
                "source": "finance.market.kline",
                "params": {
                    "symbols": ["US:NVDA", "US:AMD"],
                    "rangeDays": 90,
                    "normalize": True,
                },
            }
        ],
    )

    assert error is None
    assert components == ("TimeSeriesChart",)
    assert sources[0]["params"] == {
        "symbols": "US:NVDA,US:AMD",
        "rangeDays": 90,
        "normalize": True,
    }


def test_generation_choices_accept_nested_host_param_reference(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip"),
    )
    monkeypatch.setattr(t, "registered_data_source_ids", lambda: ("finance.market.quote",))
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.quote": {
                "required_params": ("symbol",),
                "accepted_components": ("QuoteStrip",),
                "param_specs": {
                    "symbol": {"kind": "string", "description": "prefixed symbol"},
                },
            }
        },
    )

    _, sources, error = t._validate_generation_choices(
        scope="all",
        component_names=["QuoteStrip"],
        data_sources=[
            {
                "slot": "quote",
                "source": "finance.market.quote",
                "params": {"symbol": {"$host": "symbol"}},
            }
        ],
    )

    assert error is None
    assert sources[0]["params"] == {"symbol": {"$host": "symbol"}}


def test_generation_choices_reject_unknown_host_param_reference(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip"),
    )
    monkeypatch.setattr(t, "registered_data_source_ids", lambda: ("finance.market.quote",))
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.quote": {
                "required_params": ("symbol",),
                "accepted_components": ("QuoteStrip",),
                "param_specs": {
                    "symbol": {"kind": "string", "description": "prefixed symbol"},
                },
            }
        },
    )

    _, _, error = t._validate_generation_choices(
        scope="all",
        component_names=["QuoteStrip"],
        data_sources=[
            {
                "slot": "quote",
                "source": "finance.market.quote",
                "params": {"symbol": {"$host": "company_id"}},
            }
        ],
    )

    assert "compatible host keys: 'symbol'" in str(error)


def test_generation_choices_allow_singular_company_host_for_symbol_list(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "TimeSeriesChart"),
    )
    monkeypatch.setattr(t, "registered_data_source_ids", lambda: ("finance.market.kline",))
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.kline": {
                "required_params": ("symbols",),
                "accepted_components": ("TimeSeriesChart",),
                "param_specs": {
                    "symbols": {
                        "kind": "string",
                        "description": "comma-separated same-market prefixed symbols",
                    },
                    "rangeDays": {"kind": "number", "minimum": 2, "maximum": 3650},
                },
            }
        },
    )

    _, sources, error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        data_sources=[
            {
                "slot": "prices",
                "source": "finance.market.kline",
                "params": {
                    "symbols": {"$host": "symbol"},
                    "rangeDays": 90,
                },
            }
        ],
    )

    assert error is None
    assert sources[0]["params"] == {
        "symbols": {"$host": "symbol"},
        "rangeDays": 90,
    }


def test_generation_choices_reject_unknown_and_out_of_range_params(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "TimeSeriesChart"),
    )
    monkeypatch.setattr(t, "registered_data_source_ids", lambda: ("finance.market.kline",))
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.kline": {
                "required_params": ("symbols",),
                "accepted_components": ("TimeSeriesChart",),
                "param_specs": {
                    "symbols": {"kind": "string", "description": "comma-separated"},
                    "rangeDays": {"kind": "number", "minimum": 2, "maximum": 3650},
                },
            }
        },
    )

    _, _, unknown_error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        data_sources=[
            {
                "slot": "prices",
                "source": "finance.market.kline",
                "params": {"symbols": "US:NVDA", "days": 90},
            }
        ],
    )
    _, _, range_error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        data_sources=[
            {
                "slot": "prices",
                "source": "finance.market.kline",
                "params": {"symbols": "US:NVDA", "rangeDays": 1},
            }
        ],
    )

    assert "unknown param(s): days" in str(unknown_error)
    assert "between 2 and 3650" in str(range_error)


async def test_handler_widens_component_scope_for_registered_candidates(monkeypatch, patched):
    original = t.component_names_for_scope
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": (
            ("Stack", "ResearchBrief")
            if scope == "edition"
            else ("Stack", "ResearchBrief", "TextContent")
        ),
    )
    seen: dict[str, str] = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return "\n".join(
            (
                '{"version":"v0.9.1","createSurface":{"surfaceId":"s"}}',
                '{"version":"v0.9.1","updateComponents":{"surfaceId":"s","components":[{"id":"root","component":"Stack","children":["title"]},{"id":"title","component":"TextContent","text":"Research"}]}}',
            )
        )

    monkeypatch.setattr(t, "_make_completer", lambda **kwargs: _comp)
    result = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "research workspace",
            "components": "edition",
            "component_names": ["ResearchBrief", "TextContent"],
        },
        _ctx(),
    )

    assert result.is_error is False
    assert "TextContent(" in seen["prompt"]
    assert original is not t.component_names_for_scope


def test_compiled_document_validation_rejects_changed_planned_binding(monkeypatch):
    monkeypatch.setattr(
        t,
        "registered_data_source_contracts",
        lambda: {
            "finance.market.kline": {
                "required_params": ("symbols",),
                "accepted_components": ("TimeSeriesChart",),
            }
        },
    )
    document = "\n".join(
        (
            '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}',
            '{"version":"v0.9.1","updateDataModel":{"surfaceId":"main","path":"/refs/prices","value":{"source":"finance.market.daily","params":{"symbol":"US:NVDA"}}}}',
            '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":[{"id":"root","component":"Stack","children":["chart"]},{"id":"chart","component":"TimeSeriesChart","data":{"path":"/data/prices/data"},"series":{"path":"/data/prices/series"},"xKey":"date"}]}}',
        )
    )
    error = t._compiled_document_error(
        document,
        component_names=("TimeSeriesChart",),
        data_sources=(
            {
                "slot": "prices",
                "source": "finance.market.kline",
                "params": {"symbols": "US:NVDA,US:AMD"},
            },
        ),
        current_document=None,
        generation_mode="replace",
    )

    assert error == "compiler changed planned live binding 'prices'"


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
