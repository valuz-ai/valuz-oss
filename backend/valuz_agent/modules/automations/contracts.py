"""The three contracts an automation row carries, and how runs honour them.

``input`` — what a run may be given; ``execution`` — how it runs (an agent
turn, or a program); ``result`` — what it must produce. They are orthogonal:
a JSON input serves an agent (rendered into its prompt) and a program
(``ctx["input"]``) alike; an artifact result is produced by an agent through
the ``automation`` tool's ``output`` action or by a program's return value.

Everything that validates lives here so the three entrances that start a run
(HTTP, the MCP tool, a deployment's own callers) raise the same typed error for
the same bad input.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Union

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema import exceptions as js_exceptions
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Cap on a stored artifact (JSON-encoded). Larger results belong in files.
MAX_ARTIFACT_BYTES = 1024 * 1024
#: Cap on a stored effective input (JSON-encoded).
MAX_INPUT_BYTES = 256 * 1024
MAX_CODE_TIMEOUT_S = 3600
DEFAULT_CODE_TIMEOUT_S = 600


def _check_schema(schema: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(dict(schema))
    except js_exceptions.SchemaError as exc:
        raise ValueError(f"invalid JSON Schema: {exc.message}") from exc
    declared = schema.get("type")
    if declared not in (None, "object"):
        raise ValueError("the schema must describe an object (type: object)")


# ── input ─────────────────────────────────────────────────────────────


class NoneInput(BaseModel):
    """The run takes nothing; passing an input is refused."""

    kind: Literal["none"] = "none"


class TextInput(BaseModel):
    """One free-text string — the pre-contract ``extra_input`` made explicit."""

    kind: Literal["text"] = "text"
    default: str | None = None


class JsonInput(BaseModel):
    """A JSON object validated against ``schema`` (draft 2020-12).

    ``default`` is merged UNDER the run's input at the top level before
    validation, so a schema may require a key the default supplies.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["json"] = "json"
    json_schema: dict[str, Any] = Field(
        alias="schema",
        description="JSON Schema (draft 2020-12) for an object.",
    )
    default: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> JsonInput:
        _check_schema(self.json_schema)
        if self.default is not None and not isinstance(self.default, dict):
            raise ValueError("default must be a JSON object for a json input")
        return self


InputContract = Annotated[
    Union[NoneInput, TextInput, JsonInput],  # noqa: UP007
    Field(discriminator="kind"),
]


# ── execution ─────────────────────────────────────────────────────────


class AgentExecution(BaseModel):
    """An agent turn: ``chat`` = one session, ``task`` = a project task."""

    kind: Literal["agent"] = "agent"
    mode: Literal["chat", "task"] = "chat"


class CodeExecution(BaseModel):
    """A program in the project: no agent, no model, no credits.

    ``entry`` is a path RELATIVE TO THE PROJECT DIRECTORY chosen by the author
    (``automations/daily-metric/automation.py``). It must stay inside the
    project — no absolute paths, no ``..`` — and the file must exist when the
    run starts (checked then, not here: the author usually registers the
    automation before or while writing the file).
    """

    kind: Literal["code"] = "code"
    runtime: Literal["python", "shell"] = "python"
    entry: str = Field(min_length=1, max_length=512)
    timeout_seconds: int = Field(default=DEFAULT_CODE_TIMEOUT_S, ge=1, le=MAX_CODE_TIMEOUT_S)

    @field_validator("entry")
    @classmethod
    def _check_entry(cls, value: str) -> str:
        return normalise_entry(value)


def normalise_entry(value: str) -> str:
    """A clean project-relative POSIX path, or ``ValueError``."""
    entry = value.strip().replace("\\", "/")
    if not entry:
        raise ValueError("entry is required")
    if entry.startswith("/") or (len(entry) > 1 and entry[1] == ":"):
        raise ValueError("entry must be relative to the project directory")
    parts = [p for p in entry.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("entry must stay inside the project directory")
    return posixpath.join(*parts)


ExecutionContract = Annotated[
    Union[AgentExecution, CodeExecution],  # noqa: UP007
    Field(discriminator="kind"),
]


# ── result ────────────────────────────────────────────────────────────


class ConversationResult(BaseModel):
    """The run's result is the conversation / task it produced (the default)."""

    kind: Literal["conversation"] = "conversation"


class ArtifactResult(BaseModel):
    """One JSON object per run, optionally validated against ``schema``."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["artifact"] = "artifact"
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")

    @model_validator(mode="after")
    def _check(self) -> ArtifactResult:
        if self.json_schema is not None:
            _check_schema(self.json_schema)
        return self


ResultContract = Annotated[
    Union[ConversationResult, ArtifactResult],  # noqa: UP007
    Field(discriminator="kind"),
]


# ── row ↔ contract ────────────────────────────────────────────────────


def input_contract_of(row: Any) -> NoneInput | TextInput | JsonInput:
    kind = getattr(row, "input_kind", None) or "none"
    if kind == "text":
        default = row.default_input
        return TextInput(default=default if isinstance(default, str) else None)
    if kind == "json":
        default = row.default_input
        return JsonInput(
            schema=dict(row.input_schema or {"type": "object"}),
            default=dict(default) if isinstance(default, dict) else None,
        )
    return NoneInput()


def apply_input_contract(row: Any, contract: NoneInput | TextInput | JsonInput) -> None:
    row.input_kind = contract.kind
    if isinstance(contract, JsonInput):
        row.input_schema = dict(contract.json_schema)
        row.default_input = dict(contract.default) if contract.default is not None else None
    elif isinstance(contract, TextInput):
        row.input_schema = None
        row.default_input = contract.default
    else:
        row.input_schema = None
        row.default_input = None


def execution_contract_of(row: Any) -> AgentExecution | CodeExecution:
    if getattr(row, "execution_kind", "agent") == "code":
        return CodeExecution(
            runtime=row.code_runtime or "python",
            entry=row.code_entry or "automation.py",
            timeout_seconds=row.code_timeout_s or DEFAULT_CODE_TIMEOUT_S,
        )
    if getattr(row, "action_kind", "chat") == "task":
        return AgentExecution(mode="task")
    return AgentExecution(mode="chat")


def apply_execution_contract(row: Any, contract: AgentExecution | CodeExecution) -> None:
    if isinstance(contract, CodeExecution):
        row.execution_kind = "code"
        row.code_runtime = contract.runtime
        row.code_entry = contract.entry
        row.code_timeout_s = contract.timeout_seconds
        # A program has no agent. ``action_kind`` keeps its column default so
        # the pre-contract CHECK still holds; readers must look at
        # ``execution_kind`` first.
        row.agent_kind = None
        row.agent_slug = None
        row.action_kind = "chat"
    else:
        row.execution_kind = "agent"
        row.code_runtime = None
        row.code_entry = None
        row.code_timeout_s = None
        row.action_kind = contract.mode


def result_contract_of(row: Any) -> ConversationResult | ArtifactResult:
    if getattr(row, "result_kind", "conversation") == "artifact":
        schema = row.result_schema
        return ArtifactResult(schema=dict(schema) if isinstance(schema, dict) else None)
    return ConversationResult()


def apply_result_contract(row: Any, contract: ConversationResult | ArtifactResult) -> None:
    row.result_kind = contract.kind
    row.result_schema = (
        dict(contract.json_schema)
        if isinstance(contract, ArtifactResult) and contract.json_schema is not None
        else None
    )


# ── run-time validation ───────────────────────────────────────────────


class ContractViolationError(ValueError):
    """A run input or artifact that breaks the row's contract.

    ``path`` is the JSON pointer-ish location (``input.symbol``); the message
    is the validator's own so the caller can repair the right field.
    """

    def __init__(self, message: str, *, path: str = "") -> None:
        super().__init__(message)
        self.path = path


def _first_error(
    schema: Mapping[str, Any], instance: Any, *, root: str
) -> ContractViolationError | None:
    validator = Draft202012Validator(dict(schema))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    err = errors[0]
    path = ".".join(str(p) for p in err.absolute_path)
    return ContractViolationError(err.message, path=f"{root}.{path}" if path else root)


def effective_input(
    contract: NoneInput | TextInput | JsonInput, run_input: Any
) -> str | dict[str, Any] | None:
    """Merge the default under the run's input and validate.

    Returns what the run stores: ``None`` (no input), a string (text kind) or
    an object (json kind). Raises ``ContractViolation`` for anything the
    contract refuses — the same error from every entrance.
    """
    if isinstance(contract, NoneInput):
        if run_input not in (None, "", {}):
            raise ContractViolationError("this automation takes no input", path="input")
        return None
    if isinstance(contract, TextInput):
        if run_input is None:
            return contract.default
        if not isinstance(run_input, str):
            raise ContractViolationError(
                "input must be a string for a text automation", path="input"
            )
        return run_input
    base: dict[str, Any] = dict(contract.default or {})
    if run_input is not None:
        if not isinstance(run_input, Mapping):
            raise ContractViolationError("input must be a JSON object", path="input")
        base.update(run_input)
    problem = _first_error(contract.json_schema, base, root="input")
    if problem is not None:
        raise problem
    if len(json.dumps(base, ensure_ascii=False)) > MAX_INPUT_BYTES:
        raise ContractViolationError("input is too large", path="input")
    return base


def validate_artifact(
    contract: ConversationResult | ArtifactResult, artifact: Any
) -> dict[str, Any]:
    """The artifact a run may store, or ``ContractViolation``."""
    if not isinstance(contract, ArtifactResult):
        raise ContractViolationError(
            "this automation does not declare an artifact result", path="artifact"
        )
    if not isinstance(artifact, Mapping):
        raise ContractViolationError("artifact must be a JSON object", path="artifact")
    out = dict(artifact)
    encoded = json.dumps(out, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ContractViolationError(
            f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes; put large results in files",
            path="artifact",
        )
    if contract.json_schema is not None:
        problem = _first_error(contract.json_schema, out, root="artifact")
        if problem is not None:
            raise problem
    return out


def template_input_variables(value: str | Mapping[str, Any] | None) -> dict[str, str]:
    """``{{input}}`` / ``{{input.<key>}}`` for the prompt renderer.

    Scalars only — nested objects are not flattened; the whole object is also
    handed to the agent verbatim in the run preamble.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        return {"input": value}
    out = {"input": json.dumps(value, ensure_ascii=False)}
    for key, item in value.items():
        if isinstance(item, bool):
            out[f"input.{key}"] = "true" if item else "false"
        elif isinstance(item, (str, int, float)):
            out[f"input.{key}"] = str(item)
    return out


def artifact_summary(artifact: Mapping[str, Any], *, limit: int = 200) -> str:
    """What ``result_summary`` shows for an artifact run."""
    summary = artifact.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()[:limit]
    return json.dumps(artifact, ensure_ascii=False)[:limit]


__all__ = [
    "DEFAULT_CODE_TIMEOUT_S",
    "MAX_ARTIFACT_BYTES",
    "MAX_CODE_TIMEOUT_S",
    "MAX_INPUT_BYTES",
    "AgentExecution",
    "ArtifactResult",
    "CodeExecution",
    "ContractViolationError",
    "ConversationResult",
    "ExecutionContract",
    "InputContract",
    "JsonInput",
    "NoneInput",
    "ResultContract",
    "TextInput",
    "apply_execution_contract",
    "apply_input_contract",
    "apply_result_contract",
    "artifact_summary",
    "effective_input",
    "execution_contract_of",
    "input_contract_of",
    "normalise_entry",
    "result_contract_of",
    "template_input_variables",
    "validate_artifact",
]
