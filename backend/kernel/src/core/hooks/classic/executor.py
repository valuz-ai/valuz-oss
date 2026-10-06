"""Classic hooks on the hook bus, for runtimes that do not run them.

Claude and Codex run a workspace's ``hooks`` config themselves and DSH runs it
through its own bridge plugins (``runtimes/deepseek_harness/composition.py``);
DeepAgents has nothing, so the bus fills the gap (ADR-033 §9) with the same
subset DSH's bridges run:

==================  ===================  ===========================================
classic event       bus event            what a hook can do here
==================  ===================  ===========================================
SessionStart        prompt.submit        add context (once, before the first prompt)
UserPromptSubmit    prompt.submit        block the prompt, add context
PreToolUse          tool.call            block (``deny``; ``ask`` also blocks — a
                                         tool call cannot open an approval); rewrite
                                         the input (``updatedInput``) only outside
                                         ``default`` permission mode, where the
                                         user has already approved this input
PostToolUse         tool.call            feedback / context appended to the result
Stop                turn.complete        observe (cannot force another turn)
SubagentStart       agent.spawn          observe
==================  ===================  ===========================================

Hooks see Claude Code's tool names (``Bash``, ``Write`` …), so one config
matches the same calls everywhere. They run only in a trusted workspace (H0),
in the user tier, one after another in config order.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.core.hooks.chain import HookContext, Next
from src.core.hooks.classic.config import WorkspaceHooks, load_workspace_hooks
from src.core.hooks.classic.runner import Merged, run_hooks
from src.core.hooks.events import (
    AGENT_SPAWN,
    PROMPT_SUBMIT,
    TOOL_CALL,
    TURN_COMPLETE,
    HookEvent,
    PromptDecision,
    SessionRef,
    ToolOutcome,
)
from src.core.hooks.freeze import thaw
from src.core.hooks.registry import HookRegistry

logger = logging.getLogger(__name__)

OWNER = "valuz.classic-hooks"

#: Runtimes whose classic hooks the bus runs (the others run them natively).
BUS_RUNTIMES = frozenset({"deepagents"})

#: DeepAgents' own tools under the names classic hooks match on.
_CLAUDE_NAMES = {
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "ls": "LS",
    "glob": "Glob",
    "grep": "Grep",
    "execute": "Bash",
    "task": "Task",
    "write_todos": "TodoWrite",
}
#: The kernel toolkit is the ``harness`` MCP server on Claude and Codex.
_TOOLKIT_SERVER = "harness"
_PATH_KEYS = ("file_path", "path")

_PERMISSION_MODES = {
    "default": "default",
    "auto_review": "default",
    "full_access": "bypassPermissions",
}

#: Sessions whose SessionStart hooks already ran in this process.
_started: set[str] = set()


def _hooks_for(ref: SessionRef) -> WorkspaceHooks | None:
    if ref.runtime_provider not in BUS_RUNTIMES or not ref.trusted or ref.bare or not ref.cwd:
        return None
    return load_workspace_hooks(ref.cwd)


def _applies(*events: str):  # noqa: ANN202
    def applies(ref: SessionRef) -> bool:
        hooks = _hooks_for(ref)
        return hooks is not None and hooks.has(*events)

    return applies


def _payload(ref: SessionRef, event_name: str, **fields: Any) -> dict[str, Any]:
    return {
        "session_id": ref.session_id,
        "transcript_path": "",
        "cwd": ref.cwd,
        "hook_event_name": event_name,
        "permission_mode": "plan"
        if ref.mode == "plan"
        else _PERMISSION_MODES.get(ref.permission_mode, "default"),
        "model": ref.model,
        **fields,
    }


async def _run(
    hooks: WorkspaceHooks, ref: SessionRef, event_name: str, query: str, **fields: Any
) -> Merged | None:
    matched = hooks.matching(event_name, query)
    if not matched:
        return None
    return await run_hooks(
        matched, _payload(ref, event_name, **fields), cwd=ref.cwd, event_name=event_name
    )


# -- prompts -----------------------------------------------------------------


async def on_prompt_submit(_ctx: HookContext, event: HookEvent, next_: Next) -> Any:
    ref = event.session
    hooks = _hooks_for(ref)
    if hooks is None:
        return await next_()
    context: list[str] = []
    if ref.session_id not in _started:
        _started.add(ref.session_id)
        started = await _run(hooks, ref, "SessionStart", "startup", source="startup")
        if started is not None:
            context.extend((*started.context, *started.plain))
    text = str(event.get("text") or "")
    submitted = await _run(hooks, ref, "UserPromptSubmit", "", prompt=text)
    if submitted is not None:
        if submitted.decision == "deny":
            return PromptDecision(
                text=text, drop=submitted.reason or "Blocked by a UserPromptSubmit hook."
            )
        context.extend((*submitted.context, *submitted.plain))
    decision = await next_()
    if context and isinstance(decision, PromptDecision) and not decision.drop:
        return replace(decision, context=(*decision.context, *context))
    return decision


# -- tools -------------------------------------------------------------------


def _tool_name(tool: Mapping[str, Any]) -> str:
    name = str(tool.get("name") or "")
    source = tool.get("source")
    if source == "toolkit":
        return f"mcp__{_TOOLKIT_SERVER}__{name}"
    if source == "native":
        return _CLAUDE_NAMES.get(name, name)
    return name


def _real_path(path: str, cwd: str) -> str:
    """DeepAgents' workspace-relative ``/a.md`` as the host path a script can use."""
    raw = Path(path)
    root = Path(cwd)
    if raw.is_absolute() and (raw == root or root in raw.parents):
        return path
    return str(root / path.lstrip("/"))


def _hook_input(tool: Mapping[str, Any], data: Any, cwd: str) -> dict[str, Any]:
    tool_input = dict(data) if isinstance(data, Mapping) else {}
    if tool.get("source") == "native":
        for key in _PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                tool_input[key] = _real_path(value, cwd)
    return tool_input


def _append(content: Any, text: str) -> Any:
    """*text* after a tool result, keeping the result's own shape."""
    value = thaw(content)
    if isinstance(value, list):
        return [*value, {"type": "text", "text": text}]
    if isinstance(value, str):
        return f"{value}\n\n{text}" if value else text
    return text if value in (None, "") else f"{value}\n\n{text}"


async def on_tool_call(_ctx: HookContext, event: HookEvent, next_: Next) -> Any:
    ref = event.session
    hooks = _hooks_for(ref)
    if hooks is None:
        return await next_()
    tool = thaw(event.get("tool")) or {}
    name = _tool_name(tool)
    tool_input = _hook_input(tool, thaw(event.get("input")), ref.cwd)
    tool_use_id = event.get("tool_use_id")

    notes: list[str] = []
    pre = await _run(
        hooks,
        ref,
        "PreToolUse",
        name,
        tool_name=name,
        tool_input=tool_input,
        tool_use_id=tool_use_id,
    )
    if pre is not None:
        if pre.decision in ("deny", "ask"):
            reason = pre.reason or "Blocked by a PreToolUse hook."
            return ToolOutcome(content=f"Error: {reason}", is_error=True, executed=False)
        if pre.updated_input is not None:
            if ref.permission_mode == "default":
                # The bus keeps an approved input as approved (chain.py).
                logger.info("classic PreToolUse updatedInput ignored in default permission mode")
            else:
                event = event.with_data(input=dict(pre.updated_input))
                tool_input = dict(pre.updated_input)
        notes.extend(pre.context)

    outcome = await next_(event)
    if not isinstance(outcome, ToolOutcome):
        return outcome
    if outcome.executed and not outcome.is_error:
        post = await _run(
            hooks,
            ref,
            "PostToolUse",
            name,
            tool_name=name,
            tool_input=tool_input,
            tool_use_id=tool_use_id,
            tool_response=thaw(outcome.content),
        )
        if post is not None:
            if post.decision == "deny":
                notes.append(post.reason or "Blocked by a PostToolUse hook.")
            notes.extend(post.context)
    if notes:
        return replace(outcome, content=_append(outcome.content, "\n\n".join(notes)))
    return outcome


# -- observe-only ------------------------------------------------------------


async def on_turn_complete(_ctx: HookContext, event: HookEvent, next_: Next) -> Any:
    ref = event.session
    hooks = _hooks_for(ref)
    if hooks is not None:
        stopped = await _run(
            hooks,
            ref,
            "Stop",
            "",
            stop_hook_active=False,
            last_assistant_message=str(event.get("assistant_text") or ""),
        )
        if stopped is not None and stopped.decision == "deny":
            logger.info(
                "classic Stop hook asked to continue session %s; not supported here",
                ref.session_id,
            )
    return await next_()


async def on_agent_spawn(_ctx: HookContext, event: HookEvent, next_: Next) -> Any:
    ref = event.session
    hooks = _hooks_for(ref)
    if hooks is not None:
        agent_type = str(event.get("agent_type") or "general-purpose")
        await _run(hooks, ref, "SubagentStart", agent_type, agent_id="", agent_type=agent_type)
    return await next_()


def install(registry: HookRegistry) -> None:
    # Hooks run as long as they need (each has its own timeout), so the
    # chain's per-handler budget is off.
    common: dict[str, Any] = {"owner": OWNER, "tier": "user", "budget_s": 0}
    registry.register(
        PROMPT_SUBMIT,
        on_prompt_submit,
        applies=_applies("SessionStart", "UserPromptSubmit"),
        **common,
    )
    registry.register(
        TOOL_CALL, on_tool_call, applies=_applies("PreToolUse", "PostToolUse"), **common
    )
    registry.register(TURN_COMPLETE, on_turn_complete, applies=_applies("Stop"), **common)
    registry.register(AGENT_SPAWN, on_agent_spawn, applies=_applies("SubagentStart"), **common)


__all__ = ["BUS_RUNTIMES", "OWNER", "install"]
