"""Round-trip: ``ModelSettings.max_input_tokens`` survives the store
converter, old rows (no key) deserialize to ``None``, and a malformed stored
value degrades to "not declared" instead of poisoning the session.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.adapters.sqlalchemy_store.converters import (
    dict_to_model_settings,
    model_settings_to_dict,
)
from src.core.types import ModelSettings


def test_max_input_tokens_round_trips() -> None:
    s = ModelSettings(effort="high", max_input_tokens=200_000)
    d = model_settings_to_dict(s)
    assert d is not None and d["max_input_tokens"] == 200_000
    back = dict_to_model_settings(d)
    assert back is not None
    assert back.max_input_tokens == 200_000
    assert back.effort == "high"


def test_max_input_tokens_omitted_when_none() -> None:
    d = model_settings_to_dict(ModelSettings(effort="low"))
    assert d is not None and "max_input_tokens" not in d  # keep the shape lean
    back = dict_to_model_settings(d)
    assert back is not None and back.max_input_tokens is None


def test_legacy_row_without_key_deserializes_to_none() -> None:
    back = dict_to_model_settings({"temperature": 0.5, "max_tokens": 1000, "effort": "medium"})
    assert back is not None
    assert back.max_input_tokens is None
    assert back.effort == "medium"


def test_malformed_stored_value_degrades_to_none() -> None:
    for bad in ("200000", -1, 0, 1.5, {}):
        back = dict_to_model_settings({"max_input_tokens": bad})
        assert back is not None and back.max_input_tokens is None, bad
