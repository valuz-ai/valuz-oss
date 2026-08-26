"""Channel-declared ``max_input_tokens`` → langchain ``profile`` injection.

deepagents' SummarizationMiddleware computes its compaction defaults from
``model.profile``: with ``max_input_tokens`` present it triggers at 0.85 x
the window, without it it falls back to a FIXED 170k-token trigger. The
langchain profile registry only knows exact vendor model names, so gateway
aliases (``valuz-pro-anthropic``) get ``profile=None`` and every model behind
such an alias compacts at the same 170k regardless of its real window. The
runtime restores fraction-based compaction by passing the channel-declared
window as an explicit ``profile`` kwarg — and passes nothing when no value
was declared, so registry-known names keep their bundled profiles.
"""

# ruff: noqa: I001
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel `src` on the import path)

from src.core.agent_config import AgentConfig
from src.core.types import ModelProvider, ModelSettings, Session
from src.runtimes.deepagents.runtime import DeepAgentsRuntime


def _client(*, protocol: str, model: str, max_input_tokens: int | None):
    rt = object.__new__(DeepAgentsRuntime)
    rt.model = model
    rt.model_provider = ModelProvider(
        api_key="sk-fake",
        base_url="https://gateway.example.com/v1",
        api_protocol=protocol,  # type: ignore[arg-type]
    )
    session = Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="deepagents",
        model_settings=(
            ModelSettings(max_input_tokens=max_input_tokens) if max_input_tokens else None
        ),
    )
    return rt._build_model_client(session)


def test_anthropic_alias_gets_declared_profile() -> None:
    chat = _client(protocol="anthropic", model="valuz-pro-anthropic", max_input_tokens=200_000)
    assert chat.profile == {"max_input_tokens": 200_000}


def test_openai_alias_gets_declared_profile() -> None:
    chat = _client(protocol="openai_completion", model="valuz-lite", max_input_tokens=131_072)
    assert chat.profile == {"max_input_tokens": 131_072}


def test_alias_without_declaration_keeps_registry_miss() -> None:
    # No declaration → no profile kwarg → the alias stays a registry miss
    # (None) and SummarizationMiddleware uses its conservative fixed default.
    chat = _client(protocol="anthropic", model="valuz-pro-anthropic", max_input_tokens=None)
    assert chat.profile is None


def test_known_model_without_declaration_keeps_registry_profile() -> None:
    # Registry-known names must keep their bundled profile untouched.
    chat = _client(protocol="anthropic", model="claude-sonnet-4-6", max_input_tokens=None)
    assert chat.profile is not None
    assert chat.profile.get("max_input_tokens")


def test_declared_profile_does_not_break_max_tokens_workaround() -> None:
    # The alias max_tokens workaround (``_resolve_anthropic_max_tokens``)
    # reads the bundled registry directly — an explicit profile kwarg must
    # not regress unknown-alias output caps back to langchain's 4096 floor.
    chat = _client(protocol="anthropic", model="valuz-pro-anthropic", max_input_tokens=200_000)
    assert chat.max_tokens > 4096
