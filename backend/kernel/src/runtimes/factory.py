"""Runtime factory — picks a RuntimePort based on ``Session.runtime_provider``.

Dispatch is now an explicit enum chosen by the caller at session creation:

* ``"claude_agent"`` -> ClaudeAgentRuntime
* ``"codex"``        -> CodexRuntime
* ``"deepagents"``   -> DeepAgentsRuntime

``model`` and ``model_provider`` are optional for the first two — each SDK
falls back to its ambient credentials. DeepAgents needs an explicit
langchain model client, so both fields are required when the caller picks
it; the factory raises ``ValueError`` otherwise (the route layer is the
primary enforcement point and surfaces a 400, but defending here keeps
the contract honest for direct callers).

The ``model_provider.api_protocol`` field is also constrained per
runtime — see ``ALLOWED_PROTOCOLS_BY_RUNTIME`` below. The constraint
catches mismatches like ``runtime_provider=claude_agent`` +
``api_protocol=gemini`` at session-create time rather than letting the
runtime crash on first turn with an opaque langchain / SDK error.
"""

from __future__ import annotations

from src.core.agent_config import AgentConfig
from src.core.events import EventSink
from src.core.runtime_port import RuntimePort
from src.core.tool_registry import build_toolkit_for_config
from src.core.tools import ToolKit
from src.core.types import ApiProtocol, RuntimeProvider, Session

# Per-runtime allowlist for ``ModelProvider.api_protocol``. Source of
# truth for the cross-runtime "which gateway protocol can which runtime
# speak" matrix. Mirrors the openapi ``ModelProviderInput`` description.
#
# * ``claude_agent`` — only ``anthropic``; the SDK is hard-wired to
#   Anthropic's Messages API.
# * ``codex`` — only ``openai_response``; codex uses the Responses API
#   ("``responses`` is the only supported value, default when omitted").
# * ``deepagents`` — three langchain backends:
#   ``anthropic`` (ChatAnthropic), ``openai_completion`` (ChatOpenAI
#   chat completions), ``gemini`` (ChatGoogleGenerativeAI).
ALLOWED_PROTOCOLS_BY_RUNTIME: dict[RuntimeProvider, frozenset[ApiProtocol]] = {
    "claude_agent": frozenset({"anthropic"}),
    "codex": frozenset({"openai_response"}),
    "deepagents": frozenset({"anthropic", "openai_completion", "gemini"}),
}


def validate_api_protocol(
    runtime_provider: RuntimeProvider,
    api_protocol: ApiProtocol | None,
) -> None:
    """Raise ``ValueError`` if ``api_protocol`` is not allowed for the
    runtime. ``None`` is accepted (caller chose to fall back to the
    runtime's ambient credentials — only valid for runtimes that
    support a None ``model_provider``, which the create-session route
    enforces separately).

    Pure helper so the route layer can call it before constructing the
    Session for a clean 400, and the factory can call it as defense in
    depth for direct callers.
    """
    if api_protocol is None:
        return
    allowed = ALLOWED_PROTOCOLS_BY_RUNTIME.get(runtime_provider)
    if allowed is None:
        raise ValueError(f"Unsupported runtime_provider: {runtime_provider!r}")
    if api_protocol not in allowed:
        raise ValueError(
            f"api_protocol={api_protocol!r} is not supported for "
            f"runtime_provider={runtime_provider!r}; allowed: "
            f"{sorted(allowed)}"
        )


def create_runtime(
    config: AgentConfig,
    session: Session,
    event_sink: EventSink,
    toolkit: ToolKit | None = None,
    workspace_root: str = "",
) -> RuntimePort:
    """Create the runtime that hosts ``session.model`` for this agent."""
    resolved_toolkit = toolkit or build_toolkit_for_config(config.tools)
    provider = session.runtime_provider

    # Validate api_protocol against the chosen runtime as defense in
    # depth — the route layer is the primary 400 gate.
    if session.model_provider is not None:
        validate_api_protocol(provider, session.model_provider.api_protocol)

    if provider == "claude_agent":
        from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

        return ClaudeAgentRuntime(
            config,
            session.model,
            event_sink,
            resolved_toolkit,
            workspace_root=workspace_root,
            model_provider=session.model_provider,
            model_settings=session.model_settings,
        )

    if provider == "codex":
        from src.runtimes.codex.runtime import CodexRuntime

        return CodexRuntime(
            config,
            session.model,
            event_sink,
            resolved_toolkit,
            workspace_root=workspace_root,
            model_provider=session.model_provider,
            model_settings=session.model_settings,
        )

    if provider == "deepagents":
        if session.model_provider is None or not session.model.strip():
            raise ValueError(
                "DeepAgentsRuntime requires both `model` and `model_provider` "
                "(langchain needs an explicit model client)."
            )
        from src.runtimes.deepagents.runtime import DeepAgentsRuntime

        return DeepAgentsRuntime(
            config,
            session.model,
            event_sink,
            resolved_toolkit,
            workspace_root=workspace_root,
            model_provider=session.model_provider,
            model_settings=session.model_settings,
        )

    raise ValueError(f"Unsupported runtime_provider: {provider!r}")
