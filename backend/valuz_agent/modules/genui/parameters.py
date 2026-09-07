"""Versioned component business parameters, independent of catalog prose.

Editions own schemas and fixed data adapters. This compiler validates only the
finite shared parameter vocabulary, then projects scalar DataRef wire values.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, NoReturn


class ParameterValidationError(ValueError):
    """An invalid registration or caller-supplied parameter."""


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9_]{0,15}:[A-Z0-9^][A-Z0-9.^_\-]{0,31}$")
_SAFE_INTEGER = 9007199254740991
_FIELD_KEYS = {
    "name",
    "type",
    "optional",
    "semantic",
    "description",
    "aliases",
    "format",
    "choices",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "uniqueItems",
    "itemChoices",
    "acceptArray",
    "default",
    "unit",
}


def _fail(message: str) -> NoReturn:
    raise ParameterValidationError(message)


def _string_list(value: Any, label: str, *, names: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str) or not item.strip() or (names and not _NAME.fullmatch(item))
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        _fail(f"{label} must be a nonempty list of distinct {'names' if names else 'strings'}")
    return value


def parse_parameter_schema(raw: Any) -> dict[str, Any]:
    """Fail closed on malformed/unsupported schemas, including unknown rules."""
    if not isinstance(raw, dict) or set(raw) - {"version", "fields", "constraints"}:
        _fail("registered parameterSchema must be a versioned object")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        _fail("unsupported parameterSchema version")
    fields = raw.get("fields")
    if not isinstance(fields, list):
        _fail("parameterSchema.fields must be an array")
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - _FIELD_KEYS:
            _fail("parameterSchema contains an invalid field")
        name = field.get("name")
        kind = field.get("type")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            _fail("parameterSchema field requires a valid name")
        if not isinstance(kind, str) or kind not in {"string", "integer", "boolean", "string-list"}:
            _fail(f"{name} has an unsupported registered type")
        if type(field.get("optional")) is not bool:
            _fail(f"{name}.optional must be boolean")
        aliases = (
            _string_list(field["aliases"], f"{name}.aliases", names=True)
            if "aliases" in field
            else []
        )
        for key in [name, *aliases]:
            if key in seen:
                _fail(f"duplicate parameter or alias: {key}")
            seen.add(key)
        for key in ("semantic", "description", "unit"):
            if key in field and not isinstance(field[key], str):
                _fail(f"{name}.{key} must be text")
        if "format" in field and (
            not isinstance(field["format"], str)
            or field["format"] not in {"date", "symbol"}
            or kind not in {"string", "string-list"}
            or (field["format"] == "date" and kind != "string")
        ):
            _fail(f"{name} has an unsupported format")
        for key in ("choices", "itemChoices"):
            if key in field:
                _string_list(field[key], f"{name}.{key}")
                if kind != ("string" if key == "choices" else "string-list"):
                    _fail(f"{name}.{key} does not apply to {kind}")
        for low, high, expected in (
            ("minimum", "maximum", "integer"),
            ("minItems", "maxItems", "string-list"),
        ):
            for key in (low, high):
                if key in field and (
                    kind != expected
                    or type(field[key]) is not int
                    or abs(field[key]) > _SAFE_INTEGER
                    or (expected == "string-list" and field[key] < 1)
                ):
                    _fail(f"{name}.{key} is invalid")
            if low in field and high in field and field[low] > field[high]:
                _fail(f"{name} has inverted bounds")
        for key in ("uniqueItems", "acceptArray"):
            if key in field and (kind != "string-list" or type(field[key]) is not bool):
                _fail(f"{name}.{key} is invalid")
    by_name = {field["name"]: field for field in fields}
    rules = raw.get("constraints", [])
    if not isinstance(rules, list):
        _fail("parameterSchema.constraints must be an array")
    for rule in rules:
        if not isinstance(rule, dict):
            _fail("parameterSchema contains an invalid constraint")
        kind = rule.get("kind")
        if not isinstance(kind, str):
            _fail("parameterSchema contains an unsupported constraint")
        keys = {
            "dateOrder": {"kind", "start", "end"},
            "atLeastOne": {"kind", "fields"},
            "requiredWhenAny": {"kind", "field", "source", "values"},
        }.get(kind)
        if keys is None or set(rule) != keys:
            _fail("parameterSchema contains an unsupported constraint")
        if kind == "atLeastOne":
            refs = _string_list(rule["fields"], "atLeastOne.fields", names=True)
        else:
            refs = [
                rule[key]
                for key in (("start", "end") if kind == "dateOrder" else ("field", "source"))
            ]
        if any(not isinstance(ref, str) or ref not in by_name for ref in refs):
            _fail("parameterSchema constraint references an unknown field")
        if kind == "dateOrder" and any(by_name[ref].get("format") != "date" for ref in refs):
            _fail("dateOrder requires date-formatted fields")
        if kind == "requiredWhenAny":
            _string_list(rule["values"], "requiredWhenAny.values")
            if by_name[rule["source"]]["type"] not in {"string", "string-list"}:
                _fail("requiredWhenAny requires a text or list source")
    for field in fields:
        if "default" in field:
            _validate_value(field["default"], field)
    return raw


def compatible_host_keys(field: dict[str, Any]) -> list[str]:
    keys = [field["name"], *field.get("aliases", [])]
    # Preserve the existing scalar-host -> CSV-list binding convention.
    if field["type"] == "string-list" and field["name"].endswith("s"):
        keys.append(field["name"][:-1])
    return list(dict.fromkeys(keys))


def _validate_value(value: Any, field: dict[str, Any]) -> Any:
    name, kind = field["name"], field["type"]
    if kind == "boolean":
        if type(value) is not bool:
            _fail(f"{name} must be a boolean")
    elif kind == "integer":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not (-_SAFE_INTEGER <= value <= _SAFE_INTEGER)
            or int(value) != value
        ):
            _fail(f"{name} must be a finite safe integer")
        if ("minimum" in field and value < field["minimum"]) or (
            "maximum" in field and value > field["maximum"]
        ):
            _fail(f"{name} is outside its registered bounds")
        return int(value)
    elif kind == "string-list":
        parts = (
            [part.strip() for part in value.split(",")]
            if isinstance(value, str)
            else value
            if field.get("acceptArray") and isinstance(value, list)
            else None
        )
        if parts is None or any(not isinstance(part, str) or not part.strip() for part in parts):
            _fail(f"{name} must contain nonempty text values")
        parts = [part.strip() for part in parts]
        if len(parts) < field.get("minItems", 1) or (
            "maxItems" in field and len(parts) > field["maxItems"]
        ):
            _fail(f"{name} has an invalid number of items")
        if field.get("uniqueItems") and len(set(parts)) != len(parts):
            _fail(f"{name} must contain distinct values")
        if field.get("itemChoices") and any(part not in field["itemChoices"] for part in parts):
            _fail(f"{name} contains unsupported values")
        if field.get("format") == "symbol" and any(not _SYMBOL.fullmatch(part) for part in parts):
            _fail(f"{name} must contain canonical symbols")
        # DataRef parameters stay scalar. Do not silently change list semantics
        # when a single array item itself contains the CSV delimiter.
        if any("," in part for part in parts):
            _fail(f"{name} list items cannot contain commas")
        return ",".join(parts)
    else:
        if not isinstance(value, str) or not value.strip():
            _fail(f"{name} must be nonempty text")
        if "choices" in field and value not in field["choices"]:
            _fail(f"{name} must be one of: {', '.join(field['choices'])}")
        if field.get("format") == "date":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                _fail(f"{name} must be a valid YYYY-MM-DD date")
        if field.get("format") == "symbol" and not _SYMBOL.fullmatch(value):
            _fail(f"{name} must be a canonical symbol")
    return value


def validate_component_parameters(params: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize aliases/lists; validate literals and compatible deferred refs.

    A Host reference is not a literal value. Its resolved value and constraints
    must also be checked by the data runtime before adapter invocation.
    """
    if not isinstance(params, dict):
        _fail("parameters must be an object")
    fields = schema["fields"]
    names = {name: field for field in fields for name in [field["name"], *field.get("aliases", [])]}
    result: dict[str, Any] = {}
    deferred: set[str] = set()
    for key, value in params.items():
        field = names.get(key)
        if field is None:
            _fail(f"unknown parameter: {key}")
        name = field["name"]
        if name in result:
            _fail(f"conflicting parameter aliases: {name}")
        if isinstance(value, dict) and set(value) == {"$host"}:
            if value["$host"] not in compatible_host_keys(field):
                _fail(f"{name} references an incompatible host key")
            result[name] = value
            deferred.add(name)
        else:
            result[name] = _validate_value(value, field)
    for field in fields:
        if not field["optional"] and field["name"] not in result:
            _fail(f"{field['name']} is required")
    for rule in schema.get("constraints", []):
        kind = rule["kind"]
        if kind == "dateOrder":
            start, end = rule["start"], rule["end"]
            if start in result and end in result and not deferred.intersection((start, end)):
                if result[start] > result[end]:
                    _fail(f"{start} must not follow {end}")
        elif kind == "atLeastOne" and not any(name in result for name in rule["fields"]):
            _fail(f"one of {', '.join(rule['fields'])} is required")
        elif kind == "requiredWhenAny" and rule["source"] not in deferred:
            value = result.get(rule["source"])
            items = [part.strip() for part in value.split(",")] if isinstance(value, str) else []
            if any(item in rule["values"] for item in items) and rule["field"] not in result:
                _fail(f"{rule['field']} is required for selected {rule['source']}")
    return result


def parameter_specs(schema: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    required = tuple(field["name"] for field in schema["fields"] if not field["optional"])
    specs = {}
    for field in schema["fields"]:
        detail = {key: value for key, value in field.items() if key not in {"name", "optional"}}
        specs[field["name"]] = {
            "required": not field["optional"],
            "kind": field["type"],
            "field": field,
            "description": json_parameter_description(detail),
        }
    return required, specs


def json_parameter_description(detail: dict[str, Any]) -> str:
    # Machine-owned prose for the Agent menu; never read back as a contract.
    import json

    return json.dumps(detail, ensure_ascii=False, separators=(",", ":"))


def parameter_json_schema(field: dict[str, Any]) -> dict[str, Any]:
    kind = field["type"]
    primitive: dict[str, Any] = {"type": "string" if kind == "string-list" else kind}
    if kind == "integer":
        primitive.update(
            minimum=field.get("minimum", -_SAFE_INTEGER),
            maximum=field.get("maximum", _SAFE_INTEGER),
        )
    if kind in {"string", "string-list"}:
        primitive["minLength"] = 1
    if "choices" in field:
        primitive["enum"] = field["choices"]
    if field.get("format") == "date":
        primitive["format"] = "date"
    if field.get("format") == "symbol" and kind == "string":
        primitive["pattern"] = _SYMBOL.pattern
    primitive["description"] = json_parameter_description(field)
    variants = [primitive]
    if kind == "string-list" and field.get("acceptArray"):
        item: dict[str, Any] = {"type": "string", "minLength": 1, "pattern": "^[^,]+$"}
        if "itemChoices" in field:
            item["enum"] = field["itemChoices"]
        if field.get("format") == "symbol":
            item["pattern"] = _SYMBOL.pattern
        variants.append(
            {
                "type": "array",
                "items": item,
                "minItems": field.get("minItems", 1),
                **{key: field[key] for key in ("maxItems", "uniqueItems") if key in field},
            }
        )
    variants.append(
        {
            "type": "object",
            "properties": {"$host": {"type": "string", "enum": compatible_host_keys(field)}},
            "required": ["$host"],
            "additionalProperties": False,
        }
    )
    return {"oneOf": variants}


def component_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = {}
    rules: list[dict[str, Any]] = []
    for field in schema["fields"]:
        names = [field["name"], *field.get("aliases", [])]
        for name in names:
            properties[name] = parameter_json_schema(field)
        if not field["optional"]:
            rules.append({"anyOf": [{"required": [name]} for name in names]})
        for index, name in enumerate(names):
            rules.extend({"not": {"required": [name, other]}} for other in names[index + 1 :])
    for rule in schema.get("constraints", []):
        if rule["kind"] == "atLeastOne":
            names = [
                name
                for field in schema["fields"]
                if field["name"] in rule["fields"]
                for name in [field["name"], *field.get("aliases", [])]
            ]
            rules.append({"anyOf": [{"required": [name]} for name in names]})
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        **(
            {"description": json_parameter_description({"constraints": schema["constraints"]})}
            if schema.get("constraints")
            else {}
        ),
        **({"allOf": rules} if rules else {}),
    }
