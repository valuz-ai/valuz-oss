"""The ``valuz-plugin.json`` manifest: reading, validation, small accessors.

Validation is the canonical JSON Schema (draft 2020-12, shipped as
``valuz_agent/resources/valuz-plugin.schema.json`` — a byte-identical copy of
``frontend/packages/plugin-sdk/valuz-plugin.schema.json``, pinned by a test) plus
the ``x-valuz-rules`` the schema lists but a schema cannot express: reserved id
prefixes, ``..`` segments, files that must exist, an ES-module entry, unique
automation names, no B-level ``backend``, object-typed ``config`` / ``input``
schemas. Every validator of the manifest (SDK CLI, backend, control plane, Go CLI)
applies the same rules.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema import exceptions as js_exceptions

from valuz_agent.modules.third_party.semver import is_valid_range, satisfies

#: The plugin API version of this backend (``engines.valuz-plugin-api`` is matched
#: against it).
API_VERSION = "1.0.0"
MANIFEST_NAME = "valuz-plugin.json"
MAX_MANIFEST_BYTES = 1024 * 1024
RESERVED_PREFIXES = (
    "oss-",
    "commercial.",
    "commercial-",
    "finance.",
    "finance-",
    "team.",
    "team-",
    "edition.",
    "valuz.",
)
DEFAULT_LOCALES_DIR = "locales"

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "resources" / "valuz-plugin.schema.json"


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def read_manifest(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """``(manifest, errors)`` — the parsed file, or why it cannot be read."""
    path = root / MANIFEST_NAME
    try:
        size = path.stat().st_size
    except OSError:
        return None, [f"{MANIFEST_NAME} not found"]
    if size > MAX_MANIFEST_BYTES:
        return None, [f"{MANIFEST_NAME} is larger than {MAX_MANIFEST_BYTES} bytes"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"{MANIFEST_NAME} is not valid JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"{MANIFEST_NAME} must be a JSON object"]
    return raw, []


def _format(error: js_exceptions.ValidationError) -> str:
    if error.context:
        best = js_exceptions.best_match(error.context)
        if best is not None:
            error = best
    where = ".".join(str(p) for p in error.absolute_path) or "manifest"
    return f"{where}: {error.message}"


def _bad_path(value: Any) -> bool:
    return isinstance(value, str) and any(seg == ".." for seg in value.split("/"))


def relative_paths(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Every relative path the manifest names, as ``(where, path)``."""
    out: list[tuple[str, str]] = []
    frontend = manifest.get("frontend")
    if isinstance(frontend, dict):
        if isinstance(frontend.get("entry"), str):
            out.append(("frontend.entry", frontend["entry"]))
        for i, style in enumerate(frontend.get("styles") or []):
            if isinstance(style, str):
                out.append((f"frontend.styles[{i}]", style))
    for i, item in enumerate(manifest.get("automations") or []):
        if isinstance(item, dict) and isinstance(item.get("entry"), str):
            out.append((f"automations[{i}].entry", item["entry"]))
    for key in ("icon", "locales"):
        if isinstance(manifest.get(key), str):
            out.append((key, manifest[key]))
    return out


def _check_object_schema(schema: Any, where: str) -> str | None:
    if not isinstance(schema, dict):
        return f"{where}: must be a JSON Schema object"
    try:
        Draft202012Validator.check_schema(schema)
    except js_exceptions.SchemaError as exc:
        return f"{where}: invalid JSON Schema: {exc.message}"
    if schema.get("type") != "object":
        return f"{where}: the schema must declare type: object"
    return None


def _check_cron(expr: str, timezone: str | None) -> str | None:
    from valuz_agent.modules.automations.cron_utils import (
        CronInterpreter,
        UnknownTimeZoneError,
    )

    try:
        valid, _, _, message = CronInterpreter().validate(expr, timezone or "UTC")
    except UnknownTimeZoneError:
        return f"unknown timezone {timezone!r}"
    except Exception as exc:  # noqa: BLE001 — a bad expression must not crash validation
        return str(exc)
    return None if valid else (message or "invalid cron expression")


def validate_manifest(manifest: Any, root: Path | None = None) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for a parsed manifest.

    ``root`` is the unpacked package directory: when given, the files the manifest
    names are checked on disk too. Without it only the manifest itself is judged.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict):
        return [f"{MANIFEST_NAME} must be a JSON object"], warnings

    errors.extend(sorted(_format(e) for e in _validator().iter_errors(manifest)))
    # The rules below read fields that may be malformed; each guards its own input.
    plugin_id = manifest.get("id")
    if isinstance(plugin_id, str):
        for prefix in RESERVED_PREFIXES:
            if plugin_id.startswith(prefix):
                errors.append(f"id: the prefix {prefix!r} is reserved")
                break
    for where, path in relative_paths(manifest):
        if _bad_path(path):
            errors.append(f"{where}: must not contain a '..' segment")

    backend = manifest.get("backend")
    if backend is not None:
        errors.append("backend: B-level plugins are not supported by plugin API 1.x")

    engines = manifest.get("engines")
    if isinstance(engines, dict) and isinstance(engines.get("valuz-plugin-api"), str):
        rng = engines["valuz-plugin-api"]
        if not rng.strip():
            pass  # the schema already rejects an empty range
        elif not is_valid_range(rng):
            warnings.append(
                f"engines.valuz-plugin-api: cannot parse {rng!r} as a SemVer range; "
                "Valuz will mark the plugin incompatible"
            )
        elif not satisfies(API_VERSION, rng):
            warnings.append(
                f"engines.valuz-plugin-api: {rng!r} does not include plugin API {API_VERSION}; "
                "Valuz will mark the plugin incompatible"
            )

    if "config" in manifest:
        problem = _check_object_schema(manifest["config"], "config")
        if problem:
            errors.append(problem)

    automations = manifest.get("automations")
    if isinstance(automations, list):
        seen: set[str] = set()
        for i, item in enumerate(automations):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str):
                if name in seen:
                    errors.append(f"automations[{i}].name: duplicate automation name {name!r}")
                seen.add(name)
            if "input" in item:
                problem = _check_object_schema(item["input"], f"automations[{i}].input")
                if problem:
                    errors.append(problem)
            trigger = item.get("trigger")
            if isinstance(trigger, dict) and isinstance(trigger.get("cron"), str):
                tz = trigger.get("timezone")
                problem = _check_cron(trigger["cron"], tz if isinstance(tz, str) else None)
                if problem:
                    warnings.append(
                        f"automations[{i}].trigger.cron: {problem}; "
                        "the automation will not be created"
                    )

    if root is not None:
        errors.extend(_check_files(manifest, root, warnings))
    return errors, warnings


_LOCALE_FILE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?\.json$")


def _check_files(manifest: dict[str, Any], root: Path, warnings: list[str]) -> list[str]:
    errors: list[str] = []
    real_root = root.resolve()

    def locate(where: str, rel: str) -> Path | None:
        """The file ``rel`` names inside the package; an error is recorded when it
        is missing, not a file, or resolves outside the package."""
        if _bad_path(rel) or rel.startswith("/"):
            return None  # reported by the '..' rule / the schema pattern
        target = (root / rel).resolve()
        if target != real_root and real_root not in target.parents:
            errors.append(f"{where}: {rel!r} resolves outside the package")
            return None
        if not target.exists():
            errors.append(f"{where}: {rel!r} does not exist in the package")
            return None
        if not target.is_file():
            errors.append(f"{where}: {rel!r} is not a file")
            return None
        return target

    frontend = manifest.get("frontend")
    if isinstance(frontend, dict):
        entry = frontend.get("entry")
        if isinstance(entry, str):
            path = locate("frontend.entry", entry)
            if path is not None:
                if path.suffix.lower() not in (".js", ".mjs"):
                    errors.append(f"frontend.entry: {entry!r} must be an ES module (.js or .mjs)")
                elif "export" not in path.read_text(encoding="utf-8", errors="replace"):
                    warnings.append(
                        f"frontend.entry: {entry!r} has no export; "
                        "it must default-export definePlugin(...)"
                    )
        for i, style in enumerate(frontend.get("styles") or []):
            if isinstance(style, str):
                locate(f"frontend.styles[{i}]", style)
    for i, item in enumerate(manifest.get("automations") or []):
        if isinstance(item, dict) and isinstance(item.get("entry"), str):
            locate(f"automations[{i}].entry", item["entry"])
    icon = manifest.get("icon")
    if isinstance(icon, str) and not _bad_path(icon) and not (root / icon).exists():
        warnings.append(f"icon: {icon!r} does not exist in the package")
    errors.extend(_check_locales(manifest, root, warnings))
    return errors


def _check_locales(manifest: dict[str, Any], root: Path, warnings: list[str]) -> list[str]:
    errors: list[str] = []
    name = locales_dir_name(manifest)
    if _bad_path(name):
        return errors
    directory = root / name
    if not directory.is_dir():
        if "locales" in manifest:
            warnings.append(f"locales: {name!r} does not exist in the package")
        return errors
    for file in sorted(directory.glob("*.json")):
        shown = f"{name.rstrip('/')}/{file.name}"
        if not _LOCALE_FILE.match(file.name):
            warnings.append(
                f"{shown}: locale files are named <lang>.json (e.g. zh-CN.json, en-US.json)"
            )
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{shown}: invalid JSON: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{shown}: must be a JSON object of message keys")
    return errors


# ---- accessors ----------------------------------------------------------------


def pick_text(value: Any, preferred: tuple[str, ...] = ("en-US", "en", "zh-CN")) -> str:
    """A display string out of a manifest ``localizedText`` (string or per-language map)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value:
        for lang in preferred:
            if isinstance(value.get(lang), str):
                return str(value[lang])
        first = next((v for v in value.values() if isinstance(v, str)), "")
        return first
    return ""


def locales_dir_name(manifest: dict[str, Any]) -> str:
    value = manifest.get("locales")
    return value if isinstance(value, str) and value else DEFAULT_LOCALES_DIR


def read_locales(root: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The package's locale files, inlined: ``{"zh-CN": {...}, "en-US": {...}}``."""
    directory = root / locales_dir_name(manifest)
    out: dict[str, dict[str, Any]] = {}
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return out
    for file in files:
        try:
            if file.stat().st_size > 512 * 1024:
                continue
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out[file.stem] = data
    return out


def permissions_of(manifest: dict[str, Any]) -> list[str]:
    return [p for p in (manifest.get("permissions") or []) if isinstance(p, str)]


def requires_of(manifest: dict[str, Any]) -> list[str]:
    return [r for r in (manifest.get("requires") or []) if isinstance(r, str)]


def defaults_for(schema: dict[str, Any]) -> dict[str, Any]:
    """Defaults declared by an object schema's properties (recursively for objects)."""
    out: dict[str, Any] = {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return out
    for key, sub in properties.items():
        if not isinstance(sub, dict):
            continue
        if "default" in sub:
            out[key] = sub["default"]
        elif sub.get("type") == "object":
            nested = defaults_for(sub)
            if nested:
                out[key] = nested
    return out


def fill_defaults(schema: dict[str, Any] | None, values: dict[str, Any]) -> dict[str, Any]:
    """``values`` over the schema defaults (nested objects merged key by key)."""
    if not schema:
        return dict(values)

    def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, value in over.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge(out[key], value)
            else:
                out[key] = value
        return out

    return merge(defaults_for(schema), values)


__all__ = [
    "API_VERSION",
    "DEFAULT_LOCALES_DIR",
    "MANIFEST_NAME",
    "RESERVED_PREFIXES",
    "defaults_for",
    "fill_defaults",
    "load_schema",
    "locales_dir_name",
    "permissions_of",
    "pick_text",
    "read_locales",
    "read_manifest",
    "relative_paths",
    "requires_of",
    "validate_manifest",
]
