"""Manifest validation: the canonical schema plus its x-valuz-rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.modules.third_party.helpers import build_plugin, manifest_of
from valuz_agent.modules.third_party import manifest as mf


def test_backend_schema_is_a_byte_identical_copy_of_the_sdk_schema() -> None:
    backend = Path(mf.__file__).resolve().parents[2] / "resources" / "valuz-plugin.schema.json"
    sdk = (
        Path(__file__).resolve().parents[4]
        / "frontend"
        / "packages"
        / "plugin-sdk"
        / "valuz-plugin.schema.json"
    )
    assert sdk.is_file(), f"canonical schema missing at {sdk}"
    assert backend.read_bytes() == sdk.read_bytes()


def test_a_valid_package_has_no_errors(tmp_path: Path) -> None:
    root = build_plugin(tmp_path / "p")
    manifest, read_errors = mf.read_manifest(root)
    assert read_errors == []
    errors, warnings = mf.validate_manifest(manifest, root)
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"id": "oss-thing.x"}, "reserved"),
        ({"id": "commercial.x"}, "reserved"),
        ({"id": "valuz.x"}, "reserved"),
        ({"id": "NotValid"}, "id"),
        ({"version": "1.0"}, "version"),
        ({"frontend": {"entry": "frontend/../x.js"}}, "'..'"),
        ({"frontend": {"entry": "frontend/missing.js"}}, "does not exist"),
        ({"backend": {"runtime": "node"}}, "B-level"),
        ({"config": {"type": "string"}}, "type: object"),
        ({"config": {"type": "object", "properties": {"a": {"type": 3}}}}, "invalid JSON Schema"),
        ({"permissions": ["root:everything"]}, "permissions"),
        ({"unknownKey": 1}, "unknownKey"),
    ],
)
def test_rules_reject(tmp_path: Path, over: dict, needle: str) -> None:
    root = build_plugin(tmp_path / "p", files={"automations/a.py": "def run(ctx): return {}\n"})
    manifest = manifest_of(**over)
    errors, _ = mf.validate_manifest(manifest, root)
    assert any(needle in e for e in errors), errors


def test_entry_must_be_a_js_module(tmp_path: Path) -> None:
    root = build_plugin(
        tmp_path / "p",
        frontend={"entry": "frontend/index.cjs"},
        files={"frontend/index.cjs": "export default {}\n"},
    )
    manifest, _ = mf.read_manifest(root)
    errors, _ = mf.validate_manifest(manifest, root)
    assert any("ES module (.js or .mjs)" in e for e in errors)


def test_an_entry_without_export_only_warns(tmp_path: Path) -> None:
    root = build_plugin(tmp_path / "p", files={"frontend/index.js": "(function(){})();\n"})
    manifest, _ = mf.read_manifest(root)
    errors, warnings = mf.validate_manifest(manifest, root)
    assert errors == []
    assert any("no export" in w for w in warnings)


def test_missing_style_and_automation_entry_are_errors(tmp_path: Path) -> None:
    root = build_plugin(tmp_path / "p")
    (root / "frontend" / "index.css").unlink()
    manifest = manifest_of(
        automations=[{"name": "a", "runtime": "python", "entry": "automations/a.py"}]
    )
    errors, _ = mf.validate_manifest(manifest, root)
    assert any("frontend.styles[0]" in e for e in errors)
    assert any("automations[0].entry" in e for e in errors)


def test_warnings(tmp_path: Path) -> None:
    root = build_plugin(
        tmp_path / "p",
        icon="icon.svg",
        locales="i18n",
        engines={"valuz-plugin-api": "^2.0.0"},
        files={"i18n/english.json": "{}"},
    )
    manifest = json.loads((root / "valuz-plugin.json").read_text())
    errors, warnings = mf.validate_manifest(manifest, root)
    assert errors == []
    joined = "\n".join(warnings)
    assert "icon: 'icon.svg' does not exist" in joined
    assert "does not include plugin API 1.0.0" in joined
    assert "<lang>.json" in joined
    manifest["locales"] = "nowhere"
    _, warnings = mf.validate_manifest(manifest, root)
    assert any("locales: 'nowhere' does not exist" in w for w in warnings)


def test_unparsable_engines_and_bad_cron_are_warnings(tmp_path: Path) -> None:
    root = build_plugin(tmp_path / "p", files={"automations/a.py": "x = 1\n"})
    manifest = manifest_of(
        engines={"valuz-plugin-api": "banana"},
        automations=[
            {
                "name": "a",
                "runtime": "python",
                "entry": "automations/a.py",
                "trigger": {"cron": "not a cron"},
            }
        ],
    )
    errors, warnings = mf.validate_manifest(manifest, root)
    assert errors == []
    assert any("cannot parse 'banana'" in w for w in warnings)
    assert any("trigger.cron" in w for w in warnings)


def test_locale_files_must_be_json_objects(tmp_path: Path) -> None:
    root = build_plugin(
        tmp_path / "p", files={"locales/fr-FR.json": "[1]", "locales/de-DE.json": "{x"}
    )
    manifest, _ = mf.read_manifest(root)
    errors, _ = mf.validate_manifest(manifest, root)
    assert any("fr-FR.json: must be a JSON object" in e for e in errors)
    assert any("de-DE.json: invalid JSON" in e for e in errors)


def test_a_manifest_path_that_escapes_through_a_symlink_is_an_error(tmp_path: Path) -> None:
    root = build_plugin(tmp_path / "p")
    outside = tmp_path / "outside.js"
    outside.write_text("export default {}\n")
    (root / "frontend" / "index.js").unlink()
    (root / "frontend" / "index.js").symlink_to(outside)
    manifest, _ = mf.read_manifest(root)
    errors, _ = mf.validate_manifest(manifest, root)
    assert any("resolves outside the package" in e for e in errors)


def test_read_manifest_failures(tmp_path: Path) -> None:
    assert mf.read_manifest(tmp_path)[0] is None
    (tmp_path / "valuz-plugin.json").write_text("{nope", encoding="utf-8")
    manifest, errors = mf.read_manifest(tmp_path)
    assert manifest is None and "not valid JSON" in errors[0]
    (tmp_path / "valuz-plugin.json").write_text("[]", encoding="utf-8")
    assert mf.read_manifest(tmp_path)[1] == ["valuz-plugin.json must be a JSON object"]


def test_pick_text_and_defaults() -> None:
    assert mf.pick_text("x") == "x"
    assert mf.pick_text({"zh-CN": "看板", "en-US": "Board"}) == "Board"
    assert mf.pick_text({"zh-CN": "看板"}) == "看板"
    schema = {
        "type": "object",
        "properties": {
            "region": {"type": "string", "default": "cn"},
            "nested": {"type": "object", "properties": {"a": {"default": 1}, "b": {"default": 2}}},
        },
    }
    assert mf.fill_defaults(schema, {"nested": {"b": 9}}) == {
        "region": "cn",
        "nested": {"a": 1, "b": 9},
    }
    assert mf.fill_defaults(None, {"x": 1}) == {"x": 1}
