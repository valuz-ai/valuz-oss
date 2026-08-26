"""Deterministic calculation evidence producer for citation-aware answers."""

from __future__ import annotations

import ast
import hashlib
import json
import keyword
import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.calculation import evaluate_decimal_expression
from src.core.tools import ExecContext

CITATION_CALCULATE_TOOL_NAME = "citation_calculate"
_HANDLE_RE = re.compile(r"^ev_[A-Za-z0-9_-]{8,128}$")
_COLLECTION_ADDRESS_RE = re.compile(r"^evc_[A-Za-z0-9_-]{8,128}#/[^\s#?]{1,1024}$")


def _canonical_evidence_reference(value: str) -> str:
    """Accept the protocol URI form while storing the canonical opaque reference."""

    return value.removeprefix("evidence://")

_PARAMS = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "description": "Arithmetic using input names and +, -, *, /, parentheses only.",
        },
        "inputs": {
            "type": "array",
            "minItems": 1,
            "maxItems": 128,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": ["number", "string"]},
                    "unit": {"type": "string"},
                    "evidenceHandle": {"type": "string"},
                    "origin": {
                        "type": "string",
                        "enum": ["user-input"],
                        "description": (
                            "Use only when this exact input value was explicitly supplied "
                            "by the user. Retrieved facts must use evidenceHandle instead."
                        ),
                    },
                },
                "required": ["name", "value"],
            },
        },
        "unit": {"type": "string"},
        "decimalPlaces": {"type": "integer", "minimum": 0, "maximum": 12},
        "entityId": {"type": "string"},
        "entityName": {"type": "string"},
        "metric": {"type": "string"},
        "period": {"type": "string"},
        "scope": {"type": "string"},
        "basis": {"type": "string"},
    },
    "required": ["expression", "inputs", "unit"],
}


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid_input_value")
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid_input_value") from exc
    if not result.is_finite():
        raise ValueError("invalid_input_value")
    return result


def _input_name(value: Any) -> str | None:
    """Return the canonical language-neutral expression identifier.

    Python's bounded AST accepts Unicode identifiers safely. Restricting names
    to ASCII made otherwise valid calculations fail whenever a model used
    natural Chinese, Japanese, or other localized input labels. NFKC mirrors
    Python's own identifier normalization so the stored input key and the
    parsed expression always address the same variable.
    """

    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if not normalized or len(normalized) > 64:
        return None
    if not normalized.isidentifier() or keyword.iskeyword(normalized):
        return None
    return normalized


def _stable_decimal(value: Decimal, decimal_places: int) -> str:
    quantum = Decimal(1).scaleb(-decimal_places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{decimal_places}f")


def _canonical_expression(expression: str, unit: str) -> str:
    """Normalize ratio expressions to percentage points for ``%`` output.

    Models commonly pass ``(current - prior) / prior`` with ``unit='%'``.
    Store an explicit ``* 100`` in the evidence so both the tool result and
    the later independent verifier recompute the same percentage value.  If
    the expression already explicitly scales by 100 (or divides by 0.01),
    preserve it unchanged.
    """

    stripped = expression.strip()
    if unit.strip() != "%" or _has_explicit_percent_scaling(stripped):
        return stripped
    return f"({stripped}) * 100"


def _has_explicit_percent_scaling(expression: str) -> bool:
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False
    for node in ast.walk(root):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if _numeric_constant(node.left) == Decimal(100) or _numeric_constant(
                node.right
            ) == Decimal(100):
                return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _numeric_constant(node.right) == Decimal("0.01"):
                return True
    return False


def _numeric_constant(node: ast.AST) -> Decimal | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    return None


async def _citation_calculate_handler(
    args: dict[str, Any],
    _ctx: ExecContext,
) -> ToolResult:
    expression = args.get("expression")
    raw_inputs = args.get("inputs")
    unit = args.get("unit")
    decimal_places = args.get("decimalPlaces", 2)
    if not isinstance(expression, str) or not expression.strip():
        return ToolResult("citation_calculate: 'expression' is required", is_error=True)
    if not isinstance(raw_inputs, list) or not 1 <= len(raw_inputs) <= 128:
        return ToolResult("citation_calculate: 'inputs' must contain 1-128 items", is_error=True)
    if not isinstance(unit, str) or not unit.strip():
        return ToolResult("citation_calculate: 'unit' is required", is_error=True)
    if (
        isinstance(decimal_places, bool)
        or not isinstance(decimal_places, int)
        or not 0 <= decimal_places <= 12
    ):
        return ToolResult(
            "citation_calculate: 'decimalPlaces' must be between 0 and 12", is_error=True
        )

    values: dict[str, Decimal] = {}
    inputs: list[dict[str, Any]] = []
    try:
        for raw in raw_inputs:
            if not isinstance(raw, dict):
                raise ValueError("invalid_input")
            name = _input_name(raw.get("name"))
            handle = raw.get("evidenceHandle")
            origin = raw.get("origin")
            if name is None or name in values:
                raise ValueError("invalid_input_name")
            has_handle = isinstance(handle, str) and bool(handle)
            has_user_origin = origin == "user-input"
            if has_handle == has_user_origin:
                raise ValueError("invalid_input_origin")
            if has_handle:
                handle = _canonical_evidence_reference(handle)
            if has_handle and not (
                _HANDLE_RE.fullmatch(handle) or _COLLECTION_ADDRESS_RE.fullmatch(handle)
            ):
                raise ValueError("invalid_evidence_handle")
            value = _decimal(raw.get("value"))
            values[name] = value
            item: dict[str, Any] = {
                "name": name,
                "value": format(value, "f"),
            }
            if has_handle:
                item["citationId"] = handle
            else:
                item["origin"] = "user-input"
            if isinstance(raw.get("unit"), str) and raw["unit"].strip():
                item["unit"] = raw["unit"].strip()
            inputs.append(item)
        canonical_expression = _canonical_expression(expression, unit)
        result = evaluate_decimal_expression(canonical_expression, values)
        rendered_result = _stable_decimal(result, decimal_places)
    except (ValueError, ArithmeticError) as exc:
        return ToolResult(f"citation_calculate: {exc}", is_error=True)

    canonical = {
        "expression": canonical_expression,
        "inputs": inputs,
        "result": rendered_result,
        "unit": unit.strip(),
        "decimalPlaces": decimal_places,
        **{
            key: args[key].strip()
            for key in ("entityId", "entityName", "metric", "period", "scope", "basis")
            if isinstance(args.get(key), str) and args[key].strip()
        },
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    handle = f"ev_calc_{digest}"
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    evidence = {
        "kind": "calculation",
        "toolName": "runtime.calculation",
        "expression": canonical["expression"],
        "inputs": inputs,
        "result": rendered_result,
        "unit": canonical["unit"],
        "rounding": f"{decimal_places}dp",
        "calculatedAt": now,
        **{
            key: canonical[key]
            for key in ("entityId", "entityName", "metric", "period", "scope", "basis")
            if key in canonical
        },
    }
    payload = {
        "result": rendered_result,
        "unit": canonical["unit"],
        "evidenceHandle": handle,
        "_valuz_evidence": {
            "evidenceHandle": handle,
            "source": {
                "sourceId": f"calculation-{digest}",
                "providerId": "valuz-calculation",
                "sourceType": "tool-result",
                "title": "Calculation",
                "retrievedAt": now,
            },
            "evidence": evidence,
        },
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def build_citation_calculation_tool_defs() -> tuple[ToolDef, ...]:
    return (
        ToolDef(
            name=CITATION_CALCULATE_TOOL_NAME,
            description=(
                "Compute a derived numeric result deterministically from retrieved values that "
                "have direct evidence handles or exact structured Collection Addresses, plus "
                "optional values explicitly supplied by the user using origin='user-input', and "
                "return a calculation evidence handle. Use this "
                "for growth rates, margins, ratios, differences, sums, and other arithmetic "
                "that appears in a citation-aware answer. Cite the returned handle on the "
                "derived claim; do not calculate those values only in prose. When unit is %, "
                "a unitless ratio expression is converted to percentage points automatically. "
                "Never mark a retrieved or model-invented value as user-input; the host verifies "
                "every such value against the task prompt before accepting the calculation."
            ),
            parameters=_PARAMS,
            handler=_citation_calculate_handler,
            read_only=True,
        ),
    )
