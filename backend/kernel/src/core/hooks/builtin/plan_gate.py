"""Plan mode for runtimes without a native one, as a Valuz builtin handler.

Claude, Codex and DSH have their own plan mode (docs/design/plugin-
architecture/runtime-capabilities.md §1); DeepAgents does not. Native-first,
bus-fills-gaps (ADR-033 §9): while a DeepAgents session is in plan mode, its
own tools that run commands or change files are refused before they run —
the model reads the refusal and is steered to present its plan. Read-only
tools, sub-agent research and todos stay available, as in Claude's plan mode.
The plan instructions and the proposal card live in the runtime
(``runtimes/deepagents``); toolkit tools are gated there on ``read_only``.
"""

from __future__ import annotations

from src.core.hooks.chain import HookContext, Next
from src.core.hooks.events import TOOL_CALL, HookEvent, SessionRef, ToolOutcome
from src.core.hooks.registry import HookRegistry

OWNER = "valuz.plan-gate"

# Runtimes whose plan mode is this handler (the others lower it natively).
PLAN_GATE_RUNTIMES = frozenset({"deepagents"})
_MUTATING_KINDS = ("shell", "file_write", "file_edit")

PLAN_MODE_DENY_REASON = (
    "Plan mode is on: this tool runs commands or changes files, so it is "
    "disabled until the user approves a plan. Keep investigating with "
    "read-only tools, then present your complete plan inside "
    "<proposed_plan>…</proposed_plan>."
)


def _applies(session: SessionRef) -> bool:
    return session.runtime_provider in PLAN_GATE_RUNTIMES and session.mode == "plan"


async def plan_mode_gate(_ctx: HookContext, _event: HookEvent, _next: Next) -> ToolOutcome:
    return ToolOutcome(content=PLAN_MODE_DENY_REASON, is_error=True, executed=False)


def install(registry: HookRegistry) -> None:
    registry.register(
        TOOL_CALL,
        plan_mode_gate,
        owner=OWNER,
        tier="builtin",
        matcher={"tool.kind": list(_MUTATING_KINDS), "tool.source": "native"},
        applies=_applies,
    )


__all__ = ["OWNER", "PLAN_GATE_RUNTIMES", "PLAN_MODE_DENY_REASON", "install", "plan_mode_gate"]
