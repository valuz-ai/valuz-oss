"""AgentConfig and SubAgentDef — global capability presets.

An AgentConfig is a reusable capability template. Sessions reference an
agent at creation time and seed their own ``instructions`` / ``skills`` /
``mcp_servers`` from the agent's defaults; once the session exists, the
runtime reads those fields from the *session*, not the agent. The agent's
``tools`` / ``callable_agents`` / ``permission_mode`` / ``max_turns`` /
``max_cost_usd`` / ``effort`` / ``thinking`` / ``hooks`` remain
agent-level (runtime identity, not per-turn config).

``instructions`` is intentionally named — it is appended to the runtime's
default system prompt, not a replacement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.hooks import Hooks
from src.core.time_utils import now_ms
from src.core.tools import ToolDef
from src.core.types import EffortLevel, McpServerConfig, RuntimeProvider


@dataclass(frozen=True)
class SubAgentDef:
    """Declarative sub-agent definition — describes a specialist agent callable by a parent.

    Runtime decides how to execute:
      - ClaudeAgentRuntime -> AgentDefinition (built-in Agent tool, subprocess fork)
      - OpenAIAgentsRuntime -> Agent.as_tool() (in-process delegation)
    """

    name: str
    description: str
    prompt: str = ""
    tools: tuple[str, ...] = ()
    model: str | None = None
    skills: tuple[str, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentConfig:
    """Global agent template — immutable, persistable.

    Carries per-agent identity (tools/permission/budget/...) plus default
    capability values that sessions copy at creation time
    (instructions/skills/mcp_servers/model). The runtime reads identity
    fields from the agent and capability fields from the session.
    """

    id: str
    name: str
    model: str = "claude-sonnet-4-6"
    runtime_provider: RuntimeProvider = "claude_agent"
    instructions: str = ""

    tools: tuple[ToolDef, ...] = ()
    callable_agents: tuple[SubAgentDef, ...] = ()
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()

    hooks: Hooks | None = None

    permission_mode: Literal["default", "auto_review", "full_access"] = "full_access"

    max_turns: int = 50
    max_cost_usd: float = 10.0

    # Default effort prefilled into ``session.model_settings.effort`` at
    # session create. The runtime reads the session value, not this one
    # (cold-decoupled after create — same pattern as ``instructions``).
    # Widened to match ``ModelSettings.effort``; old ``max`` rows stay
    # valid, new ``xhigh`` is the recommended high tier for non-Anthropic
    # runtimes (codex / OpenAI). Runtimes that don't support a given
    # level map it down (codex / OpenAI: ``max`` -> ``xhigh``; Gemini:
    # ``xhigh|max`` -> ``high``).
    effort: EffortLevel | None = None
    # Legacy knob — runtimes no longer consume ``thinking`` directly
    # Kept on the dataclass for backward API compat; persisted as opaque
    # JSON. New code should use ``effort`` instead.
    thinking: dict[str, Any] | None = None

    status: Literal["active", "deleted"] = "active"
    created_at: int = field(default_factory=now_ms)  # Unix epoch ms (UTC)
    metadata: dict[str, Any] = field(default_factory=dict)
