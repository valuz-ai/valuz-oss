"""Model-capability image gate (docs/design/model-capability, commercial repo).

A session whose model explicitly declares no image input
(``ModelSettings.input_modalities`` without ``"image"``) must keep agent image
reads out of the request:

* claude: a PreToolUse **soft deny** on ``Read`` of image/PDF files — hooks
  fire in every permission mode (``full_access`` → bypassPermissions kills
  ``can_use_tool``, so the gate deliberately does NOT ride that callback),
  and the deny must not terminate the turn (no ``continue_=False``).
* codex: ``include_view_image_tool=false`` — the tool is simply not
  registered.

Three-state: an undeclared model (``None``) gates nothing, so every existing
session behaves exactly as today.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import ModelSettings, Session, model_rejects_images
from src.runtimes.claude_agent.runtime import (
    ClaudeAgentRuntime,
    _is_image_file_read,
)
from src.runtimes.codex.runtime import _build_config_overrides


class _Sink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


TEXT_ONLY = ModelSettings(input_modalities=("text",))


# ── shared predicate (three-state) ───────────────────────────────────


def test_model_rejects_images_three_state() -> None:
    assert model_rejects_images(TEXT_ONLY) is True
    assert model_rejects_images(ModelSettings(input_modalities=("text", "image"))) is False
    # Not declared — settings absent, or modalities absent — never gates.
    assert model_rejects_images(None) is False
    assert model_rejects_images(ModelSettings(effort="high")) is False


# ── claude: PreToolUse soft deny ─────────────────────────────────────


def test_read_target_classification() -> None:
    assert _is_image_file_read("Read", {"file_path": "/w/chart.PNG"})
    # PDF: the CLI renders pages as image blocks, so it gates too.
    assert _is_image_file_read("Read", {"file_path": "/w/report.pdf"})
    assert not _is_image_file_read("Read", {"file_path": "/w/notes.md"})
    assert not _is_image_file_read("Write", {"file_path": "/w/chart.png"})
    assert not _is_image_file_read("Read", None)


async def test_claude_denies_image_read_softly_when_model_is_text_only() -> None:
    runtime = ClaudeAgentRuntime(
        AgentConfig(id="a", name="a"), "", _Sink(), model_settings=TEXT_ONLY
    )
    hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    output = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": "/w/screenshot.png"}},
        "tool-1",
        None,  # type: ignore[arg-type]
    )

    specific = output["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    # Model-facing guidance: point at the parsed text extract.
    assert "extracted-text" in specific["permissionDecisionReason"]
    # Soft deny — the turn must keep running.
    assert "continue_" not in output and "stopReason" not in output


async def test_claude_leaves_non_image_reads_alone() -> None:
    runtime = ClaudeAgentRuntime(
        AgentConfig(id="a", name="a"), "", _Sink(), model_settings=TEXT_ONLY
    )
    hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    output = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": "/w/notes.md"}},
        "tool-2",
        None,  # type: ignore[arg-type]
    )

    assert "hookSpecificOutput" not in output


async def test_claude_undeclared_model_never_denies() -> None:
    """Three-state regression: no declaration → image reads flow exactly as
    today, even though the PreToolUse matcher exists for citation capture."""
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _Sink())
    hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    output = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": "/w/screenshot.png"}},
        "tool-3",
        None,  # type: ignore[arg-type]
    )

    assert "hookSpecificOutput" not in output


# ── codex: tool not registered ───────────────────────────────────────


def _codex_session(model_settings: ModelSettings | None) -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        model_settings=model_settings,
    )


def test_codex_drops_view_image_tool_when_model_is_text_only() -> None:
    ov = _build_config_overrides(_codex_session(TEXT_ONLY), None, "alias")
    assert "include_view_image_tool=false" in ov
    # Emitted once even though the bare-completion path can also add it.
    assert list(ov).count("include_view_image_tool=false") == 1


def test_codex_keeps_view_image_tool_when_undeclared() -> None:
    for settings in (None, ModelSettings(effort="high")):
        ov = _build_config_overrides(_codex_session(settings), None, "alias")
        assert "include_view_image_tool=false" not in ov
