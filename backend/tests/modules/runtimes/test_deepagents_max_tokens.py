"""Output cap (``max_tokens``) for the deepagents runtime, every protocol.

Left unset, each langchain backend falls to a floor far below what the model
can emit — ChatAnthropic fills 4096 for names its profile registry doesn't
know (gateway aliases like ``openrouter/claude-sonnet-4-5``), and on the
OpenAI-compatible path the provider's server-side default applies (8192 on
DeepSeek) — so a long final answer truncates mid-sentence while the turn still
ends as a clean ``end_turn`` (#1131). The runtime now always sends
``MODEL_MAX_TOKENS`` (32k, the industry default) on every protocol, raised by
a larger declared ``ModelSettings.max_tokens``; these tests pin the value and
the wire field each client actually sends.
"""

# ruff: noqa: I001
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel `src` on the import path)

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import BaseChatOpenAI

from src.core.agent_config import AgentConfig
from src.core.types import ModelProvider, ModelSettings, Session
from src.runtimes.deepagents.runtime import (
    MODEL_MAX_TOKENS,
    DeepAgentsRuntime,
    _is_openai_family,
    _resolve_max_tokens,
)

_GATEWAY = "https://gateway.example.com/v1"


# --- resolver -----------------------------------------------------------------


def test_default_is_the_industry_cap() -> None:
    assert _resolve_max_tokens(None) == MODEL_MAX_TOKENS
    assert _resolve_max_tokens(ModelSettings()) == MODEL_MAX_TOKENS
    # Above both floors the issue was filed against: ChatAnthropic's 4096
    # and DeepSeek's 8192 server-side default.
    assert MODEL_MAX_TOKENS > 8192


def test_declared_setting_can_only_raise_the_cap() -> None:
    assert _resolve_max_tokens(ModelSettings(max_tokens=64_000)) == 64_000
    assert _resolve_max_tokens(ModelSettings(max_tokens=1234)) == MODEL_MAX_TOKENS
    assert _resolve_max_tokens(ModelSettings(max_tokens=0)) == MODEL_MAX_TOKENS


# --- OpenAI wire field ----------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "base_url"),
    [
        # No base_url = the SDK's first-party default; explicit first-party host.
        ("brand-new-model", None),
        ("brand-new-model", "https://api.openai.com/v1"),
        ("brand-new-model", "HTTPS://API.OPENAI.COM/v1"),
        # OpenAI's own families, wherever they are served.
        ("gpt-4o", _GATEWAY),
        ("GPT-5.4", _GATEWAY),
        ("o3-mini", _GATEWAY),
        ("chatgpt-4o-latest", _GATEWAY),
        ("openrouter/gpt-5", _GATEWAY),
    ],
)
def test_openai_family_is_built_on_chat_openai(model: str, base_url: str | None) -> None:
    assert _is_openai_family(model, base_url)


@pytest.mark.parametrize(
    ("model", "base_url"),
    [
        ("deepseek-v4-pro", "https://api.deepseek.com/v1"),
        ("kimi-k2", _GATEWAY),
        ("openrouter/qwen3-max", _GATEWAY),
        # Open-weight gpt-oss is served by third parties, which read the
        # legacy field.
        ("gpt-oss-120b", _GATEWAY),
        ("openai/gpt-oss-20b", _GATEWAY),
    ],
)
def test_everyone_else_is_built_on_the_base(model: str, base_url: str | None) -> None:
    assert not _is_openai_family(model, base_url)


# --- end-to-end client construction ------------------------------------------------


def _client(
    *,
    protocol: str,
    model: str,
    base_url: str | None = _GATEWAY,
    max_tokens: int | None = None,
):
    rt = object.__new__(DeepAgentsRuntime)
    rt.model = model
    rt.model_provider = ModelProvider(
        api_key="sk-fake",
        base_url=base_url,
        api_protocol=protocol,  # type: ignore[arg-type]
    )
    session = Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="deepagents",
        model_settings=ModelSettings(max_tokens=max_tokens),
    )
    return rt._build_model_client(session)


def _cap_fields(chat) -> dict[str, int]:
    payload = chat._get_request_payload([HumanMessage(content="hi")])
    return {k: v for k, v in payload.items() if k in ("max_tokens", "max_completion_tokens")}


def test_chat_anthropic_always_gets_the_cap() -> None:
    """Both a registry-known name (whose bundled default would otherwise
    apply) and an unknown alias (langchain's 4096 floor) land on the cap."""
    assert _client(protocol="anthropic", model="claude-sonnet-4-5").max_tokens == MODEL_MAX_TOKENS
    chat = _client(protocol="anthropic", model="my-custom-gateway-model")
    assert chat.max_tokens == MODEL_MAX_TOKENS


def test_third_party_model_sends_max_tokens_via_the_base_class() -> None:
    """The issue's case: DeepSeek through the OpenAI-compatible path. The cap
    must reach the wire as ``max_tokens`` — ``ChatOpenAI``'s own rename to
    ``max_completion_tokens`` is silently ignored by DeepSeek — and must not
    displace the DeepSeek ``thinking`` workaround."""
    chat = _client(
        protocol="openai_completion",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
    )
    assert type(chat) is BaseChatOpenAI
    assert chat.max_tokens == MODEL_MAX_TOKENS
    assert _cap_fields(chat) == {"max_tokens": MODEL_MAX_TOKENS}
    payload = chat._get_request_payload([HumanMessage(content="hi")])
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}


def test_openai_model_sends_max_completion_tokens_via_chat_openai() -> None:
    """OpenAI's reasoning models reject ``max_tokens``, so OpenAI's own field
    name stays for them — first-party or behind a gateway prefix."""
    chat = _client(protocol="openai_completion", model="gpt-5", base_url=None)
    assert type(chat) is ChatOpenAI
    assert _cap_fields(chat) == {"max_completion_tokens": MODEL_MAX_TOKENS}
    chat = _client(protocol="openai_completion", model="openrouter/gpt-5")
    assert _cap_fields(chat) == {"max_completion_tokens": MODEL_MAX_TOKENS}


def test_chat_openai_declared_setting_raises_the_cap() -> None:
    chat = _client(
        protocol="openai_completion",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        max_tokens=64_000,
    )
    assert _cap_fields(chat) == {"max_tokens": 64_000}


def test_chat_gemini_gets_the_cap() -> None:
    assert _client(protocol="gemini", model="gemini-2.5-pro").max_output_tokens == MODEL_MAX_TOKENS
    chat = _client(protocol="gemini", model="my-gateway/gemini-custom", max_tokens=48_000)
    assert chat.max_output_tokens == 48_000
