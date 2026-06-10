"""The advisor tool is Anthropic-API-only; gate it off behind gateways.

``ClaudeAgentRuntime._build_model_provider_env`` forces
``CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1`` whenever the per-session provider
carries a custom ``base_url`` (an LLM gateway, where the server-executed
advisor may not be forwarded to the Anthropic API and the CLI would error
every turn), and leaves it off the first-party Anthropic path
(``base_url is None``) where the advisor works. The first-party branch also
wipes any inherited opt-out so a stale parent export can't leak through.

See https://code.claude.com/docs/en/advisor.md.
"""

from __future__ import annotations

from unittest.mock import patch

# Side-effect import: puts the kernel ``src/`` on sys.path at module load,
# before any ``from src.*`` below resolves. Kept in its own group (away from
# the ``from src.*`` imports, which live inside the helper) so isort can't
# reorder the side-effect after its dependents.
import kernel  # noqa: F401

_ADVISOR = "CLAUDE_CODE_DISABLE_ADVISOR_TOOL"


def _env_for(
    *, base_url: str | None, has_provider: bool = True, model: str = "claude-sonnet-4-6"
) -> dict[str, str] | None:
    from src.core.types import ModelProvider
    from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

    provider = ModelProvider(api_key="sk-x", base_url=base_url) if has_provider else None
    # The method only reads ``model_provider`` and ``model``; bypass the
    # heavy SDK-touching __init__ and set just those two attributes.
    rt = object.__new__(ClaudeAgentRuntime)
    rt.model_provider = provider
    rt.model = model
    return rt._build_model_provider_env()


def test_no_provider_returns_none() -> None:
    """The subscription / first-party path leaves ``options.env`` unset
    entirely, so the SDK inherits the parent env verbatim."""
    assert _env_for(base_url=None, has_provider=False) is None


def test_custom_base_url_disables_advisor() -> None:
    env = _env_for(base_url="https://gw.example/v1")
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "https://gw.example/v1"
    assert env[_ADVISOR] == "1"


def test_non_claude_gateway_model_disables_advisor() -> None:
    """A non-Claude alias always runs through a gateway base_url, so the
    advisor is off there too — covered by the same base_url gate."""
    env = _env_for(base_url="https://gw.example/v1", model="deepseek-chat")
    assert env is not None
    assert env[_ADVISOR] == "1"


def test_first_party_keeps_advisor_on() -> None:
    """``base_url is None`` = real api.anthropic.com, where the advisor
    works; we must not set the opt-out."""
    env = _env_for(base_url=None)
    assert env is not None
    assert _ADVISOR not in env


def test_first_party_wipes_inherited_advisor_flag() -> None:
    """A stale parent-env opt-out must not leak onto the first-party path —
    symmetric with the ANTHROPIC_BASE_URL wipe."""
    with patch.dict("os.environ", {_ADVISOR: "1"}, clear=False):
        env = _env_for(base_url=None)
    assert env is not None
    assert _ADVISOR not in env
