"""Tests for i18n key collection logic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "i18n" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from check_keys import _collect_dot_paths


class TestCollectDotPaths:
    def test_should_flatten_flat_dict(self) -> None:
        assert _collect_dot_paths({"a": "A", "b": "B"}) == ["a", "b"]

    def test_should_flatten_nested_dict_with_dot_paths(self) -> None:
        result = _collect_dot_paths({"common": {"save": "Save", "cancel": "Cancel"}})
        assert "common.cancel" in result
        assert "common.save" in result

    def test_should_flatten_deeply_nested_dict(self) -> None:
        result = _collect_dot_paths({"a": {"b": {"c": "C"}}})
        assert "a.b.c" in result

    def test_should_return_sorted_paths(self) -> None:
        assert _collect_dot_paths({"z": "Z", "a": {"y": "Y", "x": "X"}}) == ["a.x", "a.y", "z"]

    def test_should_return_empty_for_empty_dict(self) -> None:
        assert _collect_dot_paths({}) == []

    def test_should_return_empty_for_non_dict(self) -> None:
        assert _collect_dot_paths("not a dict") == []

    def test_should_handle_real_en_us_json(self) -> None:
        en_path = Path(__file__).resolve().parents[2] / "i18n" / "locales" / "en-US.json"
        if not en_path.exists():
            pytest.skip("en-US.json not yet created")
        data = json.loads(en_path.read_text(encoding="utf-8"))
        keys = _collect_dot_paths(data)
        assert "common.save" in keys
        assert "schedule.validation.invalidCron" in keys
