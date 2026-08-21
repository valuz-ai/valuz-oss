"""Store-converter round-trip must preserve runtime_provider=deepseek_harness.

Regression for the field failure where ``_validate_runtime_provider``'s stale
three-value set silently coerced every loaded deepseek_harness session (and
its embedded agent_config) to deepagents: the create response was honest, but
the first load before a turn rewrote the runtime and the turn ran on the
wrong engine. The set is now derived from the canonical ``RuntimeProvider``
Literal, and this test pins the full model⇄domain round-trip.
"""

from __future__ import annotations

from typing import get_args

from src.adapters.sqlalchemy_store.converters import (
    _VALID_RUNTIME_PROVIDERS,
    dict_to_agent_config,
    model_to_session,
    session_to_model,
)
from src.core.agent_config import AgentConfig
from src.core.types import RuntimeProvider, Session


def test_valid_set_is_derived_from_the_canonical_literal() -> None:
    assert _VALID_RUNTIME_PROVIDERS == set(get_args(RuntimeProvider))
    assert "deepseek_harness" in _VALID_RUNTIME_PROVIDERS


def test_session_roundtrip_preserves_deepseek_harness() -> None:
    session = Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a", runtime_provider="deepseek_harness"),
        cwd="/tmp/ws",
        runtime_provider="deepseek_harness",
        user_id="u",
        model="deepseek-v4-flash",
    )
    loaded = model_to_session(session_to_model(session))
    assert loaded.runtime_provider == "deepseek_harness"
    assert loaded.agent_config.runtime_provider == "deepseek_harness"


def test_unknown_runtime_still_coerces_to_deepagents() -> None:
    cfg = dict_to_agent_config({"name": "a", "runtime_provider": "bogus"})
    assert cfg is not None
    assert cfg.runtime_provider == "deepagents"
