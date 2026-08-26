"""``model_context_window`` emission from the channel-declared window.

Codex's own model catalog can't know gateway aliases (``valuz-pro-anthropic``
style ids), so without an override it falls back to its generic context
bookkeeping. When the session carries a channel-declared
``ModelSettings.max_input_tokens``, the runtime emits ``model_context_window``
as a BARE TOML integer (quoting turns it into a string codex rejects at
startup) and leaves the compaction trigger to codex, which derives
``auto_compact_token_limit`` as 90% of the resolved window itself (and clamps
any explicit ``model_auto_compact_token_limit`` to that same 90%). No
declaration → no key, so codex keeps its tuned defaults for models it does
know.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import ModelSettings, Session
from src.runtimes.codex.runtime import _build_config_overrides


def _session(model_settings: ModelSettings | None) -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        model_settings=model_settings,
    )


def test_declared_window_emits_only_the_window_as_a_bare_int() -> None:
    ov = _build_config_overrides(_session(ModelSettings(max_input_tokens=200_000)), None, "alias")
    assert "model_context_window=200000" in ov
    # Bare integer — a quoted value is the exact shape codex rejects.
    assert not any('model_context_window="' in o for o in ov)
    # The trigger is codex's own (90% of the window); never pinned from here.
    assert not any(o.startswith("model_auto_compact_token_limit=") for o in ov)


def test_no_declaration_emits_no_window_override() -> None:
    for settings in (None, ModelSettings(effort="high")):
        ov = _build_config_overrides(_session(settings), None, "gpt-5.5")
        assert not any(o.startswith("model_context_window=") for o in ov)
        assert not any(o.startswith("model_auto_compact_token_limit=") for o in ov)
