"""Validate i18n locale files against schema and check key consistency.

Usage:
    cd backend && uv run python ../../i18n/scripts/check_keys.py

Exit codes:
    0 - all locales valid and consistent
    1 - schema validation failures
    2 - key consistency failures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("ERROR: jsonschema not installed. Run: cd backend && uv sync --group dev", file=sys.stderr)
    raise SystemExit(1)


def _repo_root() -> Path:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "Makefile").is_file() and (p / "frontend").is_dir():
            return p
        p = p.parent
    raise RuntimeError("Cannot locate repo root")


def _collect_dot_paths(obj: object, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if not isinstance(obj, dict):
        return paths
    for key, value in sorted(obj.items()):
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths.extend(_collect_dot_paths(value, full))
        else:
            paths.append(full)
    return paths


def main() -> int:
    repo = _repo_root()
    schema_path = repo / "i18n" / "schema.json"
    locales_dir = repo / "i18n" / "locales"
    reference_name = "en-US.json"

    if not schema_path.is_file():
        print(f"ERROR: schema not found: {schema_path}", file=sys.stderr)
        return 1
    if not locales_dir.is_dir():
        print(f"ERROR: locales dir not found: {locales_dir}", file=sys.stderr)
        return 1

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    locale_files = sorted(locales_dir.glob("*.json"))
    if not locale_files:
        print("ERROR: no locale files found in i18n/locales/", file=sys.stderr)
        return 1

    # Phase 1: schema validation
    schema_errors = 0
    for lf in locale_files:
        data = json.loads(lf.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            print(f"FAIL {lf.name}: {exc.message}")
            schema_errors += 1
        else:
            print(f"OK   {lf.name} (schema valid)")

    if schema_errors:
        print(f"\n{schema_errors} schema validation failure(s).")
        return 1

    # Phase 2: key consistency
    ref_keys = set(
        _collect_dot_paths(json.loads((locales_dir / reference_name).read_text(encoding="utf-8")))
    )
    key_errors = 0
    for lf in locale_files:
        if lf.name == reference_name:
            continue
        other_keys = set(_collect_dot_paths(json.loads(lf.read_text(encoding="utf-8"))))
        missing = ref_keys - other_keys
        extra = other_keys - ref_keys
        if missing:
            print(f"FAIL {lf.name}: missing keys: {sorted(missing)}")
            key_errors += 1
        if extra:
            print(f"FAIL {lf.name}: extra keys: {sorted(extra)}")
            key_errors += 1
        if not missing and not extra:
            print(f"OK   {lf.name} ({len(other_keys)} keys match {reference_name})")

    if key_errors:
        print(f"\n{key_errors} key consistency failure(s).")
        return 2

    print(f"\nAll {len(locale_files)} locale(s) valid and consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
