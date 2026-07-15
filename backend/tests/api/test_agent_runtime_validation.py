"""The agents API rejects invalid ``runtime`` values at write time.

Regression for #501: an agent created with a plausible-but-wrong runtime
(e.g. ``claude-agent``) was accepted silently and only exploded at dispatch.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from valuz_agent.api.routes.agents import (
    CreateAgentRequest,
    CreateBlankAgentRequest,
    UpdateAgentRequest,
)

VALID_RUNTIMES = ("claude_agent", "codex", "deepagents")
INVALID_RUNTIMES = ("claude-agent", "Claude_Agent", "openai", "", "claude")


@pytest.mark.parametrize("runtime", VALID_RUNTIMES)
def test_create_agent_accepts_valid_runtime(runtime: str) -> None:
    req = CreateAgentRequest(name="t", runtime=runtime)
    assert req.runtime == runtime


@pytest.mark.parametrize("runtime", INVALID_RUNTIMES)
def test_create_agent_rejects_invalid_runtime(runtime: str) -> None:
    with pytest.raises(ValidationError, match="runtime"):
        CreateAgentRequest(name="t", runtime=runtime)


@pytest.mark.parametrize("runtime", INVALID_RUNTIMES)
def test_create_blank_agent_rejects_invalid_runtime(runtime: str) -> None:
    with pytest.raises(ValidationError, match="runtime"):
        CreateBlankAgentRequest(name="t", runtime=runtime)


@pytest.mark.parametrize("runtime", INVALID_RUNTIMES)
def test_update_agent_rejects_invalid_runtime(runtime: str) -> None:
    with pytest.raises(ValidationError, match="runtime"):
        UpdateAgentRequest(runtime=runtime)


def test_update_agent_accepts_none_runtime() -> None:
    req = UpdateAgentRequest(runtime=None)
    assert req.runtime is None


def test_create_agent_defaults_to_claude_agent() -> None:
    req = CreateAgentRequest(name="t")
    assert req.runtime == "claude_agent"
