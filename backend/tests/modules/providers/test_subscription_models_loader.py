"""Tests for the subscription_models.json loader (REP-107).

Covers parsing, schema tolerance, and the per-user override merge so a
malformed override or missing field can never block backend boot.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from valuz_agent.modules.providers.service import (
    _load_subscription_models,
)


def test_loads_bundled_subscription_models() -> None:
    """The bundled JSON ships with claude/codex subscription model lists."""
    loaded = _load_subscription_models()
    assert "claude-subscription" in loaded
    assert "codex-subscription" in loaded
    claude = loaded["claude-subscription"]
    assert claude["default_model"] == "claude-sonnet-4-6"
    assert "claude-opus-4-7" in claude["model_options"]


def test_local_override_replaces_bundled_entry(tmp_path: Path) -> None:
    """Per-user override fully replaces the bundled subscription block —
    no array merge, simpler mental model."""
    override = tmp_path / "subscription_models.local.json"
    override.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "subscriptions": {
                    "claude-subscription": {
                        "default_model": "claude-opus-4-7",
                        "models": ["claude-opus-4-7", "claude-opus-4-8-preview"],
                    }
                },
            }
        )
    )

    with patch("valuz_agent.infra.config.settings") as mock_settings:
        mock_settings.data_dir = tmp_path
        loaded = _load_subscription_models()

    claude = loaded["claude-subscription"]
    assert claude["default_model"] == "claude-opus-4-7"
    assert claude["model_options"] == (
        "claude-opus-4-7",
        "claude-opus-4-8-preview",
    )
    # Subscription kinds NOT in the override fall through to bundled.
    assert "codex-subscription" in loaded


def test_missing_local_override_is_silently_ignored(tmp_path: Path) -> None:
    """No local file at all → bundled values pass through unchanged."""
    with patch("valuz_agent.infra.config.settings") as mock_settings:
        mock_settings.data_dir = tmp_path
        loaded = _load_subscription_models()
    assert loaded["claude-subscription"]["default_model"] == "claude-sonnet-4-6"


def test_malformed_local_override_does_not_crash_boot(tmp_path: Path) -> None:
    """Corrupted JSON falls through to bundled values (don't block backend boot)."""
    bad = tmp_path / "subscription_models.local.json"
    bad.write_text("{ this is not json")

    with patch("valuz_agent.infra.config.settings") as mock_settings:
        mock_settings.data_dir = tmp_path
        loaded = _load_subscription_models()

    assert "claude-subscription" in loaded
    assert "codex-subscription" in loaded


def test_models_array_with_non_string_entries_is_filtered(tmp_path: Path) -> None:
    """Defensive parsing — bad entries inside ``models`` array don't poison
    the whole subscription block."""
    override = tmp_path / "subscription_models.local.json"
    override.write_text(
        json.dumps(
            {
                "subscriptions": {
                    "codex-subscription": {
                        "default_model": "gpt-5.5",
                        "models": [
                            "gpt-5.5",
                            123,
                            None,
                            "gpt-5.4",
                            "",
                        ],
                    }
                }
            }
        )
    )

    with patch("valuz_agent.infra.config.settings") as mock_settings:
        mock_settings.data_dir = tmp_path
        loaded = _load_subscription_models()

    assert loaded["codex-subscription"]["model_options"] == ("gpt-5.5", "gpt-5.4")


def test_default_model_falls_back_to_first_model_when_missing(tmp_path: Path) -> None:
    """If JSON forgets default_model, use the first listed model id."""
    override = tmp_path / "subscription_models.local.json"
    override.write_text(
        json.dumps({"subscriptions": {"claude-subscription": {"models": ["claude-opus-4-7"]}}})
    )

    with patch("valuz_agent.infra.config.settings") as mock_settings:
        mock_settings.data_dir = tmp_path
        loaded = _load_subscription_models()

    assert loaded["claude-subscription"]["default_model"] == "claude-opus-4-7"


def test_empty_models_array_skips_the_block(tmp_path: Path) -> None:
    """A subscription block with no models is dropped, not propagated."""
    override = tmp_path / "subscription_models.local.json"
    override.write_text(json.dumps({"subscriptions": {"codex-subscription": {"models": []}}}))

    with patch("valuz_agent.infra.config.settings") as mock_settings:
        mock_settings.data_dir = tmp_path
        loaded = _load_subscription_models()

    # The override didn't supply any usable models, so codex-subscription
    # falls back to bundled defaults — not "empty model_options".
    assert len(loaded["codex-subscription"]["model_options"]) > 0
