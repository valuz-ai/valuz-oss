"""``_build_settings`` merges harness defaults without clobbering the project.

Each harness product default is injected only when the workspace's own
``.claude/settings.json`` hasn't set the key, so an explicit project value
always wins. Egress loopback fields are the deliberate exception: the CLI's
additional settings layer repeats those non-secret values because project
``env`` has higher priority than process env. ``skipWebFetchPreflight`` is
additionally gated on the ``VALUZ_SKIP_WEBFETCH_PREFLIGHT`` env var: the CLI's WebFetch preflight
(``api.anthropic.com/api/web/domain_info``) fails closed when Anthropic is
unreachable, so deployments behind restrictive egress opt in to skipping it;
everyone else keeps Anthropic's malicious-domain blocklist.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json
from pathlib import Path

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_buffer_size.py.
import kernel  # noqa: F401

import pytest

from src.core.types import ModelSettings
from src.runtimes.claude_agent.runtime import (
    SKIP_WEBFETCH_PREFLIGHT_ENV,
    ClaudeAgentRuntime,
    _merge_forced_settings_env,
)


def _build(
    workspace_root: str | None,
    model_settings: ModelSettings | None = None,
) -> dict:
    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = workspace_root
    rt.model_settings = model_settings
    raw = rt._build_settings()
    return json.loads(raw) if raw is not None else {}


def _write_project_settings(tmp_path: Path, settings: dict) -> str:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return str(tmp_path)


# -- baseline: the workflows default ----------------------------------------


def test_workflows_default_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    settings = _build(None)
    assert settings == {"enableWorkflows": True}


def test_project_explicit_workflows_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    root = _write_project_settings(tmp_path, {"enableWorkflows": False})
    settings = _build(root)
    assert "enableWorkflows" not in settings  # project value loads via setting_sources


# -- skipWebFetchPreflight: env-gated ---------------------------------------


def test_preflight_skip_not_injected_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    assert "skipWebFetchPreflight" not in _build(None)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
def test_preflight_skip_injected_when_env_truthy(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SKIP_WEBFETCH_PREFLIGHT_ENV, value)
    settings = _build(None)
    assert settings["skipWebFetchPreflight"] is True
    assert settings["enableWorkflows"] is True  # both defaults coexist


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_preflight_skip_ignores_falsy_env(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SKIP_WEBFETCH_PREFLIGHT_ENV, value)
    assert "skipWebFetchPreflight" not in _build(None)


def test_project_explicit_preflight_value_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project that explicitly KEEPS the safety check must not be overridden
    # by the deployment-wide env opt-in.
    monkeypatch.setenv(SKIP_WEBFETCH_PREFLIGHT_ENV, "1")
    root = _write_project_settings(tmp_path, {"skipWebFetchPreflight": False})
    assert "skipWebFetchPreflight" not in _build(root)


# -- autoCompactWindow: never injected from the declared window ------------


def test_declared_window_does_not_inject_auto_compact_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The channel-declared ``max_input_tokens`` travels as
    ``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` (env) and the CLI applies its own
    compaction threshold inside it. Injecting ``autoCompactWindow`` here used
    to make ``/context`` report ``0.85 x N`` as the window (``x / 850k`` for a
    declared 1M) — the CLI treats that key as the effective window."""
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    for declared in (100_000, 200_000, 1_000_000, 2_000_000):
        assert "autoCompactWindow" not in _build(None, ModelSettings(max_input_tokens=declared))
    assert "autoCompactWindow" not in _build(None)
    assert "autoCompactWindow" not in _build(None, ModelSettings(effort="high"))


def test_project_explicit_auto_compact_window_is_left_to_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project's own ``autoCompactWindow`` is loaded through
    ``setting_sources`` — the harness layer never repeats or overrides it."""
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    root = _write_project_settings(tmp_path, {"autoCompactWindow": 120_000})
    assert "autoCompactWindow" not in _build(root, ModelSettings(max_input_tokens=200_000))


def test_egress_env_is_forced_in_additional_settings_without_copying_project_env(
    tmp_path: Path,
) -> None:
    root = _write_project_settings(
        tmp_path,
        {
            "env": {
                "ANTHROPIC_BASE_URL": "https://project.example",
                "PROJECT_SECRET": "must-not-be-copied-to-cli-args",
            }
        },
    )
    local = "http://127.0.0.1:43123/capability"

    base = _build(root)
    settings = json.loads(
        _merge_forced_settings_env(
            json.dumps(base),
            {
                "ANTHROPIC_BASE_URL": local,
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1",
            },
        )
        or "{}"
    )

    assert settings["env"] == {
        "ANTHROPIC_BASE_URL": local,
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    assert "PROJECT_SECRET" not in json.dumps(settings)
