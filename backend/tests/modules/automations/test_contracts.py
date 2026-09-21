"""The three contracts: parsing, row projection, run-time validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from valuz_agent.modules.automations.contracts import (
    AgentExecution,
    ArtifactResult,
    CodeExecution,
    ContractViolationError,
    ConversationResult,
    JsonInput,
    NoneInput,
    TextInput,
    apply_execution_contract,
    apply_input_contract,
    apply_result_contract,
    artifact_summary,
    effective_input,
    execution_contract_of,
    input_contract_of,
    normalise_entry,
    result_contract_of,
    template_input_variables,
    validate_artifact,
)

SCHEMA = {
    "type": "object",
    "properties": {"symbol": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["symbol"],
    "additionalProperties": False,
}


class TestParsing:
    def test_json_input_accepts_schema_alias_and_field_name(self) -> None:
        by_alias = JsonInput.model_validate({"kind": "json", "schema": SCHEMA})
        by_name = JsonInput(schema=SCHEMA)
        assert by_alias.json_schema == by_name.json_schema == SCHEMA
        assert by_alias.model_dump(by_alias=True)["schema"] == SCHEMA

    def test_json_input_rejects_invalid_schema(self) -> None:
        with pytest.raises(ValidationError):
            JsonInput(schema={"type": "object", "properties": "nope"})

    def test_json_input_rejects_non_object_schema(self) -> None:
        with pytest.raises(ValidationError):
            JsonInput(schema={"type": "array"})

    def test_code_execution_normalises_entry(self) -> None:
        assert (
            CodeExecution(entry="./automations//daily/run.py").entry == "automations/daily/run.py"
        )
        assert CodeExecution(entry="a\\b.py").entry == "a/b.py"

    @pytest.mark.parametrize("bad", ["/etc/passwd", "../x.py", "a/../../b.py", "C:/x.py", "  "])
    def test_code_execution_rejects_escaping_entry(self, bad: str) -> None:
        with pytest.raises((ValidationError, ValueError)):
            normalise_entry(bad)
            CodeExecution(entry=bad)

    def test_code_execution_timeout_bounds(self) -> None:
        assert CodeExecution(entry="x.py").timeout_seconds == 600
        with pytest.raises(ValidationError):
            CodeExecution(entry="x.py", timeout_seconds=0)
        with pytest.raises(ValidationError):
            CodeExecution(entry="x.py", timeout_seconds=3601)


class TestRowProjection:
    def test_round_trip_code_row(self) -> None:
        row = SimpleNamespace(agent_kind="library_agent", agent_slug="x", action_kind="chat")
        apply_execution_contract(
            row, CodeExecution(entry="a/b.py", runtime="shell", timeout_seconds=5)
        )
        assert row.execution_kind == "code"
        assert row.agent_kind is None and row.agent_slug is None
        assert row.action_kind == "chat"
        back = execution_contract_of(row)
        assert isinstance(back, CodeExecution)
        assert (back.entry, back.runtime, back.timeout_seconds) == ("a/b.py", "shell", 5)

    def test_round_trip_agent_task_row(self) -> None:
        row = SimpleNamespace()
        apply_execution_contract(row, AgentExecution(mode="task"))
        assert row.execution_kind == "agent" and row.action_kind == "task"
        assert row.code_entry is None
        assert execution_contract_of(row) == AgentExecution(mode="task")

    def test_pre_contract_row_reads_as_defaults(self) -> None:
        row = SimpleNamespace(action_kind="chat")
        assert isinstance(execution_contract_of(row), AgentExecution)
        assert isinstance(input_contract_of(row), NoneInput)
        assert isinstance(result_contract_of(row), ConversationResult)

    def test_input_and_result_round_trip(self) -> None:
        row = SimpleNamespace()
        apply_input_contract(row, JsonInput(schema=SCHEMA, default={"symbol": "SH:600519"}))
        assert row.input_kind == "json" and row.default_input == {"symbol": "SH:600519"}
        assert input_contract_of(row) == JsonInput(schema=SCHEMA, default={"symbol": "SH:600519"})
        apply_input_contract(row, TextInput(default="hi"))
        assert row.input_kind == "text" and row.input_schema is None
        assert input_contract_of(row) == TextInput(default="hi")
        apply_result_contract(row, ArtifactResult(schema=SCHEMA))
        assert row.result_kind == "artifact" and row.result_schema == SCHEMA
        assert result_contract_of(row) == ArtifactResult(schema=SCHEMA)


class TestEffectiveInput:
    def test_none_refuses_input(self) -> None:
        assert effective_input(NoneInput(), None) is None
        with pytest.raises(ContractViolationError):
            effective_input(NoneInput(), "x")

    def test_text_uses_default_and_refuses_objects(self) -> None:
        assert effective_input(TextInput(default="d"), None) == "d"
        assert effective_input(TextInput(), "given") == "given"
        with pytest.raises(ContractViolationError):
            effective_input(TextInput(), {"a": 1})

    def test_json_merges_default_under_input_then_validates(self) -> None:
        contract = JsonInput(schema=SCHEMA, default={"symbol": "SH:600519", "limit": 5})
        assert effective_input(contract, None) == {"symbol": "SH:600519", "limit": 5}
        assert effective_input(contract, {"limit": 9}) == {"symbol": "SH:600519", "limit": 9}

    def test_json_reports_the_offending_path(self) -> None:
        contract = JsonInput(schema=SCHEMA)
        with pytest.raises(ContractViolationError) as excinfo:
            effective_input(contract, {"symbol": 3})
        assert excinfo.value.path == "input.symbol"
        with pytest.raises(ContractViolationError) as excinfo:
            effective_input(contract, {})
        assert excinfo.value.path == "input"
        with pytest.raises(ContractViolationError):
            effective_input(contract, "not-an-object")


class TestArtifact:
    def test_validate_against_schema(self) -> None:
        contract = ArtifactResult(schema={"type": "object", "required": ["summary"]})
        assert validate_artifact(contract, {"summary": "ok"}) == {"summary": "ok"}
        with pytest.raises(ContractViolationError):
            validate_artifact(contract, {"nope": 1})
        with pytest.raises(ContractViolationError):
            validate_artifact(contract, ["not", "an", "object"])

    def test_conversation_result_refuses_artifacts(self) -> None:
        with pytest.raises(ContractViolationError):
            validate_artifact(ConversationResult(), {"summary": "x"})

    def test_too_large_is_refused(self) -> None:
        with pytest.raises(ContractViolationError):
            validate_artifact(ArtifactResult(), {"blob": "x" * (1024 * 1024 + 1)})

    def test_summary_prefers_summary_field(self) -> None:
        assert artifact_summary({"summary": " ready ", "x": 1}) == "ready"
        assert artifact_summary({"x": 1}) == '{"x": 1}'


class TestTemplateVariables:
    def test_scalars_flatten_and_whole_object_is_json(self) -> None:
        out = template_input_variables(
            {"symbol": "A", "limit": 3, "flag": True, "nested": {"a": 1}}
        )
        assert out["input.symbol"] == "A"
        assert out["input.limit"] == "3"
        assert out["input.flag"] == "true"
        assert "input.nested" not in out
        assert '"nested"' in out["input"]

    def test_text_and_none(self) -> None:
        assert template_input_variables("hello") == {"input": "hello"}
        assert template_input_variables(None) == {}
