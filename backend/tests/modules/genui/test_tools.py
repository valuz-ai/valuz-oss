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


async def test_handler_normalizes_json_encoded_data_object(monkeypatch, patched):
    seen = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return "Table"

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    handler = build_generative_ui_tool_defs()[0].handler
    result = await handler(
        {"request": "research card", "data": '{"thesis":"AI demand persists"}'},
        _ctx(),
    )

    assert result.is_error is False
    assert '"thesis": "AI demand persists"' in seen["prompt"]


def test_tool_schema_requires_data_object():
    schema = build_generative_ui_tool_defs()[0].parameters

    assert schema["properties"]["data"]["type"] == "object"
    assert "oneOf" not in schema["properties"]["data"]


async def test_handler_rejects_non_object_json_encoded_data(patched):
    handler = build_generative_ui_tool_defs()[0].handler

    result = await handler({"request": "research card", "data": "[1,2]"}, _ctx())

    assert result.is_error is True
    assert "'data' must be an object" in result.content


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
            "generation_mode": "edit",
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


async def test_hosted_default_replaces_without_old_document(monkeypatch, patched):
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
            "request": "build a research card",
            "target_host": {"host_type": host.host_type, "host_id": host.host_id},
        },
        _ctx(),
    )

    assert "OLD COMPLETE DOCUMENT" not in seen["prompt"]
    assert "CURRENT HOST DOCUMENT" not in seen["prompt"]


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
    assert "component_data" in defs[0].parameters["properties"]
    assert "data_sources" not in defs[0].parameters["properties"]
    assert "generation_mode" in defs[0].parameters["properties"]


def test_tool_def_advertises_registered_component_contracts(monkeypatch):
    monkeypatch.setattr(
        t,
        "registered_component_data_tool_guide",
        lambda: "\nRegistered query components:\n- TimeSeriesChart {symbols}",
    )

    description = build_generative_ui_tool_defs()[0].description

    assert "TimeSeriesChart {symbols}" in description


def test_tool_def_exposes_exact_registered_component_param_schema(monkeypatch):
    monkeypatch.setattr(
        t,
        "registered_component_data_contracts",
        lambda: {
            "TimeSeriesChart": {
                "component": "TimeSeriesChart",
                "source": "finance.market.kline",
                "required_params": ("symbols",),
                "param_specs": {
                    "symbols": {
                        "kind": "string",
                        "description": "comma-separated prefixed symbols",
                    },
                    "rangeDays": {
                        "kind": "number",
                        "minimum": 2,
                        "maximum": 3650,
                        "required": False,
                    },
                },
            }
        },
    )
    monkeypatch.setattr(
        t,
        "component_property_names",
        lambda name: ("metrics",) if name == "QuoteStrip" else (),
    )

    items = build_generative_ui_tool_defs()[0].parameters["properties"][
        "component_data"
    ]["items"]
    variant = items["oneOf"][0]
    params = variant["properties"]["params"]

    assert variant["properties"]["component"]["enum"] == ["TimeSeriesChart"]
    assert set(variant["properties"]) == {"component", "params"}
    assert params["required"] == ["symbols"]
    assert params["additionalProperties"] is False
    assert params["properties"]["rangeDays"]["oneOf"][0] == {
        "type": "number",
        "minimum": 2,
        "maximum": 3650,
    }
    assert params["properties"]["symbols"]["oneOf"][1]["properties"]["$host"][
        "enum"
    ] == ["symbols", "symbol"]


async def test_handler_rejects_unknown_component_candidate(patched):
    res = await build_generative_ui_tool_defs()[0].handler(
        {"request": "chart", "component_names": ["ImaginaryChart"]}, _ctx()
    )

    assert res.is_error is True
    assert "unknown component" in res.content


async def test_handler_passes_validated_choices_to_the_compiler(monkeypatch, patched):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip"),
    )
    monkeypatch.setattr(
        t,
        "registered_component_data_names",
        lambda: ("QuoteStrip",),
    )
    monkeypatch.setattr(
        t,
        "registered_component_data_contracts",
        lambda: {
            "QuoteStrip": {
                "component": "QuoteStrip",
                "required_params": ("symbol",),
                "param_specs": {"symbol": {"kind": "string"}},
                "inputs": ({
                    "key": "main",
                    "source": "finance.market.quote",
                    "shape": "FinanceMetricData",
                    "bindings": {"metrics": "metrics"},
                    "fixed_params": {},
                    "param_map": {},
                    "refresh_interval": 60,
                },),
                "fixed_props": {},
            }
        },
    )
    monkeypatch.setattr(
        t,
        "component_property_names",
        lambda name: ("metrics",) if name == "QuoteStrip" else (),
    )
    seen: dict[str, str] = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return "\n".join(
            (
                '{"version":"v0.9.1","createSurface":{"surfaceId":"s"}}',
                '{"version":"v0.9.1","updateComponents":{"surfaceId":"s","components":[{"id":"root","component":"Stack","children":["quote"]},{"id":"quote","component":"QuoteStrip"}]}}',
            )
        )

    monkeypatch.setattr(t, "_make_completer", lambda **kwargs: _comp)
    result = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "quote strip",
            "component_names": ["QuoteStrip"],
            "component_data": [
                {
                    "component": "QuoteStrip",
                    "params": {"symbol": "US:NVDA"},
                }
            ],
        },
        _ctx(),
    )

    assert result.is_error is False
    assert "PLANNED QUERY COMPONENTS" in seen["prompt"]
    assert '"component": "QuoteStrip"' in seen["prompt"]
    assert "finance.market.quote" not in seen["prompt"]


async def test_handler_rejects_missing_registered_source_params(monkeypatch, patched):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip"),
    )
    monkeypatch.setattr(
        t,
        "registered_component_data_names",
        lambda: ("QuoteStrip",),
    )
    monkeypatch.setattr(
        t,
        "registered_component_data_contracts",
        lambda: {
            "QuoteStrip": {
                "component": "QuoteStrip",
                "required_params": ("commodity",),
                "param_specs": {"commodity": {"kind": "string"}},
                "inputs": ({
                    "key": "main",
                    "source": "finance.macro.commodities",
                    "bindings": {"metrics": "metrics"},
                    "fixed_params": {},
                    "param_map": {},
                },),
            }
        },
    )
    result = await build_generative_ui_tool_defs()[0].handler(
        {
            "request": "gold chart",
            "component_names": ["QuoteStrip"],
            "component_data": [
                {
                    "component": "QuoteStrip",
                    "params": {},
                }
            ],
        },
        _ctx(),
    )

    assert result.is_error is True
    assert "missing required param(s): commodity" in result.content


def _component_contracts():
    return {
        "QuoteStrip": {
            "component": "QuoteStrip",
            "required_params": ("symbol",),
            "param_specs": {
                "symbol": {"kind": "string", "description": "prefixed symbol"}
            },
            "inputs": ({
                "key": "main",
                "source": "finance.market.quote",
                "shape": "FinanceMetricData",
                "bindings": {
                    "metrics": "metrics",
                    "source": "source",
                    "asOf": "asOf",
                    "basis": "basis",
                },
                "fixed_params": {},
                "param_map": {},
                "refresh_interval": 60,
            },),
            "fixed_props": {},
        },
        "TimeSeriesChart": {
            "component": "TimeSeriesChart",
            "required_params": ("symbols",),
            "param_specs": {
                "symbols": {
                    "kind": "string",
                    "description": "comma-separated same-market prefixed symbols",
                },
                "rangeDays": {"kind": "number", "minimum": 2, "maximum": 3650},
                "normalize": {"kind": "boolean"},
            },
            "inputs": ({
                "key": "main",
                "source": "finance.market.kline",
                "shape": "FinanceTimeSeriesData",
                "bindings": {"data": "data", "series": "series"},
                "fixed_params": {},
                "param_map": {},
                "refresh_interval": 300,
            },),
            "fixed_props": {"xKey": "date"},
        },
        "CompanyResearchOverview": {
            "component": "CompanyResearchOverview",
            "required_params": ("symbol",),
            "param_specs": {
                "symbol": {"kind": "string", "description": "prefixed symbol"}
            },
            "inputs": (
                {
                    "key": "quote",
                    "source": "finance.market.quote",
                    "shape": "FinanceMetricData",
                    "bindings": {"quoteMetrics": "metrics"},
                    "fixed_params": {},
                    "param_map": {"symbol": "symbol"},
                    "refresh_interval": 30,
                },
                {
                    "key": "financials",
                    "source": "finance.financials.income",
                    "shape": "FinanceTrendData",
                    "bindings": {"financialItems": "items"},
                    "fixed_params": {"metric": "revenue", "period": "annual"},
                    "param_map": {},
                    "refresh_interval": 300,
                },
                {
                    "key": "documents",
                    "source": "finance.company.docs",
                    "shape": "FinanceDocumentData",
                    "bindings": {"documentItems": "items"},
                    "fixed_params": {},
                    "param_map": {"symbol": "symbol"},
                    "refresh_interval": 60,
                },
            ),
            "fixed_props": {},
        },
    }


def _patch_component_contracts(monkeypatch):
    contracts = _component_contracts()
    monkeypatch.setattr(t, "registered_component_data_contracts", lambda: contracts)
    monkeypatch.setattr(t, "registered_component_data_names", lambda: tuple(contracts))


def test_generation_choices_add_bound_component(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "RelativePerformanceChart", "TimeSeriesChart"),
    )
    _patch_component_contracts(monkeypatch)
    components, plans, error = t._validate_generation_choices(
        scope="all",
        component_names=["RelativePerformanceChart"],
        component_data=[
            {
                "component": "TimeSeriesChart",
                "params": {"symbols": "US:NVDA,US:AMD"},
            }
        ],
    )
    assert error is None
    assert components == ("RelativePerformanceChart", "TimeSeriesChart")
    assert plans[0]["inputs"][0]["source"] == "finance.market.kline"
    assert plans[0]["inputs"][0]["params"] == {
        "symbols": "US:NVDA,US:AMD"
    }


def test_generation_choices_projects_one_business_param_into_multiple_named_inputs(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "CompanyResearchOverview"),
    )
    _patch_component_contracts(monkeypatch)

    components, plans, error = t._validate_generation_choices(
        scope="all",
        component_names=["CompanyResearchOverview"],
        component_data=[{
            "component": "CompanyResearchOverview",
            "params": {"symbol": "US:NVDA"},
        }],
    )

    assert error is None
    assert components == ("CompanyResearchOverview",)
    assert [value["key"] for value in plans[0]["inputs"]] == [
        "quote", "financials", "documents"
    ]
    assert [value["params"] for value in plans[0]["inputs"]] == [
        {"symbol": "US:NVDA"},
        {"symbol": "US:NVDA", "metric": "revenue", "period": "annual"},
        {"symbol": "US:NVDA"},
    ]


def test_generation_choices_adds_only_available_composition_glue(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "InsightCard", "Card", "Grid", "TextContent", "Separator"),
    )
    components, plans, error = t._validate_generation_choices(
        scope="all", component_names=["InsightCard"], component_data=None
    )
    assert error is None
    assert components == ("InsightCard", "Card", "Grid", "TextContent", "Separator")
    assert plans == ()


def test_generation_choices_normalize_and_validate_params(monkeypatch):
    monkeypatch.setattr(
        t, "component_names_for_scope", lambda scope="all": ("Stack", "TimeSeriesChart")
    )
    _patch_component_contracts(monkeypatch)
    _, plans, error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        component_data=[{
            "component": "TimeSeriesChart",
            "params": {"symbols": ["US:NVDA", "US:AMD"], "rangeDays": 90, "normalize": True},
        }],
    )
    assert error is None
    assert plans[0]["params"] == {
        "symbols": "US:NVDA,US:AMD", "rangeDays": 90, "normalize": True
    }


def test_generation_choices_accept_host_refs_and_symbol_alias(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip", "TimeSeriesChart"),
    )
    _patch_component_contracts(monkeypatch)
    components, plans, error = t._validate_generation_choices(
        scope="all",
        component_names=[],
        component_data=[{"component": "QuoteStrip", "params": {"symbols": {"$host": "symbol"}}}],
    )
    assert error is None
    assert components == ("QuoteStrip",)
    assert plans[0]["params"] == {"symbol": {"$host": "symbol"}}

    _, list_plans, list_error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        component_data=[{
            "component": "TimeSeriesChart",
            "params": {"symbols": {"$host": "symbol"}, "rangeDays": 90},
        }],
    )
    assert list_error is None
    assert list_plans[0]["params"]["symbols"] == {"$host": "symbol"}


def test_generation_choices_reject_bad_host_unknown_and_range(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_names_for_scope",
        lambda scope="all": ("Stack", "QuoteStrip", "TimeSeriesChart"),
    )
    _patch_component_contracts(monkeypatch)
    _, _, host_error = t._validate_generation_choices(
        scope="all",
        component_names=["QuoteStrip"],
        component_data=[{"component": "QuoteStrip", "params": {"symbol": {"$host": "company_id"}}}],
    )
    _, _, unknown_error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        component_data=[{
            "component": "TimeSeriesChart",
            "params": {"symbols": "US:NVDA", "days": 90},
        }],
    )
    _, _, range_error = t._validate_generation_choices(
        scope="all",
        component_names=["TimeSeriesChart"],
        component_data=[{
            "component": "TimeSeriesChart",
            "params": {"symbols": "US:NVDA", "rangeDays": 1},
        }],
    )
    assert "compatible host keys: 'symbol'" in str(host_error)
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
        "registered_component_data_contracts",
        lambda: {
            "TimeSeriesChart": {
                "inputs": ({
                    "key": "main",
                    "source": "finance.market.kline",
                    "bindings": {"data": "data", "series": "series"},
                },),
            }
        },
    )
    document = "\n".join(
        (
            '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}',
            '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":[{"id":"root","component":"Stack","children":["chart"]},{"id":"chart","component":"TimeSeriesChart","dataRefs":{"main":{"source":"finance.market.daily","params":{"symbol":"US:NVDA"}}},"data":{"path":"/data/chart/main/data"},"series":{"path":"/data/chart/main/series"},"xKey":"date"}]}}',
        )
    )
    error = t._compiled_document_error(
        document,
        component_names=("TimeSeriesChart",),
        component_data=(
            {
                "component": "TimeSeriesChart",
                "params": {"symbols": "US:NVDA,US:AMD"},
                "inputs": ({
                    "key": "main",
                    "source": "finance.market.kline",
                    "params": {"symbols": "US:NVDA,US:AMD"},
                    "bindings": {"data": "data", "series": "series"},
                },),
            },
        ),
        current_document=None,
        generation_mode="replace",
    )

    assert error == "planned query component 'TimeSeriesChart' is missing its canonical dataRefs"


def test_ensure_planned_component_data_refs_upserts_validated_plan(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_property_names",
        lambda name: (
            ("title", "metrics", "source", "asOf", "basis")
            if name == "QuoteStrip"
            else ("data", "series", "xKey")
        ),
    )
    document = "\n".join(
        (
            '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}',
            '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":[{"id":"root","component":"Stack","children":["quote","chart"]},{"id":"quote","component":"QuoteStrip"},{"id":"chart","component":"TimeSeriesChart"}]}}',
        )
    )

    completed = t._ensure_planned_component_data_refs(
        document,
        (
            {
                "component": "QuoteStrip",
                "params": {"symbol": {"$host": "symbol"}},
                "inputs": ({
                    "key": "main",
                    "source": "finance.market.quote",
                    "params": {"symbol": {"$host": "symbol"}},
                    "shape": "FinanceMetricData",
                    "bindings": {
                        "metrics": "metrics",
                        "source": "source",
                        "asOf": "asOf",
                        "basis": "basis",
                    },
                },),
            },
            {
                "component": "TimeSeriesChart",
                "params": {"symbols": {"$host": "symbol"}, "rangeDays": 90},
                "inputs": ({
                    "key": "main",
                    "source": "finance.market.kline",
                    "params": {"symbols": {"$host": "symbol"}, "rangeDays": 90},
                    "shape": "FinanceTimeSeriesData",
                    "bindings": {"data": "data", "series": "series"},
                    "refresh_interval": 300,
                },),
                "fixed_props": {"xKey": "date"},
            },
        ),
    )

    messages = [json.loads(line) for line in str(completed).splitlines()]
    quote = next(
        component
        for message in messages
        for component in (message.get("updateComponents") or {}).get("components", ())
        if component.get("id") == "quote"
    )
    assert quote["dataRefs"] == {
        "main": {
            "source": "finance.market.quote",
            "params": {"symbol": {"$host": "symbol"}},
            "shape": "FinanceMetricData",
        }
    }
    assert quote["source"] == {"path": "/data/quote/main/source"}
    assert quote["asOf"] == {"path": "/data/quote/main/asOf"}
    assert quote["basis"] == {"path": "/data/quote/main/basis"}
    chart = next(
        component
        for message in messages
        for component in (message.get("updateComponents") or {}).get("components", ())
        if component.get("id") == "chart"
    )
    assert chart["dataRefs"]["main"]["source"] == "finance.market.kline"
    assert chart["dataRefs"]["main"]["refresh"] == {"interval": 300}
    assert chart["data"] == {"path": "/data/chart/main/data"}
    assert chart["xKey"] == "date"


def test_ensure_planned_component_data_refs_writes_every_named_input(monkeypatch):
    monkeypatch.setattr(
        t,
        "component_property_names",
        lambda name: (
            "title", "quoteMetrics", "financialItems", "documentItems"
        ) if name == "CompanyResearchOverview" else (),
    )
    document = "\n".join((
        '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}',
        '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":['
        '{"id":"company","component":"CompanyResearchOverview","title":"NVIDIA"}]}}',
    ))
    plan = _component_contracts()["CompanyResearchOverview"]
    completed = t._ensure_planned_component_data_refs(
        document,
        ({
            "component": "CompanyResearchOverview",
            "params": {"symbol": "US:NVDA"},
            "inputs": tuple({
                **value,
                "params": {
                    **value["fixed_params"],
                    **{
                        source_name: "US:NVDA"
                        for source_name in value["param_map"]
                    },
                },
            } for value in plan["inputs"]),
            "fixed_props": {},
        },),
    )

    messages = [json.loads(line) for line in str(completed).splitlines()]
    company = next(
        component
        for message in messages
        for component in (message.get("updateComponents") or {}).get("components", ())
        if component.get("id") == "company"
    )
    assert set(company["dataRefs"]) == {"quote", "financials", "documents"}
    assert company["quoteMetrics"] == {"path": "/data/company/quote/metrics"}
    assert company["financialItems"] == {"path": "/data/company/financials/items"}
    assert company["documentItems"] == {"path": "/data/company/documents/items"}


def test_planned_component_data_does_not_rewrite_a_different_component(monkeypatch):
    monkeypatch.setattr(
        t,
        "registered_component_data_contracts",
        lambda: {
            "QuoteStrip": {"source": "finance.market.quote"},
        },
    )
    monkeypatch.setattr(
        t,
        "component_property_names",
        lambda name: (
            ("title", "metrics", "source", "asOf", "basis")
            if name == "QuoteStrip"
            else ()
        ),
    )
    document = "\n".join(
        (
            '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}',
            '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":[{"id":"root","component":"Stack","children":["quote"]},{"id":"quote","component":"SecuritySnapshot","title":{"path":"/data/quote/title"},"metrics":{"path":"/data/quote/metrics"},"items":{"path":"/data/quote/items"}}]}}',
        )
    )

    completed = t._ensure_planned_component_data_refs(
        document,
        (
            {
                "component": "QuoteStrip",
                "source": "finance.market.quote",
                "params": {"symbol": {"$host": "symbol"}},
                "bindings": ("metrics", "source", "asOf", "basis"),
            },
        ),
    )

    messages = [json.loads(line) for line in str(completed).splitlines()]
    snapshot = next(
        component
        for message in messages
        for component in (message.get("updateComponents") or {}).get("components", ())
        if component.get("id") == "quote"
    )
    assert snapshot == {
        "id": "quote",
        "component": "SecuritySnapshot",
        "title": {"path": "/data/quote/title"},
        "metrics": {"path": "/data/quote/metrics"},
        "items": {"path": "/data/quote/items"},
    }


def test_ensure_supported_catalog_id_replaces_hallucinated_edition_catalog():
    document = "\n".join(
        (
            '{"version":"v0.9.1","createSurface":{"surfaceId":"main","catalogId":"https://valuz.io/a2ui/catalogs/finance/v1"}}',
            '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":[{"id":"root","component":"Stack","children":[]}]}}',
        )
    )

    normalized = t._ensure_supported_catalog_id(document)
    messages = [json.loads(line) for line in str(normalized).splitlines()]

    assert messages[0]["createSurface"]["catalogId"] == (
        "https://valuz.io/a2ui/catalogs/base/v1"
    )


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
