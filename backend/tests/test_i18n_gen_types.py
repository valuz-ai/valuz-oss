"""Tests for i18n type generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "i18n" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from gen_types import _format_union


class TestFormatUnion:
    def test_should_format_ts_union(self) -> None:
        result = _format_union(["a.b", "c.d"], '"', "  | ")
        assert result == '  | "a.b",\n  | "c.d",'

    def test_should_format_python_literal(self) -> None:
        result = _format_union(["a.b", "c.d"], '"', "    ")
        assert result == '    "a.b",\n    "c.d",'

    def test_should_handle_single_key(self) -> None:
        assert _format_union(["only"], '"', "  | ") == '  | "only",'

    def test_should_handle_empty_list(self) -> None:
        assert _format_union([], '"', "  ") == ""


class TestGenTypesEndToEnd:
    def test_should_generate_valid_ts_and_python(self, tmp_path: Path) -> None:
        locales = tmp_path / "i18n" / "locales"
        locales.mkdir(parents=True)
        (locales / "en-US.json").write_text(
            json.dumps({"app": {"title": "Valuz", "greet": "Hello"}}), encoding="utf-8"
        )
        (tmp_path / "Makefile").write_text("dev:\n", encoding="utf-8")
        (tmp_path / "frontend").mkdir()

        import gen_types

        with patch.object(gen_types, "_repo_root", return_value=tmp_path):
            assert gen_types.main() == 0

        ts = tmp_path / "frontend" / "packages" / "shared" / "src" / "types" / "i18n.ts"
        assert ts.is_file()
        ts_content = ts.read_text(encoding="utf-8")
        assert "type I18nKey" in ts_content
        assert '"app.greet"' in ts_content
        assert "DO NOT EDIT" in ts_content

        py = tmp_path / "backend" / "valuz_agent" / "generated" / "i18n_keys.py"
        assert py.is_file()
        py_content = py.read_text(encoding="utf-8")
        assert "I18nKey = Literal[" in py_content
        assert '"app.greet"' in py_content

        init = tmp_path / "backend" / "valuz_agent" / "generated" / "__init__.py"
        assert init.is_file()
        assert "I18nKey" in init.read_text(encoding="utf-8")

    def test_should_fail_when_no_en_us_json(self, tmp_path: Path) -> None:
        import gen_types

        (tmp_path / "Makefile").write_text("dev:\n")
        (tmp_path / "frontend").mkdir()
        with patch.object(gen_types, "_repo_root", return_value=tmp_path):
            assert gen_types.main() == 1
