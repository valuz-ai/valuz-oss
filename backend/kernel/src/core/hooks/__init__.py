"""The Valuz hook bus — system-level, the same in every runtime.

Handlers register on :data:`hook_registry` (``registry.register(event,
handler, owner=..., tier=..., matcher=...)``); runtimes dispatch events
through it with their own behaviour as the chain's ``core``. See
docs/design/plugin-architecture/hooks-and-plugin-ui.md §3–§4 (commercial
repo) and :mod:`src.core.hooks.events` for the event list and payloads.
"""

from __future__ import annotations

from src.core.hooks.builtin import install_builtins
from src.core.hooks.chain import (
    DEFAULT_BUDGET_S,
    Handler,
    HookContext,
    HookError,
    HookSpec,
    Next,
    Tier,
    run_chain,
)
from src.core.hooks.commands import (
    RESERVED_COMMANDS,
    CommandRegistry,
    CommandSpec,
    command_registry,
    run_command,
)
from src.core.hooks.events import (
    AGENT_SPAWN,
    COMMAND_RUN,
    EVENT_NAMES,
    PROMPT_SUBMIT,
    SESSION_COMPACT,
    SESSION_END,
    SESSION_START,
    TOOL_CALL,
    TOOL_CHECK,
    TURN_COMPLETE,
    TURN_START,
    CommandOutput,
    CompactDecision,
    HookEvent,
    PromptDecision,
    SessionRef,
    ToolDecision,
    ToolOutcome,
    ToolRef,
)
from src.core.hooks.freeze import FrozenDict, freeze, thaw
from src.core.hooks.registry import HookRegistry, SessionHooks, hook_registry
from src.core.hooks.tool_identity import (
    mcp_tool_name,
    mcp_tool_ref,
    native_tool_ref,
    toolkit_tool_ref,
)

install_builtins(hook_registry)

from src.core.hooks.overlays import install_overlay_modules  # noqa: E402

install_overlay_modules(hook_registry)

__all__ = [
    "AGENT_SPAWN",
    "COMMAND_RUN",
    "CommandOutput",
    "CommandRegistry",
    "CommandSpec",
    "CompactDecision",
    "DEFAULT_BUDGET_S",
    "EVENT_NAMES",
    "FrozenDict",
    "Handler",
    "HookContext",
    "HookEvent",
    "HookError",
    "HookRegistry",
    "HookSpec",
    "Next",
    "PROMPT_SUBMIT",
    "PromptDecision",
    "RESERVED_COMMANDS",
    "SESSION_COMPACT",
    "SESSION_END",
    "SESSION_START",
    "SessionHooks",
    "SessionRef",
    "TOOL_CALL",
    "TOOL_CHECK",
    "TURN_COMPLETE",
    "TURN_START",
    "Tier",
    "ToolDecision",
    "ToolOutcome",
    "ToolRef",
    "command_registry",
    "freeze",
    "hook_registry",
    "install_builtins",
    "mcp_tool_name",
    "mcp_tool_ref",
    "native_tool_ref",
    "run_chain",
    "run_command",
    "thaw",
    "toolkit_tool_ref",
]
