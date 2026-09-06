"""Native generate_ui schemas govern validation, not generated prose."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from src.core.tools import ExecContext

from valuz_agent.modules.genui import protocol, tools
from valuz_agent.modules.genui.parameters import (
    ParameterValidationError,
    component_json_schema,
    parse_parameter_schema,
    validate_component_parameters,
)


def field(name, kind="string", optional=True, **extra):
    return {"name": name, "type": kind, "optional": optional, **extra}


SCHEMA = {
    "version": 1,
    "fields": [
        field("symbol", optional=False, format="symbol"),
        field("period", choices=["annual", "quarterly"], aliases=["financialPeriod"]),
        field("limit", "integer", minimum=3, maximum=12),
        field("startDate", format="date"),
        field("endDate", format="date"),
        field("enabled", "boolean"),
        field(
            "metrics",
            "string-list",
            itemChoices=["price", "revenue"],
            acceptArray=True,
            uniqueItems=True,
            minItems=1,
            maxItems=2,
        ),
    ],
    "constraints": [
        {"kind": "dateOrder", "start": "startDate", "end": "endDate"},
        {"kind": "requiredWhenAny", "field": "period", "source": "metrics", "values": ["revenue"]},
    ],
}


@pytest.fixture
def register(monkeypatch):
    def install(schema=SCHEMA, **extra):
        contract = {
            "component": "QueryCard",
            "params": "{wrong: obsolete prose}",
            "parameterSchema": schema,
            "inputs": [
                {
                    "key": "main",
                    "source": "example.data",
                    "shape": "Example",
                    "bindings": {"items": "items"},
                    "paramMap": {"asset": "symbol", "period": "period", "metrics": "metrics"},
                    "fixedParams": {"limit": 4},
                }
            ],
            **extra,
        }
        monkeypatch.setattr(
            protocol,
            "edition_catalog_text",
            lambda *a, **kw: "COMPONENT_DATA_CONTRACT " + json.dumps(contract),
        )
        monkeypatch.setattr(tools, "component_names_for_scope", lambda *a: ("QueryCard",))
        return protocol.registered_component_data_contracts()["QueryCard"]

    return install


def compile_params(params):
    return tools._validate_generation_choices(
        scope="all",
        component_names=["QueryCard"],
        component_data=[{"component": "QueryCard", "params": params}],
    )


def test_native_schema_owns_catalog_tool_schema_and_fixed_projection(register):
    contract = register()
    assert "wrong" not in contract["param_specs"]
    assert contract["param_specs"]["limit"]["kind"] == "integer"
    _, plans, error = compile_params(
        {
            "symbol": "US:ABC",
            "financialPeriod": "annual",
            "metrics": ["revenue"],
            "limit": 12,
        }
    )
    assert error is None
    assert plans[0]["params"] == {
        "symbol": "US:ABC",
        "period": "annual",
        "metrics": "revenue",
        "limit": 12,
    }
    assert plans[0]["inputs"][0]["params"] == {
        "asset": "US:ABC",
        "period": "annual",
        "metrics": "revenue",
        "limit": 4,
    }
    definition = tools._registered_component_data_item_schemas()[0]
    assert definition["properties"]["params"] == component_json_schema(SCHEMA)
    Draft202012Validator.check_schema(definition)
    guide = protocol.registered_component_data_tool_guide()
    assert '"type":"integer"' in guide and "obsolete prose" not in guide


@pytest.mark.parametrize(
    "params,reason",
    [
        ({}, "symbol is required"),
        ({"symbol": "ABC"}, "canonical symbol"),
        ({"wrong": "x"}, "unknown parameter"),
        ({"slot": "main"}, "unknown parameter"),
        ({"period": "ttm"}, "must be one of"),
        ({"period": None}, "nonempty text"),
        ({"period": "annual", "financialPeriod": "annual"}, "conflicting parameter aliases"),
        ({"enabled": 1}, "boolean"),
        ({"limit": "8"}, "integer"),
        ({"limit": True}, "integer"),
        ({"limit": 3.5}, "integer"),
        ({"limit": float("nan")}, "integer"),
        ({"limit": float("inf")}, "integer"),
        ({"limit": 9007199254740992}, "integer"),
        ({"limit": 2}, "bounds"),
        ({"limit": 13}, "bounds"),
        ({"startDate": "2026-02-29"}, "valid YYYY-MM-DD"),
        ({"startDate": "0000-01-01"}, "valid YYYY-MM-DD"),
        ({"startDate": "2026-9-4"}, "valid YYYY-MM-DD"),
        ({"startDate": "2026-09-05", "endDate": "2026-09-04"}, "must not follow"),
        ({"metrics": []}, "number of items"),
        ({"metrics": "price,price"}, "distinct"),
        ({"metrics": ["price", "price"]}, "distinct"),
        ({"metrics": ["price ", "price"]}, "distinct"),
        ({"metrics": ["price", "revenue", "price"]}, "number of items"),
        ({"metrics": [None]}, "nonempty text"),
        ({"metrics": "other"}, "unsupported"),
        ({"metrics": "revenue"}, "period is required"),
        ({"symbol": {"$host": "privateToken"}}, "incompatible host"),
        ({"symbol": {"$host": "symbol", "extra": True}}, "nonempty text"),
    ],
)
def test_compiler_rejects_invalid_native_parameters(register, params, reason):
    register()
    value = {"symbol": "US:ABC", **params} if params else {}
    _, plans, error = compile_params(value)
    assert not plans
    assert reason in error


@pytest.mark.parametrize(
    "params",
    [
        {"symbol": "US:^ABC"},
        {"symbol": "US:ABC", "limit": 3.0, "enabled": False},
        {"symbol": "US:ABC", "startDate": "2024-02-29", "endDate": "2024-03-01"},
        {"symbol": "US:ABC", "metrics": ["price"]},
        {"symbol": {"$host": "symbol"}, "financialPeriod": {"$host": "period"}},
        {"symbol": "US:ABC", "startDate": {"$host": "startDate"}, "endDate": "2024-03-01"},
    ],
)
def test_compiler_accepts_literals_and_declared_deferred_refs(register, params):
    register()
    _, plans, error = compile_params(params)
    assert error is None
    assert plans


@pytest.mark.parametrize(
    "schema",
    [
        None,
        {},
        {"version": 2, "fields": []},
        {"version": True, "fields": []},
        {"version": 1, "fields": [], "unknown": True},
        {"version": 1, "fields": {}},
        {"version": 1, "fields": [None]},
        {"version": 1, "fields": [field("x", "number")]},
        {"version": 1, "fields": [field("x", [])]},
        {"version": 1, "fields": [field("x", optional="true")]},
        {"version": 1, "fields": [field("x", aliases=["x"])]},
        {"version": 1, "fields": [field("x", aliases=["y"]), field("y")]},
        {"version": 1, "fields": [field("x", format="url")]},
        {"version": 1, "fields": [field("x", format=[])]},
        {"version": 1, "fields": [field("x", "integer", minimum=3, maximum=2)]},
        {"version": 1, "fields": [field("x", "integer", minimum=True)]},
        {"version": 1, "fields": [field("x", "boolean", choices=["a"])]},
        {"version": 1, "fields": [field("x", "integer", default="5")]},
        {"version": 1, "fields": [], "constraints": [{"kind": "unknown"}]},
        {"version": 1, "fields": [], "constraints": [{"kind": []}]},
        {
            "version": 1,
            "fields": [],
            "constraints": [{"kind": "atLeastOne", "fields": ["missing"]}],
        },
    ],
)
def test_invalid_schema_cannot_fall_back_to_prose(register, schema):
    contract = register(schema)
    assert "parameter_schema_error" in contract
    _, plans, error = compile_params({"wrong": "previously accepted"})
    assert not plans and "invalid registered parameterSchema" in error
    assert tools._registered_component_data_item_schemas() == []


def test_bad_param_map_is_rejected(register):
    contract = register(
        inputs=[
            {
                "key": "main",
                "source": "data",
                "bindings": {"items": "items"},
                "paramMap": {"asset": "typo"},
            }
        ]
    )
    assert "paramMap" in contract["parameter_schema_error"]


def test_empty_schema_rejects_unknown_parameters(register):
    register(
        {"version": 1, "fields": []},
        inputs=[
            {
                "key": "main",
                "source": "data",
                "bindings": {"items": "items"},
            }
        ],
    )
    assert compile_params({})[2] is None
    assert "unknown parameter" in compile_params({"extra": 1})[2]


def test_required_alias_is_exposed_and_conflicts_rejected_by_tool_schema():
    schema = {"version": 1, "fields": [field("name", optional=False, aliases=["label"])]}
    validator = Draft202012Validator(component_json_schema(parse_parameter_schema(schema)))
    assert validator.is_valid({"name": "one"})
    assert validator.is_valid({"label": "one"})
    assert not validator.is_valid({})
    assert not validator.is_valid({"name": "one", "label": "two"})


def test_at_least_one_and_string_list_symbol_contract():
    schema = parse_parameter_schema(
        {
            "version": 1,
            "fields": [field("symbol", format="symbol"), field("asset", aliases=["code"])],
            "constraints": [{"kind": "atLeastOne", "fields": ["symbol", "asset"]}],
        }
    )
    assert validate_component_parameters({"code": "ABC"}, schema) == {"asset": "ABC"}
    with pytest.raises(ParameterValidationError, match="one of"):
        validate_component_parameters({}, schema)
    schema = parse_parameter_schema(
        {
            "version": 1,
            "fields": [
                field(
                    "symbols",
                    "string-list",
                    optional=False,
                    format="symbol",
                    minItems=2,
                    uniqueItems=True,
                ),
            ],
        }
    )
    assert validate_component_parameters({"symbols": "US:ABC, US:DEF"}, schema) == {
        "symbols": "US:ABC,US:DEF"
    }
    for value in (["US:ABC", "US:DEF"], "US:ABC", "ABC,DEF", "US:ABC,US:ABC"):
        with pytest.raises(ParameterValidationError):
            validate_component_parameters({"symbols": value}, schema)


def test_list_array_must_preserve_scalar_wire_cardinality():
    schema = parse_parameter_schema(
        {"version": 1, "fields": [field("items", "string-list", acceptArray=True)]}
    )
    with pytest.raises(ParameterValidationError, match="commas"):
        validate_component_parameters({"items": ["one,two"]}, schema)


def test_native_projection_keeps_literal_and_reference_params_without_map(register):
    register(
        inputs=[
            {
                "key": "main",
                "source": "data",
                "bindings": {"items": "items"},
                "fixedParams": {"reserved": "fixed"},
            }
        ]
    )
    _, plans, error = compile_params({"symbol": {"$host": "symbol"}, "metrics": ["price"]})
    assert error is None
    assert plans[0]["inputs"][0]["params"] == {
        "symbol": {"$host": "symbol"},
        "metrics": "price",
        "reserved": "fixed",
    }


def test_default_is_not_implicitly_injected():
    schema = parse_parameter_schema({"version": 1, "fields": [field("range", default="1Y")]})
    assert validate_component_parameters({}, schema) == {}


def test_schema_input_is_not_mutated():
    schema, params = (
        copy.deepcopy(SCHEMA),
        {"symbol": "US:ABC", "financialPeriod": "annual", "metrics": ["revenue"]},
    )
    before = copy.deepcopy((schema, params))
    validate_component_parameters(params, parse_parameter_schema(schema))
    assert (schema, params) == before


@pytest.mark.parametrize(
    "schema,params,reason",
    [
        (SCHEMA, {"symbol": "US:ABC", "limit": 3.5}, "integer"),
        ({"version": 999, "fields": []}, {"wrong": "text"}, "invalid registered parameterSchema"),
    ],
)
async def test_generate_ui_handler_validates_native_schema_before_model_call(
    register,
    monkeypatch,
    schema,
    params,
    reason,
):
    register(schema)

    async def messages(*args, **kwargs):
        return [SimpleNamespace(user_message=SimpleNamespace(text="Create a dashboard"))]

    async def session(*args, **kwargs):
        return SimpleNamespace(
            model="model",
            runtime_provider="deepagents",
            metadata={"valuz": {"locked_provider_id": "provider"}},
            agent_config=SimpleNamespace(metadata={}),
        )

    async def unexpected_model_call(*args, **kwargs):
        pytest.fail("native validation must run before resolving/calling the compiler model")

    monkeypatch.setattr(tools.kernel_client, "list_messages", messages)
    monkeypatch.setattr(tools.kernel_client, "get_session", session)
    monkeypatch.setattr(tools, "_resolve_compiler_model", unexpected_model_call)
    ctx = ExecContext(session_id="test-session")
    ctx.user_id = "test-owner"
    result = await tools.build_generative_ui_tool_defs()[0].handler(
        {
            "request": "Create a dashboard",
            "component_names": ["QueryCard"],
            "component_data": [{"component": "QueryCard", "params": params}],
        },
        ctx,
    )
    assert result.is_error
    assert reason in result.content


@pytest.mark.parametrize("component_data", [None, []])
def test_invalid_registration_cannot_be_selected_without_a_query_plan(register, component_data):
    register({"version": 999, "fields": []})
    _, plans, error = tools._validate_generation_choices(
        scope="all",
        component_names=["QueryCard"],
        component_data=component_data,
    )
    assert not plans
    assert "invalid registered parameterSchema" in error


def inline_document(component):
    return "\n".join(
        json.dumps(message)
        for message in [
            {"version": "v0.9.1", "createSurface": {"surfaceId": "main"}},
            {
                "version": "v0.9.1",
                "updateComponents": {
                    "surfaceId": "main",
                    "components": [
                        {"id": "root", "component": "Stack", "children": ["card"]},
                        {"id": "card", "component": component, "text": "Inline text", "items": []},
                    ],
                },
            },
        ]
    )


@pytest.mark.parametrize("mode", ["replace", "edit"])
def test_compiler_cannot_inline_an_invalid_query_registration(register, mode):
    register({"version": 999, "fields": []})
    document = inline_document("QueryCard")
    error = tools._compiled_document_error(
        document,
        component_names=("QueryCard",),
        component_data=(),
        current_document=document if mode == "edit" else None,
        generation_mode=mode,
    )
    assert "invalid registered parameterSchema" in error


@pytest.mark.parametrize("component", ["QueryCard", "TextContent"])
async def test_handler_without_any_plan_checks_actual_registration_before_delivery(
    register,
    monkeypatch,
    component,
):
    register({"version": 999, "fields": []})
    monkeypatch.setattr(
        tools, "component_names_for_scope", lambda *a: ("QueryCard", "TextContent", "Stack")
    )

    async def messages(*args, **kwargs):
        return [SimpleNamespace(user_message=SimpleNamespace(text="Create a dashboard"))]

    async def session(*args, **kwargs):
        return SimpleNamespace(
            model="model",
            runtime_provider="deepagents",
            metadata={"valuz": {"locked_provider_id": "provider"}},
            agent_config=SimpleNamespace(metadata={}),
        )

    async def compiler(*args, **kwargs):
        return tools._CompilerModel(
            provider_id="provider",
            model="model",
            runtime_provider="deepagents",
            model_provider=SimpleNamespace(),
            is_lite=False,
        )

    async def no_tool_id(*args, **kwargs):
        return None

    async def complete(*args, **kwargs):
        return inline_document(component)

    delivered = []

    async def deliver(**kwargs):
        delivered.append(kwargs)
        return ""

    monkeypatch.setattr(tools.kernel_client, "list_messages", messages)
    monkeypatch.setattr(tools.kernel_client, "get_session", session)
    monkeypatch.setattr(tools, "_resolve_compiler_model", compiler)
    monkeypatch.setattr(tools, "resolve_tool_use_id", no_tool_id)
    monkeypatch.setattr(tools, "_make_completer", lambda **kwargs: complete)
    monkeypatch.setattr(tools, "_deliver_generated_ui", deliver)
    ctx = ExecContext(session_id="test-session")
    ctx.user_id = "test-owner"
    result = await tools.build_generative_ui_tool_defs()[0].handler(
        {"request": "Create a dashboard"},
        ctx,
    )
    if component == "QueryCard":
        assert result.is_error
        assert "invalid registered parameterSchema" in result.content
        assert not delivered
    else:
        assert not result.is_error
        assert len(delivered) == 1
